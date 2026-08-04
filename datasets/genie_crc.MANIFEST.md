# GENIE CRC — expected layout

## Tracked (this repo): `datasets/genie_r20/`

| Artifact | Notes |
|----------|-------|
| `crc_tmb_msi_matrix.csv` / `.parquet` | Native GENIE R20-public CRC (n=24,109); **tissue panel TMB**, MSI null |
| `crc_tmb_msi_qc_receipt.json` | QC |
| `crc_tmb_msi_provenance.json` | Provenance |
| `RECEIPT.md` | Human receipt |
| `access_status.json` | AUTH OK scrubbed (no PAT) |
| `r20_download_receipt.SCRUBBED.json` | File SHAs; paths local-only |
| `enrichment_model_v1_feed.json` | Honest feed pointer (not pTMB) |
| `bayesian_prior_enrichment_feed_v3_psm.json` | IPTW prior |
| `quarantine_notes/POISON_QUARANTINE.md` | Poison stays local |

## Local-only (gitignored under `backend/data/`):

```
backend/data/features/genie_crc/raw/r20_public/   # ~1.5GB mutations/CNA/clinical/TMB
patient_selection_enrichment_v1.QUARANTINED.csv  # poison
crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.*
```

**Builder:** `scripts/s14/build_genie_r20_crc_tmb_msi_matrix.py`  
**Does NOT soft-unblock 8D-04.** Tissue TMB ≠ Guardant pTMB.
