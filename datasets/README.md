# ZetaBridge Datasets Index

**Authority:** Alpha / Zo  
**Layout rule:** Large / controlled binaries live under `backend/data/` (gitignored). This folder is the **committed map** of what exists and what we proved.

## Canonical on-disk root

```
backend/data/features/
├── maya_open/          # MAYA open transcriptomic recompute + Source Data
├── pds_crc/            # Project Data Sphere CRC trial SAS/CSV packages
├── genie_crc/          # AACR GENIE CRC TMB/MSI + EfficacyModel v3 feed
├── cbioportal_crc_*.csv / crc_*   # prior CRC IPD / efficacy v4 artifacts
├── braf_open/          # BRAF open-access bypass artifacts
└── mirror_inventory.json
```

| Tree | Source | Size (approx) | Status |
|------|--------|---------------|--------|
| `maya_open/` | Nat Commun MAYA + GEO GSE326101 | ~422 MB | Open recompute done; bulk EGA still DAC |
| `pds_crc/` | Project Data Sphere web download | ~128 MB | 13/13 packages pulled 2026-08-01 |
| `genie_crc/` | AACR GENIE (mirrored from crispr-assistant) | ~15 MB | TMB–MSI matrix + v3 feed |
| Existing `crc_*` / `cbioportal_*` | Prior zetabridge CRC work | — | Keep; do not duplicate |

## Findings (this sprint)

See [`FINDINGS_2026-08-01.md`](./FINDINGS_2026-08-01.md).

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/maya_bypass/extract_open_source_data.py` | MAYA open Source Data / Visium extracts |
| `scripts/pds/pull_pds_sas_zips.py` | Re-pull PDS packages → `backend/data/features/pds_crc` |

```bash
# PDS (env only — never hardcode password)
export ENV_FILE=/path/to/PDS-MCP/.env   # or export SAS_PASSWORD + PDS_PORTAL_USERNAME
python scripts/pds/pull_pds_sas_zips.py --env-file "$ENV_FILE"
```

## DAC still open

- MAYA bulk RNA: `dac_requests/maya_egas50000001567.md` (EGAS50000001567 / Auristone)
