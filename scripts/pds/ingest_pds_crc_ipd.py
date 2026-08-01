#!/usr/bin/env python3
"""
PDS CRC vault cracker — zip/CSV/sas7bdat → Efficacy-Predictor IPD rows.

Does NOT silently drop required columns. Emits a coverage receipt per trial.
Postgres load: --write-postgres OR CRC_IPD_SEED_ON_BOOT lifespan hook (hash-gated).

Families:
  amgen_legacy   — PEAK / PACCE / PRIME donation 264
  amgen_adam     — PRIME donation 309 / PaniBSC
  n0147_csv      — characteristic + objectives CSVs
  az_horizon     — HORIZON III rdpsubj (locked)
  sanofi_mosaic  — MOSAIC surv (locked OS + DFS→PFS proxy)
  sanofi_velour  — VELOUR dm demography-only (no ADTTE/ADSL in pack)

Usage:
  python scripts/pds/ingest_pds_crc_ipd.py
  python scripts/pds/ingest_pds_crc_ipd.py --write-postgres
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
DEFAULT_IN = REPO / "backend/data/features/pds_crc"
DEFAULT_OUT = REPO / "backend/data/features/crc_ipd_from_zips"

# Core clinical axes (Efficacy / 11D consumers)
CORE_COLS = [
    "subjid",
    "trial",
    "arm",
    "arm_class",
    "liver_met",
    "kras",
    "nras",
    "ras",
    "braf_mut",
    "ecog",
    "age",
    "sex",
    "pfs_days",
    "pfs_event",
    "os_days",
    "os_event",
]
# Cross-repo null + sampling contract (ZetaBridge ModelRouter / executeDoubleDip)
CONTRACT_COLS = [
    "liver_met_missing",  # 1.0 = axis absent → do NOT impute as 0
    "kras_missing",
    "nras_missing",
    "ras_missing",
    "subset_scale",       # available_n / published_n (1.0 if full or unknown)
    "published_n",
    "available_n",
    "pack_role",          # full_ipd | os_dfs_ipd | demography_only | subset_arm
]
SCHEMA_COLS = CORE_COLS + CONTRACT_COLS

REQUIRED_SURVIVAL = ("pfs_days", "pfs_event", "os_days", "os_event")

# Literature / protocol published ITT (defensible subset weights)
PUBLISHED_N: Dict[str, int] = {
    "MOSAIC_128": 2246,      # André et al. NEJM 2004 MOSAIC
    "VELOUR_131": 1226,      # Van Cutsem et al. JCO 2012 VELOUR
    "HORIZON_III_78": 1422,  # HORIZON III CRC (cediranib) randomized N (PDS extract is arm-subset)
}

log = logging.getLogger("pds_crc_ingest")


@dataclass
class TrialSpec:
    trial_key: str
    family: str
    donation_id: int
    package_glob: str
    notes: str = ""
    required_source_cols: Dict[str, Sequence[str]] = field(default_factory=dict)


TRIALS: List[TrialSpec] = [
    TrialSpec(
        "PEAK_263",
        "amgen_legacy",
        263,
        "PEAK/263_3426__*.zip",
        required_source_cols={
            "a_eendpt.sas7bdat": ("SUBJID", "TRT", "PFSDYCR", "PFSCR", "DTHDY", "DTH"),
        },
    ),
    TrialSpec(
        "PACCE_262",
        "amgen_legacy",
        262,
        "PACCE/262_3425__*.zip",
        notes="No LIVERMET in pack — liver_met null + liver_met_missing=1.",
        required_source_cols={
            "a_eendpt.sas7bdat": ("SUBJID", "TRT", "PFSDYCR", "PFSCR", "DTHDY", "DTH", "KRAS"),
        },
    ),
    TrialSpec(
        "PRIME_264",
        "amgen_legacy",
        264,
        "PRIME/264_3427__*.zip",
        notes="No KRAS in pack — kras null + kras_missing=1.",
        required_source_cols={
            "a_eendpt.sas7bdat": ("SUBJID", "TRT", "LIVERMET", "PFSDYCR", "PFSCR", "DTHDY", "DTH"),
        },
    ),
    TrialSpec(
        "PRIME_309",
        "amgen_adam",
        309,
        "PRIME/309_4668__*.zip",
        required_source_cols={
            "adsl_pds2019.sas7bdat": ("SUBJID", "TRT", "LIVERMET", "PFSDYCR", "PFSCR", "DTHDY", "DTH"),
            "biomark_pds2019.sas7bdat": ("SUBJID", "BMMTR1"),
        },
    ),
    TrialSpec(
        "PaniBSC_310",
        "amgen_adam",
        310,
        "PaniBSC/310_4672__*.zip",
        notes="OS aliases DTHDYX/DTHX; no LIVERMET.",
        required_source_cols={
            "adsl_pds2019.sas7bdat": ("SUBJID", "TRT", "PFSDYCR", "PFSCR", "DTHDYX", "DTHX"),
            "biomark_pds2019.sas7bdat": ("SUBJID",),
        },
    ),
    TrialSpec(
        "N0147_161",
        "n0147_csv",
        161,
        "N0147/161_*__*.csv",
        notes="Adjuvant CRC — futime8/fustat8 OS; pgtime5/pgstat5 progression proxy.",
        required_source_cols={
            "characteristic.csv": ("mask_id", "ARM", "SEX", "PS", "wild"),
            "objectives.csv": ("mask_id", "futime8", "fustat8", "pgtime5", "pgstat5"),
        },
    ),
    TrialSpec(
        "HORIZON_III_78",
        "az_horizon",
        78,
        "HORIZON_III/78_384__*.zip",
        notes="Locked: rdpsubj TIMETP/PFSEVENT + OSTIM/OSEVENT + BAS_LIV. Single-arm extract (bev).",
        required_source_cols={
            "rdpsubj.sas7bdat": ("RANDCODE", "TIMETP", "PFSEVENT", "OSTIM", "OSEVENT", "BAS_LIV"),
        },
    ),
    TrialSpec(
        "HORIZON_III_251",
        "stub_linked",
        251,
        "HORIZON_III/251_*__*.sas7bdat",
        notes="Linked PDS bridge table — not patient IPD; skipped (use HORIZON_III_78).",
    ),
    TrialSpec(
        "MOSAIC_128",
        "sanofi_mosaic",
        128,
        "MOSAIC/128_777__*.zip",
        notes="Locked: surv OSDY/OSCENS + DFSDY/DFSCENS (DFS→pfs proxy). Subset ~1122/2246.",
        required_source_cols={
            "surv.sas7bdat": ("RSUBJID", "OSDY", "OSCENS", "DFSDY", "DFSCENS"),
        },
    ),
    TrialSpec(
        "VELOUR_131",
        "sanofi_velour",
        131,
        "VELOUR/131_773__*.zip",
        notes="Pack has SDTM domains only — NO ADTTE/ADSL. Demography-only; survival forbidden.",
        required_source_cols={"dm.sas7bdat": ("RSUBJID",)},
    ),
]


def classify_path(path: Path) -> str:
    suf = path.suffix.lower()
    if suf == ".csv":
        return "csv"
    if suf in {".xls", ".xlsx"}:
        return "excel"
    if suf == ".sas7bdat":
        return "sas7bdat"
    if suf == ".zip":
        return "zip"
    return "unknown"


def yn01(val: Any) -> Optional[float]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().upper()
    if s in {"Y", "YES", "1", "TRUE", "T", "1.0"}:
        return 1.0
    if s in {"N", "NO", "0", "FALSE", "F", "0.0"}:
        return 0.0
    try:
        f = float(val)
        if f in (0.0, 1.0):
            return f
    except (TypeError, ValueError):
        pass
    return None


def kras01(val: Any) -> Optional[float]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    s = str(val).strip().lower()
    if "mutant" in s or s in {"m", "mut", "positive", "+"}:
        return 1.0
    if "wild" in s or s in {"wt", "negative", "-"}:
        return 0.0
    if "fail" in s or "unknown" in s or "indeter" in s:
        return None
    return None


def arm_class(arm: Any) -> Optional[str]:
    if arm is None or (isinstance(arm, float) and pd.isna(arm)):
        return None
    s = str(arm).lower()
    if "panitumumab" in s or "cetuximab" in s or "egfr" in s:
        return "antiEGFR"
    if "bevacizumab" in s or "aflibercept" in s or "vegf" in s or "cediranib" in s:
        return "antiVEGF"
    if "folfox" in s or "folfiri" in s or "chemo" in s or "oxali" in s or "irinotec" in s:
        return "chemo"
    if "bsc" in s or "best supportive" in s:
        return "BSC"
    return "other"


def ecog_num(val: Any) -> Optional[float]:
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return None
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        try:
            return float(int(val))
        except (TypeError, ValueError):
            pass
    s = str(val).lower()
    m = re.search(r"\b([0-4])\b", s)
    if m:
        return float(m.group(1))
    if "fully active" in s or s.strip() in {"0", "0.0"}:
        return 0.0
    if "ambulatory" in s or "symptoms but" in s:
        return 1.0
    if "selfcare" in s or "self-care" in s or "limited" in s:
        return 2.0
    return None


def _norm_subjid(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.strip()
        .str.lstrip("0")
        .replace({"": "0"})
    )


def _censor_to_event(censor: pd.Series) -> pd.Series:
    """SAS CNSR/OSCENS style: 1=censored → event 0; 0=event → event 1."""
    c = pd.to_numeric(censor, errors="coerce")
    return c.map(lambda x: None if pd.isna(x) else (0.0 if float(x) == 1.0 else 1.0))


def attach_contract(
    out: pd.DataFrame,
    trial_key: str,
    *,
    pack_role: str,
    published_n: Optional[int] = None,
) -> pd.DataFrame:
    """Explicit missingness + subset scaling — never let SQL NULL become float(0)."""
    n = len(out)
    pub = published_n if published_n is not None else PUBLISHED_N.get(trial_key)
    avail = int(n)
    if pub and pub > 0:
        scale = float(avail) / float(pub)
    else:
        scale = 1.0
    out = out.copy()
    out["liver_met_missing"] = out["liver_met"].isna().astype(float)
    out["kras_missing"] = out["kras"].isna().astype(float)
    out["nras_missing"] = out["nras"].isna().astype(float)
    out["ras_missing"] = out["ras"].isna().astype(float)
    out["subset_scale"] = scale
    out["published_n"] = float(pub) if pub else None
    out["available_n"] = float(avail)
    out["pack_role"] = pack_role
    return out


def read_sas_member(zip_or_path: Path, member: Optional[str] = None) -> pd.DataFrame:
    kind = classify_path(zip_or_path)
    if kind == "sas7bdat":
        return _read_sas7bdat(zip_or_path)
    if kind != "zip":
        raise ValueError(f"read_sas_member expected zip/sas7bdat, got {kind}: {zip_or_path}")
    if not member:
        raise ValueError("member required for zip")
    with zipfile.ZipFile(zip_or_path) as z:
        names = {Path(n).name.lower(): n for n in z.namelist()}
        key = member.lower()
        if key not in names:
            raise FileNotFoundError(f"{member} not in {zip_or_path.name}; have={sorted(names)[:20]}")
        real = names[key]
        with tempfile.TemporaryDirectory(prefix="pds_sas_") as td:
            z.extract(real, td)
            extracted = Path(td) / real
            if not extracted.exists():
                extracted = next(Path(td).rglob(Path(member).name))
            return _read_sas7bdat(extracted)


def _read_sas7bdat(path: Path) -> pd.DataFrame:
    try:
        import pyreadstat  # type: ignore

        df, _meta = pyreadstat.read_sas7bdat(str(path))
        return df
    except ImportError:
        return pd.read_sas(path, format="sas7bdat", encoding="latin-1")


def resolve_package(root: Path, spec: TrialSpec) -> List[Path]:
    return sorted(root.glob(spec.package_glob))


def assert_cols(df: pd.DataFrame, required: Sequence[str], label: str) -> None:
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(f"{label} missing required columns {missing}; have={list(df.columns)[:40]}")


def _pick(df: pd.DataFrame, *candidates: str) -> Optional[str]:
    for c in candidates:
        if c in df.columns:
            return c
    return None


def map_amgen_legacy(zip_path: Path, trial_key: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    ep = read_sas_member(zip_path, "a_eendpt.sas7bdat")
    try:
        core = read_sas_member(zip_path, "corevar.sas7bdat")
        core = core.copy()
        core["SUBJID"] = _norm_subjid(core["SUBJID"])
        ep = ep.copy()
        ep["SUBJID"] = _norm_subjid(ep["SUBJID"])
        extra_cols = [c for c in ("KRAS", "KRASCD", "LIVERMET", "LIVRONLY") if c in core.columns and c not in ep.columns]
        if extra_cols:
            ep = ep.merge(core[["SUBJID"] + extra_cols], on="SUBJID", how="left")
    except Exception:
        ep = ep.copy()
        if "SUBJID" in ep.columns:
            ep["SUBJID"] = _norm_subjid(ep["SUBJID"])

    assert_cols(ep, ["SUBJID", "TRT", "PFSDYCR", "PFSCR", "DTHDY", "DTH", "AGE", "SEX"], f"{trial_key}/a_eendpt")
    liver_c = _pick(ep, "LIVERMET", "LIVRONLY")
    kras_c = _pick(ep, "KRAS")
    ecog_c = _pick(ep, "B_ECOG", "B_ECOGI")
    liver = ep[liver_c].map(yn01) if liver_c else pd.Series([None] * len(ep))
    kras = ep[kras_c].map(kras01) if kras_c else pd.Series([None] * len(ep))
    ecog = ep[ecog_c].map(ecog_num) if ecog_c else pd.Series([None] * len(ep))
    out = pd.DataFrame(
        {
            "subjid": ep["SUBJID"],
            "trial": trial_key,
            "arm": ep["TRT"],
            "arm_class": ep["TRT"].map(arm_class),
            "liver_met": liver,
            "kras": kras,
            "nras": None,
            "ras": kras,
            "braf_mut": None,
            "ecog": ecog,
            "age": pd.to_numeric(ep["AGE"], errors="coerce"),
            "sex": ep["SEX"],
            "pfs_days": pd.to_numeric(ep["PFSDYCR"], errors="coerce"),
            "pfs_event": pd.to_numeric(ep["PFSCR"], errors="coerce"),
            "os_days": pd.to_numeric(ep["DTHDY"], errors="coerce"),
            "os_event": pd.to_numeric(ep["DTH"], errors="coerce"),
        }
    )
    out["subjid"] = _norm_subjid(out["subjid"])
    out = attach_contract(out, trial_key, pack_role="full_ipd")
    receipt = _coverage(
        out,
        trial_key,
        family="amgen_legacy",
        source=str(zip_path.name),
        extra={"aliases": {"liver_col": liver_c, "kras_col": kras_c, "ecog_col": ecog_c}},
    )
    return out[SCHEMA_COLS], receipt


def map_amgen_adam(zip_path: Path, trial_key: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    adsl = read_sas_member(zip_path, "adsl_pds2019.sas7bdat")
    assert_cols(adsl, ["SUBJID", "TRT", "PFSDYCR", "PFSCR", "AGE", "SEX"], f"{trial_key}/adsl")
    bio = read_sas_member(zip_path, "biomark_pds2019.sas7bdat")
    assert_cols(bio, ["SUBJID"], f"{trial_key}/biomark")
    adsl = adsl.copy()
    bio = bio.copy()
    adsl["SUBJID"] = _norm_subjid(adsl["SUBJID"])
    bio["SUBJID"] = _norm_subjid(bio["SUBJID"])
    os_day_c = _pick(adsl, "DTHDY", "DTHDYX")
    os_evt_c = _pick(adsl, "DTH", "DTHX")
    if not os_day_c or not os_evt_c:
        raise RuntimeError(f"{trial_key}/adsl missing OS cols (need DTHDY/DTH or DTHDYX/DTHX)")
    liver_c = _pick(adsl, "LIVERMET")
    ecog_c = _pick(adsl, "B_ECOG")
    if "BMMTR1" in bio.columns:
        kras_series = bio.drop_duplicates("SUBJID", keep="first").set_index("SUBJID")["BMMTR1"]
    else:
        kras_series = pd.Series(dtype=object)
    if "BMMTR16" in bio.columns:
        nras_series = bio.drop_duplicates("SUBJID", keep="first").set_index("SUBJID")["BMMTR16"]
    else:
        nras_series = pd.Series(dtype=object)
    kras = adsl["SUBJID"].map(kras_series).map(kras01)
    nras = adsl["SUBJID"].map(nras_series).map(kras01)
    ras = pd.Series(
        [
            1.0 if (k == 1.0 or n == 1.0) else (0.0 if (k == 0.0 and (n == 0.0 or pd.isna(n))) else None)
            for k, n in zip(kras, nras)
        ]
    )
    liver = adsl[liver_c].map(yn01) if liver_c else pd.Series([None] * len(adsl))
    ecog = adsl[ecog_c].map(ecog_num) if ecog_c else pd.Series([None] * len(adsl))
    out = pd.DataFrame(
        {
            "subjid": adsl["SUBJID"],
            "trial": trial_key,
            "arm": adsl["TRT"],
            "arm_class": adsl["TRT"].map(arm_class),
            "liver_met": liver,
            "kras": kras,
            "nras": nras,
            "ras": ras,
            "braf_mut": None,
            "ecog": ecog,
            "age": pd.to_numeric(adsl["AGE"], errors="coerce"),
            "sex": adsl["SEX"],
            "pfs_days": pd.to_numeric(adsl["PFSDYCR"], errors="coerce"),
            "pfs_event": pd.to_numeric(adsl["PFSCR"], errors="coerce"),
            "os_days": pd.to_numeric(adsl[os_day_c], errors="coerce"),
            "os_event": pd.to_numeric(adsl[os_evt_c], errors="coerce"),
        }
    )
    out = attach_contract(out, trial_key, pack_role="full_ipd")
    receipt = _coverage(
        out,
        trial_key,
        family="amgen_adam",
        source=str(zip_path.name),
        extra={"aliases": {"os_days": os_day_c, "os_event": os_evt_c, "liver_col": liver_c}},
    )
    return out[SCHEMA_COLS], receipt


def map_n0147(csv_paths: List[Path], trial_key: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    by_name = {p.name.split("__")[-1].lower(): p for p in csv_paths}
    for p in csv_paths:
        by_name[p.name.lower()] = p
    char_p = next((p for k, p in by_name.items() if "characteristic" in k), None)
    obj_p = next((p for k, p in by_name.items() if "objective" in k), None)
    if not char_p or not obj_p:
        raise FileNotFoundError(f"N0147 needs characteristic+objectives CSVs; got={list(by_name)}")
    for p in (char_p, obj_p):
        if classify_path(p) != "csv":
            raise RuntimeError(f"N0147 router refused non-CSV: {p}")
    char = pd.read_csv(char_p)
    obj = pd.read_csv(obj_p)
    assert_cols(char, ["mask_id", "ARM"], "N0147/characteristic")
    assert_cols(obj, ["mask_id", "futime8", "fustat8"], "N0147/objectives")
    m = char.merge(obj, on="mask_id", how="inner", suffixes=("", "_obj"))
    kras = None
    if "wild" in m.columns:
        kras = m["wild"].map(
            lambda v: 0.0 if str(v) in {"1", "1.0", "Y", "y"} else (1.0 if str(v) in {"0", "0.0", "N", "n"} else None)
        )
    out = pd.DataFrame(
        {
            "subjid": m["mask_id"].astype(str),
            "trial": trial_key,
            "arm": m["ARM"].map(lambda a: f"N0147_arm_{a}"),
            "arm_class": "chemo",
            "liver_met": None,
            "kras": kras,
            "nras": None,
            "ras": kras,
            "braf_mut": None,
            "ecog": pd.to_numeric(m["PS"], errors="coerce") if "PS" in m.columns else None,
            "age": None,
            "sex": m["SEX"] if "SEX" in m.columns else None,
            "pfs_days": pd.to_numeric(m["pgtime5"], errors="coerce") if "pgtime5" in m.columns else None,
            "pfs_event": pd.to_numeric(m["pgstat5"], errors="coerce") if "pgstat5" in m.columns else None,
            "os_days": pd.to_numeric(m["futime8"], errors="coerce"),
            "os_event": pd.to_numeric(m["fustat8"], errors="coerce"),
        }
    )
    out = attach_contract(out, trial_key, pack_role="full_ipd")
    receipt = _coverage(
        out,
        trial_key,
        family="n0147_csv",
        source=",".join(p.name for p in (char_p, obj_p)),
        extra={"caveat": "adjuvant; pgtime5 progression proxy; liver_met intentionally null"},
    )
    return out[SCHEMA_COLS], receipt


def map_horizon(zip_path: Path, trial_key: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """AZ HORIZON III — rdpsubj locked map (TIMETP/PFSEVENT, OSTIM/OSEVENT, BAS_LIV)."""
    subj = read_sas_member(zip_path, "rdpsubj.sas7bdat")
    assert_cols(
        subj,
        ["RANDCODE", "TIMETP", "PFSEVENT", "OSTIM", "OSEVENT", "BAS_LIV", "TRTSHORT", "SEX"],
        f"{trial_key}/rdpsubj",
    )
    n_trt = int(subj["TRTSHORT"].nunique()) if "TRTSHORT" in subj.columns else 0
    pack_role = "subset_arm" if n_trt <= 1 else "full_ipd"
    out = pd.DataFrame(
        {
            "subjid": _norm_subjid(subj["RANDCODE"]),
            "trial": trial_key,
            "arm": subj["TRTSHORT"],
            "arm_class": subj["TRTSHORT"].map(arm_class),
            "liver_met": subj["BAS_LIV"].map(yn01),
            "kras": None,
            "nras": None,
            "ras": None,
            "braf_mut": None,
            "ecog": None,
            "age": None,
            "sex": subj["SEX"],
            "pfs_days": pd.to_numeric(subj["TIMETP"], errors="coerce"),
            "pfs_event": subj["PFSEVENT"].map(yn01),
            "os_days": pd.to_numeric(subj["OSTIM"], errors="coerce"),
            "os_event": subj["OSEVENT"].map(yn01),
        }
    )
    out = attach_contract(out, trial_key, pack_role=pack_role)
    receipt = _coverage(
        out,
        trial_key,
        family="az_horizon",
        source=str(zip_path.name),
        extra={
            "aliases": {
                "pfs_days": "TIMETP",
                "pfs_event": "PFSEVENT",
                "os_days": "OSTIM",
                "os_event": "OSEVENT",
                "liver_met": "BAS_LIV",
            },
            "n_treatment_labels": n_trt,
            "subset_note": "PDS extract appears single-arm (FOLFOX+bev); weight via subset_scale",
        },
    )
    return out[SCHEMA_COLS], receipt


def map_mosaic(zip_path: Path, trial_key: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """Sanofi MOSAIC — surv table: OS locked; DFS→pfs proxy (adjuvant)."""
    surv = read_sas_member(zip_path, "surv.sas7bdat")
    assert_cols(surv, ["RSUBJID", "OSDY", "OSCENS", "DFSDY", "DFSCENS"], f"{trial_key}/surv")
    arm_src = surv["DOSE_FMT"] if "DOSE_FMT" in surv.columns else surv.get("ARM", pd.Series(["MOSAIC"] * len(surv)))
    out = pd.DataFrame(
        {
            "subjid": _norm_subjid(surv["RSUBJID"]),
            "trial": trial_key,
            "arm": arm_src,
            "arm_class": "chemo",
            "liver_met": None,  # adjuvant
            "kras": None,
            "nras": None,
            "ras": None,
            "braf_mut": None,
            "ecog": None,
            "age": pd.to_numeric(surv["AGE_DV"], errors="coerce") if "AGE_DV" in surv.columns else None,
            "sex": surv["SEX_F"] if "SEX_F" in surv.columns else None,
            "pfs_days": pd.to_numeric(surv["DFSDY"], errors="coerce"),
            "pfs_event": _censor_to_event(surv["DFSCENS"]),
            "os_days": pd.to_numeric(surv["OSDY"], errors="coerce"),
            "os_event": _censor_to_event(surv["OSCENS"]),
        }
    )
    out = attach_contract(out, trial_key, pack_role="os_dfs_ipd", published_n=PUBLISHED_N["MOSAIC_128"])
    receipt = _coverage(
        out,
        trial_key,
        family="sanofi_mosaic",
        source=str(zip_path.name),
        extra={
            "aliases": {
                "pfs_days": "DFSDY (DFS proxy)",
                "pfs_event": "1-DFSCENS",
                "os_days": "OSDY",
                "os_event": "1-OSCENS",
            },
            "subset_scale": float(out["subset_scale"].iloc[0]) if len(out) else None,
            "published_n": PUBLISHED_N["MOSAIC_128"],
            "caveat": "Adjuvant DFS mapped to pfs_*; OS complete; under-published vs N=2246",
        },
    )
    return out[SCHEMA_COLS], receipt


def map_velour_demography(zip_path: Path, trial_key: str) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    VELOUR zip is demography-only: SDTM dm/ex/ae/… with NO ADTTE/ADSL survival.
    Emit rows with null survival + pack_role=demography_only + loud warning.
    Never invent PFS/OS.
    """
    warning = (
        f"VELOUR_PACKAGE_DEMOGRAPHY_ONLY: {zip_path.name} has no ADTTE/ADSL PFS/OS columns. "
        f"Processing demography from dm.sas7bdat only; survival endpoints remain null. "
        f"Do NOT include this trial in OS/PFS models until a survival package is acquired."
    )
    log.warning(warning)
    print(f"  ⚠ {warning}")

    # Prove absence of survival tables (automated audit in receipt)
    with zipfile.ZipFile(zip_path) as z:
        members = [Path(n).name.lower() for n in z.namelist() if n.lower().endswith(".sas7bdat")]
    surv_like = [m for m in members if any(k in m for k in ("adtte", "adsl", "surv", "tte", "pfs", "os"))]
    if surv_like:
        # Unexpected — still refuse to invent without locked map
        log.warning("VELOUR unexpected surv-like members %s — still demography-only until mapped", surv_like)

    dm = read_sas_member(zip_path, "dm.sas7bdat")
    assert_cols(dm, ["RSUBJID"], f"{trial_key}/dm")
    arm = dm["ARM"] if "ARM" in dm.columns else None
    out = pd.DataFrame(
        {
            "subjid": _norm_subjid(dm["RSUBJID"]),
            "trial": trial_key,
            "arm": arm,
            "arm_class": arm.map(arm_class) if arm is not None else None,
            "liver_met": None,
            "kras": None,
            "nras": None,
            "ras": None,
            "braf_mut": None,
            "ecog": None,
            "age": pd.to_numeric(dm["AGEC"], errors="coerce") if "AGEC" in dm.columns else None,
            "sex": dm["SEX"] if "SEX" in dm.columns else None,
            "pfs_days": None,
            "pfs_event": None,
            "os_days": None,
            "os_event": None,
        }
    )
    out = attach_contract(out, trial_key, pack_role="demography_only", published_n=PUBLISHED_N["VELOUR_131"])
    receipt = _coverage(
        out,
        trial_key,
        family="sanofi_velour",
        source=str(zip_path.name),
        require_survival=False,
        extra={
            "status_override": "DEMOGRAPHY_ONLY",
            "warning": warning,
            "sas_members": members,
            "surv_like_members": surv_like,
            "subset_scale": float(out["subset_scale"].iloc[0]) if len(out) else None,
            "published_n": PUBLISHED_N["VELOUR_131"],
            "model_policy": "EXCLUDE_FROM_SURVIVAL_ENDPOINTS",
        },
    )
    receipt["status"] = "DEMOGRAPHY_ONLY"
    return out[SCHEMA_COLS], receipt


