# PDS SAS vault cracker

**Script:** `scripts/pds/ingest_pds_crc_ipd.py`  
**Input:** `backend/data/features/pds_crc/`  
**Output:** `backend/data/features/crc_ipd_from_zips/`  
- `crc_ipd_harmonized_v3.{csv,parquet}`  
- `ingest_receipt.json`  
- `subset_scaling_manifest.json`  
- per-trial parquet/CSV  

**Null / subset contract:** [`CRC_IPD_NULL_CONTRACT.md`](./CRC_IPD_NULL_CONTRACT.md)

## Pipeline (RAM-safe)

1. `classify_path(path)` → `csv | zip | sas7bdat | excel | unknown`
2. Family router — CSV never enters SAS reader
3. Zip: extract **one** `.sas7bdat` → tempfile → pyreadstat/pandas → delete
4. Map + attach contract columns (`*_missing`, `subset_scale`, `pack_role`)
5. Coverage receipt: `silent_drop=false`; VELOUR → `DEMOGRAPHY_ONLY` warning
6. Postgres:
   - CLI: `--write-postgres`
   - Boot: `CRC_IPD_SEED_ON_BOOT=1` lifespan hook (hash-gated)

## Locked column maps

| Family | Trials | LIVERMET | KRAS | PFS | OS |
|--------|--------|----------|------|-----|----|
| amgen_legacy | PEAK, PACCE, PRIME_264 | `LIVERMET`/`LIVRONLY` or null | `KRAS` or null | `PFSDYCR`/`PFSCR` | `DTHDY`/`DTH` |
| amgen_adam | PRIME_309, PaniBSC | `LIVERMET` or null | biomark `BMMTR1` | `PFSDYCR`/`PFSCR` | `DTHDY`/`DTH` or `DTHDYX`/`DTHX` |
| n0147_csv | N0147 | null (adjuvant) | `wild` inverted | `pgtime5`/`pgstat5` | `futime8`/`fustat8` |
| az_horizon | HORIZON_III_78 | `BAS_LIV` | null | `TIMETP`/`PFSEVENT` | `OSTIM`/`OSEVENT` |
| sanofi_mosaic | MOSAIC_128 | null (adjuvant) | null | `DFSDY`/`1-DFSCENS` (DFS proxy) | `OSDY`/`1-OSCENS` |
| sanofi_velour | VELOUR_131 | null | null | **null — demography only** | **null — demography only** |

## Subset scales (baked into every row)

| Trial | available | published | scale |
|-------|-----------|-----------|-------|
| MOSAIC_128 | ~1122 | 2246 | ~0.50 |
| HORIZON_III_78 | ~690 | 1422 | ~0.49 |
| VELOUR_131 | ~610 | 1226 | demography-only (exclude survival) |

```bash
python scripts/pds/ingest_pds_crc_ipd.py
python scripts/pds/ingest_pds_crc_ipd.py --write-postgres

# Render / staging boot seed (render.yaml → CRC_IPD_SEED_ON_BOOT=1)
# Artifact path on dyno: backend/resources/crc_ipd/crc_ipd_harmonized_v3.csv
```
