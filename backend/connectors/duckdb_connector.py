"""DuckDB file-backed connector."""

from __future__ import annotations

import logging
import os
from typing import Any

import duckdb

from config import cfg

log = logging.getLogger("zetabridge.duckdb_connector")

_conn: duckdb.DuckDBPyConnection | None = None


def _path() -> str:
    p = cfg.DUCKDB_PATH
    parent = os.path.dirname(os.path.abspath(p))
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            log.warning("Could not create DuckDB dir %s: %s", parent, exc)
    return p


def _get_conn() -> duckdb.DuckDBPyConnection:
    global _conn
    if _conn is None:
        _conn = duckdb.connect(_path())
    return _conn


class DuckDBConnector:
    def list_tables(self) -> list[dict[str, Any]]:
        try:
            con = _get_conn()
            rows = con.execute(
                "SELECT table_schema, table_name FROM information_schema.tables "
                "WHERE table_type = 'BASE TABLE' ORDER BY 1, 2"
            ).fetchall()
            return [
                {"TABLE_SCHEMA": r[0], "TABLE_NAME": r[1], "name": r[1]}
                for r in rows
            ]
        except Exception as exc:
            log.warning("DuckDB list_tables failed: %s", exc)
            return []

    def get_schema(self, table: str) -> list[dict[str, Any]]:
        try:
            con = _get_conn()
            rows = con.execute("DESCRIBE SELECT * FROM " + table).fetchall()
            return [{"name": r[0], "type": r[1]} for r in rows]
        except Exception as exc:
            log.warning("DuckDB get_schema failed: %s", exc)
            return []

    def run_query(self, sql: str) -> list[dict[str, Any]]:
        con = _get_conn()
        result = con.execute(sql)
        if result.description:
            columns = [d[0] for d in result.description]
            return [dict(zip(columns, row)) for row in result.fetchall()]
        return []

    def test_connection(self) -> bool:
        try:
            _get_conn().execute("SELECT 1")
            return True
        except Exception:
            return False
