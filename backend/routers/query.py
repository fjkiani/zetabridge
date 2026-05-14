"""Query router — POST /api/query/execute, GET /api/query/tables."""

from __future__ import annotations

import re

from fastapi import APIRouter
from pydantic import BaseModel

from agents.nl_to_sql import ask, _TCGA_TABLES
from lineage.local_store import emit_query_lineage

router = APIRouter(prefix="/api/query", tags=["query"])


class QueryRequest(BaseModel):
    question: str
    # BRENUS INTEGRATION HOOK: add `schema: str = "default"` here to route
    # to different TableSpec sets (e.g., "brenus_trials" vs "tcga_biotech")


@router.post("/execute")
async def execute_query(req: QueryRequest):
    """
    NL question → SQL → DuckDB → results.

    Returns sql, columns, rows, row_count, fallback_used, sql_hash.
    Emits a lineage event to the local store on every successful query.
    """
    result = ask(req.question)

    # Emit lineage (best-effort — never fails the request)
    if not result.get("error") and result.get("sql"):
        try:
            tables = _extract_tables_from_sql(result["sql"])
            emit_query_lineage(
                job_name="query.nl_to_sql",
                sql=result["sql"],
                input_tables=tables,
                output_table=None,
                source="duckdb",
            )
        except Exception:
            pass

    return result


@router.get("/tables")
async def list_tables():
    """List all tables available in the active schema context."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "columns": [
                {"name": c.name, "type": c.type, "description": c.description}
                for c in t.columns
            ],
        }
        for t in _TCGA_TABLES
    ]


def _extract_tables_from_sql(sql: str) -> list[str]:
    """Extract table names from a SQL string (best-effort)."""
    pattern = re.compile(
        r"\bFROM\s+([a-zA-Z_][a-zA-Z0-9_]*)"
        r"|\bJOIN\s+([a-zA-Z_][a-zA-Z0-9_]*)",
        re.IGNORECASE,
    )
    tables = []
    for m in pattern.finditer(sql):
        name = m.group(1) or m.group(2)
        if name and name.upper() not in ("SELECT", "WHERE", "ON"):
            tables.append(name)
    return list(dict.fromkeys(tables))  # deduplicate, preserve order
