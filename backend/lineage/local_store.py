"""
local_store — Embedded SQLite lineage store.

Replaces the Marquez HTTP dependency while keeping the OpenLineage RunEvent
shape (job, inputs, outputs, run_id, eventTime, eventType). This preserves
forward compatibility with Marquez if it is ever needed.

Public API:
    init_lineage_store()                    — create schema if not exists
    emit_event(event: LineageEvent) -> str  — write event, return run_id
    get_graph(namespace: str) -> dict       — nodes + edges for D3 rendering
    get_events(job: str, limit: int) -> list — paginated event log

Brenus integration hooks (stubs — not wired yet):
    emit_claim_lineage(claim_id, source_id, artifact_id)
    emit_artifact_lineage(artifact_id, consumer_id)

See lineage/README.md for integration instructions.
"""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from config import cfg

log = logging.getLogger("zetabridge.lineage")

_OL_PRODUCER = "https://github.com/OpenLineage/OpenLineage/tree/main/spec/OpenLineage.json"


# ── Data contract (OpenLineage RunEvent shape) ────────────────────────────────

@dataclass
class DatasetRef:
    namespace: str
    name: str


@dataclass
class LineageEvent:
    """OpenLineage-compatible RunEvent."""
    job_name: str
    namespace: str
    event_type: str = "COMPLETE"          # START | COMPLETE | FAIL
    inputs: list[DatasetRef] = field(default_factory=list)
    outputs: list[DatasetRef] = field(default_factory=list)
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_time: str = field(
        default_factory=lambda: datetime.now(timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%S.%f"
        )[:-3] + "Z"
    )
    sql: Optional[str] = None             # stored as hash only
    source: Optional[str] = None          # connector name

    def to_ol_dict(self) -> dict:
        """Serialize to OpenLineage RunEvent JSON shape."""
        return {
            "eventType": self.event_type,
            "eventTime": self.event_time,
            "run": {"runId": self.run_id},
            "job": {"namespace": self.namespace, "name": self.job_name},
            "inputs": [{"namespace": d.namespace, "name": d.name} for d in self.inputs],
            "outputs": [{"namespace": d.namespace, "name": d.name} for d in self.outputs],
            "producer": _OL_PRODUCER,
        }


# ── Schema ────────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS lineage_events (
    run_id      TEXT PRIMARY KEY,
    event_type  TEXT NOT NULL,
    event_time  TEXT NOT NULL,
    job_name    TEXT NOT NULL,
    namespace   TEXT NOT NULL,
    inputs      TEXT NOT NULL,
    outputs     TEXT NOT NULL,
    sql_hash    TEXT,
    source      TEXT
);

CREATE INDEX IF NOT EXISTS idx_job_name   ON lineage_events(job_name);
CREATE INDEX IF NOT EXISTS idx_event_time ON lineage_events(event_time);
CREATE INDEX IF NOT EXISTS idx_namespace  ON lineage_events(namespace);
"""


def _get_conn() -> sqlite3.Connection:
    con = sqlite3.connect(cfg.LINEAGE_DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_lineage_store() -> None:
    """Create SQLite schema if it does not exist. Idempotent."""
    import os
    os.makedirs(os.path.dirname(cfg.LINEAGE_DB_PATH), exist_ok=True)
    with _get_conn() as con:
        con.executescript(_DDL)
    log.debug("Lineage store initialized at %s", cfg.LINEAGE_DB_PATH)


# ── Write ─────────────────────────────────────────────────────────────────────

def emit_event(event: LineageEvent) -> str:
    """
    Write a LineageEvent to the local store.

    Returns the run_id. Idempotent on run_id (INSERT OR IGNORE).
    """
    sql_hash = (
        hashlib.sha256(event.sql.encode()).hexdigest() if event.sql else None
    )
    inputs_json = json.dumps(
        [{"namespace": d.namespace, "name": d.name} for d in event.inputs]
    )
    outputs_json = json.dumps(
        [{"namespace": d.namespace, "name": d.name} for d in event.outputs]
    )
    with _get_conn() as con:
        con.execute(
            """
            INSERT OR IGNORE INTO lineage_events
                (run_id, event_type, event_time, job_name, namespace,
                 inputs, outputs, sql_hash, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.run_id,
                event.event_type,
                event.event_time,
                event.job_name,
                event.namespace,
                inputs_json,
                outputs_json,
                sql_hash,
                event.source,
            ),
        )
    log.debug("Lineage event emitted: %s / %s", event.job_name, event.run_id)
    return event.run_id


