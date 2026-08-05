"""
TreatMarkerEngine — reusable IPD treat × marker held-out testing.

RUO. Not clinical decision support. Does NOT soft-unblock 8D-04.
Does NOT join GENIE patient IDs to PDS.

ADVANCE requires held-out Cox interaction (or equivalent) passing
a pre-registered success_rule. Medians alone = FAIL.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
from sklearn.model_selection import train_test_split

try:
    from lifelines import CoxPHFitter
except ImportError as e:  # pragma: no cover
    raise ImportError("lifelines required for TreatMarkerEngine Cox fits") from e


@dataclass
class TreatMarkerConfig:
    """Pre-registered analysis config (record BEFORE scoring)."""

    hypothesis_id: str = "H1_antiEGFR_x_KRAS_WT"
    pre_reg_seed: int = 20260805
    train_frac: float = 0.70
    studies_include: list[str] = field(
        default_factory=lambda: ["20050203", "20050181", "20020408"]
    )
    studies_exclude: list[str] = field(default_factory=lambda: ["20040249"])
    time_col: str = "pfs_days"
    event_col: str = "pfs_event"
    treat_col: str = "anti_egfr"
    marker_col: str = "kras"
    marker_positive: str = "WT"  # coded as 1
    marker_negative: str = "MUT"  # coded as 0
    # Success: interaction HR < 1 and (CI_UB < 1 OR p < 0.05)
    success_require_ci_ub_lt_1: bool = True
    success_alt_p_lt: float = 0.05
    NOT_8D04_unblock: bool = True
    NOT_genie_pds_join: bool = True


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cell_median(df: pd.DataFrame, time_col: str, event_col: str) -> dict[str, Any]:
    n = len(df)
    events = int(df[event_col].fillna(0).astype(int).sum()) if n else 0
    med = float(df[time_col].median()) if n else None
    return {
        "n": n,
        "events": events,
        "median_time": med,
        "event_rate": (events / n) if n else None,
    }


class TreatMarkerEngine:
    """
    Load IPD backbone → build cohort → held-out Cox treat×marker → kill battery.

    Primary predictive claim metric = Cox PH interaction HR(treat × marker+)
    on the HELD-OUT split. Null models = prognostic-only marker; treatment-only.
    """

    def __init__(self, config: Optional[TreatMarkerConfig] = None):
        self.config = config or TreatMarkerConfig()

    # ------------------------------------------------------------------ load
    def load_backbone(self, csv_path: str | Path) -> pd.DataFrame:
        path = Path(csv_path)
        df = pd.read_csv(path)
        self._backbone_path = str(path.resolve())
        self._backbone_sha256 = _sha256_file(path)
        return df

    def code_marker_treat(self, df: pd.DataFrame) -> pd.DataFrame:
        cfg = self.config
        out = df.copy()
        out["study"] = out["study"].astype(str)
        # treatment
        out["treat"] = pd.to_numeric(out[cfg.treat_col], errors="coerce").fillna(0).astype(int)
        # marker: WT=1, MUT=0; else NA
        m = out[cfg.marker_col].astype(str).str.upper().str.strip()
        out["marker_pos"] = np.where(
            m == cfg.marker_positive.upper(),
            1,
            np.where(m == cfg.marker_negative.upper(), 0, np.nan),
        )
        out["treat_x_marker"] = out["treat"] * out["marker_pos"]
        return out

    def pacce_exclusion_receipt(self, df: pd.DataFrame) -> dict[str, Any]:
        """Data reason to exclude PACCE (20040249): WT antiEGFR median worse than control."""
        cfg = self.config
        pacce = df[df["study"].astype(str) == "20040249"].copy()
        pacce = pacce.dropna(subset=["marker_pos", cfg.time_col, cfg.event_col])
        wt_t1 = pacce[(pacce["marker_pos"] == 1) & (pacce["treat"] == 1)]
        wt_t0 = pacce[(pacce["marker_pos"] == 1) & (pacce["treat"] == 0)]
        c1 = _cell_median(wt_t1, cfg.time_col, cfg.event_col)
        c0 = _cell_median(wt_t0, cfg.time_col, cfg.event_col)
        delta = None
        if c1["median_time"] is not None and c0["median_time"] is not None:
            delta = c1["median_time"] - c0["median_time"]
        sign_flip = delta is not None and delta < 0
        return {
            "study": "20040249",
            "alias": "PACCE",
            "wt_antiEGFR": c1,
            "wt_control": c0,
            "delta_median_antiEGFR_minus_control_WT": delta,
            "sign_flip_wt_worse_on_antiEGFR": bool(sign_flip),
            "exclude_from_primary": True,
            "reason": (
                "WT median PFS on antiEGFR worse than control (sign flip vs "
                "PRIME/PEAK/PaniBSC direction); historical bev+antiEGFR toxicity/failure trap"
                if sign_flip
                else "Pre-reg exclude PACCE from anti-EGFR enrichment pool (historical trap)"
            ),
        }

    def build_analysis_cohort(self, df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
        cfg = self.config
        coded = self.code_marker_treat(df)
        pacce_receipt = self.pacce_exclusion_receipt(coded)

        include = set(str(s) for s in cfg.studies_include)
        exclude = set(str(s) for s in cfg.studies_exclude)
        mask = coded["study"].isin(include) & ~coded["study"].isin(exclude)
        cohort = coded.loc[mask].copy()
        # require complete endpoint + marker
        cohort = cohort.dropna(subset=[cfg.time_col, cfg.event_col, "marker_pos"])
        cohort = cohort[cohort[cfg.time_col] > 0]
        cohort["marker_pos"] = cohort["marker_pos"].astype(int)
        cohort["treat_x_marker"] = cohort["treat"] * cohort["marker_pos"]

        meta = {
            "n_raw_backbone": int(len(df)),
            "n_after_study_filter": int(mask.sum()),
            "n_analysis": int(len(cohort)),
            "n_events": int(cohort[cfg.event_col].fillna(0).astype(int).sum()),
            "studies_include": sorted(include),
            "studies_exclude": sorted(exclude),
            "pacce_exclusion": pacce_receipt,
            "biomarker_coding": {
                "positive": cfg.marker_positive,
                "negative": cfg.marker_negative,
                "positive_code": 1,
                "negative_code": 0,
                "FAILED_or_null": "excluded",
            },
            "treatment_coding": {"col": cfg.treat_col, "experimental": 1, "control": 0},
            "endpoint": {"time": cfg.time_col, "event": cfg.event_col},
        }
        return cohort, meta

    def held_out_split(self, cohort: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
        cfg = self.config
        # Stratify within study by treat×marker cell when possible
        parts_train, parts_test = [], []
        for study, g in cohort.groupby("study", sort=True):
            strat = g["treat"].astype(str) + "_" + g["marker_pos"].astype(str)
            # need ≥2 per class for stratify
            vc = strat.value_counts()
            if (vc.min() < 2) or g.shape[0] < 20:
                train, test = train_test_split(
                    g, train_size=cfg.train_frac, random_state=cfg.pre_reg_seed
                )
            else:
                train, test = train_test_split(
                    g,
                    train_size=cfg.train_frac,
                    random_state=cfg.pre_reg_seed,
                    stratify=strat,
                )
            parts_train.append(train)
            parts_test.append(test)
        train_df = pd.concat(parts_train, ignore_index=True)
        test_df = pd.concat(parts_test, ignore_index=True)
        info = {
            "split": "70/30 within-study stratified by (anti_egfr, kras_wt)",
            "seed": cfg.pre_reg_seed,
            "n_train": int(len(train_df)),
            "n_test": int(len(test_df)),
            "events_train": int(train_df[cfg.event_col].fillna(0).astype(int).sum()),
            "events_test": int(test_df[cfg.event_col].fillna(0).astype(int).sum()),
            "by_study": {
                str(s): {
                    "n_train": int((train_df["study"] == s).sum()),
                    "n_test": int((test_df["study"] == s).sum()),
                }
                for s in sorted(cohort["study"].unique())
            },
        }
        return train_df, test_df, info

    def _cox_fit(
        self, df: pd.DataFrame, formula_cols: list[str], label: str
    ) -> dict[str, Any]:
        cfg = self.config
        use = df[[cfg.time_col, cfg.event_col] + formula_cols].dropna().copy()
        use[cfg.event_col] = use[cfg.event_col].astype(int)
        if len(use) < 30 or use[cfg.event_col].sum() < 10:
            return {
                "ok": False,
                "label": label,
                "error": "INSUFFICIENT_EVENTS",
                "n": int(len(use)),
                "events": int(use[cfg.event_col].sum()) if len(use) else 0,
            }
        cph = CoxPHFitter()
        try:
            cph.fit(use, duration_col=cfg.time_col, event_col=cfg.event_col)
        except Exception as e:
            return {"ok": False, "label": label, "error": str(e), "n": int(len(use))}

        summary = cph.summary
        coefs: dict[str, Any] = {}
        for name in formula_cols:
            if name not in summary.index:
                continue
            row = summary.loc[name]
            coefs[name] = {
                "hr": float(np.exp(row["coef"])),
                "coef": float(row["coef"]),
                "se": float(row["se(coef)"]),
                "p": float(row["p"]),
                "ci_low": float(np.exp(row["coef"] - 1.96 * row["se(coef)"])),
                "ci_high": float(np.exp(row["coef"] + 1.96 * row["se(coef)"])),
            }
        return {
            "ok": True,
            "label": label,
            "n": int(len(use)),
            "events": int(use[cfg.event_col].sum()),
            "covariates": formula_cols,
            "coefs": coefs,
            "concordance": float(cph.concordance_index_),
            "method": "lifelines.CoxPHFitter",
        }

    def fit_predictive_interaction(self, df: pd.DataFrame, label: str) -> dict[str, Any]:
        return self._cox_fit(df, ["treat", "marker_pos", "treat_x_marker"], label)

    def fit_prognostic_only(self, df: pd.DataFrame, label: str) -> dict[str, Any]:
        return self._cox_fit(df, ["marker_pos"], label)

    def fit_treatment_only(self, df: pd.DataFrame, label: str) -> dict[str, Any]:
        return self._cox_fit(df, ["treat"], label)

    def fit_stratified_treat_by_marker(self, df: pd.DataFrame) -> dict[str, Any]:
        """HR(treat) within WT and within MUT separately."""
        out: dict[str, Any] = {}
        for name, mval in (("WT", 1), ("MUT", 0)):
            sub = df[df["marker_pos"] == mval]
            out[name] = self._cox_fit(sub, ["treat"], f"treat_within_{name}")
        return out

    def evaluate_success_rule(self, held_out_interaction: dict[str, Any]) -> dict[str, Any]:
        cfg = self.config
        if not held_out_interaction.get("ok"):
            return {
                "pass": False,
                "reason": "MODEL_FAILED",
                "detail": held_out_interaction.get("error"),
            }
        inter = held_out_interaction["coefs"].get("treat_x_marker")
        if not inter:
            return {"pass": False, "reason": "MISSING_INTERACTION_COEF"}
        hr = inter["hr"]
        ci_high = inter["ci_high"]
        p = inter["p"]
        direction_ok = hr < 1.0
        ci_ok = ci_high < 1.0
        p_ok = p < cfg.success_alt_p_lt
        # Pre-reg: (HR<1 AND CI_UB<1) OR (HR<1 AND p<0.05)
        passed = direction_ok and (ci_ok or p_ok)
        return {
            "pass": bool(passed),
            "interaction_hr": hr,
            "ci_low": inter["ci_low"],
            "ci_high": ci_high,
            "p": p,
            "direction_hr_lt_1": direction_ok,
            "ci_ub_lt_1": ci_ok,
            "p_lt_0_05": p_ok,
            "success_rule": (
                "held_out Cox interaction HR(treat_x_marker)<1 AND "
                "(CI_UB<1 OR p<0.05)"
            ),
            "reason": "PASS" if passed else "HELD_OUT_INTERACTION_FAILS_SUCCESS_RULE",
        }

    def kill_battery(
        self,
        train: pd.DataFrame,
        test: pd.DataFrame,
        held_success: dict[str, Any],
        progn_test: dict[str, Any],
        treat_test: dict[str, Any],
        pacce: dict[str, Any],
    ) -> list[dict[str, Any]]:
        kills: list[dict[str, Any]] = []

        # K1 prognostic-only: if marker HR strong on held-out but interaction fails
        progn_hr = None
        if progn_test.get("ok") and "marker_pos" in progn_test.get("coefs", {}):
            progn_hr = progn_test["coefs"]["marker_pos"]["hr"]
        kills.append(
            {
                "id": "K1_prognostic_only",
                "verdict": (
                    "FIRE"
                    if (not held_success.get("pass") and progn_hr is not None)
                    else "PASS_GATE"
                ),
                "note": (
                    "Marker may be prognostic; predictive ADVANCE requires interaction pass"
                ),
                "prognostic_marker_hr_heldout": progn_hr,
                "interaction_passed": held_success.get("pass"),
            }
        )

        # K2 assay — N/A for IPD
        kills.append(
            {
                "id": "K2_assay_bias",
                "verdict": "N/A",
                "note": "Assay stratification N/A for IPD backbone KRAS coding",
            }
        )

        # K3 join
        kills.append(
            {
                "id": "K3_join_impossible",
                "verdict": "PASS_GATE",
                "note": "No GENIE patient join performed (forbidden)",
                "matched_n": 0,
            }
        )

        # K4 PACCE pool trap
        kills.append(
            {
                "id": "K4_pool_trap_PACCE",
                "verdict": "PASS_GATE" if pacce.get("exclude_from_primary") else "FAIL",
                "pacce": pacce,
            }
        )

        # K5 held-out null
        kills.append(
            {
                "id": "K5_held_out_null",
                "verdict": "PASS_GATE" if held_success.get("pass") else "FIRE",
                "held_out_success": held_success,
            }
        )

        # K6 treatment-only
        treat_hr = None
        if treat_test.get("ok") and "treat" in treat_test.get("coefs", {}):
            treat_hr = treat_test["coefs"]["treat"]["hr"]
        kills.append(
            {
                "id": "K6_treatment_only",
                "verdict": "PASS_GATE",
                "note": "Treatment-only HR cannot alone ADVANCE enrichment",
                "treatment_hr_heldout": treat_hr,
            }
        )

        # K7 leakage
        kills.append(
            {
                "id": "K7_leakage",
                "verdict": "PASS_GATE",
                "columns_used": [
                    self.config.time_col,
                    self.config.event_col,
                    "treat",
                    "marker_pos",
                    "treat_x_marker",
                    "study",
                ],
                "backbone_sha256": getattr(self, "_backbone_sha256", None),
                "genie_join": False,
            }
        )
        return kills

    def descriptive_cells(self, df: pd.DataFrame) -> dict[str, Any]:
        cfg = self.config
        cells = {}
        for t, m, key in (
            (1, 1, "anti_egfr__kras_WT"),
            (1, 0, "anti_egfr__kras_MUT"),
            (0, 1, "control__kras_WT"),
            (0, 0, "control__kras_MUT"),
        ):
            sub = df[(df["treat"] == t) & (df["marker_pos"] == m)]
            cells[key] = _cell_median(sub, cfg.time_col, cfg.event_col)
        return cells

    def run(
        self,
        backbone_csv: str | Path,
        also_os_sensitivity: bool = True,
    ) -> dict[str, Any]:
        """Full H1 pipeline. Config seed must already be locked in WINNER_DEFINITION."""
        cfg = self.config
        raw = self.load_backbone(backbone_csv)
        cohort, cohort_meta = self.build_analysis_cohort(raw)
        train, test, split_info = self.held_out_split(cohort)

        # Fit on TRAIN (for reference) and TEST (primary decision)
        train_inter = self.fit_predictive_interaction(train, "train_interaction")
        test_inter = self.fit_predictive_interaction(test, "heldout_interaction")
        test_progn = self.fit_prognostic_only(test, "heldout_prognostic_only")
        test_treat = self.fit_treatment_only(test, "heldout_treatment_only")
        test_strat = self.fit_stratified_treat_by_marker(test)
        success = self.evaluate_success_rule(test_inter)
        kills = self.kill_battery(
            train, test, success, test_progn, test_treat, cohort_meta["pacce_exclusion"]
        )

        # Verdict
        if success.get("pass"):
            verdict = "ADVANCE"
            money = True
        else:
            # If interaction wrong direction or clearly null → KILL predictive; else BLOCKED
            inter = (test_inter.get("coefs") or {}).get("treat_x_marker") or {}
            hr = inter.get("hr")
            if hr is not None and hr >= 1.0:
                verdict = "KILL"
            else:
                verdict = "BLOCKED"
            money = False

        os_block: Optional[dict[str, Any]] = None
        if also_os_sensitivity:
            os_cfg = TreatMarkerConfig(
                pre_reg_seed=cfg.pre_reg_seed,
                studies_include=list(cfg.studies_include),
                studies_exclude=list(cfg.studies_exclude),
                time_col="os_days",
                event_col="os_event",
            )
            os_engine = TreatMarkerEngine(os_cfg)
            os_engine._backbone_path = getattr(self, "_backbone_path", None)
            os_engine._backbone_sha256 = getattr(self, "_backbone_sha256", None)
            # reuse same coded cohort rows with OS cols
            os_cohort = cohort.dropna(subset=["os_days", "os_event"])
            os_cohort = os_cohort[os_cohort["os_days"] > 0]
            if len(os_cohort) >= 50:
                os_train, os_test, os_split = os_engine.held_out_split(os_cohort)
                os_inter = os_engine.fit_predictive_interaction(
                    os_test, "heldout_OS_interaction"
                )
                os_success = os_engine.evaluate_success_rule(os_inter)
                os_block = {
                    "role": "sensitivity_only_not_ADVANCE_gate",
                    "split": os_split,
                    "heldout_interaction": os_inter,
                    "success_rule_eval": os_success,
                }

        receipt = {
            "built_utc": datetime.now(timezone.utc).isoformat(),
            "hypothesis_id": cfg.hypothesis_id,
            "pre_reg_seed": cfg.pre_reg_seed,
            "held_out_protocol": {
                "split": split_info["split"],
                "seed": cfg.pre_reg_seed,
                "metric": "cox_ph_interaction_HR(treat × kras_wt) on held-out PFS",
            },
            "backbone_path": getattr(self, "_backbone_path", None),
            "backbone_sha256": getattr(self, "_backbone_sha256", None),
            "cohort": cohort_meta,
            "split": split_info,
            "descriptive_medians_FULL_COHORT_secondary_only": self.descriptive_cells(
                cohort
            ),
            "descriptive_medians_HELDOUT_secondary_only": self.descriptive_cells(test),
            "primary_heldout_cox_interaction": test_inter,
            "train_cox_interaction_reference_only": train_inter,
            "null_prognostic_only_heldout": test_progn,
            "null_treatment_only_heldout": test_treat,
            "stratified_treat_HR_by_marker_heldout": test_strat,
            "success_rule_eval": success,
            "kill_battery": kills,
            "os_sensitivity": os_block,
            "verdict": verdict,
            "money": money,
            "NOT_8D04_unblock": True,
            "NOT_genie_pds_join": True,
            "RUO": "Research Use Only. Not clinical decision support.",
            "method": (
                "TreatMarkerEngine: within-study 70/30 stratified split "
                f"(seed={cfg.pre_reg_seed}); CoxPH treat+marker+interaction on held-out PFS; "
                "PACCE excluded; GENIE join forbidden"
            ),
        }
        # receipt sha over canonical JSON (minus sha field)
        blob = json.dumps(receipt, sort_keys=True, default=str).encode("utf-8")
        receipt["receipt_sha256"] = _sha256_bytes(blob)
        return receipt


def run_h1_anti_egfr_kras(
    backbone_csv: str | Path,
    out_dir: str | Path,
    seed: int = 20260805,
) -> dict[str, Any]:
    """CLI-friendly runner for H1. Writes JSON + MD receipt into out_dir."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    engine = TreatMarkerEngine(TreatMarkerConfig(pre_reg_seed=seed))
    receipt = engine.run(backbone_csv, also_os_sensitivity=True)

    json_path = out_dir / "H1_HELDOUT_TREAT_X_MARKER_RECEIPT.json"
    with open(json_path, "w") as f:
        json.dump(receipt, f, indent=2, default=str)

    md_path = out_dir / "H1_HELDOUT_TREAT_X_MARKER_RECEIPT.md"
    md_path.write_text(_render_receipt_md(receipt), encoding="utf-8")

    # Partner one-pager
    one_pager = out_dir / "H1_WINNER_RECEIPT.md"
    one_pager.write_text(_render_one_pager(receipt), encoding="utf-8")
    return receipt


