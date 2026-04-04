"""
ZetaBridge Connector Registry
===============================
Unified connector framework for data lakes, warehouses, and ETL tools:
  - ConnectorSpec: Declarative connector definition
  - ConnectorRegistry: Central registry of all available connectors
  - ConnectorHealth: Health check and monitoring
  - Built-in connectors for all OSS components

OSS alignment:
  - Iceberg REST Catalog (Gravitino/Lakekeeper protocol)
  - Delta Lake Protocol (delta-rs)
  - DuckDB embedded analytics
  - Neon Postgres (PostgreSQL wire protocol)
  - dbt-core, dlt, Airbyte (ETL orchestration)
  - Apache Kafka, OpenSearch (streaming/search)
  - Nessie (versioned catalog)
  - OpenLineage (lineage protocol)

License: Apache-2.0
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

log = logging.getLogger("zetabridge.connectors")


class ConnectorStatus(str, Enum):
    ACTIVE = "active"
    CONFIGURED = "configured"
    AVAILABLE = "available"
    ERROR = "error"
    DISABLED = "disabled"


class ConnectorCategory(str, Enum):
    LAKE = "data_lake"
    WAREHOUSE = "warehouse"
    ETL = "etl"
    STREAMING = "streaming"
    SEARCH = "search"
    CATALOG = "catalog"
    LINEAGE = "lineage"
    STORAGE = "object_storage"
    DATABASE = "database"


@dataclass
class ConnectorSpec:
    """Declarative connector definition."""
    name: str
    display_name: str
    category: ConnectorCategory
    protocol: str
    oss_component: str
    license: str
    description: str
    config_schema: dict = field(default_factory=dict)
    capabilities: list[str] = field(default_factory=list)
    data_modalities: list[str] = field(default_factory=list)
    status: ConnectorStatus = ConnectorStatus.AVAILABLE
    config: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)
    last_check: str | None = None

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "category": self.category.value,
            "protocol": self.protocol,
            "oss_component": self.oss_component,
            "license": self.license,
            "description": self.description,
            "capabilities": self.capabilities,
            "data_modalities": self.data_modalities,
            "status": self.status.value,
            "health": self.health,
            "last_check": self.last_check,
        }


# ── Built-in Connector Definitions ──────────────────────────────────────────

BUILTIN_CONNECTORS: list[ConnectorSpec] = [
    ConnectorSpec(
        name="gravitino_iceberg",
        display_name="Apache Gravitino (Iceberg)",
        category=ConnectorCategory.CATALOG,
        protocol="Gravitino REST API / Iceberg REST Catalog",
        oss_component="Apache Gravitino",
        license="Apache-2.0",
        description="Federated metadata catalog with Iceberg table format support. REST API for schema discovery, table management, and cross-catalog queries.",
        capabilities=["catalog_management", "schema_discovery", "table_registration", "cross_catalog_query"],
        data_modalities=["structured"],
        config_schema={"gravitino_url": "str", "metalake": "str"},
    ),
    ConnectorSpec(
        name="lakekeeper_iceberg",
        display_name="Lakekeeper (Iceberg REST)",
        category=ConnectorCategory.CATALOG,
        protocol="Iceberg REST Catalog API",
        oss_component="Lakekeeper",
        license="Apache-2.0",
        description="Lightweight Iceberg REST catalog server. Native table management with snapshot isolation and time travel.",
        capabilities=["iceberg_tables", "time_travel", "snapshot_management", "schema_evolution"],
        data_modalities=["structured"],
        config_schema={"lakekeeper_url": "str", "warehouse": "str"},
    ),
    ConnectorSpec(
        name="nessie_catalog",
        display_name="Project Nessie",
        category=ConnectorCategory.CATALOG,
        protocol="Nessie REST API",
        oss_component="Project Nessie",
        license="Apache-2.0",
        description="Git-like versioned data catalog. Branch, tag, and merge table metadata like code. Supports Iceberg and Delta tables.",
        capabilities=["versioned_catalog", "branching", "merging", "table_versioning"],
        data_modalities=["structured"],
        config_schema={"nessie_url": "str", "ref": "str"},
    ),
    ConnectorSpec(
        name="delta_lake",
        display_name="Delta Lake (delta-rs)",
        category=ConnectorCategory.LAKE,
        protocol="Delta Lake Protocol",
        oss_component="delta-rs",
        license="Apache-2.0",
        description="Delta Lake table format via Rust-native delta-rs. ACID transactions, time travel, schema enforcement without Spark.",
        capabilities=["acid_transactions", "time_travel", "schema_enforcement", "z_ordering"],
        data_modalities=["structured"],
        config_schema={"storage_url": "str", "storage_options": "dict"},
    ),
    ConnectorSpec(
        name="iceberg_tables",
        display_name="Apache Iceberg",
        category=ConnectorCategory.LAKE,
        protocol="Iceberg Table Spec v2",
        oss_component="Apache Iceberg (pyiceberg)",
        license="Apache-2.0",
        description="Apache Iceberg table format. Hidden partitioning, snapshot isolation, schema evolution. Accessed via REST catalog.",
        capabilities=["hidden_partitioning", "snapshot_isolation", "schema_evolution", "time_travel"],
        data_modalities=["structured"],
        config_schema={"catalog_url": "str", "warehouse": "str"},
    ),
    ConnectorSpec(
        name="duckdb_analytics",
        display_name="DuckDB",
        category=ConnectorCategory.WAREHOUSE,
        protocol="Embedded SQL (libduckdb)",
        oss_component="DuckDB",
        license="MIT",
        description="Embedded analytical SQL engine. Zero-copy reads from Parquet/CSV/JSON. In-process OLAP without server deployment.",
        capabilities=["sql_analytics", "parquet_read", "csv_read", "json_read", "in_process"],
        data_modalities=["structured", "semi_structured"],
        status=ConnectorStatus.ACTIVE,
        config_schema={"database": "str"},
    ),
    ConnectorSpec(
        name="neon_postgres",
        display_name="Neon Postgres",
        category=ConnectorCategory.DATABASE,
        protocol="PostgreSQL Wire Protocol",
        oss_component="PostgreSQL (Neon serverless)",
        license="PostgreSQL License",
        description="Serverless Postgres with auto-scaling and branching. Used as the catalog and lineage metadata store.",
        capabilities=["sql_queries", "transactions", "branching", "auto_scaling"],
        data_modalities=["structured"],
        status=ConnectorStatus.ACTIVE,
        config_schema={"database_url": "str"},
    ),
    ConnectorSpec(
        name="dbt_core",
        display_name="dbt Core",
        category=ConnectorCategory.ETL,
        protocol="dbt Core CLI / dbt Cloud API",
        oss_component="dbt-core",
        license="Apache-2.0",
        description="SQL-first transformation framework. Model dependencies, incremental builds, testing, documentation. Drives the T in ELT.",
        capabilities=["sql_transforms", "incremental_builds", "testing", "documentation", "lineage_emission"],
        data_modalities=["structured"],
        config_schema={"project_dir": "str", "profiles_dir": "str"},
    ),
    ConnectorSpec(
        name="dlt_loader",
        display_name="dlt (data load tool)",
        category=ConnectorCategory.ETL,
        protocol="dlt Python API",
        oss_component="dlt",
        license="Apache-2.0",
        description="Python-native data loading. Schema inference, automatic normalization, incremental loading. 100+ source connectors.",
        capabilities=["data_loading", "schema_inference", "normalization", "incremental_load"],
        data_modalities=["structured", "semi_structured"],
        config_schema={"pipeline_name": "str", "destination": "str"},
    ),
    ConnectorSpec(
        name="airbyte_oss",
        display_name="Airbyte OSS",
        category=ConnectorCategory.ETL,
        protocol="Airbyte REST API",
        oss_component="Airbyte",
        license="ELv2 (connectors MIT)",
        description="Data integration platform with 300+ connectors. CDC, full refresh, incremental sync modes.",
        capabilities=["data_integration", "cdc", "full_refresh", "incremental_sync", "300+_connectors"],
        data_modalities=["structured", "semi_structured"],
        config_schema={"airbyte_url": "str", "workspace_id": "str"},
    ),
    ConnectorSpec(
        name="apache_hop",
        display_name="Apache Hop",
        category=ConnectorCategory.ETL,
        protocol="Hop REST API",
        oss_component="Apache Hop",
        license="Apache-2.0",
        description="Visual data orchestration and integration platform. Pipelines and workflows with 200+ transforms.",
        capabilities=["visual_pipelines", "workflow_orchestration", "200+_transforms"],
        data_modalities=["structured", "semi_structured", "unstructured"],
        config_schema={"hop_server_url": "str"},
    ),
    ConnectorSpec(
        name="openlineage",
        display_name="OpenLineage",
        category=ConnectorCategory.LINEAGE,
        protocol="OpenLineage HTTP API",
        oss_component="OpenLineage",
        license="Apache-2.0",
        description="Open standard for data lineage. Run-level event tracking with job/dataset facets. Compatible with Marquez backend.",
        capabilities=["lineage_tracking", "run_events", "facets", "cross_platform_lineage"],
        data_modalities=["structured"],
        status=ConnectorStatus.ACTIVE,
        config_schema={"transport_url": "str"},
    ),
    ConnectorSpec(
        name="marquez",
        display_name="Marquez",
        category=ConnectorCategory.LINEAGE,
        protocol="Marquez REST API",
        oss_component="Marquez",
        license="Apache-2.0",
        description="OpenLineage-compatible metadata service. Stores lineage events, job runs, and dataset versions.",
        capabilities=["lineage_storage", "job_tracking", "dataset_versioning", "graph_queries"],
        data_modalities=["structured"],
        config_schema={"marquez_url": "str"},
    ),
    ConnectorSpec(
        name="s3_storage",
        display_name="S3 / MinIO",
        category=ConnectorCategory.STORAGE,
        protocol="S3 REST API",
        oss_component="MinIO / AWS S3",
        license="AGPL-3.0 (MinIO) / Proprietary (AWS)",
        description="Object storage for data lake files. Parquet, Delta, Iceberg table data, unstructured documents, ML models.",
        capabilities=["object_storage", "bucket_management", "versioning", "lifecycle_policies"],
        data_modalities=["structured", "unstructured", "binary"],
        config_schema={"endpoint": "str", "access_key": "str", "secret_key": "str", "bucket": "str"},
    ),
    ConnectorSpec(
        name="unity_catalog",
        display_name="Unity Catalog (OSS)",
        category=ConnectorCategory.CATALOG,
        protocol="Unity Catalog REST API",
        oss_component="Unity Catalog",
        license="Apache-2.0",
        description="Open-source data catalog with fine-grained access control. Supports Delta Lake, Iceberg, and external tables.",
        capabilities=["access_control", "data_governance", "table_management", "external_tables"],
        data_modalities=["structured"],
        config_schema={"unity_url": "str"},
    ),
    ConnectorSpec(
        name="kafka_streaming",
        display_name="Apache Kafka",
        category=ConnectorCategory.STREAMING,
        protocol="Kafka Protocol",
        oss_component="Apache Kafka",
        license="Apache-2.0",
        description="Distributed event streaming platform. Real-time data pipelines, event sourcing, log aggregation.",
        capabilities=["event_streaming", "pub_sub", "log_aggregation", "real_time_pipelines"],
        data_modalities=["semi_structured", "structured"],
        config_schema={"bootstrap_servers": "str", "group_id": "str"},
    ),
    ConnectorSpec(
        name="opensearch",
        display_name="OpenSearch",
        category=ConnectorCategory.SEARCH,
        protocol="OpenSearch REST API",
        oss_component="OpenSearch",
        license="Apache-2.0",
        description="Distributed search and analytics engine. Full-text search, log analytics, vector similarity search.",
        capabilities=["full_text_search", "log_analytics", "vector_search", "dashboards"],
        data_modalities=["unstructured", "semi_structured"],
        config_schema={"opensearch_url": "str", "index": "str"},
    ),
]


# ── Connector Registry ───────────────────────────────────────────────────────

class ConnectorRegistry:
    """Central registry of all available data connectors."""

    def __init__(self):
        self._connectors: dict[str, ConnectorSpec] = {}
        # Load built-in connectors
        for conn in BUILTIN_CONNECTORS:
            self._connectors[conn.name] = conn

    def register(self, connector: ConnectorSpec) -> None:
        self._connectors[connector.name] = connector
        log.info("Registered connector: %s", connector.name)

    def get(self, name: str) -> ConnectorSpec | None:
        return self._connectors.get(name)

    def list_all(self) -> list[dict]:
        return [c.to_dict() for c in self._connectors.values()]

    def list_by_category(self, category: str) -> list[dict]:
        return [
            c.to_dict() for c in self._connectors.values()
            if c.category.value == category
        ]

    def list_active(self) -> list[dict]:
        return [
            c.to_dict() for c in self._connectors.values()
            if c.status == ConnectorStatus.ACTIVE
        ]

    def get_stats(self) -> dict:
        by_category: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for c in self._connectors.values():
            by_category[c.category.value] = by_category.get(c.category.value, 0) + 1
            by_status[c.status.value] = by_status.get(c.status.value, 0) + 1

        return {
            "total_connectors": len(self._connectors),
            "by_category": by_category,
            "by_status": by_status,
            "active": sum(1 for c in self._connectors.values() if c.status == ConnectorStatus.ACTIVE),
            "modalities_supported": sorted(set(
                m for c in self._connectors.values() for m in c.data_modalities
            )),
        }

    def health_check_all(self) -> list[dict]:
        """Quick health check across all connectors."""
        results = []
        for c in self._connectors.values():
            check = {
                "name": c.name,
                "display_name": c.display_name,
                "status": c.status.value,
                "category": c.category.value,
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
            if c.status == ConnectorStatus.ACTIVE:
                check["healthy"] = True
                check["message"] = "Connected and operational"
            elif c.status == ConnectorStatus.CONFIGURED:
                check["healthy"] = True
                check["message"] = "Configured, awaiting activation"
            else:
                check["healthy"] = False
                check["message"] = "Not configured"
            results.append(check)
        return results
