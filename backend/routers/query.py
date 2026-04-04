"""Instructions-shaped POST /api/query."""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from agents.query_agent import execute_nl_query, extract_input_tables
from lineage.marquez_client import emit_query_lineage

router = APIRouter(prefix="/api/query", tags=["query"])


class QueryRequest(BaseModel):
    question: str
    source: str = "duckdb"


@router.post("")
async def run_query(req: QueryRequest):
    result = execute_nl_query(req.question, req.source)
    if not result.get("error"):
        tables = extract_input_tables(result.get("sql", ""))
        emit_query_lineage(
            job_name="copilot_query",
            sql=result.get("sql", ""),
            input_tables=tables,
            output_table=None,
            source=req.source,
        )
    return result
