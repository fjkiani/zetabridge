# CRC IPD ↔ ZetaBridge null / subset contract

**Authority:** Alpha / Zo  
**Schema:** `crc_ipd_harmonized_v3` + `subset_scaling_manifest.json`  
**Consumers:** ModelRouter, executeDoubleDip, any 11D patient-vector builder

## Problem

SQL `NULL` on `liver_met` / `kras` must **not** become `0.0` in float vectors. Coercing null→0 silently encodes “no liver met” / “KRAS WT” and corrupts Monday’s model.

## Contract columns (every row)

| Column | Meaning | Consumer rule |
|--------|---------|---------------|
| `liver_met` | 0/1 or NULL | Use only if `liver_met_missing==0` |
| `liver_met_missing` | 1.0 = axis unknown | If 1 → omit axis OR set indicator bit; **never fill 0** |
| `kras` / `kras_missing` | same | same |
| `nras_missing` / `ras_missing` | same | same |
| `subset_scale` | `available_n / published_n` | Multiply OS/PFS contribution by this weight |
| `published_n` | literature ITT N | Audit |
| `available_n` | rows in this pack | Audit |
| `pack_role` | `full_ipd` \| `os_dfs_ipd` \| `demography_only` \| `subset_arm` | Gate endpoint eligibility |

## Null policy (locked)

1. **Default:** complete-case **per axis** (drop axis from 11D if `*_missing==1`), keep patient if other axes present.
2. **Forbidden:** mean / mode / zero imputation of biomarker axes without an explicit experiment flag.
3. **Optional:** multiple imputation only behind `CRC_IPD_ALLOW_MI=1` and must write `*_imputed=1` provenance — not default for Monday.
4. **VELOUR (`pack_role=demography_only`):** `EXCLUDE_FROM_SURVIVAL_ENDPOINTS` — demographics ok, OS/PFS forbidden.

## Subset sampling (MOSAIC / HORIZON)

| Trial | available | published | `subset_scale` |
|-------|-----------|-----------|----------------|
| MOSAIC_128 | ~1122 | 2246 | ≈0.50 |
| HORIZON_III_78 | ~690 | 1422 | ≈0.49 (single-arm extract) |
| VELOUR_131 | ~610 | 1226 | ≈0.50 but **demography-only** |

Implementation: `subset_scale` is a **row-level float** in CSV/Postgres. Survival models must weight by `subset_scale` (or document IPW). Manifest: `subset_scaling_manifest.json` regenerated on every ingest.

## Pseudocode for executeDoubleDip / ModelRouter

```python
def build_11d(row):
    vec = []
    mask = []  # True = observed
    for axis, val, miss in [
        ("liver_met", row.liver_met, row.liver_met_missing),
        ("kras", row.kras, row.kras_missing),
        # … remaining axes
    ]:
        if miss == 1.0 or val is None:
            vec.append(0.0)      # placeholder slot
            mask.append(False)   # MUST accompany vector
        else:
            vec.append(float(val))
            mask.append(True)
    w = float(row.subset_scale or 1.0)
    if row.pack_role == "demography_only":
        raise SkipTrial("EXCLUDE_FROM_SURVIVAL_ENDPOINTS")
    return vec, mask, w
```

## Boot seed

Set on **Render** — `render.yaml` already sets `CRC_IPD_SEED_ON_BOOT=1`.
Artifact: `backend/resources/crc_ipd/crc_ipd_harmonized_v3.csv` (committed; survives `rootDir: backend`).

```bash
# DATABASE_URL wired from zetabridge-pg in render.yaml
# After deploy: GET /health → crc_ipd_seed.{postgres,upserted|reason}
```

Lifespan hook: `backend/services/crc_ipd_seed.py` → `write_postgres` with content-hash skip.
