#!/usr/bin/env python3
"""
Build CRC TMB/MSI matrix from native GENIE 20.0-public (R20).

Fail loud if CRC case list ∩ clinical_sample ∩ TMB join breaks.
Does NOT soft-unblock 8D-04.
Does NOT label tissue panel TMB as Guardant pTMB.
MSI: not present in R20-public clinical tables — recorded as missing.
"""
from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

BASE = Path("/Users/fahadkiani/Desktop/development/zetabridge/backend/data/features/genie_crc")
RAW = BASE / "raw" / "r20_public"
RELEASE = "20.0-public"
PARENT_SYN = "syn76285058"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_crc_case_ids(path: Path) -> list[str]:
    text = path.read_text()
    lines = [l for l in text.splitlines() if l.startswith("case_list_ids:")]
    if len(lines) != 1:
        raise SystemExit(f"FAIL LOUD: expected 1 case_list_ids line in {path}, got {len(lines)}")
    ids = lines[0].split(":", 1)[1].strip().split("\t")
    ids = [i for i in ids if i]
    if not ids:
        raise SystemExit(f"FAIL LOUD: empty CRC case list in {path}")
    if len(ids) != len(set(ids)):
        raise SystemExit("FAIL LOUD: duplicate SAMPLE_IDs in CRC case list")
    return ids


