# GENIE Synapse Worker Plan — STC-1010 / CRC enrichment

**Date:** 2026-08-04 (hardened break-track pass)  
**Status:** Living checklist — **VERIFY ON DISK via MCP**  
**Branch:** `agent/genie-winners-mcp-break-track`  
**RUO:** Research Use Only. Does **not** soft-unblock 8D-04.

---

## ⚠️ BANNER — VERIFY ON DISK

**Sections below may be outdated, contradictory, or wrong.**  
Prior agents rewrote this file after Synapse attempts; header/body have disagreed before.

| Rule | Action |
|------|--------|
| Ground truth | Whatever **MCP tools** measure (`genie.list_assets`, …) |
| Docs | Hints only until tool receipt confirms or refutes |
| Primary build orders | `engagements/brenus/genie_synapse/GENIE_WINNERS_MCP_BUILD.md` |
| HARD brief | **Superseded** — history only, not an answer key |
| Break-track | `engagements/brenus/genie_synapse/agent_break_track/` (ledger + protocol) |
| Re-download | Only after tools prove local files missing or corrupt |
| 8D-04 | Never soft-unblocked by GENIE work alone |
| Stale claims | “raw empty”, “PAT invalid”, fixed AUROCs, `JOIN_*` as settled fact → **quarantine**; re-measure |

Do **not** copy numeric claims from this plan into scientific receipts without MCP re-measurement.

---

## Agent break-track (mandatory for implementers)

| Item | Path / note |
|------|-------------|
| Branch (Brenus + zetabridge) | `agent/genie-winners-mcp-break-track` |
| Metrics ledger | `genie_synapse/agent_break_track/METRICS_LEDGER.md` — **one row per tool call** |
| Run protocol | `agent_break_track/RUN_PROTOCOL.md` |
| MCP scaffold | `zetabridge/backend/mcp_servers/genie_winners_mcp/` |
| Done without ledger | **Invalid / void** |

Loop: implement one tool → smoke → append ledger → push break-track → next. Stop when blocked (`BREAK_LOG.md`).

---

## Hunt map (inspect — do not trust sizes here)

| Role | Path to inspect |
|------|-----------------|
| Datasets / scrubbed receipts | `zetabridge/datasets/genie_r20/` |
| Feature workspace + raw candidates | `zetabridge/backend/data/features/genie_crc/` |
| Synapse probe / download receipts | same feature dir + `datasets/genie_r20/*receipt*` |
| Brenus GENIE notes + MCP build | `Brenus-repo/engagements/brenus/genie_synapse/` |
| MoA JSON candidates | `Brenus-repo/engagements/brenus/resources/` → `moa.probe_schema` |
| PDS outcomes | `Brenus-repo/engagements/brenus/pds_extraction/` → `pds.outcomes_manifest_read` |
| This plan (zetabridge copy) | `zetabridge/docs/GENIE_WORKER_PLAN.md` |
| This plan (Brenus copy) | `Brenus-repo/engagements/brenus/genie_synapse/GENIE_WORKER_PLAN.md` |

Keep both plan copies **in sync** when editing.

---

## Conceptual goals (not results)

1. Use public GENIE-like CRC genomic tables as a **research enrichment feature layer** when honestly available.  
2. Keep poison / quarantine artifacts out of ranking (`genie.refuse_poison`).  
3. Keep Guardant plasma pTMB, MSI, Immunoscore, liver-met **null unless tools find columns**.  
4. Treat PDS trial IPD as a **separate** ID namespace until `ids.intersect` proves a join.  
5. PATH A `fit_A` remains the ranking formula until held-out predictive lift is proven under project governance — GENIE alone does not change that.

---

## Methodology hints (measure-first via winners MCP)

- Prefer parquet/csv matrices over full raw dumps when both exist — **confirm with `genie.list_assets`**.  
- Stream large mutation/CNA text (`genie.stream_mutation_flags`, `genie.stream_cna`).  
- Assay TMB + outliers: `genie.assay_tmb_strata`, `genie.tmb_outlier_report`.  
- Doc↔disk: `genie.diff_doc_claims` (contradiction detection only).  
- Prefer duckdb / pandas / pyarrow if installed; confirm locally.  
- Never commit `.env`, Synapse tokens, or gitignored raw.

---

## Checklist — agent fills with MCP receipts + ledger rows

- [ ] Inventory via `genie.list_assets` (sizes you measured)  
- [ ] Doc claims vs assets via `genie.diff_doc_claims`  
- [ ] Matrix via `genie.matrix_summary` (+ assay/outlier tools if applicable)  
- [ ] Feed / consumer wiring via `genie.probe_consumer_wiring`  
- [ ] MoA REBUILT via `moa.probe_schema`  
- [ ] GENIE ID ∩ PDS via `ids.intersect` + `pds.outcomes_manifest_read`  
- [ ] Clinical headers via `genie.clinical_header_probe`  
- [ ] Quarantine refuse via `genie.refuse_poison`  
- [ ] W0–W4 via `winners.*` tools + **METRICS_LEDGER**  
- [ ] Explicit: **not an 8D-04 unblock**

---

## Poison / quarantine (do not load)

Search for and refuse (via tool):

- `patient_selection_enrichment_v1*QUARANTINE*`  
- `crc_tmb_msi_matrix*cbioportal_bridge*QUARANTINE*`  
- Anything under `quarantine_notes/` that says poison  

Confirm current paths with `genie.list_assets` / `find` — names may move.

---

## Synapse entities (targets — verify availability yourself)

Release parent often cited: `syn76285058` (20.0-public).  
Common critical file syn IDs (confirm names via Synapse / local receipts / `genie.list_assets`):

| syn ID | Expected name (verify) |
|--------|-------------------------|
| syn76285103 | tmb_20.0-public.tsv |
| syn76285062 | data_clinical_patient.txt |
| syn76285063 | data_clinical_sample.txt |
| syn76285182 | cases_Colorectal_Cancer.txt |
| syn76285059 | assay_information.txt |
| syn76285060 | data_CNA.txt |
| syn76285067 | data_mutations_extended.txt |

Controlled / BPC entities may remain 403 without ACT grant — probe before claiming access. Do **not** treat prior “PAT invalid / download 0/8 / raw empty” prose as authoritative.

---

## Linkage sketch (hypothesis — prove or refute with `ids.intersect`)

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

## SUPERSEDED / STALE RISK (quarantine list)

Any paragraph (here or elsewhere) that asserts as **settled fact**:

- “raw empty” / “PAT invalid” / “download 0/8”  
- fixed row counts / fixed AUROCs  
- `JOIN_IMPOSSIBLE` / “MSI is null” as gospel  

…is **suspect**. Re-measure with `genie.list_assets` + matrix/intersect tools. Prefer MCP receipts over copying claims forward.

`GENIE_HARD_AGENT_BRIEF.md` is superseded. `EXPLOITATION_KILL_ORDER.md` may contain prior measured claims — still re-verify with tools before reuse.

---

*Worker plan: measure-first checklist. Canonical MCP orders: GENIE_WINNERS_MCP_BUILD.md. Break-track: agent_break_track/.*