def emit_query_lineage(
    job_name: str,
    sql: str,
    input_tables: list[str],
    output_table: Optional[str],
    source: str,
) -> str:
    """
    Convenience wrapper: emit a COMPLETE query lineage event.
    Drop-in replacement for marquez_client.emit_query_lineage().
    """
    namespace = f"zetabridge.{source}"
    inputs = [DatasetRef(namespace=namespace, name=t) for t in input_tables if t]
    if not inputs:
        inputs = [DatasetRef(namespace=namespace, name="adhoc_query_source")]
    outputs = (
        [DatasetRef(namespace=namespace, name=output_table)] if output_table else []
    )
    event = LineageEvent(
        job_name=job_name,
        namespace=namespace,
        event_type="COMPLETE",
        inputs=inputs,
        outputs=outputs,
        sql=sql,
        source=source,
    )
    return emit_event(event)


# ── Read ──────────────────────────────────────────────────────────────────────

def get_events(job: Optional[str] = None, limit: int = 100) -> list[dict]:
    """Return paginated lineage events, newest first."""
    with _get_conn() as con:
        if job:
            rows = con.execute(
                "SELECT * FROM lineage_events WHERE job_name = ? "
                "ORDER BY event_time DESC LIMIT ?",
                (job, limit),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM lineage_events ORDER BY event_time DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_graph(namespace: Optional[str] = None) -> dict:
    """
    Build a D3-compatible node/edge graph from lineage events.

    Nodes: unique (namespace, name) dataset refs + job nodes.
    Edges: job → input dataset (read), job → output dataset (write).
    """
    with _get_conn() as con:
        if namespace:
            rows = con.execute(
                "SELECT * FROM lineage_events WHERE namespace LIKE ? "
                "ORDER BY event_time DESC LIMIT 500",
                (f"{namespace}%",),
            ).fetchall()
        else:
            rows = con.execute(
                "SELECT * FROM lineage_events ORDER BY event_time DESC LIMIT 500"
            ).fetchall()

    nodes: dict[str, dict] = {}
    edges: list[dict] = []

    for row in rows:
        r = dict(row)
        job_id = f"job:{r['namespace']}:{r['job_name']}"
        if job_id not in nodes:
            nodes[job_id] = {
                "id": job_id,
                "label": r["job_name"],
                "type": "job",
                "namespace": r["namespace"],
            }

        for ds in json.loads(r["inputs"]):
            ds_id = f"dataset:{ds['namespace']}:{ds['name']}"
            if ds_id not in nodes:
                nodes[ds_id] = {
                    "id": ds_id,
                    "label": ds["name"],
                    "type": "dataset",
                    "namespace": ds["namespace"],
                }
            edges.append({"source": ds_id, "target": job_id, "type": "read"})

        for ds in json.loads(r["outputs"]):
            ds_id = f"dataset:{ds['namespace']}:{ds['name']}"
            if ds_id not in nodes:
                nodes[ds_id] = {
                    "id": ds_id,
                    "label": ds["name"],
                    "type": "dataset",
                    "namespace": ds["namespace"],
                }
            edges.append({"source": job_id, "target": ds_id, "type": "write"})

    return {"nodes": list(nodes.values()), "edges": edges}


# ── Brenus integration stubs ──────────────────────────────────────────────────
# These functions are NOT wired yet. They are documented extension points
# for the Brenus integration sprint.
#
# See: backend/lineage/README.md for integration instructions.

def emit_claim_lineage(
    claim_id: str,
    source_id: str,
    artifact_id: str,
) -> str:
    """
    BRENUS INTEGRATION HOOK — not wired yet.

    Emit a lineage edge: source → claim → artifact.

    When wired:
        - source_id maps to a Brenus source registry entry (T1-SCI / T1-CORP / T2 / T3)
        - claim_id maps to a claim in engine_safe_input_block_v2.yaml
        - artifact_id maps to a Brenus artifact (lane output, deck component, etc.)

    Integration steps:
        1. Load source tier from Brenus source registry
        2. Emit LineageEvent with namespace="brenus.claims"
        3. Store claim_id and source_id as facets on the event
    """
    log.debug(
        "emit_claim_lineage stub called: claim=%s source=%s artifact=%s "
        "(not wired — Brenus integration pending)",
        claim_id, source_id, artifact_id,
    )
    return ""


def emit_artifact_lineage(
    artifact_id: str,
    consumer_id: str,
) -> str:
    """
    BRENUS INTEGRATION HOOK — not wired yet.

    Emit a lineage edge: artifact → consumer.

    When wired:
        - artifact_id maps to a Brenus artifact with a known admissibility level
        - consumer_id maps to a consumer (deck component, outreach brief, escape map)
        - Admissibility gate must be checked before emitting: if artifact is
          QUARANTINED or BLOCKED, do not emit to external consumers.

    Integration steps:
        1. Check artifact.admissibility >= consumer.admissibility_gate
        2. Emit LineageEvent with namespace="brenus.consumers"
        3. Store admissibility level as a facet on the event
    """
    log.debug(
        "emit_artifact_lineage stub called: artifact=%s consumer=%s "
        "(not wired — Brenus integration pending)",
        artifact_id, consumer_id,
    )
    return ""
