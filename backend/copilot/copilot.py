"""
ZetaBridge 360 Co-Pilot
========================
Conversational API with:
  - Intent detection and automatic agent dispatch
  - Conversation memory across sessions
  - Streaming-ready response formatting
  - Context-aware follow-ups
  - Multi-turn reasoning with agent chain history

OSS alignment:
  - Conversation patterns inspired by LangChain/LlamaIndex
  - Zero external dependencies for co-pilot logic

License: Apache-2.0
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agents.base import AgentContext, AgentStatus
from agents.orchestrator import Orchestrator, classify_intent, Intent

log = logging.getLogger("zetabridge.copilot")


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""
    turn_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    role: str = "user"  # user | assistant | system
    content: str = ""
    intent: str | None = None
    agent_results: list[dict] = field(default_factory=list)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    latency_ms: float = 0


@dataclass
class Session:
    """A co-pilot conversation session with memory."""
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turns: list[ConversationTurn] = field(default_factory=list)
    context: AgentContext = field(default_factory=AgentContext)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    metadata: dict = field(default_factory=dict)

    def add_turn(self, role: str, content: str, **kwargs) -> ConversationTurn:
        turn = ConversationTurn(role=role, content=content, **kwargs)
        self.turns.append(turn)
        return turn

    def get_history(self, limit: int = 20) -> list[dict]:
        return [
            {
                "turn_id": t.turn_id,
                "role": t.role,
                "content": t.content[:500],
                "intent": t.intent,
                "timestamp": t.timestamp,
                "latency_ms": t.latency_ms,
            }
            for t in self.turns[-limit:]
        ]


class CoPilot:
    """360 Co-Pilot: Single conversational entry point for all data operations.

    Features:
      - Automatic intent detection and agent routing
      - Multi-turn conversation with context memory
      - Smart follow-ups: "tell me more", "why?", "compare with..."
      - Parallel multi-agent execution for complex requests
      - Response formatting for frontend consumption
    """

    def __init__(self, orchestrator: Orchestrator):
        self.orchestrator = orchestrator
        self._sessions: dict[str, Session] = {}

    def create_session(self, session_id: str | None = None, metadata: dict | None = None) -> Session:
        session = Session(
            session_id=session_id or str(uuid.uuid4()),
            metadata=metadata or {},
        )
        # Inject platform metadata into agent context
        session.context.metadata.update(metadata or {})
        self._sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def chat(
        self,
        message: str,
        session_id: str | None = None,
        params: dict | None = None,
    ) -> dict:
        """Main co-pilot entry point: processes a user message and returns a response."""
        start = time.monotonic()

        # Get or create session
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
        else:
            session = self.create_session(session_id)

        # Record user turn
        user_turn = session.add_turn("user", message)

        # Detect intent
        intent = classify_intent(message)
        user_turn.intent = intent.value

        # Handle follow-up intents
        resolved_message = self._resolve_follow_up(message, session)

        # Execute through orchestrator
        result = await self.orchestrator.handle_copilot(
            resolved_message,
            session.context,
            params or {},
        )

        elapsed = (time.monotonic() - start) * 1000

        # Format response
        response = self._format_response(intent, result, message)

        # Record assistant turn
        assistant_turn = session.add_turn(
            "assistant",
            response["summary"],
            intent=intent.value,
            agent_results=[r for r in result.get("results", {}).values()],
            latency_ms=round(elapsed, 2),
        )

        return {
            "session_id": session.session_id,
            "turn_id": assistant_turn.turn_id,
            "intent": intent.value,
            "response": response,
            "execution": {
                "plan_id": result.get("plan_id"),
                "tasks": result.get("total_tasks", 0),
                "succeeded": result.get("succeeded", 0),
                "failed": result.get("failed", 0),
                "latency_ms": round(elapsed, 2),
            },
            "lineage": result.get("lineage_chain", []),
            "benchmarks": result.get("benchmarks", []),
        }

    def _resolve_follow_up(self, message: str, session: Session) -> str:
        """Resolve follow-up references like 'that table', 'those results'."""
        lower = message.lower().strip()

        # Common follow-up patterns
        if lower in ("tell me more", "more details", "expand on that", "more"):
            # Re-run last query with more context
            last_assistant = None
            for turn in reversed(session.turns):
                if turn.role == "assistant" and turn.intent:
                    last_assistant = turn
                    break
            if last_assistant:
                return f"Give me more details about the previous {last_assistant.intent} results"

        if lower.startswith("why") and len(lower) < 20:
            return f"Explain the results of the last query"

        return message

    def _format_response(self, intent: Intent, result: dict, original_message: str) -> dict:
        """Format orchestrator output into a user-friendly response."""
        # Extract the first successful result
        task_results = result.get("results", {})
        primary_output = None
        for tid, tres in task_results.items():
            if tres.get("status") == "success" and tres.get("output"):
                primary_output = tres["output"]
                break

        summary = self._generate_summary(intent, primary_output, result)

        response = {
            "summary": summary,
            "data": primary_output,
            "intent": intent.value,
            "agents_used": list({
                tres.get("agent_name", "unknown")
                for tres in task_results.values()
            }),
            "suggestions": self._generate_suggestions(intent, primary_output),
        }

        # Attach specific data types for frontend rendering
        if intent in (Intent.CATALOG_DISCOVER, Intent.CATALOG_DESCRIBE, Intent.CATALOG_SEARCH):
            response["render_type"] = "table"
        elif intent in (Intent.QUERY_NL, Intent.QUERY_SQL):
            response["render_type"] = "query_result"
        elif intent in (Intent.LINEAGE_SUMMARY, Intent.LINEAGE_IMPACT, Intent.LINEAGE_ROOT_CAUSE):
            response["render_type"] = "graph"
        elif intent in (Intent.QUALITY_SUITE, Intent.QUALITY_VALIDATE):
            response["render_type"] = "quality_report"
        elif intent == Intent.PLATFORM_OVERVIEW:
            response["render_type"] = "dashboard"
        else:
            response["render_type"] = "text"

        return response

    def _generate_summary(self, intent: Intent, output: Any, result: dict) -> str:
        """Generate a natural-language summary of agent results."""
        if not output:
            failed = result.get("failed", 0)
            if failed > 0:
                return "I encountered an issue processing your request. Let me try a different approach."
            return "I processed your request but found no results."

        if intent == Intent.CATALOG_DISCOVER:
            total = output.get("total_tables", 0)
            catalogs = list(output.get("catalogs", {}).keys())
            return f"Found {total} tables across {len(catalogs)} catalogs: {', '.join(catalogs)}."

        elif intent == Intent.QUERY_NL:
            sql = output.get("sql", "")
            rows = output.get("row_count", 0)
            error = output.get("error")
            if error:
                return f"Generated SQL but execution failed: {error}"
            return f"Query returned {rows} rows. SQL: `{sql[:100]}`"

        elif intent == Intent.LINEAGE_SUMMARY:
            nodes = output.get("total_nodes", 0)
            jobs = output.get("jobs", 0)
            datasets = output.get("datasets", 0)
            return f"Lineage graph: {nodes} nodes ({jobs} jobs, {datasets} datasets). Critical path length: {output.get('critical_path_length', 0)}."

        elif intent == Intent.QUALITY_SUITE:
            score = output.get("score", 0)
            grade = output.get("grade", "?")
            checks = output.get("total_checks", 0)
            return f"Data quality score: {score}% (Grade {grade}). Ran {checks} checks across {output.get('tables_checked', 0)} tables."

        elif intent == Intent.ETL_LIST:
            total = output.get("total", 0)
            return f"Found {total} ETL pipelines: {', '.join(p['name'] for p in output.get('pipelines', [])[:5])}."

        elif intent == Intent.CONNECTOR_LIST:
            active = output.get("active", 0)
            total = output.get("available", 0)
            return f"{active} active connectors out of {total} available."

        elif intent == Intent.PLATFORM_OVERVIEW:
            succeeded = result.get("succeeded", 0)
            total = result.get("total_tasks", 0)
            return f"Platform overview complete: {succeeded}/{total} checks passed. Review the dashboard for details."

        else:
            return f"Processed request with {result.get('total_tasks', 0)} agent tasks."

    def _generate_suggestions(self, intent: Intent, output: Any) -> list[str]:
        """Generate contextual follow-up suggestions."""
        base = []

        if intent == Intent.CATALOG_DISCOVER:
            base = [
                "Profile a specific table for data quality",
                "Search for columns across catalogs",
                "Show me the lineage graph",
            ]
        elif intent == Intent.QUERY_NL:
            base = [
                "Explain these results",
                "Show me related tables",
                "Run a data quality check",
            ]
        elif intent == Intent.LINEAGE_SUMMARY:
            base = [
                "Run impact analysis on a specific dataset",
                "Check data freshness",
                "Show me the ETL pipelines",
            ]
        elif intent == Intent.QUALITY_SUITE:
            base = [
                "Detect anomalies in specific tables",
                "Show me the lineage for failed checks",
                "Profile the tables with issues",
            ]
        elif intent == Intent.PLATFORM_OVERVIEW:
            base = [
                "Drill into data quality details",
                "Show me the lineage graph",
                "List all ETL pipelines",
                "Search for specific columns",
            ]
        else:
            base = [
                "Give me a platform overview",
                "Show me all tables",
                "Run a data quality check",
            ]

        return base[:4]

    def list_sessions(self) -> list[dict]:
        return [
            {
                "session_id": s.session_id,
                "turns": len(s.turns),
                "created_at": s.created_at,
                "last_activity": s.turns[-1].timestamp if s.turns else s.created_at,
            }
            for s in self._sessions.values()
        ]

    def get_session_history(self, session_id: str) -> list[dict]:
        session = self._sessions.get(session_id)
        if not session:
            return []
        return session.get_history()
