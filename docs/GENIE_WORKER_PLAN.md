# GENIE Synapse Worker Plan — STC-1010 / CRC enrichment

**Date:** 2026-08-04  
**Status:** Living checklist — **VERIFY ON DISK**  
**RUO:** Research Use Only. Does **not** soft-unblock 8D-04.

---

## ⚠️ BANNER — VERIFY ON DISK

**Sections below may be outdated, contradictory, or wrong.**  
Prior agents rewrote this file after a Synapse download attempt; header/body have disagreed before.

| Rule | Action |
|------|--------|
| Ground truth | Whatever you measure under the paths in **Hunt map** |
| Docs | Hints only until your receipt confirms or refutes |
| Virgin agent | Start at `engagements/brenus/genie_synapse/GENIE_HARD_AGENT_BRIEF.md` (discovery mandate) |
| Winners strategy | Same brief → section **WINNERS STRATEGY (design + prove)** (W0–W4); MoA candidacy ≠ predictive winners; no 8D-04 soft-unblock |
| Re-download | Only after you prove local files missing or corrupt |
| 8D-04 | Never soft-unblocked by GENIE work alone |

Do **not** copy numeric claims from this plan into scientific receipts without re-measurement.

---

## Hunt map (inspect — do not trust sizes here)

| Role | Path to inspect |
|------|-----------------|
| Datasets / scrubbed receipts | `zetabridge/datasets/genie_r20/` |
| Feature workspace + raw candidates | `zetabridge/backend/data/features/genie_crc/` |
| Synapse probe / download receipts | same feature dir + `datasets/genie_r20/*receipt*` |
| Brenus GENIE notes | `Brenus-repo/engagements/brenus/genie_synapse/` |
| MoA JSON candidates | `Brenus-repo/engagements/brenus/resources/` |
| PDS outcomes | `Brenus-repo/engagements/brenus/pds_extraction/` |
| This plan (zetabridge copy) | `zetabridge/docs/GENIE_WORKER_PLAN.md` |
| This plan (Brenus copy) | `Brenus-repo/engagements/brenus/genie_synapse/GENIE_WORKER_PLAN.md` |

Keep both plan copies **in sync** when editing.

---

## Conceptual goals (not results)

1. Use public GENIE-like CRC genomic tables as a **research enrichment feature layer** when honestly available.  
2. Keep poison / quarantine artifacts out of ranking.  
3. Keep Guardant plasma pTMB, MSI, Immunoscore, liver-met **null** unless the tables actually contain them.  
4. Treat PDS trial IPD as a **separate** ID namespace until a join is proven.  
5. PATH A `fit_A` remains the ranking formula until held-out predictive lift is proven under project governance — GENIE alone does not change that.

---

## Methodology hints (measure-first)

- Prefer parquet/csv matrices over full raw dumps when both exist.  
- Stream large mutation/CNA text; do not full-load blindly.  
- Prefer duckdb / pandas / pyarrow if installed; confirm locally (`polars` may be absent).  
- Pilot a SAMPLE_ID subset before full joins.  
- Never commit `.env`, Synapse tokens, or gitignored raw.

---

## Checklist — agent fills with receipts

- [ ] Inventory: what files exist under datasets + raw (sizes you measured)  
- [ ] Access / approval claims in JSON vs what download/auth actually does today  
- [ ] Matrix (if any): n rows, MSI availability, TMB assay labeling — measured  
- [ ] Feed JSON(s): contract vs feature table — measured  
- [ ] MoA REBUILT (if present): structure + count — measured  
- [ ] GENIE ID ∩ PDS outcome ID — intersection you computed  
- [ ] Clinical headers: any OS/PFS/treatment fields?  
- [ ] Quarantine files listed and not loaded  
- [ ] Explicit: **not an 8D-04 unblock**

---

## Poison / quarantine (do not load)

Search for and refuse:

- `patient_selection_enrichment_v1*QUARANTINE*`  
- `crc_tmb_msi_matrix*cbioportal_bridge*QUARANTINE*`  
- Anything under `quarantine_notes/` that says poison  

Confirm current paths with `find` — names may move.

---

## Synapse entities (targets — verify availability yourself)

Release parent often cited: `syn76285058` (20.0-public).  
Common critical file syn IDs (confirm names via Synapse / local receipts):

| syn ID | Expected name (verify) |
|--------|-------------------------|
| syn76285103 | tmb_20.0-public.tsv |
| syn76285062 | data_clinical_patient.txt |
| syn76285063 | data_clinical_sample.txt |
| syn76285182 | cases_Colorectal_Cancer.txt |
| syn76285059 | assay_information.txt |
| syn76285060 | data_CNA.txt |
| syn76285067 | data_mutations_extended.txt |

Controlled / BPC entities may remain 403 without ACT grant — probe before claiming access.

---

## Linkage sketch (hypothesis — prove or refute)

```
GENIE genomic tables  ??join??  PDS trial IPD outcomes
         │                         │
         ▼                         ▼
   enrichment features      treat × marker / OS-PFS
         │                         │
         └──────── honesty gates ──┘
                      │
                      ▼
              PATH A governance (no 8D-04 from GENIE alone)
```

If join fails, money-path work may still live entirely on the PDS side — verify under `pds_extraction/`.

---

## SUPERSEDED / STALE RISK

Any paragraph that asserts “raw empty”, “PAT invalid”, “download 0/8”, fixed row counts, fixed AUROCs, or “JOIN_IMPOSSIBLE” as settled fact is **suspect** until re-verified. Prefer the discovery brief’s hint box over copying those claims forward.

---

*Worker plan: measure-first checklist. Discovery mandate lives in GENIE_HARD_AGENT_BRIEF.md.*