def _coverage(
    df: pd.DataFrame,
    trial_key: str,
    family: str,
    source: str,
    extra: Optional[Dict[str, Any]] = None,
    require_survival: bool = True,
) -> Dict[str, Any]:
    n = len(df)
    cov = {
        c: float(df[c].notna().mean()) if n else 0.0
        for c in SCHEMA_COLS
        if c not in {"subjid", "trial", "arm", "arm_class", "sex", "pack_role"}
    }
    surv_complete = int(df[list(REQUIRED_SURVIVAL)].notna().all(axis=1).sum()) if n else 0
    flagged = int(n - surv_complete)
    scale = float(df["subset_scale"].iloc[0]) if n and "subset_scale" in df.columns else 1.0
    pub = df["published_n"].iloc[0] if n and "published_n" in df.columns else None
    pack_role = str(df["pack_role"].iloc[0]) if n and "pack_role" in df.columns else None
    rec: Dict[str, Any] = {
        "trial": trial_key,
        "family": family,
        "source": source,
        "n_rows": n,
        "n_survival_complete": surv_complete,
        "n_survival_incomplete_flagged": flagged,
        "coverage": cov,
        "schema": SCHEMA_COLS,
        "silent_drop": False,
        "subset_scale": scale,
        "published_n": None if pub is None or (isinstance(pub, float) and pd.isna(pub)) else int(pub),
        "available_n": n,
        "pack_role": pack_role,
        "status": "ok" if n > 0 and (not require_survival or flagged == 0) else ("partial" if n > 0 else "empty"),
    }
    if extra:
        rec.update(extra)
    if require_survival:
        for c in REQUIRED_SURVIVAL:
            if n and df[c].notna().sum() == 0:
                rec["status"] = "MAPPING_FAILURE"
                rec["error"] = f"{c} entirely null after map — refusing silent success"
    if extra and extra.get("status_override"):
        rec["status"] = extra["status_override"]
    return rec


