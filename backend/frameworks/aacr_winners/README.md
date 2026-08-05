# AACR GENIE EnrichmentHypothesisFramework (RUO)

**Package:** `backend/frameworks/aacr_winners/`  
**Branch:** `agent/aacr-genie-winners-framework`  
**Sibling lanes (do not steal):**
- `agent/genie-winners-mcp-break-track` — owns `genie_winners_mcp` server.py
- `agent/winners-ipd-parallel` — owns IPD scoreboard under `parallel_ipd_winners/`

## What this is

A **framework layer** (not MCP stubs, not one-liner scripts) that turns AACR Project GENIE R20 local assets into a **winners engine**:

| Concept | Framework object / method | MCP tool ID (align only) |
|---------|---------------------------|---------------------------|
| Pre-reg | `PreReg` / W0 YAML | `winners.define` |
| Hypotheses | `Hypothesis` ≤5 | `winners.hypotheses_draft` |
| Kill battery | `FrameworkKillBattery` | `winners.kill_tests` |
| Scoreboard | `ScorecardRow` | `winners.scoreboard` |
| Pick | `Pick` | `winners.pick` |
| Matrix | `GenieMatrixAdapter.load_cohort` | `genie.matrix_summary` |
| Assay TMB | `AssayStratifier` | `genie.assay_tmb_strata` |
| Mutations | `MutationFlagStream` (DuckDB) | `genie.stream_mutation_flags` |
| Poison | `refuse_poison` | `genie.refuse_poison` |

## Honesty gates (enforced)

- **tissue_panel_TMB ≠ plasma pTMB** (`IS_GUARDANT_PTMB` must be false)
- **MSI** only if columns exist **and** non-null &gt; 0 (probe — never invent)
- **AssayStratifier** mandatory before any TMB claim (`SEQ_ASSAY_ID`)
- **No full pandas load** of ~990MB `data_mutations_extended.txt`
- **Does NOT soft-unblock 8D-04**
- **Does NOT require GENIE∩PDS join** for prior ADVANCE

## Exploitative plays (plugins)

| Play | Class | Output |
|------|-------|--------|
| **A** | `PlayAAssayTMB` | Assay-calibrated TMB prior + DEAD prognostic **NEGATIVE control** |
| **B** | `PlayBDriverComutation` | Driver pack × TMB prevalence + OR within GENIE; OS claim → needs IPD |
| **C** | `PlayCPriorExport` | `IPD_PRIOR_HANDOFF.json` (`ipd_prior_handoff/0.1.0`) |

## Run

```bash
cd zetabridge
PYTHONPATH=backend python -m frameworks.aacr_winners.runner \
  --artifact-dir /path/to/Brenus-repo/engagements/brenus/genie_synapse/aacr_framework_winners
```

Artifacts land in Brenus `aacr_framework_winners/` (scoreboard + handoff). See that folder’s README for how priors feed IPD **without** patient-level GENIE∩PDS join.

## Layout

```
backend/frameworks/aacr_winners/
  specs.py interfaces.py assay.py biomarker_endpoint.py
  kill_battery.py score_pick.py runner.py
  adapter/   # matrix + duckdb stream + poison
  plays/     # A/B/C plugins
  schemas/ipd_prior_handoff.schema.json
```
