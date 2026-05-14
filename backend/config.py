"""Centralized environment configuration for ZetaBridge substrate.

Stripped to only what the active runtime uses:
  - GROQ_API_KEY / HF_API_TOKEN / HF_TEXT2SQL_MODEL  — NL→SQL engine
  - DUCKDB_PATH                                        — embedded analytics store
  - LINEAGE_DB_PATH                                    — local SQLite lineage store
  - CORS_ORIGINS                                       — explicit allowlist (no wildcard)
  - LOG_LEVEL

Removed: SNOWFLAKE_*, DATABRICKS_*, GRAVITINO_URL, MARQUEZ_URL,
         DATABASE_URL, USE_LEGACY_STORE — none of these are used.
"""

import json
import os

from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── NL→SQL engine ────────────────────────────────────────────────────────
    GROQ_API_KEY: str = os.environ.get("GROQ_API_KEY", "")
    HF_API_TOKEN: str = os.environ.get("HF_API_TOKEN", "")
    HF_TEXT2SQL_MODEL: str = os.environ.get(
        "HF_TEXT2SQL_MODEL",
        "Snowflake/Arctic-Text2SQL-R1-7B",
    )

    # ── Storage ───────────────────────────────────────────────────────────────
    DUCKDB_PATH: str = os.environ.get(
        "DUCKDB_PATH",
        os.path.join(os.path.dirname(__file__), "data", "biotech", "biotech.duckdb"),
    )
    LINEAGE_DB_PATH: str = os.environ.get(
        "LINEAGE_DB_PATH",
        os.path.join(os.path.dirname(__file__), "data", "lineage.sqlite"),
    )

    # ── CORS — explicit allowlist, never wildcard ─────────────────────────────
    # Set CORS_ORIGINS as a JSON array string, e.g.:
    #   CORS_ORIGINS='["https://myapp.example.com","http://localhost:5173"]'
    # Defaults to localhost dev only.
    CORS_ORIGINS: list[str] = json.loads(
        os.environ.get("CORS_ORIGINS", '["http://localhost:5173"]')
    )

    # ── Observability ─────────────────────────────────────────────────────────
    LOG_LEVEL: str = os.environ.get("LOG_LEVEL", "INFO")


cfg = Config()
