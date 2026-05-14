"""Catalog router — GET /api/catalog/tables, GET /api/catalog/tables/{name}.

Backed by DuckDB only. Gravitino, Snowflake, and Databricks paths removed.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from config import cfg

router = APIRouter(prefix="/api/catalog", tags=["catalog"])


def _get_duckdb_tables() -> list[dict]:
    """List all tables in the DuckDB main schema with column details."""
    import duckdb
    con = duckdb.connect(cfg.DUCKDB_PATH, read_only=True)
    try:
        tables = con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()
        result = []
        for (table_name,) in tables:
            cols = con.execute(
                "SELECT column_name, data_type "
                "FROM information_schema.columns "
                "WHERE table_name = ? AND table_schema = 'main' "
                "ORDER BY ordinal_position",
                [table_name],
            ).fetchall()
            result.append({
                "name": table_name,
                "source": "duckdb",
                "columns": [
                    {"name": col_name, "type": data_type}
                    for col_name, data_type in cols
                ],
            })
        return result
    finally:
        con.close()


@router.get("/tables")
async def list_tables():
    """List all tables in the DuckDB catalog with column metadata."""
    try:
        return _get_duckdb_tables()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/tables/{table_name}")
async def get_table(table_name: str):
    """Get schema details for a single table."""
    try:
        tables = _get_duckdb_tables()
        for t in tables:
            if t["name"] == table_name:
                return t
        raise HTTPException(status_code=404, detail=f"Table '{table_name}' not found")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