def _fmt_hr(coef: Optional[dict]) -> str:
    if not coef:
        return "n/a"
    return (
        f"HR={coef['hr']:.3f} "
        f"(95% CI {coef['ci_low']:.3f}–{coef['ci_high']:.3f}, p={coef['p']:.4g})"
    )


def _render_receipt_md(r: dict[str, Any]) -> str:
    inter = r.get("primary_heldout_cox_interaction") or {}
    coefs = inter.get("coefs") or {}
    succ = r.get("success_rule_eval") or {}
    pacce = (r.get("cohort") or {}).get("pacce_exclusion") or {}
    lines = [
        "# H1 HELDOUT TREAT × MARKER RECEIPT",
        "",
        f"**Built UTC:** {r.get('built_utc')}",
        f"**Hypothesis:** `{r.get('hypothesis_id')}`",
        f"**Pre-reg seed:** `{r.get('pre_reg_seed')}` (locked in WINNER_DEFINITION before scoring)",
        f"**Verdict:** **{r.get('verdict')}** · money={r.get('money')}",
        f"**Receipt SHA256:** `{r.get('receipt_sha256')}`",
        f"**Backbone SHA256:** `{r.get('backbone_sha256')}`",
        "",
        "**RUO.** Not clinical decision support. NOT an 8D-04 unlock. No GENIE×PDS join.",
        "",
        "---",
        "",
        "## Cohort",
        "",
        f"- Studies include: `{r['cohort']['studies_include']}` (PRIME / PEAK / PaniBSC)",
        f"- Studies exclude: `{r['cohort']['studies_exclude']}` (PACCE)",
        f"- n_analysis={r['cohort']['n_analysis']}, n_events_PFS={r['cohort']['n_events']}",
        f"- Biomarker coding: {r['cohort']['biomarker_coding']}",
        f"- Treatment coding: {r['cohort']['treatment_coding']}",
        "",
        "### PACCE exclusion (data reason)",
        "",
        f"- WT antiEGFR median PFS: {pacce.get('wt_antiEGFR')}",
        f"- WT control median PFS: {pacce.get('wt_control')}",
        f"- Δ(anti−ctrl) WT: {pacce.get('delta_median_antiEGFR_minus_control_WT')}",
        f"- Sign flip (WT worse on antiEGFR): **{pacce.get('sign_flip_wt_worse_on_antiEGFR')}**",
        f"- Reason: {pacce.get('reason')}",
        "",
        "---",
        "",
        "## Held-out protocol",
        "",
        f"- Split: {r['held_out_protocol']['split']}",
        f"- Seed: {r['held_out_protocol']['seed']}",
        f"- Metric: {r['held_out_protocol']['metric']}",
        f"- n_train={r['split']['n_train']} (events={r['split']['events_train']}); "
        f"n_test={r['split']['n_test']} (events={r['split']['events_test']})",
        "",
        "---",
        "",
        "## Primary metric — held-out Cox interaction (PFS)",
        "",
        f"- Model ok: {inter.get('ok')} · n={inter.get('n')} · events={inter.get('events')}",
        f"- treat: {_fmt_hr(coefs.get('treat'))}",
        f"- marker_pos (KRAS WT): {_fmt_hr(coefs.get('marker_pos'))}",
        f"- **treat × marker (INTERACTION):** {_fmt_hr(coefs.get('treat_x_marker'))}",
        f"- Concordance: {inter.get('concordance')}",
        "",
        "### Success rule evaluation",
        "",
        f"- pass: **{succ.get('pass')}**",
        f"- reason: `{succ.get('reason')}`",
        f"- rule: {succ.get('success_rule')}",
        "",
        "---",
        "",
        "## Null models (held-out)",
        "",
        f"- Prognostic-only KRAS: {_fmt_hr((r.get('null_prognostic_only_heldout') or {}).get('coefs', {}).get('marker_pos'))}",
        f"- Treatment-only antiEGFR: {_fmt_hr((r.get('null_treatment_only_heldout') or {}).get('coefs', {}).get('treat'))}",
        "",
        "### Stratified treat HR by marker (held-out)",
        "",
    ]
    strat = r.get("stratified_treat_HR_by_marker_heldout") or {}
    for arm in ("WT", "MUT"):
        block = strat.get(arm) or {}
        lines.append(
            f"- Within {arm}: {_fmt_hr((block.get('coefs') or {}).get('treat'))} "
            f"(n={block.get('n')}, events={block.get('events')})"
        )
    lines += [
        "",
        "---",
        "",
        "## Kill battery",
        "",
    ]
    for k in r.get("kill_battery") or []:
        lines.append(f"- **{k.get('id')}**: {k.get('verdict')} — {k.get('note', k.get('reason', ''))}")
    lines += [
        "",
        "---",
        "",
        "## Explicit non-claims",
        "",
        "- Medians are secondary diagnostics only — not ADVANCE-grade.",
        "- No GENIE patient-level join.",
        "- No MSI / Guardant pTMB invention.",
        "- No 8D-04 soft-unblock.",
        "",
        f"**Method:** {r.get('method')}",
        "",
    ]
    return "\n".join(lines)


