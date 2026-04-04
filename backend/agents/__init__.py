"""ZetaBridge Agent Framework — Multi-agent orchestration for data platforms."""
from .base import BaseAgent, AgentContext, AgentResult, AgentStatus, DataModality, ToolRegistry
from .specialized import (
    CatalogAgent, QueryAgent, LineageAgent,
    ETLAgent, DataQualityAgent, ConnectorAgent,
)
from .orchestrator import Orchestrator, create_orchestrator, classify_intent, Intent

__all__ = [
    "BaseAgent", "AgentContext", "AgentResult", "AgentStatus", "DataModality", "ToolRegistry",
    "CatalogAgent", "QueryAgent", "LineageAgent", "ETLAgent", "DataQualityAgent", "ConnectorAgent",
    "Orchestrator", "create_orchestrator", "classify_intent", "Intent",
]