def ingest_trial(root: Path, spec: TrialSpec) -> Tuple[Optional[pd.DataFrame], Dict[str, Any]]:
    paths = resolve_package(root, spec)
    if not paths:
        return None, {
            "trial": spec.trial_key,
            "family": spec.family,
            "status": "MISSING_PACKAGE",
            "error": f"no files for glob {spec.package_glob}",
            "silent_drop": False,
        }
    if spec.family == "stub_linked":
        return None, {
            "trial": spec.trial_key,
            "family": spec.family,
            "status": "SKIPPED_LINKED_BRIDGE",
            "package": [p.name for p in paths],
            "notes": spec.notes,
            "silent_drop": False,
        }
    if spec.family == "n0147_csv":
        for p in paths:
            if classify_path(p) != "csv":
                raise RuntimeError(f"N0147 expected CSV only, got {classify_path(p)}:{p}")
        return map_n0147(paths, spec.trial_key)

    zip_paths = [p for p in paths if classify_path(p) == "zip"]
    if not zip_paths:
        return None, {
            "trial": spec.trial_key,
            "status": "NO_ZIP",
            "paths": [p.name for p in paths],
            "silent_drop": False,
        }
    z = zip_paths[0]
    if spec.family == "amgen_legacy":
        return map_amgen_legacy(z, spec.trial_key)
    if spec.family == "amgen_adam":
        return map_amgen_adam(z, spec.trial_key)
    if spec.family == "az_horizon":
        return map_horizon(z, spec.trial_key)
    if spec.family == "sanofi_mosaic":
        return map_mosaic(z, spec.trial_key)
    if spec.family == "sanofi_velour":
        return map_velour_demography(z, spec.trial_key)
    raise RuntimeError(f"unknown family {spec.family}")


