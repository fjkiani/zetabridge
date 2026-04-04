"""Instructions-shaped catalog routes."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from catalog.gravitino_client import GravitinoClient
from connectors.databricks_connector import DatabricksConnector
from connectors.snowflake_connector import SnowflakeConnector

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


@router.get("/tables")
async def list_tables(source: str = "unified"):
    if source == "snowflake":
        return SnowflakeConnector().list_tables()
    if source == "databricks":
        return DatabricksConnector().list_unity_catalog_tables()
    if source == "unified":
        return GravitinoClient().list_all_tables()
    from connectors.duckdb_connector import DuckDBConnector

    return DuckDBConnector().list_tables()


@router.get("/schema/{table}")
async def get_schema(table: str, source: str = "snowflake"):
    if source == "snowflake":
        return SnowflakeConnector().get_schema(table)
    if source == "databricks":
        return DatabricksConnector().get_schema(table)
    if source == "duckdb":
        from connectors.duckdb_connector import DuckDBConnector

        return DuckDBConnector().get_schema(table)
    raise HTTPException(400, "Unsupported source for schema")


@router.get("/health")
async def catalog_health():
    return {
        "snowflake": SnowflakeConnector().test_connection(),
        "databricks": DatabricksConnector().test_connection(),
    }
