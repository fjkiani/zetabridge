# METRICS — genie-winners-mcp

**Branch:** `agent/genie-winners-mcp-break-track`

## Canonical ledger (Brenus)

Alpha tracks every tool call / deliverable here:

`Brenus-repo/engagements/brenus/genie_synapse/agent_break_track/METRICS_LEDGER.md`

Also: `BREAK_LOG.md`, `RUN_PROTOCOL.md`, `SCOREBOARD_PATHS.md`, `AGENT_PASTE.md` in that folder.

## Local receipts (this package)

Write tool response envelopes (JSON) under:

`backend/mcp_servers/genie_winners_mcp/receipts/`

- Keep small JSON envelopes here.
- Do **not** commit multi-GB blobs; `.gitignore` ignores large/local dumps under `receipts/` except `.gitkeep` and `*.envelope.json` if you choose to track small ones.
- Prefer also setting `artifacts[]` / `receipt_sha` in the uniform envelope (`envelope.py`).

Helper: `write_receipt(tool_id, envelope_dict)` in `genie_winners_mcp/envelope.py` (optional; agent may also write files manually and still must log METRICS_LEDGER).
