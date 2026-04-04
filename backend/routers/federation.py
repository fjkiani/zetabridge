"""Federation router - exposes /api/federation endpoints."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from agents.federation_agent import ask, nl_to_sql, execute_query

router = APIRouter(prefix="/api/federation", tags=["federation"])


class AskRequest(BaseModel):
    question: str


class SQLRequest(BaseModel):
    sql: str


@router.post("/ask")
async def federation_ask(req: AskRequest):
    """NL question -> SQL -> execute -> results."""
    try:
        result = ask(req.question)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
    return result


@router.post("/sql")
async def federation_sql(req: SQLRequest):
    """Execute raw SQL against biotech DuckDB."""
    try:
        result = execute_query(req.sql)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return result


@router.get("/tables")
async def federation_tables():
    """List biotech tables and row counts."""
    try:
        tables = ["tcga_clinical", "genomic_variants", "hrd_scores", "drug_responses", "synthetic_lethality"]
        counts = {}
        for t in tables:
            try:
                r = execute_query(f"SELECT COUNT(*) as cnt FROM {t}")
                counts[t] = r["rows"][0]["cnt"] if r["rows"] else 0
            except Exception:
                counts[t] = "not_seeded"
        return {"tables": counts}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
