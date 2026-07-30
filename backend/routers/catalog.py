"""Catalog routes — serve the live federated Neo4j KG as a browsable catalog.

The Catalog page previously rendered empty because this router read legacy
Gravitino/Snowflake/Databricks connectors, none of which hold the federated KG.
The graph is the real catalog, so the default (``source=graph``) now maps each
Neo4j node label to a "table" (catalog = endpoint, schema = label, columns =
property keys). The legacy connectors remain available behind an explicit
``?source=snowflake|databricks|unified|duckdb`` opt-in.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/catalog", tags=["catalog"])

_graph_service = None


def _get_graph():
    """Lazy singleton GraphService (read-only)."""
    global _graph_service
    if _graph_service is None:
        from federation.graph_service import GraphService

        _graph_service = GraphService.from_env()
    return _graph_service


@router.get("/tables")
async def list_tables(source: str = "graph"):
    if source == "graph":
        try:
            return _get_graph().catalog_tables()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Graph catalog unavailable: {exc}")
    # legacy connector opt-in
    if source == "snowflake":
        from connectors.snowflake_connector import SnowflakeConnector

        return SnowflakeConnector().list_tables()
    if source == "databricks":
        from connectors.databricks_connector import DatabricksConnector

        return DatabricksConnector().list_unity_catalog_tables()
    if source == "unified":
        from catalog.gravitino_client import GravitinoClient

        return GravitinoClient().list_all_tables()
    if source == "duckdb":
        from connectors.duckdb_connector import DuckDBConnector

        return DuckDBConnector().list_tables()
    raise HTTPException(400, f"Unsupported catalog source: {source}")


@router.get("/stats")
async def catalog_stats(source: str = "graph"):
    if source == "graph":
        try:
            return _get_graph().catalog_stats()
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Graph catalog unavailable: {exc}")
    tables = await list_tables(source=source)
    catalogs = sorted({t.get("catalog_name", "?") for t in tables}) if isinstance(tables, list) else []
    return {"n_tables": len(tables) if isinstance(tables, list) else 0,
            "n_catalogs": len(catalogs), "catalogs": catalogs}


@router.get("/schema/{table}")
async def get_schema(table: str, source: str = "graph"):
    if source == "graph":
        try:
            rows = [t for t in _get_graph().catalog_tables() if t["table_name"] == table]
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"Graph catalog unavailable: {exc}")
        if not rows:
            raise HTTPException(404, f"No graph node label: {table}")
        return rows[0]
    if source == "snowflake":
        from connectors.snowflake_connector import SnowflakeConnector

        return SnowflakeConnector().get_schema(table)
    if source == "databricks":
        from connectors.databricks_connector import DatabricksConnector

        return DatabricksConnector().get_schema(table)
    if source == "duckdb":
        from connectors.duckdb_connector import DuckDBConnector

        return DuckDBConnector().get_schema(table)
    raise HTTPException(400, "Unsupported source for schema")


@router.get("/health")
async def catalog_health():
    try:
        graph_ok = _get_graph().health()
    except Exception as exc:
        graph_ok = {"status": "error", "detail": str(exc)}
    return {"graph": graph_ok}
