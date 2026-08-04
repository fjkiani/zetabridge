# GENIE Synapse Worker Plan — STC-1010 / CRC enrichment

**Date:** 2026-08-04 (truth-sync post deep audit)  
**Status:** ✅ **`APPROVED_R20_PUBLIC`** — R20-public raw on disk + CRC matrix rebuilt. Does **not** soft-unblock 8D-04.  
**RUO:** Research Use Only. Does **not** soft-unblock 8D-04.

> **START HERE (virgin agent):** read this file, then  
> `Brenus-repo/engagements/brenus/genie_synapse/GENIE_HARD_AGENT_BRIEF.md`  
> Do **not** re-download R20 unless a file is corrupt (size/sha mismatch vs receipt).

---

## START HERE — boot commands (duckdb / pandas / pyarrow; **no polars**)

```bash
# 0) Paths (zetabridge root)
ZB=/Users/fahadkiani/Desktop/development/zetabridge
RAW=$ZB/backend/data/features/genie_crc/raw/r20_public
MX=$ZB/datasets/genie_r20/crc_tmb_msi_matrix.parquet

# 1) Confirm status (must say APPROVED_R20_PUBLIC)
python3 -c "import json; print(json.load(open('$ZB/datasets/genie_r20/access_status.json'))['genie_synapse'])"

# 2) Raw inventory (expect ~1.5GB total; mutations ~990MB; CNA ~443MB)
ls -lah "$RAW"

# 3) Matrix reality check (n=24109, MSI null, IS_GUARDANT_PTMB=false)
python3 - <<'PY'
import pandas as pd
df = pd.read_parquet("/Users/fahadkiani/Desktop/development/zetabridge/datasets/genie_r20/crc_tmb_msi_matrix.parquet")
assert len(df) == 24109
assert df["MSI_STATUS"].isna().all()
assert (~df["IS_GUARDANT_PTMB"].astype(bool)).all()
print("OK", len(df), "TMB_max_mutMb", float(df["tmb_mut_per_mb"].max()))
PY

# 4) DuckDB stream mutations (do NOT load full 990MB into RAM blindly)
python3 - <<'PY'
import duckdb
raw = "/Users/fahadkiani/Desktop/development/zetabridge/backend/data/features/genie_crc/raw/r20_public"
con = duckdb.connect()
print(con.execute(f"""
  SELECT Hugo_Symbol, COUNT(*) AS n
  FROM read_csv_auto('{raw}/data_mutations_extended.txt', delim='\t', header=true,
                     ignore_errors=true, sample_size=-1)
  WHERE Tumor_Sample_Barcode LIKE 'GENIE-%'
  GROUP BY 1 ORDER BY n DESC LIMIT 15
""").fetchdf())
PY
```

**Stack rule:** `polars` is **missing** in this environment. Use **duckdb + pandas + pyarrow** only.

---

## A) Ground truth — unlocked NOW (2026-08-04 audit)

### Status receipt

| Field | Value |
|-------|-------|
| `genie_synapse` | **`APPROVED_R20_PUBLIC`** |
| Live probe | Bearer auth OK (non-anonymous); **7/7** R20 critical files downloaded |
| Matrix | Native GENIE R20 tissue panel TMB; **MSI null**; **`IS_GUARDANT_PTMB=false`** |
| Canonical matrix (git) | `zetabridge/datasets/genie_r20/crc_tmb_msi_matrix.parquet` — **n=24109** |
| Local twin (gitignored-heavy dir) | `backend/data/features/genie_crc/crc_tmb_msi_matrix.parquet` (same build) |
| Feed v2 | **Contract JSON** (schema/provenance) — **not** per-patient feature rows |
| Features | Live in **parquet/csv matrix** (+ raw mutations/CNA) |
| GENIE↔PDS | **`JOIN_IMPOSSIBLE`** (0 matched IDs) — see Brenus `GENIE_PDS_JOIN_RECEIPT.md` |
| Held-out AUROC | Tissue TMB → GENIE `DEAD` ≈ **0.506** (null / no lift) — done |
| Product consumer | **None** yet — research enrichment only |
| 8D-04 | **LOCKED** — GENIE never soft-unblocks |

