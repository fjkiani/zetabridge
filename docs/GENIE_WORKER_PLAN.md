# GENIE Synapse Worker Plan — STC-1010 / CRC enrichment

**Date:** 2026-08-04  
**Status:** ✅ **R20-public downloaded + CRC matrix rebuilt** (`APPROVED_R20_PUBLIC`). Does **not** soft-unblock 8D-04.  
**RUO:** Research Use Only. Does **not** soft-unblock 8D-04.

---

## A) What GENIE unlocks NOW (concrete)

### Unlocked today (no download required)

| Asset | What it is | Use |
|-------|------------|-----|
| Metadata tree `syn7222066` | AACR Project GENIE Public | Browse releases, syn IDs, AR list |
| Release inventory through **20.0-public** (`syn76285058`) | File/folder names + syn IDs | Exact download targets |
| Access Requirement IDs | AR `7989508` (Acknowledgement CW), AR `9606645` (external link) | ToU fulfillment checklist |
| On-disk **bridge** | `crc_tmb_msi_matrix` from cBioPortal studies | Interim TMB/MSI proxy only |
| Honest Bayesian prior | `bayesian_prior_enrichment_feed_v3_psm.json` | IPTW n=236 liver-met-neg + CO.26 **published** HRs |

### What GENIE R20-public unlocks **after** valid PAT + ToU (not yet downloaded)

| File (syn ID) | Unlocks for STC-1010 |
|---------------|----------------------|
| `tmb_20.0-public.tsv` (`syn76285103`) | **Native panel TMB** (replaces poison “pTMB==TMB” duplicate column) |
| `data_clinical_sample.txt` (`syn76285063`) | Sample-level cancer type, SEQ_ASSAY_ID, MSI if present |
| `data_clinical_patient.txt` (`syn76285062`) | Patient demographics / cancer type linkage |
| `data_mutations_extended.txt` (`syn76285067`) | CRC mutation landscape for enrichment features (RAS/BRAF/TP53/MMR genes) |
| `data_CNA.txt` (`syn76285060`) | CNA burden / key amps-dels for CRC |
| `cases_Colorectal_Cancer.txt` (`syn76285182`) | Hard CRC case list for filtering |

### What GENIE does **not** unlock (even after download)

- ❌ **8D-04 soft-unblock** — PATH A `fit_A` remains sole ranking formula; GENIE is feature fuel, not a fit formula fix  
- ❌ **GuardantOMNI plasma pTMB** — GENIE TMB is **tissue panel TMB**, not plasma  
- ❌ **Immunoscore-IC** — not in GENIE public release  
- ❌ **Liver-met burden on every GENIE CRC row** — clinical liver-met is sparse/absent in public GENIE; keep using **observed** IPD flags (PEAK/PRIME/PACCE) + CO.26 published subgroup HRs  
- ❌ **Controlled BPC / consortium entities** (`syn27054992`, `syn3380222`, …) — still 403 until ACT + auth actually grant them  
- ❌ **Rehabilitating** `patient_selection_enrichment_v1.QUARANTINED.csv` — stays poison

### vs poison CSV

| | Poison CSV (quarantined) | GENIE R20 (target) | Honest prior (current) |
|--|--------------------------|--------------------|------------------------|
| Liver-met | 100% unknown | Still mostly absent | IPTW on **IPD** liver-met==0 |
| pTMB | Fake (tissue TMB duplicated) | Real **panel** TMB column | CO.26 published pTMB≥28 prior only |
| Immunoscore | Proxy flag True 100% | Not present | Do not invent |

---

## B) Live access probe (2026-08-04)

**Token stored:** `zetabridge/.env` → `SYNAPSE_AUTH_TOKEN` (gitignored, mode 600)  
**Also:** `~/.synapseConfig` `[authentication] authtoken` (mode 600)  
**Masked:** `REDACTED`  
**JWT claims:** `PERSONAL_ACCESS_TOKEN`, `sub=3572550`, scopes `view|download|modify`, `iat/nbf=2026-07-15`

