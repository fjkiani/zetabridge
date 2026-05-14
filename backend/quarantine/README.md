# Quarantine

This folder holds modules that are **disabled from the active module graph** but preserved for potential future use.

None of these files are imported anywhere in the active codebase. They do not participate in startup, routing, or any runtime path.

---

## What is here and why

### `orchestrator.py`
**Original role:** Intent classifier (20+ keyword-scored intents), DAG task decomposer, async parallel executor, benchmark aggregator, lineage chain propagation.

**Why quarantined:** The DAG execution and benchmark telemetry are overkill for the v0.1 substrate. The intent classification logic is the only part worth revisiting.

**What may be reusable later:** The `classify_intent()` function and the intent enum are good primitives for a BrenusBridge copilot layer that needs to route NL questions to the right handler (trial query vs. blocker lookup vs. lineage trace vs. source refresh).

**Conditions for revival:** When BrenusBridge has a copilot endpoint that needs to route across multiple query types. Extract only `classify_intent()` and the intent enum; do not revive the DAG executor.

---

### `specialized.py`
**Original role:** 1,328-line file of specialized agent subclasses (DataCatalogAgent, QueryAgent, LineageAgent, ETLAgent, etc.) built on `BaseAgent`.

**Why quarantined:** None of these agents are wired to any active router. They implement `_execute()` methods that call LLMs but the calls are hollow stubs. The `BaseAgent` class itself (in `agents/base.py`) is retained in the active graph because its lifecycle, lineage emission, and `AgentResult` dataclass are useful primitives.

**What may be reusable later:** The `QueryAgent._execute()` pattern — wrapping `nl_to_sql` in an agent lifecycle — is the right shape for a future BrenusBridge agent that needs conversation memory and lineage tracking per query.

**Conditions for revival:** When BrenusBridge needs agent-level lineage (not just query-level lineage) and conversation memory. At that point, adapt `QueryAgent` to call `nl_to_sql.ask()` and emit to `lineage.local_store`.

---

### `copilot.py` + `copilot_init.py`
**Original role:** Conversation memory manager with session history, context window management, and Groq-backed chat completions.

**Why quarantined:** The copilot router was wired to `legacy_app._copilot` which required `USE_LEGACY_STORE=1` and a Postgres backend. Both are gone. The conversation memory pattern itself is sound.

**What may be reusable later:** The session history management and context window truncation logic are directly useful for a BrenusBridge copilot that maintains conversation state across governed trial queries.

**Conditions for revival:** When BrenusBridge has a `/api/copilot/chat` endpoint. Wire `copilot.py` to the new `nl_to_sql.ask()` path instead of the legacy Postgres path. Remove the `legacy_app` dependency entirely.

---

## What was deleted (not quarantined)

These were removed entirely because they have no reusable parts:

| Module | Reason for deletion |
|--------|-------------------|
| `backend/benchmarks/` | Performance harness with no domain value. Recoverable from git history. |
| `backend/multimodal/` | In-memory dict masquerading as a vector store. No real implementation. |
| `backend/legacy_app.py` | Postgres-backed legacy stack. Removed with `USE_LEGACY_STORE` dual-mode. |
| `backend/catalog/gravitino_client.py` | Gravitino REST client. Falls back silently. No real Gravitino. |
| `backend/connectors/snowflake_connector.py` | Unwired. No Snowflake credentials or schema. |
| `backend/connectors/databricks_connector.py` | Unwired. No Databricks credentials or schema. |
| `artifacts/.../pages/benchmarks.tsx` | Frontend for deleted benchmarks harness. |

Frontend pages `agents.tsx` and `copilot.tsx` are in `artifacts/zetabridge/src/pages/quarantine/` for the same reasons as their backend counterparts.

---

## Revival protocol

Before reviving any quarantined module:
1. Confirm the Brenus integration sprint requires it
2. Extract only the specific function/class needed — do not revive the whole file
3. Write a test for the extracted piece before wiring it
4. Remove it from this quarantine folder once it is active
