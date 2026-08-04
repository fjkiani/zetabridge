# Poison quarantine (local-only)

**DO NOT LOAD / DO NOT RANK WITH:**

- `backend/data/features/genie_crc/patient_selection_enrichment_v1.QUARANTINED.csv` (local)
- `backend/data/features/genie_crc/crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.*` (local)

Reason: fake `pTMB == tissue TMB` + 100% unknown liver-met. Native matrix is `datasets/genie_r20/crc_tmb_msi_matrix.*` (tissue panel TMB; MSI null).

Does **not** soft-unblock 8D-04.
