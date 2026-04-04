"""Databricks SQL warehouse + Unity Catalog listing via SDK."""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger("zetabridge.databricks")

try:
    from databricks import sql as dbsql
    from databricks.sdk import WorkspaceClient
except ImportError:
    dbsql = None
    WorkspaceClient = None

from config import cfg


class DatabricksConnector:
    def get_conn(self):
        if dbsql is None:
            raise RuntimeError("databricks-sql-connector not installed")
        if not cfg.DATABRICKS_HOST or not cfg.DATABRICKS_TOKEN:
            raise RuntimeError("Databricks credentials not configured")
        return dbsql.connect(
            server_hostname=cfg.DATABRICKS_HOST,
            http_path=cfg.DATABRICKS_HTTP_PATH,
            access_token=cfg.DATABRICKS_TOKEN,
        )

    def list_unity_catalog_tables(self, max_results: int = 50) -> list[dict[str, Any]]:
        if WorkspaceClient is None or not cfg.DATABRICKS_HOST or not cfg.DATABRICKS_TOKEN:
            return []
        try:
            w = WorkspaceClient(host="https://" + cfg.DATABRICKS_HOST.replace("https://", ""), token=cfg.DATABRICKS_TOKEN)
            tables: list[dict[str, Any]] = []
            count = 0
            for t in w.tables.list(
                catalog_name=cfg.DATABRICKS_CATALOG,
                schema_name=cfg.DATABRICKS_SCHEMA,
            ):
                tables.append(
                    {
                        "name": t.name,
                        "catalog": t.catalog_name,
                        "schema": t.schema_name,
                        "type": str(t.table_type),
                        "format": str(t.data_source_format),
                    }
                )
                count += 1
                if count >= max_results:
                    break
            return tables
        except Exception as exc:
            log.warning("Databricks list_unity_catalog_tables failed: %s", exc)
            return []

    def get_schema(self, table: str) -> list[dict[str, Any]]:
        if dbsql is None:
            return []
        fq = "{}.{}.{}".format(cfg.DATABRICKS_CATALOG, cfg.DATABRICKS_SCHEMA, table)
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute("DESCRIBE TABLE " + fq)
            cols = cur.fetchall()
            cur.close()
            conn.close()
            return [{"name": r[0], "type": r[1]} for r in cols]
        except Exception as exc:
            log.warning("Databricks get_schema failed: %s", exc)
            return []

    def run_query(self, sql_str: str) -> list[dict[str, Any]]:
        if dbsql is None:
            return []
        try:
            conn = self.get_conn()
            cur = conn.cursor()
            cur.execute(sql_str)
            cols = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchall()
            cur.close()
            conn.close()
            return [dict(zip(cols, row)) for row in rows]
        except Exception as exc:
            log.warning("Databricks run_query failed: %s", exc)
            raise

    def test_connection(self) -> bool:
        if dbsql is None or not cfg.DATABRICKS_HOST or not cfg.DATABRICKS_TOKEN:
            return False
        try:
            self.run_query("SELECT 1")
            return True
        except Exception:
            return False
