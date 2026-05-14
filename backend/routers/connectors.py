"""Connectors router — GET /api/connectors, GET /api/connectors/{name}/health."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from connectors.registry import registry

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


@router.get("")
async def list_connectors():
    """List all connectors. wired=True means actually connected."""
    return [c.to_dict() for c in registry.list_all()]


@router.get("/{name}")
async def get_connector(name: str):
    """Get a single connector by name."""
    spec = registry.get(name)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Connector '{name}' not found")
    return spec.to_dict()


@router.get("/{name}/health")
async def connector_health(name: str):
    """
    Run a health check for a connector.

    For wired=True connectors: runs a real ping (DuckDB: SELECT 1, SQLite: SELECT 1).
    For wired=False connectors: returns not_wired status immediately.
    """
    spec = registry.get(name)
    if not spec:
        raise HTTPException(status_code=404, detail=f"Connector '{name}' not found")
    return spec.run_health_check()