def main() -> None:
    required = [
        "tmb_20.0-public.tsv",
        "data_clinical_sample.txt",
        "data_clinical_patient.txt",
        "cases_Colorectal_Cancer.txt",
        "assay_information.txt",
    ]
    missing = [f for f in required if not (RAW / f).exists()]
    if missing:
        raise SystemExit(f"FAIL LOUD: missing R20 files: {missing}")

    crc_ids = load_crc_case_ids(RAW / "cases_Colorectal_Cancer.txt")
    crc_set = set(crc_ids)
    n_crc = len(crc_ids)

    tmb = pd.read_csv(RAW / "tmb_20.0-public.tsv", sep="\t")
    if "SAMPLE_ID" not in tmb.columns or "tmb" not in tmb.columns:
        raise SystemExit(f"FAIL LOUD: unexpected TMB columns: {list(tmb.columns)}")
    if tmb["SAMPLE_ID"].duplicated().any():
        raise SystemExit("FAIL LOUD: duplicate SAMPLE_ID in TMB file")

    samp = pd.read_csv(RAW / "data_clinical_sample.txt", sep="\t", comment="#", low_memory=False)
    pat = pd.read_csv(RAW / "data_clinical_patient.txt", sep="\t", comment="#", low_memory=False)
    assay = pd.read_csv(RAW / "assay_information.txt", sep="\t", low_memory=False)

    # --- Join integrity gates ---
    missing_tmb = crc_set - set(tmb["SAMPLE_ID"])
    missing_samp = crc_set - set(samp["SAMPLE_ID"])
    if missing_tmb:
        raise SystemExit(f"FAIL LOUD: {len(missing_tmb)} CRC samples missing from TMB")
    if missing_samp:
        raise SystemExit(f"FAIL LOUD: {len(missing_samp)} CRC samples missing from clinical_sample")

    crc_tmb = tmb[tmb["SAMPLE_ID"].isin(crc_set)].copy()
    crc_samp = samp[samp["SAMPLE_ID"].isin(crc_set)].copy()
    if len(crc_tmb) != n_crc or len(crc_samp) != n_crc:
        raise SystemExit(
            f"FAIL LOUD: row count mismatch CRC={n_crc} TMB={len(crc_tmb)} sample={len(crc_samp)}"
        )

    bad_type = crc_samp[crc_samp["CANCER_TYPE"] != "Colorectal Cancer"]
    if len(bad_type):
        raise SystemExit(
            f"FAIL LOUD: {len(bad_type)} case-list samples have CANCER_TYPE != Colorectal Cancer"
        )

    # MSI: R20-public clinical tables have no MSI/MMR columns
    msi_cols = [
        c
        for c in list(samp.columns) + list(pat.columns)
        if any(x in c.upper() for x in ("MSI", "MMR", "MS_STATUS", "INSTAB"))
    ]
    msi_available = bool(msi_cols)

    # Native TMB is fraction of bases; bins are labeled in mut/Mb (2, 16).
    # Document both; never call it pTMB.
    crc_tmb["tmb_mut_per_mb"] = crc_tmb["tmb"].astype(float) * 1e6

    # Assay join (left)
    assay_keep = [c for c in ["SEQ_ASSAY_ID", "CENTER", "number_of_genes", "platform", "alteration_types"] if c in assay.columns]
    assay_u = assay[assay_keep].drop_duplicates(subset=["SEQ_ASSAY_ID"]) if "SEQ_ASSAY_ID" in assay_keep else pd.DataFrame()

    mat = crc_samp.merge(crc_tmb, on="SAMPLE_ID", how="inner", validate="one_to_one")
    if len(mat) != n_crc:
        raise SystemExit(f"FAIL LOUD: sample⨝TMB produced {len(mat)} rows, expected {n_crc}")

    mat = mat.merge(pat, on="PATIENT_ID", how="left", validate="many_to_one", suffixes=("", "_pat"))
    if mat["PATIENT_ID"].isna().any():
        raise SystemExit("FAIL LOUD: PATIENT_ID null after patient join")
    n_pat_miss = int((~mat["PATIENT_ID"].isin(set(pat["PATIENT_ID"]))).sum())
    if n_pat_miss:
        raise SystemExit(f"FAIL LOUD: {n_pat_miss} rows missing patient clinical")

    if len(assay_u):
        mat = mat.merge(assay_u, on="SEQ_ASSAY_ID", how="left", suffixes=("", "_assay"))

    # Explicit MSI absence
    mat["MSI_STATUS"] = pd.NA
    mat["MSI_AVAILABLE"] = False
    mat["TMB_ASSAY_TYPE"] = "tissue_panel_TMB"
    mat["IS_GUARDANT_PTMB"] = False
    mat["GENIE_RELEASE"] = RELEASE
    mat["SOURCE"] = "genie_r20_public_native"

    # Column order for deliverable
    front = [
        "SAMPLE_ID",
        "PATIENT_ID",
        "tmb",
        "tmb_mut_per_mb",
        "tmb_bin",
        "TMB_ASSAY_TYPE",
        "IS_GUARDANT_PTMB",
        "MSI_STATUS",
        "MSI_AVAILABLE",
        "ONCOTREE_CODE",
        "CANCER_TYPE",
        "CANCER_TYPE_DETAILED",
        "SAMPLE_TYPE",
        "SAMPLE_TYPE_DETAILED",
        "SAMPLE_CLASS",
        "SEQ_ASSAY_ID",
        "SEQ_YEAR",
        "AGE_AT_SEQ_REPORT",
        "AGE_AT_SEQ_REPORT_DAYS",
        "SEX",
        "PRIMARY_RACE",
        "ETHNICITY",
        "BIRTH_YEAR",
        "CENTER",
        "DEAD",
        "YEAR_CONTACT",
        "YEAR_DEATH",
        "INT_CONTACT",
        "INT_DOD",
        "GENIE_RELEASE",
        "SOURCE",
    ]
    # keep assay extras if present
    for c in ["number_of_genes", "platform", "alteration_types"]:
        if c in mat.columns and c not in front:
            front.append(c)
    cols = [c for c in front if c in mat.columns]
    extra = [c for c in mat.columns if c not in cols]
    out_df = mat[cols + extra].copy()

    # Write artifacts
    csv_path = BASE / "crc_tmb_msi_matrix.csv"
    pq_path = BASE / "crc_tmb_msi_matrix.parquet"
    out_df.to_csv(csv_path, index=False)
    out_df.to_parquet(pq_path, index=False)

    # QC
    tmb_missing = int(out_df["tmb"].isna().sum())
    tmb_bin_dist = out_df["tmb_bin"].value_counts(dropna=False).to_dict()
    msi_dist = {
        "available": msi_available,
        "msi_columns_found_in_clinical": msi_cols,
        "MSI_STATUS_non_null": int(out_df["MSI_STATUS"].notna().sum()),
        "note": "R20-public data_clinical_sample/patient have no MSI/MMR fields; MSI left null",
    }
    oncotree = out_df["ONCOTREE_CODE"].value_counts().head(20).to_dict()
    assay_dist = out_df["SEQ_ASSAY_ID"].value_counts().head(15).to_dict()

    # Sanity: within each bin, tmb*1e6 respects labeled mut/Mb thresholds
    mb = out_df["tmb_mut_per_mb"]
    low_mask = out_df["tmb_bin"] == "Low (<2)"
    mid_mask = out_df["tmb_bin"] == "Mid (2-16)"
    high_mask = out_df["tmb_bin"] == "High (>16)"
    low_ok = float((mb[low_mask] <= 2.05).mean()) if low_mask.any() else 1.0
    mid_ok = float(((mb[mid_mask] >= 1.95) & (mb[mid_mask] <= 16.05)).mean()) if mid_mask.any() else 1.0
    high_ok = float((mb[high_mask] >= 15.9).mean()) if high_mask.any() else 1.0
    if low_ok < 0.99 or mid_ok < 0.99 or high_ok < 0.99:
        raise SystemExit(
            f"FAIL LOUD: tmb_bin vs tmb*1e6 inconsistency "
            f"low_ok={low_ok:.4f} mid_ok={mid_ok:.4f} high_ok={high_ok:.4f}"
        )

    raw_hashes = {f: sha256_file(RAW / f) for f in required}
    out_hashes = {
        "crc_tmb_msi_matrix.csv": sha256_file(csv_path),
        "crc_tmb_msi_matrix.parquet": sha256_file(pq_path),
    }

    qc = {
        "artifact": "crc_tmb_msi_matrix",
        "source": "genie_r20_public_native",
        "release": RELEASE,
        "parent_syn": PARENT_SYN,
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "builder": "scripts/s14/build_genie_r20_crc_tmb_msi_matrix.py",
        "n_crc": n_crc,
        "n_rows_matrix": int(len(out_df)),
        "n_patients": int(out_df["PATIENT_ID"].nunique()),
        "join": {
            "crc_case_list": n_crc,
            "crc_cap_tmb": int(len(crc_tmb)),
            "crc_cap_clinical_sample": int(len(crc_samp)),
            "crc_cap_patient": int(out_df["PATIENT_ID"].isin(set(pat["PATIENT_ID"])).sum()),
            "missing_tmb": 0,
            "missing_sample": 0,
            "missing_patient": 0,
        },
        "tmb": {
            "column_native": "tmb",
            "units_native": "fraction_of_bases (GENIE file as-downloaded)",
            "column_derived_mut_per_mb": "tmb_mut_per_mb = tmb * 1e6",
            "tmb_bin_source": "native tmb_bin from tmb_20.0-public.tsv",
            "missingness": tmb_missing,
            "missingness_rate": tmb_missing / len(out_df),
            "describe_native": out_df["tmb"].describe().to_dict(),
            "describe_mut_per_mb": out_df["tmb_mut_per_mb"].describe().to_dict(),
            "tmb_bin_distribution": tmb_bin_dist,
            "NOT_guardant_ptmb": True,
            "label": "tissue_panel_TMB",
        },
        "msi": msi_dist,
        "oncotree_top": oncotree,
        "seq_assay_id_top": assay_dist,
        "sha256_inputs": raw_hashes,
        "sha256_outputs": out_hashes,
        "do_not_claim": [
            "8D-04 soft-unblock",
            "GuardantOMNI plasma pTMB",
            "Immunoscore-IC",
            "liver-met burden from this matrix",
        ],
        "quarantine_notes": {
            "cbioportal_bridge": "crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.*",
            "poison_enrichment_csv": "patient_selection_enrichment_v1.QUARANTINED.csv (unchanged)",
        },
    }

    qc_path = BASE / "crc_tmb_msi_qc_receipt.json"
    prov_path = BASE / "crc_tmb_msi_provenance.json"
    qc_path.write_text(json.dumps(qc, indent=2, default=str) + "\n")
    provenance = {
        "source": "genie_r20_public_native",
        "release": RELEASE,
        "parent_syn": PARENT_SYN,
        "n_rows": int(len(out_df)),
        "n_crc": n_crc,
        "n_patients": int(out_df["PATIENT_ID"].nunique()),
        "tmb_missing": tmb_missing,
        "msi_available": msi_available,
        "tmb_bin_distribution": tmb_bin_dist,
        "sha256_csv": out_hashes["crc_tmb_msi_matrix.csv"],
        "sha256_parquet": out_hashes["crc_tmb_msi_matrix.parquet"],
        "raw_dir": str(RAW),
        "qc_receipt": str(qc_path),
        "built_utc": qc["built_utc"],
        "NOT_8D04_unblock": True,
        "NOT_guardant_ptmb": True,
    }
    prov_path.write_text(json.dumps(provenance, indent=2, default=str) + "\n")

    print("OK")
    print(f"n_crc={n_crc} n_patients={out_df['PATIENT_ID'].nunique()} tmb_missing={tmb_missing}")
    print(f"tmb_bin={tmb_bin_dist}")
    print(f"msi_available={msi_available}")
    print(f"csv={csv_path}")
    print(f"parquet={pq_path}")
    print(f"qc={qc_path}")
    print(f"sha256_csv={out_hashes['crc_tmb_msi_matrix.csv']}")


if __name__ == "__main__":
    try:
        import pyarrow  # noqa: F401
    except ImportError:
        print("WARN: pyarrow missing — will try fastparquet / fail on parquet", file=sys.stderr)
    main()
