"""Health router — GET /health."""

from __future__ import annotations

import os

from fastapi import APIRouter

from config import cfg

router = APIRouter(tags=["health"])


@router.get("/health")
async def health():
    """Service health check. Returns DuckDB table count and lineage event count."""
    duckdb_tables = 0
    lineage_events = 0

    try:
        import duckdb
        con = duckdb.connect(cfg.DUCKDB_PATH, read_only=True)
        result = con.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchone()
        duckdb_tables = result[0] if result else 0
        con.close()
    except Exception:
        pass

    try:
        import sqlite3
        if os.path.exists(cfg.LINEAGE_DB_PATH):
            con = sqlite3.connect(cfg.LINEAGE_DB_PATH)
            result = con.execute("SELECT COUNT(*) FROM lineage_events").fetchone()
            lineage_events = result[0] if result else 0
            con.close()
    except Exception:
        pass

    return {
        "status": "ok",
        "version": "0.2.0",
        "duckdb_tables": duckdb_tables,
        "lineage_events": lineage_events,
        "duckdb_path": cfg.DUCKDB_PATH,
        "lineage_db_path": cfg.LINEAGE_DB_PATH,
    }
