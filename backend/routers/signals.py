"""Signals router — read-only /api/signals endpoints over the federated KG.

The Session-14 "value" layer. Surfaces the strongest cross-endpoint signals,
the genomic->clinical bridges, and the blind spots ("what pharma got wrong"),
all grounded in real graph nodes via SignalService. Also exposes the three
signal-intelligence agents (signal_miner / bridge_hunter / gap_auditor).

Auth: same ``X-Zeta-Api-Key`` header as /api/graph. Closed-by-default (503) when
no key is configured server-side. Read-only — no write path.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from config import cfg

router = APIRouter(prefix="/api/signals", tags=["signals"])

_service = None
_agents = None


def _get_service():
    global _service
    if _service is None:
        from federation.signal_service import SignalService

        _service = SignalService.from_env()
    return _service


def _get_agents():
    global _agents
    if _agents is None:
        from agents.signal_agents import build_signal_agents

        _agents = build_signal_agents(_get_service())
    return _agents


async def require_api_key(x_zeta_api_key: Optional[str] = Header(default=None)) -> None:
    configured = cfg.ZETA_GRAPH_API_KEY
    if not configured:
        raise HTTPException(status_code=503, detail="Graph API key not configured on server.")
    if not x_zeta_api_key or x_zeta_api_key != configured:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Zeta-Api-Key.")


_VALID_FAMILIES = {"all", "drug_ae", "pharmacovig", "genomic_bridge", "cross_trial", "outlier"}
_VALID_AGENTS = {"signal_miner", "bridge_hunter", "gap_auditor"}


class AgentRequest(BaseModel):
    agent: str = Field(..., description="signal_miner | bridge_hunter | gap_auditor")
    action: Optional[str] = None
    family: Optional[str] = None
    slug: Optional[str] = None
    limit: int = Field(default=8, ge=1, le=50)


@router.get("/health", dependencies=[Depends(require_api_key)])
async def signals_health():
    try:
        svc = _get_service()
        return {"status": "ok", "counts_by_family": svc.counts_by_family()}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Neo4j unavailable: {exc}")


@router.get("/overview", dependencies=[Depends(require_api_key)])
async def signals_overview():
    try:
        return _get_service().overview()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/top", dependencies=[Depends(require_api_key)])
async def signals_top(family: str = "all", limit: int = 20):
    if family not in _VALID_FAMILIES:
        raise HTTPException(status_code=400,
                            detail=f"Invalid family '{family}'. Valid: {sorted(_VALID_FAMILIES)}")
    limit = max(1, min(int(limit), 200))
    try:
        return _get_service().top_signals(family=family, limit=limit)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/bridges", dependencies=[Depends(require_api_key)])
async def signals_bridges():
    try:
        return _get_service().bridges()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/gaps", dependencies=[Depends(require_api_key)])
async def signals_gaps():
    try:
        return _get_service().gaps()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.post("/agent", dependencies=[Depends(require_api_key)])
async def signals_agent(req: AgentRequest):
    if req.agent not in _VALID_AGENTS:
        raise HTTPException(status_code=400,
                            detail=f"Invalid agent '{req.agent}'. Valid: {sorted(_VALID_AGENTS)}")
    from agents.base import AgentContext

    agent = _get_agents()[req.agent]
    task: dict[str, Any] = {"limit": req.limit}
    if req.action:
        task["action"] = req.action
    if req.family:
        task["family"] = req.family
    if req.slug:
        task["slug"] = req.slug
    try:
        result = await agent.run(AgentContext(), task)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    if result.status.value != "success":
        raise HTTPException(status_code=422, detail=result.error or "agent failed")
    return {"agent": req.agent, "status": result.status.value,
            "benchmark": result.benchmark, **result.output}


# NOTE: /{slug:path} MUST be declared last so it does not shadow the static
# routes above (e.g. /overview, /top, /bridges, /gaps, /agent).
@router.get("/{slug:path}", dependencies=[Depends(require_api_key)])
async def signals_detail(slug: str):
    detail = _get_service().signal_detail(slug)
    if detail is None:
        raise HTTPException(status_code=404, detail=f"Signal not found: {slug}")
    return detail
