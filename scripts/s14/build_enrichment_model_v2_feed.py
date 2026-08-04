#!/usr/bin/env python3
"""M1: Honest GENIE R20 enrichment feed v2.

Rules:
- tissue_panel_TMB only; IS_GUARDANT_PTMB=false
- MSI null (R20 clinical has no MSI)
- Refuse poison / quarantine CSVs
- Does NOT soft-unblock 8D-04
RUO: Research Use Only.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "datasets" / "genie_r20" / "crc_tmb_msi_matrix.parquet"
OUT_FEED = REPO / "datasets" / "genie_r20" / "enrichment_model_v2_feed.json"
OUT_RECEIPT = REPO / "datasets" / "genie_r20" / "ENRICHMENT_MODEL_V2_RECEIPT.md"
LOCAL_MIRROR = REPO / "backend" / "data" / "features" / "genie_crc"

POISON_GLOBS = [
    "**/patient_selection_enrichment_v1.QUARANTINED.csv",
    "**/patient_selection_enrichment_v1.csv",
    "**/crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.*",
]


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def refuse_poison() -> list[str]:
    """Fail loud if caller tries to load poison paths as primary matrix."""
    refused = []
    for root in (REPO / "datasets" / "genie_r20", LOCAL_MIRROR):
        if not root.exists():
            continue
        for pattern in POISON_GLOBS:
            for p in root.glob(pattern.replace("**/", "")):
                refused.append(str(p.relative_to(REPO) if p.is_relative_to(REPO) else p))
            # also scan one level
            for p in root.rglob("*QUARANTINE*"):
                refused.append(str(p))
            for p in root.rglob("*QUARANTINED*"):
                refused.append(str(p))
    # Dedup
    refused = sorted(set(refused))
    # Explicit refuse: never use poison as MATRIX
    if MATRIX.name.lower().endswith(".quarantined.csv") or "QUARANTINE" in MATRIX.name:
        raise SystemExit(f"FAIL_LOUD: matrix path is poison: {MATRIX}")
    poison_as_input = LOCAL_MIRROR / "patient_selection_enrichment_v1.QUARANTINED.csv"
    if poison_as_input.exists() and poison_as_input.resolve() == MATRIX.resolve():
        raise SystemExit("FAIL_LOUD: refused to load QUARANTINED poison CSV as feed source")
    return refused


def main() -> int:
    if not MATRIX.exists():
        print(f"FAIL_LOUD: missing matrix {MATRIX}", file=sys.stderr)
        return 2

    refused = refuse_poison()
    df = pd.read_parquet(MATRIX)

    required = ["SAMPLE_ID", "PATIENT_ID", "tmb", "tmb_mut_per_mb", "tmb_bin", "IS_GUARDANT_PTMB"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        print(f"FAIL_LOUD: matrix missing columns {missing}", file=sys.stderr)
        return 2

    if bool(df["IS_GUARDANT_PTMB"].fillna(True).any()):
        print("FAIL_LOUD: IS_GUARDANT_PTMB has True/null — feed must be tissue-only", file=sys.stderr)
        return 2

    if "TMB_ASSAY_TYPE" in df.columns:
        bad = df.loc[df["TMB_ASSAY_TYPE"] != "tissue_panel_TMB"]
        if len(bad):
            print(f"FAIL_LOUD: non-tissue TMB_ASSAY_TYPE rows={len(bad)}", file=sys.stderr)
            return 2

    msi_non_null = int(df["MSI_STATUS"].notna().sum()) if "MSI_STATUS" in df.columns else 0
    if msi_non_null > 0:
        print(
            f"FAIL_LOUD: unexpected MSI_STATUS non-null={msi_non_null} (R20 should be null)",
            file=sys.stderr,
        )
        return 2

    bin_counts = df["tmb_bin"].value_counts(dropna=False).to_dict()
    bin_counts = {str(k): int(v) for k, v in bin_counts.items()}

    matrix_sha = sha256_file(MATRIX)
    built = datetime.now(timezone.utc).isoformat()

    feed = {
        "model": "patient_selection_enrichment_v2",
        "version": "v2",
        "disease": "crc",
        "built_utc": built,
        "description": (
            "Honest GENIE R20 enrichment feature feed. Tissue panel TMB only. "
            "MSI unavailable in R20-public clinical. Poison CSV refused. "
            "NOT Guardant plasma pTMB. Does NOT soft-unblock 8D-04."
        ),
        "source_matrix": {
            "path": "datasets/genie_r20/crc_tmb_msi_matrix.parquet",
            "sha256": matrix_sha,
            "n_rows": int(len(df)),
            "n_patients": int(df["PATIENT_ID"].nunique()),
            "release": "20.0-public",
            "source": "genie_r20_public_native",
        },
        "IS_GUARDANT_PTMB": False,
        "tmb": {
            "label": "tissue_panel_TMB",
            "columns": ["tmb", "tmb_mut_per_mb", "tmb_bin"],
            "assay_type": "tissue_panel_TMB",
            "missingness": int(df["tmb"].isna().sum()),
            "missingness_rate": float(df["tmb"].isna().mean()),
            "tmb_bin_distribution": bin_counts,
            "NOT_guardant_ptmb": True,
            "NOT_plasma_ptmb": True,
        },
        "msi": {
            "available": False,
            "MSI_STATUS_non_null": msi_non_null,
            "policy": "leave_null_do_not_invent_MSI_H",
        },
        "features_available_from_genie_r20": [
            {
                "name": "tissue_panel_TMB",
                "type": "continuous",
                "column": "tmb_mut_per_mb",
                "bin_column": "tmb_bin",
                "enrichment_use": "prior/strata only — not GuardantOMNI pTMB threshold (≥28)",
            },
            {
                "name": "tmb_bin",
                "type": "categorical",
                "levels": sorted(bin_counts.keys()),
                "source": "native tmb_bin from tmb_20.0-public.tsv",
            },
        ],
        "features_explicitly_null": [
            {
                "name": "pTMB_guardant",
                "status": "null",
                "reason": "GENIE R20 has tissue panel TMB only; refuse tissue→pTMB copy",
            },
            {
                "name": "immunoscore_ic",
                "status": "null",
                "reason": "not in GENIE R20 public",
            },
            {
                "name": "liver_met_burden",
                "status": "null",
                "reason": "GENIE public unreliable for liver-met burden; use IPD flags",
            },
            {
                "name": "MSI_STATUS",
                "status": "null",
                "reason": "absent in R20-public clinical sample/patient",
            },
        ],
        "poison_refusal": {
            "refused_paths_detected_local": refused,
            "policy": "DO_NOT_LOAD — patient_selection_enrichment_v1.QUARANTINED.csv and cBio bridge quarantine",
            "loaded_poison": False,
        },
        "null_contract": {
            "pTMB_guardant": "If missing, leave null. Never copy tissue_panel_TMB into pTMB.",
            "MSI": "If missing, leave null. Do not invent MSI-H.",
            "immunoscore_ic": "If missing, do not invent from tissue TMB.",
            "liver_met_burden": "Use IPD (PEAK/PRIME/PACCE) only.",
        },
        "do_not_claim": [
            "8D-04 soft-unblock",
            "GuardantOMNI plasma pTMB",
            "MSI coverage from this feed",
            "PATH A/B ranking winner",
            "fit_B ranking",
        ],
        "NOT_8D04_unblock": True,
        "RUO": "Research Use Only. Not clinical decision support.",
    }

    OUT_FEED.write_text(json.dumps(feed, indent=2) + "\n")
    feed_sha = sha256_file(OUT_FEED)

    receipt = f"""# ENRICHMENT MODEL V2 FEED — RECEIPT

