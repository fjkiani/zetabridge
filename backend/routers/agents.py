"""Agents router — /api/agents endpoints.

Surfaces the live agent framework (orchestrator + registered agents + tool
registry) to the front-end Agents page and the Overview dashboard.

  - GET /api/agents                    -> roster of registered agents
  - GET /api/agents/stats              -> aggregated per-agent run stats
  - GET /api/agents/tools              -> the tool registry (name/desc/category)
  - GET /api/agents/execution-history  -> recent agent runs (persisted to disk)

Execution history is persisted to a JSON file so it survives process restarts
(the orchestrator keeps it in-memory otherwise).
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/api/agents", tags=["agents"])

_orchestrator = None
_orch_lock = threading.Lock()

# Persist execution history next to the outcome anchors (baked/mounted dir).
_HIST_FILE = os.environ.get(
    "ZETA_AGENT_HISTORY",
    os.path.join(os.path.dirname(__file__), "..", "data", "anchors", "agent_execution_history.json"),
)


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        with _orch_lock:
            if _orchestrator is None:
                from agents.orchestrator import create_orchestrator
                _orchestrator = create_orchestrator()
    return _orchestrator


def _load_history() -> list[dict]:
    try:
        if os.path.exists(_HIST_FILE):
            with open(_HIST_FILE) as f:
                return json.load(f)
    except Exception:
        pass
    return []


def _save_history(records: list[dict]) -> None:
    try:
        os.makedirs(os.path.dirname(_HIST_FILE), exist_ok=True)
        with open(_HIST_FILE, "w") as f:
            json.dump(records[-200:], f, default=str)
    except Exception:
        pass


@router.get("")
def list_agents() -> list:
    """Roster of registered agents (array, as the front-end expects)."""
    orch = _get_orchestrator()
    agents = []
    for name, agent in getattr(orch, "_agents", {}).items():
        try:
            agents.append({
                "name": agent.name,
                "description": agent.description,
                "capabilities": list(agent.capabilities),
                "n_capabilities": len(agent.capabilities),
                "max_iterations": agent.max_iterations,
                "timeout_seconds": agent.timeout_seconds,
                "status": "active",
            })
        except Exception:
            agents.append({"name": name, "description": "", "capabilities": [],
                           "n_capabilities": 0, "status": "active"})
    return agents


@router.get("/stats")
def agent_stats() -> dict:
    """Aggregated per-agent performance stats across executions."""
    orch = _get_orchestrator()
    mem = orch.get_execution_history(limit=500)
    disk = _load_history()
    stats = orch.get_agent_stats()
    return {"stats": stats, "n_executions_memory": len(mem), "n_executions_persisted": len(disk)}


# Static tool catalog — mirrors the ToolSpec registrations in legacy_app.py so
# the Tools tab is populated even when the legacy store (which registers them)
# is disabled. Category-grouped.
_STATIC_TOOLS = [
    {"name": "catalog_list_tables", "description": "List all tables in the federated catalog", "category": "catalog"},
    {"name": "catalog_describe_table", "description": "Describe a table's schema + columns", "category": "catalog"},
    {"name": "duckdb_query", "description": "Execute SQL on the embedded DuckDB analytics engine", "category": "query"},
    {"name": "build_schema_context", "description": "Build DDL schema context for Text2SQL prompts", "category": "query"},
    {"name": "text2sql", "description": "Generate SQL from natural language", "category": "query"},
    {"name": "lineage_graph", "description": "Build a lineage graph from the federated KG", "category": "lineage"},
    {"name": "lineage_list_events", "description": "List lineage/ingestion events newest first", "category": "lineage"},
    {"name": "lineage_emit", "description": "Emit an agent-level lineage event", "category": "lineage"},
    {"name": "pg_health_check", "description": "Check Postgres connectivity", "category": "connector"},
    {"name": "synapse_query", "description": "Query the Synapse/MSK SPECTRUM source", "category": "connector"},
    {"name": "pds_interrogate", "description": "Interrogate the PDS / SAS Viya CAS trials", "category": "connector"},
    {"name": "ega_list_files", "description": "List EGA BriTROC dataset files", "category": "connector"},
]


@router.get("/tools")
def list_tools() -> dict:
    """The tool registry (live if registered, else the static catalog)."""
    from agents.base import ToolRegistry
    tools = ToolRegistry.to_schema_list()
    if not tools:
        tools = _STATIC_TOOLS
    return {"n_tools": len(tools), "tools": tools}


def _normalize_exec(rec: dict) -> dict:
    """Map an orchestrator execution record to the front-end activity shape."""
    plan = rec.get("plan", {})
    results = rec.get("results", {})
    succeeded = sum(1 for r in results.values() if r.get("status") == "success")
    benches = rec.get("benchmarks", [])
    total_latency = sum(b.get("latency_ms", 0) for b in benches)
    return {
        "plan_id": plan.get("plan_id") or rec.get("plan_id") or rec.get("timestamp"),
        "intent": rec.get("intent", plan.get("intent", "query")),
        "user_input": rec.get("user_input", ""),
        "total_tasks": len(results) or len(plan.get("tasks", [])),
        "succeeded": succeeded,
        "total_latency_ms": round(total_latency),
        "timestamp": rec.get("timestamp"),
    }


@router.get("/execution-history")
def execution_history(limit: int = 50) -> list:
    """Recent agent executions (array). Merges live memory with persisted history."""
    orch = _get_orchestrator()
    mem = orch.get_execution_history(limit=limit)
    if mem:
        existing = _load_history()
        existing.extend(mem)
        _save_history(existing)
        return [_normalize_exec(r) for r in mem]
    disk = _load_history()
    return [_normalize_exec(r) for r in disk[-limit:]]