def content_hash_dataframe(df: pd.DataFrame) -> str:
    """Stable hash of core clinical payload for lifespan seed gate."""
    payload = df[CORE_COLS].fillna("__NA__").astype(str).to_csv(index=False).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def write_postgres(df: pd.DataFrame, table: str = "crc_ipd_harmonized_v3") -> Dict[str, Any]:
    """Delegate to backend seeder (psycopg2) so CLI and lifespan share one path."""
    dsn = os.getenv("CRC_IPD_DSN") or os.getenv("DATABASE_URL")
    if not dsn:
        return {"postgres": "skipped", "reason": "no CRC_IPD_DSN/DATABASE_URL"}
    backend = REPO / "backend"
    if str(backend) not in sys.path:
        sys.path.insert(0, str(backend))
    try:
        from services.crc_ipd_seed import write_postgres as _seed_write
    except Exception as e:
        return {"postgres": "skipped", "reason": f"seed import failed: {e}"}
    return _seed_write(df, dsn, table=table)


def build_subset_manifest(receipts: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Statistical defense: every trial's scaling factor for Monday's models."""
    trials = {}
    for r in receipts:
        if r.get("available_n") is None and r.get("n_rows") is None:
            continue
        key = r.get("trial")
        if not key:
            continue
        trials[key] = {
            "available_n": r.get("available_n", r.get("n_rows")),
            "published_n": r.get("published_n"),
            "subset_scale": r.get("subset_scale"),
            "pack_role": r.get("pack_role"),
            "status": r.get("status"),
            "model_policy": r.get("model_policy"),
            "survival_weight_instruction": (
                "Multiply patient-level OS/PFS contributions by subset_scale; "
                "or use inverse-probability trial weight = 1/subset_scale only when "
                "assuming MCAR within published ITT (document assumption)."
                if r.get("subset_scale") is not None and float(r.get("subset_scale") or 1) < 0.999
                else "full_weight"
            ),
        }
    return {
        "schema": "crc_ipd_subset_manifest:v1",
        "null_policy": {
            "liver_met_missing": "If 1.0, axis is UNKNOWN — never coerce to 0 in 11D vector",
            "kras_missing": "If 1.0, axis is UNKNOWN — never coerce to 0 in 11D vector",
            "imputation": "complete_case_per_axis OR explicit missingness indicator; NO mean-impute by default",
            "demography_only": "EXCLUDE_FROM_SURVIVAL_ENDPOINTS",
        },
        "trials": trials,
    }


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--in-dir", type=Path, default=DEFAULT_IN)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--trials", nargs="*", default=None)
    ap.add_argument("--write-postgres", action="store_true")
    ap.add_argument("--fail-on-mapping-error", action="store_true", default=True)
    args = ap.parse_args()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    specs = TRIALS
    if args.trials:
        specs = [s for s in TRIALS if any(s.trial_key.startswith(t) or t in s.trial_key for t in args.trials)]

    frames: List[pd.DataFrame] = []
    receipts: List[Dict[str, Any]] = []
    fatal = False

    for spec in specs:
        print(f"\n=== {spec.trial_key} ({spec.family}) ===")
        try:
            df, receipt = ingest_trial(args.in_dir, spec)
        except Exception as e:
            receipt = {
                "trial": spec.trial_key,
                "family": spec.family,
                "status": "ERROR",
                "error": str(e),
                "silent_drop": False,
            }
            df = None
            fatal = True
            print(f"  ✗ {e}")
        receipts.append(receipt)
        print(
            f"  status={receipt.get('status')} n={receipt.get('n_rows')} "
            f"scale={receipt.get('subset_scale')} role={receipt.get('pack_role')}"
        )
        if receipt.get("status") == "MAPPING_FAILURE":
            fatal = True
        if df is not None and len(df):
            frames.append(df)
            df.to_parquet(args.out_dir / f"{spec.trial_key}.parquet", index=False)
            df.to_csv(args.out_dir / f"{spec.trial_key}.csv", index=False)

    if frames:
        all_df = pd.concat(frames, ignore_index=True)
        all_df.to_parquet(args.out_dir / "crc_ipd_harmonized_v3.parquet", index=False)
        all_df.to_csv(args.out_dir / "crc_ipd_harmonized_v3.csv", index=False)
        (args.out_dir / "crc_ipd_harmonized_v3.sha256").write_text(content_hash_dataframe(all_df) + "\n")
        pg = write_postgres(all_df) if args.write_postgres else {"postgres": "skipped"}
    else:
        all_df = pd.DataFrame(columns=SCHEMA_COLS)
        pg = {"postgres": "skipped", "reason": "no frames"}

    subset_manifest = build_subset_manifest(receipts)
    (args.out_dir / "subset_scaling_manifest.json").write_text(json.dumps(subset_manifest, indent=2, default=str))

    summary = {
        "in_dir": str(args.in_dir),
        "out_dir": str(args.out_dir),
        "n_trials_attempted": len(specs),
        "n_rows_total": int(len(all_df)),
        "content_sha256": content_hash_dataframe(all_df) if len(all_df) else None,
        "receipts": receipts,
        "postgres": pg,
        "schema": SCHEMA_COLS,
        "subset_manifest_path": str(args.out_dir / "subset_scaling_manifest.json"),
        "policy": {
            "no_silent_drop": True,
            "null_axes": "liver_met_missing/kras_missing flags — never coerce null→0",
            "subset_scale": "available_n/published_n baked into every row",
            "velour": "DEMOGRAPHY_ONLY warning + EXCLUDE_FROM_SURVIVAL_ENDPOINTS",
            "format_router": "classify_path by suffix before reader",
            "ram": "one sas7bdat member extracted to tempfile at a time",
        },
    }
    (args.out_dir / "ingest_receipt.json").write_text(json.dumps(summary, indent=2, default=str))
    print(f"\nDONE rows={len(all_df)} receipt={args.out_dir / 'ingest_receipt.json'}")
    if fatal and args.fail_on_mapping_error:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
