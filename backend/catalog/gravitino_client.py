"""Gravitino REST client — metalake, catalogs, table listing."""

from __future__ import annotations

import logging
from typing import Any

import requests

from config import cfg

log = logging.getLogger("zetabridge.gravitino")

METALAKE = "zetabridge"


class GravitinoClient:
    def __init__(self, base: str | None = None):
        self.base = (base or cfg.GRAVITINO_URL).rstrip("/")
        self._ml = self.base + "/api/metalakes"

    def setup_metalake(self) -> bool:
        try:
            r = requests.post(
                self._ml,
                json={
                    "name": METALAKE,
                    "comment": "ZetaBridge unified catalog",
                    "properties": {},
                },
                timeout=15,
            )
            return r.status_code in (200, 201, 409)
        except Exception as exc:
            log.warning("Gravitino setup_metalake failed: %s", exc)
            return False

    def register_snowflake_catalog(self) -> bool:
        if not cfg.SNOWFLAKE_ACCOUNT or not cfg.SNOWFLAKE_USER:
            return False
        try:
            url = "{}/{}/catalogs".format(self._ml, METALAKE)
            r = requests.post(
                url,
                json={
                    "name": "snowflake",
                    "type": "RELATIONAL",
                    "provider": "jdbc",
                    "comment": "Snowflake production warehouse",
                    "properties": {
                        "jdbc-driver": "net.snowflake.client.jdbc.SnowflakeDriver",
                        "jdbc-url": "jdbc:snowflake://{}.snowflakecomputing.com".format(
                            cfg.SNOWFLAKE_ACCOUNT
                        ),
                        "jdbc-user": cfg.SNOWFLAKE_USER,
                        "jdbc-password": cfg.SNOWFLAKE_PASSWORD or "",
                    },
                },
                timeout=30,
            )
            return r.status_code in (200, 201, 409)
        except Exception as exc:
            log.warning("Gravitino register_snowflake_catalog failed: %s", exc)
            return False

    def register_databricks_catalog(self) -> bool:
        if not cfg.DATABRICKS_HOST or not cfg.DATABRICKS_TOKEN:
            return False
        try:
            host = cfg.DATABRICKS_HOST.replace("https://", "")
            http_path = cfg.DATABRICKS_HTTP_PATH
            url = "{}/{}/catalogs".format(self._ml, METALAKE)
            jdbc_url = (
                "jdbc:spark://{}:443/default;transportMode=http;ssl=1;httpPath={}".format(
                    host, http_path
                )
            )
            r = requests.post(
                url,
                json={
                    "name": "databricks",
                    "type": "RELATIONAL",
                    "provider": "jdbc",
                    "comment": "Databricks Unity Catalog",
                    "properties": {
                        "jdbc-driver": "com.simba.spark.jdbc.Driver",
                        "jdbc-url": jdbc_url,
                        "jdbc-user": "token",
                        "jdbc-password": cfg.DATABRICKS_TOKEN,
                    },
                },
                timeout=30,
            )
            return r.status_code in (200, 201, 409)
        except Exception as exc:
            log.warning("Gravitino register_databricks_catalog failed: %s", exc)
            return False

    def list_all_tables(self) -> list[dict[str, Any]]:
        try:
            catalogs = (
                requests.get(
                    "{}/{}/catalogs".format(self._ml, METALAKE),
                    timeout=15,
                )
                .json()
                .get("catalogs", [])
            )
            tables: list[dict[str, Any]] = []
            for cat in catalogs:
                cname = cat.get("name")
                if not cname:
                    continue
                try:
                    schemas = (
                        requests.get(
                            "{}/{}/catalogs/{}/schemas".format(
                                self._ml, METALAKE, cname
                            ),
                            timeout=15,
                        )
                        .json()
                        .get("schemas", [])
                    )
                    for schema in schemas:
                        sname = schema.get("name")
                        if not sname:
                            continue
                        tbls = (
                            requests.get(
                                "{}/{}/catalogs/{}/schemas/{}/tables".format(
                                    self._ml, METALAKE, cname, sname
                                ),
                                timeout=15,
                            )
                            .json()
                            .get("tables", [])
                        )
                        for t in tbls:
                            t = dict(t)
                            t["catalog"] = cname
                            t["schema"] = sname
                            tables.append(t)
                except Exception:
                    continue
            return tables
        except Exception as exc:
            log.warning("Gravitino list_all_tables failed: %s", exc)
            return []
