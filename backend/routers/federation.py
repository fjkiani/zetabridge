"""Federation router — POST /api/federation/ask, POST /api/federation/sql."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agents.nl_to_sql import ask, execute_duckdb, parse_and_sanitize, UnsafeSQLError
from config import cfg
from lineage.local_store import emit_query_lineage

router = APIRouter(prefix="/api/federation", tags=["federation"])


class AskRequest(BaseModel):
    question: str


class SQLRequest(BaseModel):
    sql: str


@router.post("/ask")
async def federation_ask(req: AskRequest):
    """NL question → SQL → execute → results. Primary end-to-end path."""
    try:
        result = ask(req.question)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result


@router.post("/sql")
async def federation_sql(req: SQLRequest):
    """Execute raw SQL against DuckDB. SQL is sanitized before execution."""
    try:
        sql = parse_and_sanitize(req.sql)
    except UnsafeSQLError as exc:
        raise HTTPException(status_code=400, detail=f"Unsafe SQL: {exc}")
    try:
        result = execute_duckdb(sql, cfg.DUCKDB_PATH)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))

    # Emit lineage
    try:
        from routers.query import _extract_tables_from_sql
        tables = _extract_tables_from_sql(sql)
        emit_query_lineage(
            job_name="federation.raw_sql",
            sql=sql,
            input_tables=tables,
            output_table=None,
            source="duckdb",
        )
    except Exception:
        pass

    return result
