"""
ZetaBridge Agent Framework — Base Agent & Tool Registry
========================================================
Provides the foundation for all ZetaBridge agents:
  - BaseAgent: abstract agent with lifecycle hooks, lineage emission,
    benchmark telemetry, and tool access
  - ToolRegistry: global registry of callable tools agents can invoke
  - AgentResult: standardized output from any agent execution
  - AgentContext: shared state passed through agent chains

OSS alignment:
  - OpenLineage protocol for agent-level lineage
  - Inspired by LangChain/CrewAI agent patterns but zero-dependency

License: Apache-2.0
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import traceback
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable, Awaitable

log = logging.getLogger("zetabridge.agents")


# ── Enums ────────────────────────────────────────────────────────────────────

class AgentStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class DataModality(str, Enum):
    """Multi-data-modal support."""
    STRUCTURED = "structured"       # SQL tables, CSV, Parquet
    UNSTRUCTURED = "unstructured"   # Documents, PDFs, text blobs
    SEMI_STRUCTURED = "semi_structured"  # JSON, XML, logs
    BINARY = "binary"               # Images, audio, video
    EMBEDDING = "embedding"         # Vector embeddings


# ── Data Structures ──────────────────────────────────────────────────────────

@dataclass
class ToolSpec:
    """A tool that agents can invoke."""
    name: str
    description: str
    handler: Callable[..., Awaitable[Any]]
    input_schema: dict = field(default_factory=dict)
    output_schema: dict = field(default_factory=dict)
    category: str = "general"  # catalog | query | lineage | etl | connector


@dataclass
class AgentContext:
    """Shared state across an agent chain execution."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    conversation_history: list[dict] = field(default_factory=list)
    working_memory: dict[str, Any] = field(default_factory=dict)
    data_refs: dict[str, Any] = field(default_factory=dict)  # named data references
    lineage_chain: list[dict] = field(default_factory=list)   # agent-level lineage
    parent_agent: str | None = None
    depth: int = 0
    max_depth: int = 10
    metadata: dict[str, Any] = field(default_factory=dict)

    def fork(self, parent_agent: str) -> "AgentContext":
        """Create a child context for sub-agent delegation."""
        return AgentContext(
            session_id=self.session_id,
            conversation_history=list(self.conversation_history),
            working_memory=dict(self.working_memory),
            data_refs=dict(self.data_refs),
            lineage_chain=list(self.lineage_chain),
            parent_agent=parent_agent,
            depth=self.depth + 1,
            max_depth=self.max_depth,
            metadata=dict(self.metadata),
        )


@dataclass
class AgentResult:
    """Standardized output from any agent."""
    agent_name: str
    status: AgentStatus
    output: Any = None
    error: str | None = None
    data_modality: DataModality | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)  # named outputs
    lineage_event: dict | None = None
    benchmark: dict = field(default_factory=dict)  # timing, token counts, etc.
    sub_results: list["AgentResult"] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "agent_name": self.agent_name,
            "status": self.status.value,
            "output": self.output if not isinstance(self.output, bytes) else f"<binary {len(self.output)} bytes>",
            "error": self.error,
            "data_modality": self.data_modality.value if self.data_modality else None,
            "artifacts": {k: str(v)[:500] for k, v in self.artifacts.items()},
            "benchmark": self.benchmark,
            "sub_results": [r.to_dict() for r in self.sub_results],
        }


# ── Tool Registry ────────────────────────────────────────────────────────────

class ToolRegistry:
    """Global registry of tools available to agents.

    Tools are registered at boot by each service module and agents
    discover them by name or category.
    """

    _tools: dict[str, ToolSpec] = {}

    @classmethod
    def register(cls, tool: ToolSpec) -> None:
        cls._tools[tool.name] = tool
        log.debug("Registered tool: %s [%s]", tool.name, tool.category)

    @classmethod
    def get(cls, name: str) -> ToolSpec | None:
        return cls._tools.get(name)

    @classmethod
    def list_tools(cls, category: str | None = None) -> list[ToolSpec]:
        if category:
            return [t for t in cls._tools.values() if t.category == category]
        return list(cls._tools.values())

    @classmethod
    def list_names(cls, category: str | None = None) -> list[str]:
        return [t.name for t in cls.list_tools(category)]

    @classmethod
    def invoke(cls, name: str, **kwargs) -> Awaitable[Any]:
        tool = cls._tools.get(name)
        if not tool:
            raise ValueError(f"Tool '{name}' not found. Available: {cls.list_names()}")
        return tool.handler(**kwargs)

    @classmethod
    def clear(cls) -> None:
        cls._tools.clear()

    @classmethod
    def to_schema_list(cls, category: str | None = None) -> list[dict]:
        """Return tool specs as JSON-serializable list for LLM function calling."""
        return [
            {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
                "category": t.category,
            }
            for t in cls.list_tools(category)
        ]