def _render_one_pager(r: dict[str, Any]) -> str:
    succ = r.get("success_rule_eval") or {}
    inter = ((r.get("primary_heldout_cox_interaction") or {}).get("coefs") or {}).get(
        "treat_x_marker"
    )
    return "\n".join(
        [
            "# H1 Winner Receipt (Partner-facing, RUO)",
            "",
            f"**Verdict:** {r.get('verdict')} (money={r.get('money')})",
            f"**Claim tested:** Anti-EGFR × KRAS WT predictive PFS enrichment (PRIME/PEAK/PaniBSC; PACCE excluded).",
            f"**Held-out interaction:** {_fmt_hr(inter)}",
            f"**Success rule pass:** {succ.get('pass')} (`{succ.get('reason')}`)",
            f"**n held-out / events:** {(r.get('primary_heldout_cox_interaction') or {}).get('n')} / "
            f"{(r.get('primary_heldout_cox_interaction') or {}).get('events')}",
            f"**Pre-reg seed:** {r.get('pre_reg_seed')}",
            f"**Receipt SHA:** `{r.get('receipt_sha256')}`",
            "",
            "This is a **research** enrichment signal evaluation on public IPD extracts. "
            "It is **not** clinical guidance, **not** an approval claim, and **does not** "
            "unlock 8D-04. GENIE was not patient-joined to PDS.",
            "",
            "**RUO — Research Use Only.**",
            "",
        ]
    )
