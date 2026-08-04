# ENRICHMENT MODEL V2 FEED — RECEIPT

**Built UTC:** `2026-08-04T23:34:33.860051+00:00`  
**Feed:** `datasets/genie_r20/enrichment_model_v2_feed.json`  
**Feed SHA256:** `32c4df69975b0cf12a1dab0ecdd23abc3fb37752509dd42c68c73105589f77bc`  
**Matrix SHA256:** `308f4325a59a102e307a66e9213b59593c869c1d139ef65564d4509256f03121`

## Counts

| Metric | Value |
|--------|-------|
| n_rows | 24109 |
| n_patients | 22782 |
| TMB missingness | 0 |
| MSI non-null | 0 |
| **IS_GUARDANT_PTMB** | **false** |

## TMB bins

{
  "Mid (2-16)": 15188,
  "High (>16)": 6259,
  "Low (<2)": 2662
}

## Honesty gates

- Label: **tissue_panel_TMB** (never Guardant / plasma pTMB)
- MSI: **null** (R20 clinical columns absent)
- Poison CSV: **refused** (`loaded_poison=false`)
- **Does NOT soft-unblock 8D-04**

## Poison paths detected (local, not loaded)

- `zetabridge/backend/data/features/genie_crc/crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.csv`
- `zetabridge/backend/data/features/genie_crc/crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.parquet`
- `zetabridge/backend/data/features/genie_crc/crc_tmb_msi_provenance.cbioportal_bridge.QUARANTINE.json`
- `zetabridge/backend/data/features/genie_crc/patient_selection_enrichment_v1.QUARANTINE.json`
- `zetabridge/backend/data/features/genie_crc/patient_selection_enrichment_v1.QUARANTINED.csv`
- `zetabridge/datasets/genie_r20/quarantine_notes/POISON_QUARANTINE.md`
- `backend/data/features/genie_crc/crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.csv`
- `backend/data/features/genie_crc/crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.parquet`
- `backend/data/features/genie_crc/patient_selection_enrichment_v1.QUARANTINED.csv`

## Builder

`scripts/s14/build_enrichment_model_v2_feed.py`

---
**RUO:** Research Use Only. Outputs are for research enrichment / IPD engineering. Not for clinical care.
