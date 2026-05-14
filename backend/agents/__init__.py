"""ZetaBridge Agent Framework — substrate module.

Active exports:
  - BaseAgent, AgentContext, AgentResult, AgentStatus, DataModality, ToolRegistry
    (from base.py — lifecycle, lineage emission, tool registry)
  - ask, TableSpec, ColumnSpec, UnsafeSQLError
    (from nl_to_sql.py — canonical NL→SQL engine)

Quarantined (not exported):
  - specialized.py  → backend/quarantine/specialized.py
  - orchestrator.py → backend/quarantine/orchestrator.py
"""

from .base import (
    BaseAgent,
    AgentContext,
    AgentResult,
    AgentStatus,
    DataModality,
    ToolRegistry,
)
from .nl_to_sql import ask, TableSpec, ColumnSpec, UnsafeSQLError

__all__ = [
    "BaseAgent",
    "AgentContext",
    "AgentResult",
    "AgentStatus",
    "DataModality",
    "ToolRegistry",
    "ask",
    "TableSpec",
    "ColumnSpec",
    "UnsafeSQLError",
]
