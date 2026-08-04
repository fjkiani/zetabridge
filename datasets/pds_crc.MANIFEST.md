# PDS CRC packages — expected layout

Mirrored into (gitignored):

```
backend/data/features/pds_crc/
├── pull_receipt.json
├── PRIME/
├── PACCE/
├── PEAK/
├── N0147/
├── HORIZON_III/
├── MOSAIC/
├── VELOUR/
└── PaniBSC/
```

Re-pull:

```bash
export ENV_FILE=/path/to/PDS-MCP/.env
python scripts/pds/pull_pds_sas_zips.py --env-file "$ENV_FILE" \
  --out-dir backend/data/features/pds_crc
```

See `datasets/FINDINGS_2026-08-01.md` for donation→file mapping and auth notes.
