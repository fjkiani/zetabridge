"""Federation Agent - Groq-powered NL-to-SQL for biotech research tables.

Converts natural language oncology queries into DuckDB SQL,
executes against seeded TCGA-style tables, returns structured results.
"""
import os
import json
import duckdb
from typing import Optional
from config import cfg

try:
    from groq import Groq
except ImportError:
    Groq = None

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "biotech", "biotech.duckdb")

TABLE_SCHEMA = """
Available tables in DuckDB:

1. tcga_clinical (patient_id TEXT PK, cancer_type TEXT, stage TEXT, age INT, brca_status TEXT, hrd_score FLOAT, os_months FLOAT, pfs_months FLOAT, response TEXT)
2. genomic_variants (variant_id TEXT PK, patient_id TEXT FK->tcga_clinical, gene TEXT, mutation TEXT, vaf FLOAT, pathogenicity TEXT, drug_target TEXT)
3. hrd_scores (patient_id TEXT PK FK->tcga_clinical, hrd_score FLOAT, loh_score FLOAT, tai_score FLOAT, lst_score FLOAT, brca1_methyl BOOLEAN, signature3_pct FLOAT)
4. drug_responses (response_id TEXT PK, patient_id TEXT FK->tcga_clinical, drug TEXT, ic50 FLOAT, auc FLOAT, response TEXT, line_of_therapy INT)
5. synthetic_lethality (pair_id TEXT PK, gene_a TEXT, gene_b TEXT, lethality_score FLOAT, evidence_level TEXT, mechanism TEXT, drug_target TEXT)
"""

SYSTEM_PROMPT = f"""You are a biotech SQL analyst. Convert the user question into a single DuckDB SQL query.
Rules:
- Output ONLY the SQL, no explanation, no markdown fences.
- Use only the tables and columns listed below.
- For aggregations, always alias computed columns.
- Limit results to 50 rows unless the user specifies otherwise.
{TABLE_SCHEMA}"""


def _get_groq_client() -> Optional[object]:
    api_key = getattr(cfg, "GROQ_API_KEY", None) or os.getenv("GROQ_API_KEY")
    if not api_key or Groq is None:
        return None
    return Groq(api_key=api_key)


def nl_to_sql(question: str) -> str:
    client = _get_groq_client()
    if client is None:
        raise RuntimeError("Groq client unavailable - set GROQ_API_KEY")
    resp = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": question},
        ],
        temperature=0,
        max_tokens=512,
    )
    sql = resp.choices[0].message.content.strip()
    # Strip markdown fences if model includes them
    if sql.startswith("```"):
        sql = sql.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
    return sql


def execute_query(sql: str) -> dict:
    con = duckdb.connect(DB_PATH, read_only=True)
    try:
        result = con.execute(sql)
        columns = [desc[0] for desc in result.description]
        rows = result.fetchall()
        return {
            "columns": columns,
            "rows": [dict(zip(columns, row)) for row in rows],
            "row_count": len(rows),
        }
    finally:
        con.close()


def ask(question: str) -> dict:
    """End-to-end: NL question -> SQL -> execute -> structured result."""
    sql = nl_to_sql(question)
    try:
        data = execute_query(sql)
    except Exception as exc:
        return {"question": question, "sql": sql, "error": str(exc), "rows": []}
    return {
        "question": question,
        "sql": sql,
        "columns": data["columns"],
        "rows": data["rows"],
        "row_count": data["row_count"],
    }
