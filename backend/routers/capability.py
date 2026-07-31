"""Capability router — /api/capability endpoints.

The outcome-anchor + Efficacy Predictor layer. Surfaces:
  - GET  /api/capability/anchors          -> the unified outcome-anchor index
  - GET  /api/capability/anchors/{source} -> one source's anchor (publication, cohort, outcomes, signals)
  - GET  /api/capability/cohorts          -> cohorts available for efficacy modeling
  - POST /api/capability/efficacy         -> train a Cox/logistic model with user-configured inputs

Read-only. Grounded in the byte-verified outcome-anchor CSVs produced by the
deep-extraction workstreams (SPECTRUM, BriTROC-1, ARGO/POG570, PDS).
"""

from __future__ import annotations

import json
import os
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/capability", tags=["capability"])

# Location of the outcome-anchor artifacts (baked into the deployment image or
# mounted). Falls back to a repo-relative data dir for local dev.
_ANCHOR_DIR = os.environ.get("ZETA_ANCHOR_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "anchors"))
_INDEX_FILE = os.path.join(_ANCHOR_DIR, "outcome_anchor_index.json")

_index_cache: Optional[dict] = None


def _load_index() -> dict:
    global _index_cache
    if _index_cache is None:
        if not os.path.exists(_INDEX_FILE):
            raise HTTPException(status_code=503, detail="outcome-anchor index not available on this deployment")
        with open(_INDEX_FILE) as f:
            _index_cache = json.load(f)
    return _index_cache


@router.get("/anchors")
def list_anchors() -> dict:
    """Return the unified outcome-anchor index (all sources)."""
    idx = _load_index()
    return {"generated_utc": idx.get("generated_utc"), "n_sources": len(idx.get("sources", [])),
            "sources": [
                {"source_id": s["source_id"], "name": s["name"], "cancer_type": s["cancer_type"],
                 "is_trial": s["is_trial"], "cohort_n": s["cohort_n"],
                 "efficacy_ready": s["efficacy_ready"], "model_target": s["model_target"],
                 "publication": s.get("publication", {})}
                for s in idx.get("sources", [])
            ]}


@router.get("/anchors/{source_id}")
def get_anchor(source_id: str) -> dict:
    """Return one source's full anchor: publication, cohort, outcome vars, signal vars, files."""
    idx = _load_index()
    for s in idx.get("sources", []):
        if s["source_id"].lower() == source_id.lower():
            return s
    raise HTTPException(status_code=404, detail=f"unknown source_id '{source_id}'")


# ---- Efficacy Predictor ----

class EfficacyRequest(BaseModel):
    cohort: str = Field(..., description="spectrum | britroc")
    analysis: str = Field(..., description="os | pfs | platinum_sensitivity")
    features: list[str] = Field(..., description="feature columns, e.g. ['is_fbi'] or ['LST_score','CCNE1']")
    cv_folds: int = Field(5, ge=2, le=10)


# cohort -> (loader config). CSVs resolved under _ANCHOR_DIR.
_COHORT_FILES = {
    "spectrum": {"file": "synapse/survival_table.json", "kind": "json"},
    "britroc": {"file": "ega/britroc_outcome_anchor.csv", "kind": "csv"},
    "pds": {"file": "pds/pds_outcome_anchor.csv", "kind": "csv"},
}


def _load_cohort(name: str) -> pd.DataFrame:
    cfg_ = _COHORT_FILES.get(name)
    if not cfg_:
        raise HTTPException(status_code=404, detail=f"unknown cohort '{name}'. Available: {list(_COHORT_FILES)}")
    path = os.path.join(_ANCHOR_DIR, cfg_["file"])
    if not os.path.exists(path):
        raise HTTPException(status_code=503, detail=f"cohort file not available: {cfg_['file']}")
    if cfg_["kind"] == "json":
        df = pd.read_json(path)
        df["os_days"] = df["os_time"]; df["os_ev"] = df["os_event"]
        df["pfs_days"] = df["pfs_time"]; df["pfs_ev"] = df["pfs_event"]
        df["is_fbi"] = (df["group"] == "FBI").astype(int)
    elif name == "pds":
        df = pd.read_csv(path)
        # months -> days for consistency with other cohorts
        df["os_days"] = df["os_mos"] * 30.44; df["os_ev"] = df["os_event"]
        df["pfs_days"] = df["pfs_mos"] * 30.44; df["pfs_ev"] = df["pfs_event"]
        df["dfs_days"] = df["dfs_mos"] * 30.44; df["dfs_ev"] = df["dfs_event"]
        # numeric arm indicator within each trial (1 = first arm, 0 = reference)
        df["arm_str"] = df["arm"].astype(str)
        df["arm_ind"] = df.groupby("caslib")["arm_str"].transform(lambda s: (s == s.mode().iloc[0]).astype(int))
    else:
        df = pd.read_csv(path)
        df["os_days"] = df["os"]; df["os_ev"] = df["status"]
        df["pfs_days"] = df["pfs"]; df["pfs_ev"] = df["status"]
        df["pt_resistant"] = (df["pt_sensitivity_at_reg"] == "resistant").astype(int)
    return df


@router.get("/cohorts")
def list_cohorts() -> dict:
    """List cohorts available for efficacy modeling + their features/targets."""
    return {"cohorts": [
        {"cohort": "spectrum", "n": 39, "cancer": "ovarian HGSOC",
         "features": ["is_fbi"], "targets": ["os", "pfs"]},
        {"cohort": "britroc", "n": 273, "cancer": "ovarian HGSOC (relapse)",
         "features": ["LST_score", "fraction_genome_altered", "CCNE1", "KRAS", "MYC", "age"],
         "targets": ["os", "pfs", "platinum_sensitivity"]},
        {"cohort": "pds", "n": 12069, "cancer": "multi (breast/colorectal/head&neck/lung RCTs)",
         "features": ["arm_ind"], "targets": ["os", "pfs", "dfs"],
         "note": "RCT arm indicator within trial; filter by caslib for single-trial model"},
    ]}


@router.post("/efficacy")
def run_efficacy(req: EfficacyRequest) -> dict:
    """Train a Cox PH (os/pfs) or logistic (platinum_sensitivity) model with CV."""
    from lifelines import CoxPHFitter
    from lifelines.statistics import proportional_hazard_test
    from sklearn.model_selection import KFold
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    df = _load_cohort(req.cohort)
    feats = req.features
    missing = [f for f in feats if f not in df.columns]
    if missing:
        raise HTTPException(status_code=400, detail=f"unknown features {missing}. See /api/capability/cohorts")

    if req.analysis in ("os", "pfs"):
        time_col, event_col = f"{req.analysis}_days", f"{req.analysis}_ev"
        d = df.dropna(subset=feats + [time_col, event_col]).copy()
        d = d[d[time_col] > 0]
        if len(d) < 20:
            raise HTTPException(status_code=400, detail=f"insufficient complete cases n={len(d)}")
        kf = KFold(n_splits=min(req.cv_folds, max(2, len(d)//8)), shuffle=True, random_state=42)
        cindices = []
        for tr, te in kf.split(d):
            cph = CoxPHFitter(penalizer=0.1)
            try:
                cph.fit(d.iloc[tr][[time_col, event_col] + feats], duration_col=time_col, event_col=event_col)
                cindices.append(cph.score(d.iloc[te][[time_col, event_col] + feats], scoring_method="concordance_index"))
            except Exception:
                pass
        cph = CoxPHFitter(penalizer=0.1)
        cph.fit(d[[time_col, event_col] + feats], duration_col=time_col, event_col=event_col)
        try:
            ph = proportional_hazard_test(cph, d[[time_col, event_col] + feats], time_transform="rank")
            ph_p = {k: round(float(v), 4) for k, v in zip(ph.summary.index, ph.summary["p"])}
        except Exception:
            ph_p = {}
        return {"cohort": req.cohort, "analysis": req.analysis, "model": "cox_ph",
                "n": len(d), "events": int(d[event_col].sum()),
                "cv_concordance_mean": round(float(np.mean(cindices)), 3) if cindices else None,
                "cv_concordance_folds": [round(float(c), 3) for c in cindices],
                "hazard_ratios": {k: round(float(v), 3) for k, v in np.exp(cph.params_).items()},
                "p_values": {k: round(float(v), 4) for k, v in cph.summary["p"].items()},
                "ph_test_p": ph_p, "ph_assumption_ok": all(p > 0.05 for p in ph_p.values()) if ph_p else None,
                "discovery_only": True,
                "note": "Single-cohort model; no external validation. Interpret as discovery."}

    if req.analysis == "platinum_sensitivity":
        d = df.dropna(subset=feats + ["pt_resistant"]).copy()
        if len(d) < 20 or d["pt_resistant"].nunique() < 2:
            raise HTTPException(status_code=400, detail=f"insufficient n={len(d)} or single class")
        X = d[feats].values; y = d["pt_resistant"].values
        kf = KFold(n_splits=min(req.cv_folds, max(2, len(d)//8)), shuffle=True, random_state=42)
        aucs = []
        for tr, te in kf.split(X):
            m = LogisticRegression(max_iter=1000, class_weight="balanced")
            m.fit(X[tr], y[tr])
            try:
                aucs.append(roc_auc_score(y[te], m.predict_proba(X[te])[:, 1]))
            except Exception:
                pass
        return {"cohort": req.cohort, "analysis": req.analysis, "model": "logistic",
                "n": len(d), "events": int(y.sum()),
                "cv_auc_mean": round(float(np.mean(aucs)), 3) if aucs else None,
                "cv_auc_folds": [round(float(a), 3) for a in aucs],
                "discovery_only": True}

    raise HTTPException(status_code=400, detail=f"unknown analysis '{req.analysis}'")
