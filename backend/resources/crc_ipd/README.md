# CRC IPD deploy artifact

Committed so Render (`rootDir: backend`) can seed Postgres on boot.

| File | Purpose |
|------|---------|
| `crc_ipd_harmonized_v3.csv` | Harmonized IPD (9,418 rows) |
| `subset_scaling_manifest.json` | Trial subset scales + null policy |
| `ingest_receipt.json` | Last ingest coverage receipt |

Regenerate via:

```bash
python3 scripts/pds/ingest_pds_crc_ipd.py
cp backend/data/features/crc_ipd_from_zips/crc_ipd_harmonized_v3.csv backend/resources/crc_ipd/
cp backend/data/features/crc_ipd_from_zips/subset_scaling_manifest.json backend/resources/crc_ipd/
cp backend/data/features/crc_ipd_from_zips/ingest_receipt.json backend/resources/crc_ipd/
```

Boot: `CRC_IPD_SEED_ON_BOOT=1` (set in `render.yaml`).