### Raw R20-public on disk (gitignored) — **PRESENT; do not re-download**

Dir: `zetabridge/backend/data/features/genie_crc/raw/r20_public/`

| File | syn ID | ~Size |
|------|--------|-------|
| `tmb_20.0-public.tsv` | syn76285103 | ~15 MB |
| `data_clinical_patient.txt` | syn76285062 | ~28 MB |
| `data_clinical_sample.txt` | syn76285063 | ~44 MB |
| `cases_Colorectal_Cancer.txt` | syn76285182 | ~608 KB |
| `assay_information.txt` | syn76285059 | ~37 KB |
| `data_CNA.txt` | syn76285060 | **~443 MB** |
| `data_mutations_extended.txt` | syn76285067 | **~990 MB** |

Receipt (scrubbed in git): `datasets/genie_r20/r20_download_receipt.SCRUBBED.json`  
Local full receipt: `backend/data/features/genie_crc/raw/r20_download_receipt.json` (gitignored)

**Re-download policy:** only if corrupt (size/sha256 mismatch vs scrubbed receipt). Do **not** “refresh” casually.

### Matrix facts (fail loud)

| Fact | Truth |
|------|-------|
| n rows | **24109** CRC samples |
| n patients | 22782 |
| TMB | Tissue **panel** TMB (`tmb`, `tmb_mut_per_mb`, `tmb_bin`) |
| TMB outliers | `tmb_mut_per_mb` max ≈ **411765** — **assay-stratify** before any model |
| MSI | **NULL** (`MSI_AVAILABLE=false`; no MSI columns in R20 clinical) |
| Guardant pTMB | **False** — never copy tissue TMB → pTMB |
| `DEAD` | Messy stringy clinical flag — treat as dirty label; held-out AUROC already ~0.506 |

### Poison / quarantine (DO NOT LOAD)

| Path | Why |
|------|-----|
| `backend/data/features/genie_crc/patient_selection_enrichment_v1.QUARANTINED.csv` | Fake pTMB==tissue TMB; 100% unknown liver-met |
| `backend/data/features/genie_crc/crc_tmb_msi_matrix.cbioportal_bridge.QUARANTINE.*` | Prior cBio bridge — superseded |
| Notes | `datasets/genie_r20/quarantine_notes/POISON_QUARANTINE.md` |

### Honest adjacent assets

| Asset | Path | Role |
|-------|------|------|
| Enrichment feed **v2 contract** | `datasets/genie_r20/enrichment_model_v2_feed.json` | Schema + honesty gates |
| Bayesian prior (IPTW) | `datasets/genie_r20/bayesian_prior_enrichment_feed_v3_psm.json` | IPD liver-met-neg + CO.26 published HRs — **not** GENIE outcomes |
| MoA registry | `Brenus-repo/.../resources/trial_moa_vectors.REBUILT.json` | **N=322 pathway vectors** (ddr/mapk/pi3k/vegf/her2/io/efflux/rss) — **NOT a gene list** |
| Join receipt | `Brenus-repo/.../genie_synapse/GENIE_PDS_JOIN_RECEIPT.md` | `JOIN_IMPOSSIBLE` |
| Held-out sitrep | `Brenus-repo/.../results/ENRICHMENT_HELDOUT_SITREP.md` | AUROC 0.5064 |

### What GENIE does **not** unlock

- ❌ **8D-04 soft-unblock**
- ❌ GuardantOMNI **plasma** pTMB
- ❌ Immunoscore-IC
- ❌ Liver-met burden on every GENIE CRC row
- ❌ Controlled BPC / consortium entities (still 403 without ACT grant)
- ❌ Rehabilitating poison CSV / cBio bridge quarantine
- ❌ GENIE patient IDs joined to PDS `SUBJID` (namespaces disjoint)

---

## B) Linkage map (honest)

