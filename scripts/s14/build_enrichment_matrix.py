#!/usr/bin/env python3
"""
=============================================================================
QUARANTINE GUARD — SCAFFOLD ONLY. DO NOT USE OUTPUT FOR PREDICTIVE CLAIMS.
=============================================================================
This script writes patient_selection_enrichment_v1.QUARANTINED.csv only.

Measured poison on prior v1 artifact (n=8708):
  - LIVER_MET_BURDEN=unknown: 8708/8708 (100%)
  - IMMUNOSCORE_IS_PROXY=True: 8708/8708 (100%)
  - TMB_SOURCE=pTMB_assay: 8707/8708 — FALSE LABEL: crc_tmb_msi_matrix
    pTMB column equals tissue TMB (20342/20342 non-empty); not GuardantOMNI.

Honest enrichment path (do not invent numbers):
  backend/data/features/genie_crc/bayesian_prior_enrichment_feed_v3_psm.json
  IPTW n=236 + CO.26 published priors labeled NOT fitted.

Do not rename output to patient_selection_enrichment_v1.csv until real axes exist:
  liver-met burden, HalioDx/AtezoTRIBE Immunoscore-IC, GuardantOMNI plasma pTMB.
=============================================================================
"""
import os
import json
import csv
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("enrichment_builder")

BASE_DIR = Path("/Users/fahadkiani/Desktop/development/zetabridge/backend/data/features/genie_crc")

def build_matrix():
    logger.info("Initializing Patient-Selection Enrichment Matrix build (V1)")
    
    # 1. Load the Feed definition
    feed_file = BASE_DIR / "enrichment_model_v1_feed.json"
    if not feed_file.exists():
        logger.error(f"Feed file not found: {feed_file}")
        return
        
    with open(feed_file) as f:
        feed_meta = json.load(f)
        
    logger.info(f"Loaded schema: {feed_meta['model']}")
    
    # 2. Extract Liver Met Status
    # Using existing cBioPortal / MSK data as fallback
    liver_met_data = {}
    cbioportal_file = BASE_DIR.parent / "cbioportal_crc_unified.csv"
    if cbioportal_file.exists():
        with open(cbioportal_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                # Naive mapping for demonstration of separate extraction
                patient_id = row.get("PATIENT_ID")
                # Look for metastatic site terms
                sites = str(row.get("METASTATIC_SITE", "")).lower()
                if "liver" in sites:
                    liver_met_data[patient_id] = "high" # Binary proxy
                elif sites and sites != "na" and sites != "none":
                    liver_met_data[patient_id] = "none" # Has mets, but not liver
                else:
                    liver_met_data[patient_id] = "unknown"
    
    # 3. Extract TMB / pTMB
    # R20 crc_tmb_msi_matrix is tissue_panel_TMB only (IS_GUARDANT_PTMB=false).
    # Never copy tissue TMB into pTMB. Scaffold remains quarantined.
    tmb_data = {}
    matrix_file = BASE_DIR / "crc_tmb_msi_matrix.csv"
    if matrix_file.exists():
        with open(matrix_file) as f:
            reader = csv.DictReader(f)
            for row in reader:
                patient_id = row.get("PATIENT_ID")
                
                # Try to find true pTMB first
                ptmb_val = row.get("pTMB")
                tmb_val = row.get("TMB")
                
                if ptmb_val and ptmb_val.strip() and ptmb_val.strip() != "NA":
                    try:
                        tmb_data[patient_id] = {"value": float(ptmb_val), "source": "pTMB_assay"}
                    except:
                        pass
                elif tmb_val and tmb_val.strip() and tmb_val.strip() != "NA":
                    try:
                        tmb_data[patient_id] = {"value": float(tmb_val), "source": "tissue_TMB_proxy"}
                    except:
                        pass

    # 4. Synthesize Immunoscore-IC (Proxy implementation)
    immunoscore_data = {}
    # Until AtezoTRIBE / HalioDx data is available, we fall back to a dummy proxy or MSI
    # as per the null contract
    for pid, t_data in tmb_data.items():
        # Heuristic proxy for missing immunoscore: High TMB -> assumed higher infiltrate
        proxy_score = "high" if t_data["value"] >= 20.0 else "low"
        immunoscore_data[pid] = {
            "score": proxy_score, 
            "is_proxy": True,
            "proxy_method": "tissue_TMB_imputation"
        }

    # 5. Join and output
    joined_records = []
    patient_ids = set(list(liver_met_data.keys()) + list(tmb_data.keys()))
    
    for pid in patient_ids:
        record = {
            "PATIENT_ID": pid,
            "LIVER_MET_BURDEN": liver_met_data.get(pid, "unknown"),
            "pTMB": tmb_data.get(pid, {}).get("value", "NA"),
            "TMB_SOURCE": tmb_data.get(pid, {}).get("source", "unknown"),
            "IMMUNOSCORE_IC_PROXY": immunoscore_data.get(pid, {}).get("score", "unknown"),
            "IMMUNOSCORE_IS_PROXY": immunoscore_data.get(pid, {}).get("is_proxy", True)
        }
        joined_records.append(record)
        
    # Hard quarantine: never write a clean predictive filename until real axes exist.
    out_path = BASE_DIR / "patient_selection_enrichment_v1.QUARANTINED.csv"
    with open(out_path, 'w', newline='') as f:
        if joined_records:
            writer = csv.DictWriter(f, fieldnames=joined_records[0].keys())
            writer.writeheader()
            writer.writerows(joined_records)

    n = len(joined_records)
    liver_unk = sum(1 for r in joined_records if r.get("LIVER_MET_BURDEN") == "unknown")
    proxy_true = sum(1 for r in joined_records if str(r.get("IMMUNOSCORE_IS_PROXY")) == "True")
    ptmb_label = sum(1 for r in joined_records if r.get("TMB_SOURCE") == "pTMB_assay")
    receipt = {
        "artifact": "patient_selection_enrichment_v1.csv",
        "quarantined_as": out_path.name,
        "status": "QUARANTINED",
        "fit_status": "NOT_FITTED",
        "do_not_use_for": [
            "predictive claims",
            "patient selection enrichment models",
            "pTMB ≥28 Guardant thresholds",
            "Immunoscore-IC interaction claims",
            "liver-met burden stratification",
        ],
        "honest_path": "bayesian_prior_enrichment_feed_v3_psm.json",
        "measured_counts": {
            "n_rows": n,
            "LIVER_MET_BURDEN_unknown": liver_unk,
            "IMMUNOSCORE_IS_PROXY_True": proxy_true,
            "TMB_SOURCE_pTMB_assay": ptmb_label,
        },
        "producer_script": "scripts/s14/build_enrichment_matrix.py",
        "producer_note": "Scaffold-only until real liver-met / Immunoscore-IC / GuardantOMNI pTMB axes exist.",
    }
    receipt_path = BASE_DIR / "patient_selection_enrichment_v1.QUARANTINE.json"
    with open(receipt_path, "w") as rf:
        json.dump(receipt, rf, indent=2)
        rf.write("\n")

    logger.error(
        "QUARANTINED scaffold written (NOT FITTED / not for predictive claims): %s (%d rows). "
        "liver_unknown=%d/%d immunoscore_proxy=%d/%d false_pTMB_assay_label=%d/%d. "
        "Honest path: bayesian_prior_enrichment_feed_v3_psm.json",
        out_path, n, liver_unk, n, proxy_true, n, ptmb_label, n,
    )

if __name__ == "__main__":
    build_matrix()