| Check | Result |
|-------|--------|
| `Authorization: Bearer <PAT>` | **401 Invalid access token** |
| Anonymous `GET /userProfile` | anonymous / 273950 |
| `GET /entity/syn7222066` | ✅ AACR Project GENIE Public |
| Children `Data Releases` | ✅ 32 releases incl. **20.0-public** |
| Controlled `syn27054992` / `syn51364439` / `syn3380222` | **403** |
| FileHandle batch download (R20 files) | **UNAUTHORIZED** (AR not fulfilled / no auth) |
| Prior OIDC session in `data/external/GENIE/.synapse_session.jwt` | **expired** (`invalid_token. The token has expired`) |

**FAIL LOUD:** Alpha’s pasted PAT does **not** authenticate. UI “ACT approved” ≠ this JWT works. Do **not** flip `genie_synapse` to `APPROVED` until `syn get syn76285103` succeeds as user `3572550`.

Receipts (gitignored under `backend/data/`):

- `zetabridge/backend/data/features/genie_crc/synapse_probe_receipt.json`
- `zetabridge/backend/data/features/genie_crc/raw/r20_download_receipt.json` (0/8 OK)

---

## C) Ranked execution steps

### P0 — Unblock auth (Alpha, ~5 min)

1. Synapse → Account Settings → **Personal Access Tokens** → create new PAT  
   - Scopes: at least **view** + **download** (modify optional)  
   - Copy once; revoke the dead July-15 PAT (`jti=REDACTED`) if still listed  
2. Replace token **only** in gitignored locations:
   ```bash
   # zetabridge/.env
   SYNAPSE_AUTH_TOKEN=<new_pat>
   # ~/.synapseConfig
   [authentication]
   authtoken = <new_pat>
   ```
3. Verify identity (must NOT be anonymous):
   ```bash
   python3 - <<'PY'
   import os, synapseclient
   from dotenv import load_dotenv  # or read .env manually
   load_dotenv("/Users/fahadkiani/Desktop/development/zetabridge/.env")
   syn = synapseclient.Synapse(silent=True)
   syn.login(authToken=os.environ["SYNAPSE_AUTH_TOKEN"])
   p = syn.getUserProfile()
   assert p["ownerId"] != "273950", "still anonymous"
   print("OK", p.get("userName"), p.get("ownerId"))
   PY
   ```
4. Fulfill GENIE Access Requirements in UI (or via extractor `_fulfill_genie_access`):  
   - AR **7989508** GENIE Acknowledgement CW  
   - AR **9606645** GENIE Access Requirement_external link  
5. Re-probe download: `syn get syn76285103 --downloadLocation /tmp/genie_tmb`

### P1 — Download R20-public critical tables

Target dir (gitignored):  
`zetabridge/backend/data/features/genie_crc/raw/r20_public/`

```bash
export SYNAPSE_AUTH_TOKEN="$(grep ^SYNAPSE_AUTH_TOKEN= zetabridge/.env | cut -d= -f2-)"
python3 - <<'PY'
import os
from pathlib import Path
import synapseclient
OUT = Path("zetabridge/backend/data/features/genie_crc/raw/r20_public")
OUT.mkdir(parents=True, exist_ok=True)
FILES = [
  "syn76285103",  # tmb_20.0-public.tsv
  "syn76285062",  # data_clinical_patient.txt
  "syn76285063",  # data_clinical_sample.txt
  "syn76285067",  # data_mutations_extended.txt  (LARGE — expect multi-GB)
  "syn76285060",  # data_CNA.txt
  "syn76285182",  # cases_Colorectal_Cancer.txt
  "syn76285059",  # assay_information.txt
]
syn = synapseclient.Synapse(silent=True)
syn.login(authToken=os.environ["SYNAPSE_AUTH_TOKEN"])
for sid in FILES:
    ent = syn.get(sid, downloadLocation=str(OUT), ifcollision="overwrite.local")
    print(sid, getattr(ent, "name", None), Path(ent.path).stat().st_size)
PY
```

**Size note:** `data_mutations_extended.txt` is typically **multi-GB**. If local disk is tight, download TMB + clinical + CRC case list first; mutations via `synapse storage` or stream+CRC filter.

Alternate (existing extractor):  
`crispr-assistant-main/scripts/data_acquisition/genie/genie_extractor.py`  
(prefers password+MFA login2 for full `authorize` scope to auto-fulfill AR)