```
GENIE R20 tissue panel TMB + mutations/CNA  ──►  genomic feature layer (parquet + duckdb)
         │
         │  IDs: GENIE-<CENTER>-<num> only
         │  JOIN_IMPOSSIBLE ↔ PDS SUBJID / mask_id
         ▼
PDS CRC IPD (Amgen / Alliance / …)  ──►  treat×marker / outcomes  (SEPARATE lane)
         │
         ▼
bayesian_prior_enrichment_feed_v3_psm  ──►  published CO.26 HRs + IPTW ESS
         │
         ▼
PATH A fit_A ranking only  ──►  NEVER claim 8D-04 unblocked by GENIE alone
```

**Correct hard work (ranked):**

1. Assay-stratified TMB QC / caps (SEQ_ASSAY_ID) — kill insane outliers  
2. DuckDB gene flags from mutations (KRAS/NRAS/BRAF/TP53/APC/PIK3CA/MMR) → join to matrix  
3. **IPD** treat×marker on Amgen caslibs — **not** GENIE×PDS join  

---

## C) Ranked next steps (post-download — auth/download DONE)

### P0 — Do not regress

1. Keep `genie_synapse: APPROVED_R20_PUBLIC` aligned with on-disk 7/7 files  
2. Keep poison quarantine untouched  
3. Never invent MSI / Guardant pTMB / liver-met from this matrix  
4. Never claim 8D-04 unblock  

### P1 — Assay-stratified TMB

- Stratify `tmb_mut_per_mb` by `SEQ_ASSAY_ID` / `number_of_genes`  
- Fail loud on panels driving max~411k mut/Mb  
- Document caps before any enrichment model  

### P2 — DuckDB gene flags

- Stream `data_mutations_extended.txt` with duckdb  
- CRC-filter via matrix `SAMPLE_ID` set  
- Emit gene-level flags; join to parquet  

### P3 — IPD treat×marker (Amgen)

- Use PDS backbone IDs only  
- Do **not** attempt GENIE↔PDS patient join (already `JOIN_IMPOSSIBLE`)  

### P4 — Controlled / BPC (only if ACT truly grants)

```text
syn27054992  syn3380222  syn51364439
```

If still 403 → stay on public R20.

---

## D) Secrets / git hygiene

- `SYNAPSE_AUTH_TOKEN` / `~/.synapseConfig` — gitignored only; never commit  
- Never commit `raw/r20_public/`, poison CSVs, or `.env`  
- Scrubbed receipts only under `datasets/genie_r20/`

---

## Success criteria (current)

- [x] Non-anonymous Synapse login + R20 download (7/7)  
- [x] `access_status.json` → `APPROVED_R20_PUBLIC`  
- [x] CRC matrix n=24109 cites `20.0-public` / `syn76285058`  
- [x] MSI documented **null**; `IS_GUARDANT_PTMB=false`  
- [x] Feed v2 = contract JSON pointing at parquet  
- [x] `JOIN_IMPOSSIBLE` receipt; held-out AUROC ~0.506 logged  
- [x] Quarantine poison CSV remains quarantined  
- [ ] Explicit note in any Brenus product output: **not an 8D-04 unblock** (ongoing)

---

## SUPERSEDED — historical probe notes (do not follow as current truth)

<details>
<summary>Click to expand superseded 2026-08-04 morning probe (PAT invalid / raw empty)</summary>

The following claims are **STALE** and contradicted by the afternoon download + matrix build:

- ~~`raw/r20_public/` Empty (0/8 downloads)~~ → **FALSE**; 7/7 present (~1.5GB)  
- ~~`access_status.json` → `PAT_INVALID_DOWNLOAD_BLOCKED`~~ → **FALSE**; now `APPROVED_R20_PUBLIC`  
- ~~Bearer PAT 401 / do not flip APPROVED~~ → **SUPERSEDED**; download succeeded as non-anonymous user  
- ~~“What GENIE unlocks after valid PAT (not yet downloaded)”~~ → **SUPERSEDED**; files are local  

Kept only as audit trail of the failed morning probe. **Obey section A / START HERE.**

</details>

---

*Worker plan truth-synced by Zo for Alpha after deep GENIE audit. Mars rules: minimal viable proof — matrix + raw already on disk; next proof is assay-stratified TMB + duckdb gene flags. Never 8D-04 from GENIE alone.*
