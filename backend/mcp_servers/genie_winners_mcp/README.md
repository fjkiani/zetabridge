# genie-winners-mcp

FastMCP stdio server: **GENIE data plane** + **probe plane** + **Winners decision plane**.

Build orders (doctrine): Brenus-repo  
`engagements/brenus/genie_synapse/GENIE_WINNERS_MCP_BUILD.md`

**Branch:** `agent/genie-winners-mcp-break-track`  
**Void rule:** no `receipt_sha` + `METRICS_LEDGER` row = claim invalid.

## Status

v0.1.1 scaffold — tools are registered stubs that **fail loud** (`ok=false`, `NOT_IMPLEMENTED:`) until implemented. `genie.refuse_poison` is live (refuse only). Implement per build orders; then call your own tools to produce W0–W4 artifacts.

## Install

```bash
cd backend/mcp_servers/genie_winners_mcp
python3 -m pip install -e ".[dev]"
```

Requires `mcp` (same stack as `backend/mcp_server/zeta_graph_mcp.py`).

## Run

```bash
cd backend/mcp_servers/genie_winners_mcp
python3 -m genie_winners_mcp
```

## Cursor `mcp.json` (example — no secrets)

See [`mcp.json.example`](./mcp.json.example). Copy into local Cursor config only.

```json
{
  "mcpServers": {
    "genie-winners": {
      "command": "python3",
      "args": ["-m", "genie_winners_mcp"],
      "cwd": "/absolute/path/to/zetabridge/backend/mcp_servers/genie_winners_mcp",
      "env": {
        "GENIE_R20_ROOT": "/absolute/path/to/zetabridge/datasets/genie_r20",
        "BRENUS_GENIE_SYNAPSE": "/absolute/path/to/Brenus-repo/engagements/brenus/genie_synapse"
      }
    }
  }
}
```

Do **not** put Synapse tokens / SAS passwords in this package. Use existing PDS-MCP / zeta-graph live tools for CAS/Synapse.

## Smoke

```bash
cd backend/mcp_servers/genie_winners_mcp
python3 -m genie_winners_mcp.tests.smoke_list_tools
```

Expect ≥18 tool IDs including new: `genie.diff_doc_claims`, `genie.stream_cna`, `genie.tmb_outlier_report`, `moa.probe_schema`, `genie.probe_consumer_wiring`.

## Tool IDs

| ID | Plane |
|----|-------|
| `genie.list_assets` | GENIE |
| `genie.diff_doc_claims` | GENIE (contradiction detect only) |
| `genie.matrix_summary` | GENIE |
| `genie.clinical_header_probe` | GENIE |
| `genie.stream_mutation_flags` | GENIE |
| `genie.stream_cna` | GENIE |
| `genie.assay_tmb_strata` | GENIE |
| `genie.tmb_outlier_report` | GENIE |
| `genie.refuse_poison` | GENIE (implemented) |
| `genie.probe_consumer_wiring` | GENIE / probe |
| `moa.probe_schema` | MoA probe |
| `winners.define` | Winners |
| `winners.hypotheses_draft` | Winners |
| `winners.kill_tests` | Winners |
| `winners.scoreboard` | Winners |
| `winners.pick` | Winners |
| `ids.intersect` | Bridge (join-test) |
| `pds.outcomes_manifest_read` | Bridge (local files; no re-auth) |

## Bans

- No 8D-04 soft-unblock
- No secrets in repo
- No loading QUARANTINE/poison paths
- No inventing MSI / plasma pTMB without source columns
- No multi-GB GENIE raw commits
- No “done” without ledger row

**RUO:** Research Use Only. Not clinical care.

## Metrics / break-track

See [`METRICS.md`](./METRICS.md) and Brenus `agent_break_track/` on branch `agent/genie-winners-mcp-break-track`.
