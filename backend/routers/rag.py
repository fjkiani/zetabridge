"""RAG router — /api/rag endpoints (multi-hop GraphRAG over the live KG, no cache)."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, Field

from federation.graph_rag_neo4j import Neo4jGraphRAG

router = APIRouter(prefix="/api/rag", tags=["rag"])

_rag = None


def _get_rag() -> Neo4jGraphRAG:
    global _rag
    if _rag is None:
        from federation.graph_service import GraphService
        _rag = Neo4jGraphRAG(GraphService.from_env())
    return _rag


class RagRequest(BaseModel):
    query: str
    max_hops: int = Field(3, ge=1, le=6)


@router.post("/query")
def rag_query(req: RagRequest) -> dict:
    """Answer a natural-language graph question with cited multi-hop paths."""
    return _get_rag().answer(req.query, max_hops=req.max_hops)


@router.get("/resolve")
def rag_resolve(q: str, limit: int = 8) -> dict:
    """Resolve a term to its seed nodes (degree-ordered)."""
    seeds = _get_rag().resolve(q, limit=limit)
    return {"query": q, "n": len(seeds), "seeds": seeds}
