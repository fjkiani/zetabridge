"""Graph access router - read-only /api/graph endpoints over the federated Neo4j KG.

Third-party agents authenticate with header ``X-Zeta-Api-Key`` matching
``cfg.ZETA_GRAPH_API_KEY``. Neo4j credentials are never exposed to the caller.

Fail-safe: if no API key is configured server-side, every endpoint returns 503
(closed by default) rather than allowing unauthenticated access.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field

from config import cfg
from federation.cypher_guard import CypherWriteAttempt

router = APIRouter(prefix="/api/graph", tags=["graph"])

_service = None


def _get_service():
    """Lazy singleton GraphService."""
    global _service
    if _service is None:
        from federation.graph_service import GraphService

        _service = GraphService.from_env()
    return _service


async def require_api_key(x_zeta_api_key: Optional[str] = Header(default=None)) -> None:
    """Application-layer auth. Closed-by-default if no key configured."""
    configured = cfg.ZETA_GRAPH_API_KEY
    if not configured:
        raise HTTPException(status_code=503, detail="Graph API key not configured on server.")
    if not x_zeta_api_key or x_zeta_api_key != configured:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Zeta-Api-Key.")


# --- request models -------------------------------------------------------
class SearchRequest(BaseModel):
    prefix: Optional[str] = None
    label: Optional[str] = None
    type: Optional[str] = None
    name_contains: Optional[str] = None
    limit: int = Field(default=50, ge=1, le=200)


class NeighborsRequest(BaseModel):
    id: str
    hops: int = Field(default=1, ge=1, le=3)
    rel_types: Optional[list[str]] = None
    direction: str = Field(default="both", pattern="^(both|in|out)$")
    cap: int = Field(default=500, ge=1, le=2000)


class PathsRequest(BaseModel):
    source_id: str
    target_id: Optional[str] = None
    target_prefix: Optional[str] = None
    max_hops: int = Field(default=5, ge=1, le=5)
    k: int = Field(default=10, ge=1, le=25)


class CypherRequest(BaseModel):
    cypher: str
    params: Optional[dict[str, Any]] = None
    cap: Optional[int] = Field(default=None, ge=1, le=1000)


# --- endpoints ------------------------------------------------------------
@router.get("/health", dependencies=[Depends(require_api_key)])
async def graph_health():
    try:
        return _get_service().health()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Neo4j unavailable: {exc}")


@router.get("/schema", dependencies=[Depends(require_api_key)])
async def graph_schema():
    try:
        return _get_service().schema()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@router.get("/node/{node_id:path}", dependencies=[Depends(require_api_key)])
async def graph_node(node_id: str):
    node = _get_service().get_node(node_id)
    if node is None:
        raise HTTPException(status_code=404, detail=f"Node not found: {node_id}")
    return node


@router.post("/search", dependencies=[Depends(require_api_key)])
async def graph_search(req: SearchRequest):
    try:
        nodes = _get_service().search(
            prefix=req.prefix, label=req.label, type_=req.type,
            name_contains=req.name_contains, limit=req.limit,
        )
        return {"count": len(nodes), "nodes": nodes}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/neighbors", dependencies=[Depends(require_api_key)])
async def graph_neighbors(req: NeighborsRequest):
    try:
        return _get_service().neighbors(
            req.id, hops=req.hops, rel_types=req.rel_types,
            direction=req.direction, cap=req.cap,
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/paths", dependencies=[Depends(require_api_key)])
async def graph_paths(req: PathsRequest):
    try:
        return _get_service().find_paths(
            req.source_id, target_id=req.target_id,
            target_prefix=req.target_prefix, max_hops=req.max_hops, k=req.k,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.post("/cypher", dependencies=[Depends(require_api_key)])
async def graph_cypher(req: CypherRequest):
    try:
        return _get_service().run_cypher(req.cypher, params=req.params, cap=req.cap)
    except CypherWriteAttempt as exc:
        # write / DDL / security attempt -> forbidden
        raise HTTPException(status_code=403, detail=f"Read-only violation: {exc}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
