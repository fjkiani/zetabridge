# GENIE CRC R20 — RECEIPT

**UTC build:** see `crc_tmb_msi_qc_receipt.json` → `built_utc`  
**Release:** AACR Project GENIE **20.0-public** (`syn76285058`)  
**access_status:** `APPROVED_R20_PUBLIC` (downloads verified + CRC matrix rebuilt)

## Deliverables

| Artifact | Path |
|----------|------|
| Joined CRC matrix (CSV) | `crc_tmb_msi_matrix.csv` |
| Joined CRC matrix (parquet) | `crc_tmb_msi_matrix.parquet` |
| QC receipt | `crc_tmb_msi_qc_receipt.json` |
| Provenance | `crc_tmb_msi_provenance.json` |
| Raw R20 | `raw/r20_public/` |
| Download receipt | `raw/r20_download_receipt.json` |

## Key counts (native R20 join)

- **n CRC** (case list ∩ clinical_sample ∩ TMB): **24,109 / 24,109** (0 missing TMB)
- **n patients:** 22,782
- **TMB missingness:** 0
- **TMB bin:** Low (&lt;2) 2,662 · Mid (2–16) 15,188 · High (&gt;16) 6,259
- **MSI:** **not available** in R20-public `data_clinical_sample` / `data_clinical_patient` (columns absent → `MSI_STATUS` null, `MSI_AVAILABLE=false`)

## TMB honesty

- Source file: `tmb_20.0-public.tsv` (`syn76285103`)
- Native column `tmb` = GENIE as-downloaded (**fraction of bases**)
- Derived `tmb_mut_per_mb = tmb × 1e6` matches labeled `tmb_bin` mut/Mb thresholds
- Labeled **`tissue_panel_TMB`** — **`IS_GUARDANT_PTMB=false`**
- **Do not** treat as GuardantOMNI plasma pTMB

## Quarantine (still poison / bridge)

| File | Status |
|------|--------|
| `patient_selection_enrichment_v1.QUARANTINED.csv` | Poison — fake pTMB==tissue TMB + 100% unknown liver-met |
| `crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.*` | Prior cBioPortal bridge proxy |

## Explicit non-claims

- **Does NOT soft-unblock 8D-04** (PATH A `fit_A` remains sole ranking formula)
- Does NOT provide Immunoscore-IC
- Does NOT provide liver-met burden
- Does NOT provide Guardant plasma pTMB

## Builder

`zetabridge/scripts/s14/build_genie_r20_crc_tmb_msi_matrix.py`

Honest enrichment prior (unchanged, IPD/published):  
`bayesian_prior_enrichment_feed_v3_psm.json`