# ── Base Agent ───────────────────────────────────────────────────────────────

class BaseAgent(ABC):
    """Abstract base agent with lifecycle, lineage, and benchmarking.

    Subclasses implement:
      - name (property): unique agent identifier
      - description (property): what the agent does
      - capabilities (property): list of what it can handle
      - _execute(ctx, task): core logic
    """

    def __init__(self):
        self._run_id: str = ""
        self._start_time: float = 0
        self._token_usage: dict[str, int] = {"input": 0, "output": 0}

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @property
    @abstractmethod
    def description(self) -> str:
        ...

    @property
    def capabilities(self) -> list[str]:
        return []

    @property
    def max_iterations(self) -> int:
        return 15

    @property
    def timeout_seconds(self) -> float:
        return 120.0

    async def run(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        """Execute the agent with full lifecycle management."""
        self._run_id = str(uuid.uuid4())
        self._start_time = time.monotonic()
        self._token_usage = {"input": 0, "output": 0}

        # Depth guard
        if ctx.depth > ctx.max_depth:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"Max agent chain depth ({ctx.max_depth}) exceeded",
            )

        # Emit lineage START
        lineage_start = self._emit_lineage("START", task, ctx)
        ctx.lineage_chain.append(lineage_start)

        try:
            result = await asyncio.wait_for(
                self._execute(ctx, task),
                timeout=self.timeout_seconds,
            )

            # Attach benchmark metrics
            elapsed = time.monotonic() - self._start_time
            result.benchmark = {
                "agent": self.name,
                "run_id": self._run_id,
                "latency_ms": round(elapsed * 1000, 2),
                "token_usage": dict(self._token_usage),
                "iterations": task.get("_iterations", 1),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Emit lineage COMPLETE
            lineage_end = self._emit_lineage(
                "COMPLETE" if result.status == AgentStatus.SUCCESS else "FAIL",
                task, ctx, result,
            )
            result.lineage_event = lineage_end
            ctx.lineage_chain.append(lineage_end)

            return result

        except asyncio.TimeoutError:
            lineage_fail = self._emit_lineage("FAIL", task, ctx, error="Timeout")
            ctx.lineage_chain.append(lineage_fail)
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.TIMEOUT,
                error=f"Agent timed out after {self.timeout_seconds}s",
                benchmark={"latency_ms": self.timeout_seconds * 1000},
            )

        except Exception as exc:
            lineage_fail = self._emit_lineage("FAIL", task, ctx, error=str(exc))
            ctx.lineage_chain.append(lineage_fail)
            log.error("Agent %s failed: %s\n%s", self.name, exc, traceback.format_exc())
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=str(exc),
                benchmark={"latency_ms": round((time.monotonic() - self._start_time) * 1000, 2)},
            )

    @abstractmethod
    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        """Core agent logic. Override in subclasses."""
        ...

    async def use_tool(self, tool_name: str, **kwargs) -> Any:
        """Invoke a registered tool."""
        return await ToolRegistry.invoke(tool_name, **kwargs)

    def _emit_lineage(
        self,
        event_type: str,
        task: dict,
        ctx: AgentContext,
        result: AgentResult | None = None,
        error: str | None = None,
    ) -> dict:
        """Produce an OpenLineage-compatible event for agent execution tracking."""
        inputs = []
        if ctx.parent_agent:
            inputs.append({"name": f"agent:{ctx.parent_agent}", "namespace": "zetabridge"})
        for ref_name in task.get("input_refs", []):
            inputs.append({"name": ref_name, "namespace": "zetabridge"})

        outputs = []
        if result and result.artifacts:
            for art_name in result.artifacts:
                outputs.append({"name": f"artifact:{art_name}", "namespace": "zetabridge"})

        facets = {
            "agent": {
                "name": self.name,
                "run_id": self._run_id,
                "task_type": task.get("type", "unknown"),
                "depth": ctx.depth,
            },
        }
        if error:
            facets["error"] = {"message": error}
        if result and result.benchmark:
            facets["benchmark"] = result.benchmark

        return {
            "run_id": self._run_id,
            "job_name": f"agent.{self.name}",
            "job_namespace": "zetabridge",
            "event_type": event_type,
            "inputs": inputs,
            "outputs": outputs,
            "facets": facets,
            "event_time": datetime.now(timezone.utc).isoformat(),
        }
