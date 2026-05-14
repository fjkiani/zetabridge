"""
nl_to_sql — Canonical NL→SQL engine for ZetaBridge substrate.

This is the single authoritative query path. It replaces both
federation_agent.py and query_agent.py, which are now thin wrappers
that call ask() here.

Architecture:
    NL question
      → schema_context_builder(tables: list[TableSpec]) → prompt
      → groq_generate(prompt)                           → raw SQL   [primary]
      → [fallback 1] hf_inference_client(prompt)        → raw SQL
      → [fallback 2] hf_http_api(prompt)                → raw SQL
      → [fallback 3] heuristic_fallback(question,tables)→ safe SELECT
      → parse_and_sanitize(raw)                         → clean SQL
      → execute_duckdb(sql, db_path)
      → {columns, rows, row_count, sql_used, fallback_used}

Schema context is injected at call time via TableSpec/ColumnSpec dataclasses.
The schema is NOT hardcoded here — this is the Brenus integration hook.
To retarget to the Brenus trial domain, pass a different list of TableSpec
objects to ask() or build_schema_context().

Extension point:
    from agents.nl_to_sql import ask, TableSpec, ColumnSpec
    tables = [TableSpec("trials", [...], "Governed trial packages"), ...]
    result = ask("All 1L MSS mCRC comparators", tables=tables, db_path=BRENUS_DB)
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional

import duckdb
import httpx

from config import cfg

log = logging.getLogger("zetabridge.nl_to_sql")

# ── Default TCGA schema (substrate default; replaced by Brenus schema at integration) ──

_DEFAULT_DB_PATH = cfg.DUCKDB_PATH

_TCGA_TABLES: list["TableSpec"] = []  # populated lazily after class definition


# ── Data contracts ────────────────────────────────────────────────────────────

@dataclass
class ColumnSpec:
    """A single column in a table."""
    name: str
    type: str
    description: str = ""


@dataclass
class TableSpec:
    """A queryable table with its schema context."""
    name: str
    columns: list[ColumnSpec]
    description: str = ""

    def to_prompt_block(self) -> str:
        col_lines = "\n".join(
            f"    {c.name} {c.type}" + (f"  -- {c.description}" if c.description else "")
            for c in self.columns
        )
        desc = f"  -- {self.description}" if self.description else ""
        return f"{self.name}{desc}\n(\n{col_lines}\n)"


# ── Default TCGA tables (substrate seed schema) ───────────────────────────────

_TCGA_TABLES = [
    TableSpec(
        name="tcga_clinical",
        description="TCGA-style patient clinical data",
        columns=[
            ColumnSpec("patient_id", "TEXT", "Primary key"),
            ColumnSpec("cancer_type", "TEXT", "e.g. BRCA, COAD, GBM"),
            ColumnSpec("stage", "TEXT", "I/II/III/IV"),
            ColumnSpec("age", "INT"),
            ColumnSpec("brca_status", "TEXT", "BRCA1/BRCA2/WT"),
            ColumnSpec("hrd_score", "FLOAT"),
            ColumnSpec("os_months", "FLOAT", "Overall survival months"),
            ColumnSpec("pfs_months", "FLOAT", "Progression-free survival months"),
            ColumnSpec("response", "TEXT", "CR/PR/SD/PD"),
        ],
    ),
    TableSpec(
        name="genomic_variants",
        description="Somatic variant calls per patient",
        columns=[
            ColumnSpec("variant_id", "TEXT", "Primary key"),
            ColumnSpec("patient_id", "TEXT", "FK → tcga_clinical"),
            ColumnSpec("gene", "TEXT"),
            ColumnSpec("mutation", "TEXT", "e.g. p.R175H"),
            ColumnSpec("vaf", "FLOAT", "Variant allele frequency"),
            ColumnSpec("pathogenicity", "TEXT", "Pathogenic/VUS/Benign"),
            ColumnSpec("drug_target", "TEXT"),
        ],
    ),
    TableSpec(
        name="hrd_scores",
        description="Homologous recombination deficiency scores",
        columns=[
            ColumnSpec("patient_id", "TEXT", "PK/FK → tcga_clinical"),
            ColumnSpec("hrd_score", "FLOAT"),
            ColumnSpec("loh_score", "FLOAT"),
            ColumnSpec("tai_score", "FLOAT"),
            ColumnSpec("lst_score", "FLOAT"),
            ColumnSpec("brca1_methyl", "BOOLEAN"),
            ColumnSpec("signature3_pct", "FLOAT"),
        ],
    ),
    TableSpec(
        name="drug_responses",
        description="Drug response measurements per patient",
        columns=[
            ColumnSpec("response_id", "TEXT", "Primary key"),
            ColumnSpec("patient_id", "TEXT", "FK → tcga_clinical"),
            ColumnSpec("drug", "TEXT"),
            ColumnSpec("ic50", "FLOAT"),
            ColumnSpec("auc", "FLOAT"),
            ColumnSpec("response", "TEXT", "CR/PR/SD/PD"),
            ColumnSpec("line_of_therapy", "INT"),
        ],
    ),
    TableSpec(
        name="synthetic_lethality",
        description="Synthetic lethality gene pairs",
        columns=[
            ColumnSpec("pair_id", "TEXT", "Primary key"),
            ColumnSpec("gene_a", "TEXT"),
            ColumnSpec("gene_b", "TEXT"),
            ColumnSpec("lethality_score", "FLOAT"),
            ColumnSpec("evidence_level", "TEXT", "A/B/C"),
            ColumnSpec("mechanism", "TEXT"),
            ColumnSpec("drug_target", "TEXT"),
        ],
    ),
]


# ── Errors ────────────────────────────────────────────────────────────────────

class UnsafeSQLError(ValueError):
    """Raised when generated SQL contains disallowed statements."""


# ── Schema context builder ────────────────────────────────────────────────────

def build_schema_context(tables: list[TableSpec]) -> str:
    """Build a schema context string for LLM prompt injection."""
    blocks = "\n\n".join(t.to_prompt_block() for t in tables)
    return (
        "Available tables:\n\n"
        + blocks
        + "\n\nUse ONLY these tables and columns. "
        "Output ONLY the SQL query — no explanation, no markdown fences."
    )


def _build_prompt(question: str, tables: list[TableSpec]) -> str:
    schema = build_schema_context(tables)
    return (
        "You are a SQL analyst. Convert the user question into a single DuckDB SQL query.\n"
        "Rules:\n"
        "- Output ONLY the SQL, no explanation, no markdown fences.\n"
        "- Use only the tables and columns listed below.\n"
        "- For aggregations, always alias computed columns.\n"
        "- Limit results to 50 rows unless the user specifies otherwise.\n\n"
        f"{schema}\n\n"
        f"Question: {question}"
    )


# ── SQL sanitizer ─────────────────────────────────────────────────────────────

_UNSAFE_KEYWORDS = re.compile(
    r"\b(DROP|DELETE|INSERT|UPDATE|CREATE|ALTER|TRUNCATE|REPLACE|MERGE|EXEC|EXECUTE|GRANT|REVOKE)\b",
    re.IGNORECASE,
)
_FENCE_RE = re.compile(r"```(?:sql)?\s*([\s\S]*?)```", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\b", re.IGNORECASE)


def parse_and_sanitize(raw: str) -> str:
    """
    Clean raw LLM output into a safe, executable DuckDB SQL string.

    Raises UnsafeSQLError if the SQL contains write/DDL statements or
    does not start with SELECT or WITH.
    """
    s = (raw or "").strip()

    # Strip markdown fences
    m = _FENCE_RE.search(s)
    if m:
        s = m.group(1).strip()

    # Strip trailing semicolons, normalize whitespace
    s = s.rstrip(";").strip()

    if not s:
        raise UnsafeSQLError("Empty SQL after sanitization")

    # Reject write/DDL statements
    if _UNSAFE_KEYWORDS.search(s):
        raise UnsafeSQLError(f"SQL contains disallowed statement: {s[:200]}")

    # Must start with SELECT or WITH (CTEs)
    first_token = s.split()[0].upper()
    if first_token not in ("SELECT", "WITH"):
        raise UnsafeSQLError(
            f"SQL must start with SELECT or WITH, got: {first_token!r}"
        )

    # Inject LIMIT if missing
    if not _LIMIT_RE.search(s):
        s = s + " LIMIT 50"

    # Re-add exactly one semicolon
    return s + ";"


# ── LLM backends ─────────────────────────────────────────────────────────────

def _groq_generate(prompt: str) -> str:
    """Call Groq chat completions (primary path)."""
    if not cfg.GROQ_API_KEY:
        return ""
    with httpx.Client(timeout=60.0) as client:
        resp = client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {cfg.GROQ_API_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0,
                "max_tokens": 512,
            },
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()


def _hf_inference_client(prompt: str) -> str:
    """HuggingFace InferenceClient fallback."""
    try:
        from huggingface_hub import InferenceClient
    except ImportError:
        return ""
    if not cfg.HF_API_TOKEN:
        return ""
    try:
        client = InferenceClient(token=cfg.HF_API_TOKEN)
        result = client.text_generation(
            prompt,
            model=cfg.HF_TEXT2SQL_MODEL,
            max_new_tokens=300,
            temperature=0.1,
        )
        return result.strip() if isinstance(result, str) else ""
    except Exception as exc:
        log.debug("HF InferenceClient failed: %s", exc)
        return ""


def _hf_http_api(prompt: str) -> str:
    """HuggingFace HTTP API fallback."""
    if not cfg.HF_API_TOKEN:
        return ""
    try:
        with httpx.Client(timeout=120.0) as client:
            resp = client.post(
                f"https://api-inference.huggingface.co/models/{cfg.HF_TEXT2SQL_MODEL}",
                headers={"Authorization": f"Bearer {cfg.HF_API_TOKEN}"},
                json={
                    "inputs": prompt,
                    "parameters": {
                        "max_new_tokens": 300,
                        "temperature": 0.1,
                        "return_full_text": False,
                    },
                },
            )
            resp.raise_for_status()
            data = resp.json()
            if isinstance(data, list) and data:
                return data[0].get("generated_text", "").strip()
            return ""
    except Exception as exc:
        log.debug("HF HTTP API failed: %s", exc)
        return ""


def _heuristic_fallback(question: str, tables: list[TableSpec]) -> str:
    """
    Last-resort fallback: pick the best-matching table by keyword overlap
    and return a safe SELECT * LIMIT 20.

    Always succeeds — never raises.
    """
    q_lower = question.lower()
    best_table = tables[0].name if tables else "unknown"
    best_score = 0
    for t in tables:
        score = sum(
            1
            for word in q_lower.split()
            if word in t.name.lower()
            or any(word in c.name.lower() for c in t.columns)
            or word in t.description.lower()
        )
        if score > best_score:
            best_score = score
            best_table = t.name
    return f"SELECT * FROM {best_table} LIMIT 20;"


# ── DuckDB executor ───────────────────────────────────────────────────────────

def execute_duckdb(sql: str, db_path: str) -> dict:
    """Execute a sanitized SQL string against DuckDB. Returns structured result."""
    con = duckdb.connect(db_path, read_only=True)
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


# ── Public API ────────────────────────────────────────────────────────────────

def ask(
    question: str,
    tables: Optional[list[TableSpec]] = None,
    db_path: Optional[str] = None,
) -> dict:
    """
    Full NL→SQL→execute pipeline.

    Args:
        question: Natural language question.
        tables:   Schema context. Defaults to TCGA substrate tables.
                  BRENUS INTEGRATION HOOK: pass Brenus TableSpec list here.
        db_path:  DuckDB file path. Defaults to cfg.DUCKDB_PATH.
                  BRENUS INTEGRATION HOOK: pass Brenus DuckDB path here.

    Returns:
        {
            "sql": str,
            "columns": list[str],
            "rows": list[dict],
            "row_count": int,
            "fallback_used": str | None,   # None | "hf_client" | "hf_http" | "heuristic"
            "sql_hash": str,               # SHA256 of final SQL (for lineage dedup)
        }
    """
    if tables is None:
        tables = _TCGA_TABLES
    if db_path is None:
        db_path = _DEFAULT_DB_PATH

    prompt = _build_prompt(question, tables)
    fallback_used: str | None = None
    raw_sql = ""

    # Primary: Groq
    try:
        raw_sql = _groq_generate(prompt)
        if raw_sql:
            sql = parse_and_sanitize(raw_sql)
        else:
            raise RuntimeError("Groq returned empty response")
    except Exception as exc:
        log.info("Groq path failed (%s), trying HF InferenceClient", exc)
        raw_sql = ""

        # Fallback 1: HF InferenceClient
        try:
            raw_sql = _hf_inference_client(prompt)
            if raw_sql:
                sql = parse_and_sanitize(raw_sql)
                fallback_used = "hf_client"
            else:
                raise RuntimeError("HF InferenceClient returned empty")
        except Exception as exc2:
            log.info("HF InferenceClient failed (%s), trying HF HTTP API", exc2)

            # Fallback 2: HF HTTP API
            try:
                raw_sql = _hf_http_api(prompt)
                if raw_sql:
                    sql = parse_and_sanitize(raw_sql)
                    fallback_used = "hf_http"
                else:
                    raise RuntimeError("HF HTTP API returned empty")
            except Exception as exc3:
                log.info("HF HTTP API failed (%s), using heuristic fallback", exc3)

                # Fallback 3: heuristic (always succeeds)
                sql = _heuristic_fallback(question, tables)
                fallback_used = "heuristic"

    sql_hash = hashlib.sha256(sql.encode()).hexdigest()

    try:
        result = execute_duckdb(sql, db_path)
    except Exception as exc:
        return {
            "sql": sql,
            "columns": [],
            "rows": [],
            "row_count": 0,
            "fallback_used": fallback_used,
            "sql_hash": sql_hash,
            "error": str(exc),
        }

    return {
        "sql": sql,
        "columns": result["columns"],
        "rows": result["rows"],
        "row_count": result["row_count"],
        "fallback_used": fallback_used,
        "sql_hash": sql_hash,
    }
