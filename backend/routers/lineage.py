"""Marquez-backed lineage graph."""

from __future__ import annotations

from fastapi import APIRouter

from lineage.marquez_client import get_lineage_graph

router = APIRouter(prefix="/api/lineage", tags=["lineage"])


@router.get("")
async def lineage(dataset: str, source: str = "snowflake"):
    return get_lineage_graph(dataset, source)
