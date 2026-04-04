"""Connector health for Snowflake / Databricks / DuckDB."""

from __future__ import annotations

from fastapi import APIRouter

from config import cfg
from connectors.databricks_connector import DatabricksConnector
from connectors.duckdb_connector import DuckDBConnector
from connectors.snowflake_connector import SnowflakeConnector

router = APIRouter(prefix="/api/connectors", tags=["connectors"])


@router.get("")
async def connectors_health():
    sf_ok = SnowflakeConnector().test_connection()
    dbx_ok = DatabricksConnector().test_connection()
    duck_ok = DuckDBConnector().test_connection()
    return {
        "snowflake": {
            "configured": bool(cfg.SNOWFLAKE_ACCOUNT and cfg.SNOWFLAKE_USER),
            "ok": sf_ok,
        },
        "databricks": {
            "configured": bool(cfg.DATABRICKS_HOST and cfg.DATABRICKS_TOKEN),
            "ok": dbx_ok,
        },
        "duckdb": {"ok": duck_ok},
    }
