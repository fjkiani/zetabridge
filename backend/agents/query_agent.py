"""NL→SQL via Hugging Face InferenceClient (Arctic Text2SQL) with HTTP fallbacks."""

from __future__ import annotations

import logging
import re
from typing import Any

import httpx
from huggingface_hub import InferenceClient

from catalog.gravitino_client import GravitinoClient
from config import cfg
from connectors.databricks_connector import DatabricksConnector
from connectors.duckdb_connector import DuckDBConnector
from connectors.snowflake_connector import SnowflakeConnector

log = logging.getLogger("zetabridge.query_agent")

_client: InferenceClient | None = None


def get_inference_client() -> InferenceClient | None:
    global _client
    if not cfg.HF_API_TOKEN:
        return None
    if _client is None:
        _client = InferenceClient(token=cfg.HF_API_TOKEN)
    return _client


def _parse_generated_sql(raw: str) -> str:
    """Extract SQL from model output (plain text or fenced ```sql blocks)."""
    s = (raw or "").strip()
    m = re.search(r"```(?:sql)?\s*([\s\S]*?)```", s, re.I)
    if m:
        s = m.group(1).strip()
    s = s.rstrip(";").strip()
    if not s:
        return ""
    return s + ";"


def _hf_text_generation_api(prompt: str) -> str:
    """Direct Inference API (same contract as legacy httpx path)."""
    model = cfg.HF_TEXT2SQL_MODEL
    url = "https://api-inference.huggingface.co/models/" + model
    with httpx.Client(timeout=120.0) as client:
        resp = client.post(
            url,
            headers={"Authorization": "Bearer " + cfg.HF_API_TOKEN},
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
        item = data[0]
        if isinstance(item, dict):
            return str(item.get("generated_text", ""))
        return str(item)
    if isinstance(data, dict):
        return str(data.get("generated_text", ""))
    return ""


def generate_sql_from_prompt(prompt: str) -> str:
    """Try InferenceClient.text_generation, then same call with model= kwarg, then HTTP API."""
    if not cfg.HF_API_TOKEN:
        return ""

    model_id = cfg.HF_TEXT2SQL_MODEL
    client = get_inference_client()

    if client is not None:
        try:
            raw = client.text_generation(
                prompt,
                model=model_id,
                max_new_tokens=300,
                temperature=0.1,
                stop_sequences=[";", "\n\n"],
            )
            sql = _parse_generated_sql(str(raw))
            if sql and sql != ";":
                return sql
        except TypeError:
            try:
                raw = client.text_generation(
                    prompt,
                    max_new_tokens=300,
                    temperature=0.1,
                    stop_sequences=[";", "\n\n"],
                )
                sql = _parse_generated_sql(str(raw))
                if sql and sql != ";":
                    return sql
            except Exception as exc:
                log.warning("InferenceClient text_generation (no model kwarg) failed: %s", exc)
        except Exception as exc:
            log.warning("InferenceClient text_generation failed: %s", exc)

    try:
        raw = _hf_text_generation_api(prompt)
        sql = _parse_generated_sql(raw)
        if sql and sql != ";":
            return sql
    except Exception as exc:
        log.warning("HF inference HTTP fallback failed: %s", exc)

    return ""


def get_schema_context(source: str) -> str:
    lines: list[str] = []
    try:
        if source == "snowflake":
            tables = SnowflakeConnector().list_tables()
        elif source == "databricks":
            tables = DatabricksConnector().list_unity_catalog_tables()
        elif source == "unified":
            tables = GravitinoClient().list_all_tables()
        else:
            tables = DuckDBConnector().list_tables()
    except Exception as exc:
        log.warning("get_schema_context failed for %s: %s", source, exc)
        tables = []

    for t in tables[:30]:
        ts = t.get("TABLE_SCHEMA")
        tn = t.get("TABLE_NAME") or t.get("name")
        if ts and tn:
            name = str(ts) + "." + str(tn)
        else:
            name = str(tn or t.get("name", "unknown"))
        lines.append("- " + name)
    return "Available tables:\n" + "\n".join(lines)


def nl_to_sql(question: str, source: str = "duckdb") -> dict[str, Any]:
    schema_ctx = get_schema_context(source)
    prompt = (
        "### Task\nGenerate a SQL query to answer: `"
        + question
        + "`\n\n### Database schema\n"
        + schema_ctx
        + "\n\n### Answer\n```sql\n"
    )
    sql = generate_sql_from_prompt(prompt)
    if not sql or sql == ";":
        sql = _heuristic_sql(question, schema_ctx)
    return {"sql": sql, "source": source, "schema_context": schema_ctx}


def _heuristic_sql(question: str, schema_context: str) -> str:
    """Last-resort SQL when HF is unavailable (DuckDB-friendly quoted identifiers)."""
    q = question.lower()
    tables: list[str] = []
    for line in schema_context.split("\n"):
        if line.strip().startswith("-"):
            name = line.strip().lstrip("-").strip()
            if name:
                tables.append(name)
    if not tables:
        return "SELECT 1 AS ok;"
    best = tables[0]
    for t in tables:
        for word in q.split():
            if len(word) > 2 and word in t.lower():
                best = t
                break
    parts = best.replace('"', "").split(".", 1)
    if len(parts) == 2:
        fq = '"' + parts[0] + '"."' + parts[1] + '"'
    else:
        fq = '"' + parts[0] + '"'
    if any(w in q for w in ["count", "how many", "total"]):
        return "SELECT COUNT(*) AS total FROM " + fq + ";"
    return "SELECT * FROM " + fq + " LIMIT 20;"


def execute_nl_query(question: str, source: str = "duckdb") -> dict[str, Any]:
    result = nl_to_sql(question, source)
    sql = result["sql"]
    rows: list[dict[str, Any]] = []
    err: str | None = None
    try:
        if source == "snowflake":
            rows = SnowflakeConnector().run_query(sql)
        elif source == "databricks":
            rows = DatabricksConnector().run_query(sql)
        else:
            rows = DuckDBConnector().run_query(sql)
    except Exception as exc:
        err = str(exc)
        rows = []
    return {"sql": sql, "rows": rows, "source": source, "error": err}


def extract_input_tables(sql: str) -> list[str]:
    found = re.findall(r"(?i)FROM\s+([`\w.]+)", sql)
    return list({t.strip("`") for t in found})