**Built UTC:** `{built}`  
**Feed:** `datasets/genie_r20/enrichment_model_v2_feed.json`  
**Feed SHA256:** `{feed_sha}`  
**Matrix SHA256:** `{matrix_sha}`

## Counts

| Metric | Value |
|--------|-------|
| n_rows | {len(df)} |
| n_patients | {df["PATIENT_ID"].nunique()} |
| TMB missingness | {int(df["tmb"].isna().sum())} |
| MSI non-null | {msi_non_null} |
| **IS_GUARDANT_PTMB** | **false** |

## TMB bins

{json.dumps(bin_counts, indent=2)}

## Honesty gates

- Label: **tissue_panel_TMB** (never Guardant / plasma pTMB)
- MSI: **null** (R20 clinical columns absent)
- Poison CSV: **refused** (`loaded_poison=false`)
- **Does NOT soft-unblock 8D-04**

## Poison paths detected (local, not loaded)

{chr(10).join(f"- `{p}`" for p in refused) if refused else "- (none under tracked genie_r20)"}

## Builder

`scripts/s14/build_enrichment_model_v2_feed.py`

---
**RUO:** Research Use Only. Outputs are for research enrichment / IPD engineering. Not for clinical care.
"""
    OUT_RECEIPT.write_text(receipt)
    # Mirror feed into local genie_crc for ops (gitignored ok)
    if LOCAL_MIRROR.exists():
        (LOCAL_MIRROR / "enrichment_model_v2_feed.json").write_text(json.dumps(feed, indent=2) + "\n")

    print(json.dumps({"ok": True, "feed": str(OUT_FEED), "sha256": feed_sha, "n": len(df)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
