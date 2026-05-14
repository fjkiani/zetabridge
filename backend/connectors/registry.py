"""
ZetaBridge Connector Registry — honest, minimal implementation.

Key design principle: every connector has a `wired` field.
  wired=True  → actually connected; health checks run real pings
  wired=False → declared extension point only; health returns not_wired status

This replaces the original 17-connector theater where all connectors
appeared ACTIVE but only DuckDB was actually wired.

Active connectors (wired=True):
  - duckdb        — embedded analytics store (SELECT 1 health check)
  - local_lineage — embedded SQLite lineage store (SELECT 1 health check)

Extension points (wired=False, for Brenus integration):
  - clinicaltrials_gov — ClinicalTrials.gov API v2
  - pubmed             — PubMed / NCBI Entrez
  - sec_edgar          — SEC EDGAR full-text search

To add a Brenus source connector:
  1. Add a ConnectorSpec with wired=False and category=ConnectorCategory.SOURCE
  2. When the connector is implemented, set wired=True and add a health_check()
  3. See connectors/README.md for the integration pattern
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Optional

log = logging.getLogger("zetabridge.connectors")


# ── Enums ─────────────────────────────────────────────────────────────────────

class ConnectorStatus(str, Enum):
    ACTIVE = "active"           # wired=True, last health check passed
    CONFIGURED = "configured"   # wired=True, not yet health-checked
    AVAILABLE = "available"     # wired=False, declared extension point
    ERROR = "error"             # wired=True, last health check failed
    DISABLED = "disabled"       # explicitly turned off


class ConnectorCategory(str, Enum):
    DATABASE = "database"       # embedded stores (DuckDB, SQLite)
    LINEAGE = "lineage"         # lineage stores
    SOURCE = "source"           # external data sources (Brenus extension points)
    WAREHOUSE = "warehouse"     # cloud warehouses (future)
    LAKE = "data_lake"          # data lakes (future)


# ── Data contract ─────────────────────────────────────────────────────────────

@dataclass
class ConnectorSpec:
    """
    Declarative connector definition.

    The `wired` field is the critical addition over the original registry.
    Any connector with wired=False is honest about its status — it is a
    declared extension point, not a production connection.
    """
    name: str
    display_name: str
    category: ConnectorCategory
    protocol: str
    description: str
    capabilities: list[str] = field(default_factory=list)
    status: ConnectorStatus = ConnectorStatus.AVAILABLE
    wired: bool = False                    # True = actually connected
    config: dict = field(default_factory=dict)
    health: dict = field(default_factory=dict)
    last_check: Optional[str] = None
    _health_fn: Optional[Callable[[], dict]] = field(default=None, repr=False)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "category": self.category.value,
            "protocol": self.protocol,
            "description": self.description,
            "capabilities": self.capabilities,
            "status": self.status.value,
            "wired": self.wired,
            "health": self.health,
            "last_check": self.last_check,
        }

    def run_health_check(self) -> dict:
        """
        Run a real health check for wired=True connectors.
        For wired=False, return a not_wired status immediately.
        """
        if not self.wired:
            result = {
                "status": "not_wired",
                "message": "Declared extension point only — not connected",
                "wired": False,
            }
            self.health = result
            return result

        if self._health_fn is None:
            result = {"status": "unknown", "message": "No health check function registered"}
            self.health = result
            return result

        t0 = time.monotonic()
        try:
            result = self._health_fn()
            result["latency_ms"] = round((time.monotonic() - t0) * 1000, 2)
            result["wired"] = True
            self.status = ConnectorStatus.ACTIVE
        except Exception as exc:
            result = {
                "status": "error",
                "error": str(exc),
                "latency_ms": round((time.monotonic() - t0) * 1000, 2),
                "wired": True,
            }
            self.status = ConnectorStatus.ERROR

        self.health = result
        self.last_check = datetime.now(timezone.utc).isoformat()
        return result


# ── Health check functions ────────────────────────────────────────────────────

def _duckdb_health() -> dict:
    """Ping DuckDB with SELECT 1."""
    import duckdb
    from config import cfg
    con = duckdb.connect(cfg.DUCKDB_PATH, read_only=True)
    try:
        con.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    finally:
        con.close()


def _sqlite_lineage_health() -> dict:
    """Ping SQLite lineage store with SELECT 1."""
    import sqlite3
    from config import cfg
    con = sqlite3.connect(cfg.LINEAGE_DB_PATH)
    try:
        con.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    finally:
        con.close()


# ── Registry ──────────────────────────────────────────────────────────────────

class ConnectorRegistry:
    """Central registry of all connectors."""

    def __init__(self):
        self._connectors: dict[str, ConnectorSpec] = {}

    def register(self, spec: ConnectorSpec) -> None:
        self._connectors[spec.name] = spec
        log.debug("Registered connector: %s (wired=%s)", spec.name, spec.wired)

    def get(self, name: str) -> Optional[ConnectorSpec]:
        return self._connectors.get(name)

    def list_all(self) -> list[ConnectorSpec]:
        return list(self._connectors.values())

    def list_wired(self) -> list[ConnectorSpec]:
        return [c for c in self._connectors.values() if c.wired]

    def list_extension_points(self) -> list[ConnectorSpec]:
        return [c for c in self._connectors.values() if not c.wired]

    def health_check_all(self) -> dict[str, dict]:
        return {name: spec.run_health_check() for name, spec in self._connectors.items()}


# ── Default registry ──────────────────────────────────────────────────────────

registry = ConnectorRegistry()

# Active connectors (wired=True)
registry.register(ConnectorSpec(
    name="duckdb",
    display_name="DuckDB (embedded)",
    category=ConnectorCategory.DATABASE,
    protocol="duckdb",
    description="Embedded analytical database. Primary query store for substrate tables.",
    capabilities=["query", "schema-discovery", "nl-to-sql"],
    status=ConnectorStatus.ACTIVE,
    wired=True,
    _health_fn=_duckdb_health,
))

registry.register(ConnectorSpec(
    name="local_lineage",
    display_name="Local Lineage Store (SQLite)",
    category=ConnectorCategory.LINEAGE,
    protocol="sqlite",
    description="Embedded SQLite lineage store. OpenLineage RunEvent shape. No external server required.",
    capabilities=["lineage-emit", "lineage-query", "graph"],
    status=ConnectorStatus.ACTIVE,
    wired=True,
    _health_fn=_sqlite_lineage_health,
))

# Extension points (wired=False) — Brenus source registry stubs
registry.register(ConnectorSpec(
    name="clinicaltrials_gov",
    display_name="ClinicalTrials.gov API v2",
    category=ConnectorCategory.SOURCE,
    protocol="http-rest",
    description=(
        "BRENUS EXTENSION POINT. ClinicalTrials.gov API v2 for trial metadata retrieval. "
        "NCT ID is the primary key. Re-fetch + fingerprint required for T1-SCI admissibility."
    ),
    capabilities=["trial-metadata", "enrollment-data", "eligibility-criteria"],
    status=ConnectorStatus.AVAILABLE,
    wired=False,
))

registry.register(ConnectorSpec(
    name="pubmed",
    display_name="PubMed / NCBI Entrez",
    category=ConnectorCategory.SOURCE,
    protocol="http-rest",
    description=(
        "BRENUS EXTENSION POINT. PubMed for peer-reviewed publication retrieval. "
        "PMID is the primary key. Fingerprint = first_author:journal:year."
    ),
    capabilities=["publication-retrieval", "abstract-extraction", "citation-lookup"],
    status=ConnectorStatus.AVAILABLE,
    wired=False,
))

registry.register(ConnectorSpec(
    name="sec_edgar",
    display_name="SEC EDGAR",
    category=ConnectorCategory.SOURCE,
    protocol="http-rest",
    description=(
        "BRENUS EXTENSION POINT. SEC EDGAR full-text search for corporate filings. "
        "Accession number is the primary key. T1-CORP tier source."
    ),
    capabilities=["filing-retrieval", "10-k", "8-k", "proxy-statement"],
    status=ConnectorStatus.AVAILABLE,
    wired=False,
))
