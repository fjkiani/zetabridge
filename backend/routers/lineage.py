"""Lineage router — GET /api/lineage/graph, GET /api/lineage/events, POST /api/lineage/emit."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from lineage.local_store import (
    DatasetRef,
    LineageEvent,
    emit_event,
    get_events,
    get_graph,
)

router = APIRouter(prefix="/api/lineage", tags=["lineage"])


class EmitRequest(BaseModel):
    job_name: str
    namespace: str
    event_type: str = "COMPLETE"
    inputs: list[dict] = []   # [{namespace, name}, ...]
    outputs: list[dict] = []
    sql: str | None = None
    source: str | None = None


@router.post("/emit")
async def emit_lineage(req: EmitRequest):
    """Write a lineage event to the local store. Returns run_id."""
    event = LineageEvent(
        job_name=req.job_name,
        namespace=req.namespace,
        event_type=req.event_type,
        inputs=[DatasetRef(**d) for d in req.inputs],
        outputs=[DatasetRef(**d) for d in req.outputs],
        sql=req.sql,
        source=req.source,
    )
    run_id = emit_event(event)
    return {"run_id": run_id, "event_time": event.event_time}


@router.get("/graph")
async def lineage_graph(namespace: str | None = None):
    """Return nodes + edges for D3 force-directed graph rendering."""
    return get_graph(namespace=namespace)


@router.get("/events")
async def lineage_events(job: str | None = None, limit: int = 100):
    """Return paginated lineage events, newest first."""
    return get_events(job=job, limit=min(limit, 500))