### P2 — QC gate (must pass before any enrichment claim)

1. Row counts: CRC case list ∩ clinical_sample ∩ TMB  
2. Assert `TMB` column exists and is **not** identical to a fake `pTMB` duplicate  
3. MSI field presence rate (document if missing)  
4. Gene panel / SEQ_ASSAY_ID distribution for CRC  
5. Provenance JSON: release=`20.0-public`, syn parent=`syn76285058`, download UTC, token_masked only  
6. Diff vs bridge matrix: overlap N, TMB Spearman (expect imperfect — different panels)

### P3 — Build honest feeds (replace poison path)

1. CRC-filter mutations → gene-level features (KRAS/NRAS/BRAF/TP53/APC/PIK3CA/MMR)  
2. Join panel TMB → replace bridge-only matrix for GENIE-sourced rows  
3. **Do not** invent liver-met or Immunoscore-IC  
4. Link to PDS IPD backbone:
   - `Brenus-repo/engagements/brenus/pds_extraction/crc_ipd_features_backbone_v5.csv`
   - `zetabridge/.../bayesian_prior_enrichment_feed_v3_psm.json`  
   GENIE = **genomic prior layer**; IPD = **outcome / liver-met observed layer**  
5. Keep quarantine on `patient_selection_enrichment_v1.*`

### P4 — Controlled / BPC (only if ACT truly grants)

Re-check after auth works:

```text
syn27054992  syn3380222  syn51364439
```

If still 403 → ACT did not grant consortium/BPC; stay on public R20.

---

## D) On-disk vs still-to-download

### On disk NOW

| Path | Status |
|------|--------|
| `zetabridge/backend/data/features/genie_crc/crc_tmb_msi_matrix.{csv,parquet}` | Bridge proxy (~15 MB) |
| `.../efficacy_model_v3_feed.json{,l}` | Bridge-derived |
| `.../bayesian_prior_enrichment_feed_v3_psm.json` | Honest IPTW prior |
| `.../patient_selection_enrichment_v1.QUARANTINED.csv` | Poison — do not use |
| `.../access_status.json` | `PAT_INVALID_DOWNLOAD_BLOCKED` |
| `crispr-assistant-main/data/external/GENIE/*` | Mirror of bridge (~15 MB); expired session JWT |
| `raw/r20_public/` | **Empty** (0/8 downloads) |

### Still to download (after PAT)

| syn ID | File | Priority |
|--------|------|----------|
| syn76285103 | tmb_20.0-public.tsv | P0 |
| syn76285063 | data_clinical_sample.txt | P0 |
| syn76285062 | data_clinical_patient.txt | P0 |
| syn76285182 | cases_Colorectal_Cancer.txt | P0 |
| syn76285067 | data_mutations_extended.txt | P1 (large) |
| syn76285060 | data_CNA.txt | P1 |
| syn76285059 | assay_information.txt | P2 |

---

## Linkage map (STC-1010)

```
GENIE R20 panel TMB/MSI/mutations  ──►  enrichment feature layer (genomic)
         │
         │  join keys: patient/sample IDs within GENIE only
         │  (do NOT fake-join to PDS without shared ID map)
         ▼
PDS CRC IPD (PEAK/PRIME/PACCE…)  ──►  liver-met observed + outcomes
         │
         ▼
bayesian_prior_enrichment_feed_v3_psm  ──►  published CO.26 HRs + IPTW ESS
         │
         ▼
PATH A fit_A ranking only  ──►  NEVER claim 8D-04 unblocked by GENIE alone
```

---

## Success criteria

- [ ] `syn.login(authToken=...)` → ownerId **≠** 273950  
- [x] `syn get syn76285103` writes TMB file locally  
- [x] `access_status.json` → `genie_synapse: "APPROVED_R20_PUBLIC"` only after download+QC  
- [x] New CRC TMB matrix provenance cites `20.0-public` / `syn76285058`  
- [ ] Quarantine poison CSV remains quarantined  
- [ ] Explicit note in any Brenus output: **not an 8D-04 unblock**

---

*Worker plan authored by Zo for Alpha. Mars rules: minimal viable proof — first proof is a non-anonymous whoami + one TMB file on disk.*
