"""GPS router — /api/gps endpoints (Agent GPS navigation layer).

  - GET  /api/gps/tasks                    -> task registry (filter by source/status)
  - POST /api/gps/tasks/{task_id}/claim    -> claim a task for an agent
  - POST /api/gps/tasks/{task_id}/complete -> mark a task complete
  - GET  /api/gps/coordinates/{node_id}    -> a node's graph coordinates
  - GET  /api/gps/coordinates              -> top broker nodes by degree
  - GET  /api/gps/provenance/{dataset}     -> a dataset's extraction ledger entry
  - GET  /api/gps/provenance               -> all ledger entries
"""

from __future__ import annotations

import os

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from federation.agent_gps import TaskRegistry, ProvenanceLedger

router = APIRouter(prefix="/api/gps", tags=["gps"])

_ANCHOR_DIR = os.environ.get("ZETA_ANCHOR_DIR", os.path.join(os.path.dirname(__file__), "..", "data", "anchors"))
_TASKS = TaskRegistry(os.path.join(_ANCHOR_DIR, "gps_tasks.json"))
_LEDGER = ProvenanceLedger(os.path.join(_ANCHOR_DIR, "gps_provenance.json"))

_service = None


def _get_service():
    global _service
    if _service is None:
        from federation.graph_service import GraphService
        _service = GraphService.from_env()
    return _service


# ── Task registry ────────────────────────────────────────────────────────────

@router.get("/tasks")
def list_tasks(source: str | None = None, status: str | None = None) -> dict:
    tasks = _TASKS.list(source=source, status=status)
    return {"n": len(tasks), "tasks": tasks}


class ClaimRequest(BaseModel):
    agent: str


@router.post("/tasks/{task_id}/claim")
def claim_task(task_id: str, req: ClaimRequest) -> dict:
    res = _TASKS.claim(task_id, req.agent)
    if not res.get("ok"):
        raise HTTPException(status_code=409, detail=res.get("reason"))
    return res


class CompleteRequest(BaseModel):
    output: str | None = None


@router.post("/tasks/{task_id}/complete")
def complete_task(task_id: str, req: CompleteRequest) -> dict:
    res = _TASKS.complete(task_id, req.output)
    if not res.get("ok"):
        raise HTTPException(status_code=404, detail=res.get("reason"))
    return res


# ── Graph coordinates ────────────────────────────────────────────────────────

@router.get("/coordinates")
def top_coordinates(limit: int = 20) -> dict:
    """Top broker nodes by GPS degree centrality."""
    svc = _get_service()
    rows = svc._read(
        "MATCH (n) WHERE n.gps_degree IS NOT NULL "
        "RETURN n.id AS id, n.gps_degree AS degree, n.gps_community AS community, "
        "n.gps_endpoint AS endpoint ORDER BY degree DESC LIMIT $lim",
        {"lim": limit},
    )
    return {"n": len(rows), "brokers": rows}


@router.get("/coordinates/{node_id:path}")
def node_coordinates(node_id: str) -> dict:
    svc = _get_service()
    rows = svc._read(
        "MATCH (n {id: $id}) RETURN n.id AS id, n.gps_community AS community, "
        "n.gps_degree AS degree, n.gps_hop_A_MSK AS hop_a_msk, n.gps_hop_B_SAS AS hop_b_sas, "
        "n.gps_hop_C_EGA AS hop_c_ega, n.gps_endpoint AS endpoint",
        {"id": node_id},
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"no coordinates for '{node_id}' (run the GPS batch job)")
    return rows[0]


# ── Provenance ledger ────────────────────────────────────────────────────────

@router.get("/provenance")
def all_provenance() -> dict:
    entries = _LEDGER.all()
    return {"n": len(entries), "entries": entries}


@router.get("/provenance/{dataset}")
def dataset_provenance(dataset: str) -> dict:
    entry = _LEDGER.get(dataset)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"no provenance for '{dataset}'")
    return entry
