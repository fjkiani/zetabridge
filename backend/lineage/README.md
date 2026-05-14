# Lineage Module

## Active implementation

`local_store.py` — embedded SQLite lineage store with OpenLineage RunEvent shape.

**No external dependencies.** No Marquez server required. The event shape is identical to OpenLineage 1.x RunEvent, so if Marquez is ever needed, the adapter is trivial.

### Schema

```sql
lineage_events (
    run_id      TEXT PRIMARY KEY,
    event_type  TEXT,   -- START | COMPLETE | FAIL
    event_time  TEXT,   -- ISO8601 UTC
    job_name    TEXT,
    namespace   TEXT,
    inputs      TEXT,   -- JSON array of {namespace, name}
    outputs     TEXT,   -- JSON array of {namespace, name}
    sql_hash    TEXT,   -- SHA256 of SQL (for dedup)
    source      TEXT    -- connector name
)
```

### API

```python
from lineage.local_store import emit_query_lineage, get_graph, get_events

# Emit a query lineage event (called automatically by query router)
emit_query_lineage(
    job_name="query.nl_to_sql",
    sql="SELECT * FROM tcga_clinical LIMIT 50;",
    input_tables=["tcga_clinical"],
    output_table=None,
    source="duckdb",
)

# Get D3-compatible graph
graph = get_graph()  # {nodes: [...], edges: [...]}

# Get event log
events = get_events(job="query.nl_to_sql", limit=50)
```

---

## Brenus integration hooks

Two stub functions are defined in `local_store.py` but **not wired yet**:

### `emit_claim_lineage(claim_id, source_id, artifact_id)`

Emits a lineage edge: `source → claim → artifact`.

**When to wire:** During the Brenus integration sprint, after `engine_safe_input_block_v2.yaml` is loaded into DuckDB and the source registry is populated.

**Integration steps:**
1. Load source tier from Brenus source registry (`GET /api/connectors/{source_id}`)
2. Construct `LineageEvent` with `namespace="brenus.claims"`
3. Add `claim_id`, `source_id`, `source_tier` as facets
4. Call `emit_event(event)`

**Admissibility rule:** Only emit if `source.tier` is T1-SCI or T1-CORP. T2/T3 sources should be flagged in the event facets.

---

### `emit_artifact_lineage(artifact_id, consumer_id)`

Emits a lineage edge: `artifact → consumer`.

**When to wire:** During the Brenus integration sprint, after the consumer adapter routes (`/api/consumers/{id}/claims`) are implemented.

**Integration steps:**
1. Check `artifact.admissibility >= consumer.admissibility_gate`
2. If artifact is `QUARANTINED` or `BLOCKED`, do NOT emit to external consumers — log a warning instead
3. Construct `LineageEvent` with `namespace="brenus.consumers"`
4. Add `admissibility_level` as a facet
5. Call `emit_event(event)`

---

## Removed: `marquez_client.py`

The original `marquez_client.py` emitted OpenLineage events over HTTP to a Marquez server. It has been replaced by `local_store.py`.

The `emit_query_lineage()` function signature is identical, so all existing call sites work without changes.

If Marquez is ever needed (e.g., for cross-service lineage federation), create a thin adapter:

```python
# marquez_adapter.py (future, if needed)
from lineage.local_store import LineageEvent, emit_event
import requests

def emit_to_marquez(event: LineageEvent, marquez_url: str) -> bool:
    try:
        r = requests.post(f"{marquez_url}/api/v1/lineage", json=event.to_ol_dict())
        return r.ok
    except Exception:
        return False
```
