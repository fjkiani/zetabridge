"""Snowflake JDBC-style access via snowflake-connector-python."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("zetabridge.snowflake")

try:
    import snowflake.connector as sc
    from snowflake.connector import DictCursor
except ImportError:
    sc = None
    DictCursor = None

from config import cfg


class SnowflakeConnector:
    def connect(self):
        if sc is None:
            raise RuntimeError("snowflake-connector-python not installed")
        if not cfg.SNOWFLAKE_ACCOUNT or not cfg.SNOWFLAKE_USER:
            raise RuntimeError("Snowflake credentials not configured")
        return sc.connect(
            account=cfg.SNOWFLAKE_ACCOUNT,
            user=cfg.SNOWFLAKE_USER,
            password=cfg.SNOWFLAKE_PASSWORD or "",
            warehouse=cfg.SNOWFLAKE_WAREHOUSE,
            database=cfg.SNOWFLAKE_DATABASE,
            schema=cfg.SNOWFLAKE_SCHEMA,
        )

    def list_tables(self) -> list[dict[str, Any]]:
        if sc is None:
            return []
        try:
            conn = self.connect()
            cur = conn.cursor(DictCursor)
            cur.execute(
                "SELECT TABLE_CATALOG, TABLE_SCHEMA, TABLE_NAME "
                "FROM INFORMATION_SCHEMA.TABLES "
                "WHERE TABLE_TYPE = 'BASE TABLE'"
            )
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return rows or []
        except Exception as exc:
            log.warning("Snowflake list_tables failed: %s", exc)
            return []

    def get_schema(self, table: str) -> list[dict[str, Any]]:
        if sc is None:
            return []
        try:
            conn = self.connect()
            cur = conn.cursor(DictCursor)
            cur.execute("DESCRIBE TABLE " + table)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return rows or []
        except Exception as exc:
            log.warning("Snowflake get_schema failed: %s", exc)
            return []

    def run_query(self, sql: str) -> list[dict[str, Any]]:
        if sc is None:
            return []
        try:
            conn = self.connect()
            cur = conn.cursor(DictCursor)
            cur.execute(sql)
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return rows or []
        except Exception as exc:
            log.warning("Snowflake run_query failed: %s", exc)
            raise

    def test_connection(self) -> bool:
        if sc is None:
            return False
        try:
            conn = self.connect()
            conn.cursor().execute("SELECT 1")
            conn.close()
            return True
        except Exception:
            return False
