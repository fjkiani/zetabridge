"""
ZetaBridge Agent Orchestrator
==============================
Multi-agent DAG planner and execution engine:
  - Intent classification → agent routing
  - Task decomposition into parallel/sequential sub-tasks
  - Agent chaining with shared context
  - 360 co-pilot router: single entry point for all data operations
  - Full lineage tracking across agent chains

OSS alignment:
  - DAG execution pattern inspired by Apache Hop/Airflow
  - Agent coordination inspired by CrewAI/AutoGen (zero-dependency)
  - OpenLineage protocol for cross-agent lineage

License: Apache-2.0
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from .base import (
    BaseAgent, AgentContext, AgentResult, AgentStatus,
    DataModality, ToolRegistry,
)
from .specialized import (
    CatalogAgent, QueryAgent, LineageAgent,
    ETLAgent, DataQualityAgent, ConnectorAgent,
)
from .federation_bridge_agents import (
    SynapseAgent, PDSAgent, SynthesisAgent,
)

log = logging.getLogger("zetabridge.orchestrator")


# ── Intent Classification ────────────────────────────────────────────────────

class Intent(str, Enum):
    CATALOG_DISCOVER = "catalog_discover"
    CATALOG_DESCRIBE = "catalog_describe"
    CATALOG_SEARCH = "catalog_search"
    CATALOG_PROFILE = "catalog_profile"
    QUERY_NL = "query_nl"
    QUERY_SQL = "query_sql"
    QUERY_SUGGEST = "query_suggest"
    LINEAGE_SUMMARY = "lineage_summary"
    LINEAGE_IMPACT = "lineage_impact"
    LINEAGE_ROOT_CAUSE = "lineage_root_cause"
    ETL_LIST = "etl_list"
    ETL_RUN = "etl_run"
    ETL_STATUS = "etl_status"
    ETL_GENERATE = "etl_generate"
    QUALITY_SUITE = "quality_suite"
    QUALITY_VALIDATE = "quality_validate"
    QUALITY_ANOMALIES = "quality_anomalies"
    CONNECTOR_LIST = "connector_list"
    CONNECTOR_TEST = "connector_test"
    CONNECTOR_DISCOVER = "connector_discover"
    MULTI_STEP = "multi_step"       # requires multiple agents
    PLATFORM_OVERVIEW = "platform_overview"  # full 360 view
    FEDERATE_BRIDGE = "federate_bridge"  # Synapse<->PDS cross-endpoint bridge
    CAPABILITY_ANCHOR = "capability_anchor"    # outcome-anchor index lookup
    CAPABILITY_EFFICACY = "capability_efficacy"  # Efficacy Predictor (Cox/logistic)
    UNKNOWN = "unknown"


# Keyword → intent mapping for fast classification
_INTENT_KEYWORDS: dict[str, list[str]] = {
    Intent.CATALOG_DISCOVER: ["catalog", "tables", "list tables", "show tables", "what tables", "discover", "schemas", "all tables", "show me all tables"],
    Intent.CATALOG_DESCRIBE: ["describe", "schema of", "columns in", "structure of", "what columns"],
    Intent.CATALOG_SEARCH: ["search columns", "find column", "where is", "which tables have"],
    Intent.CATALOG_PROFILE: ["profile", "statistics", "data profile", "null rate", "completeness"],
    Intent.QUERY_NL: ["query", "how many", "what is the", "total", "average", "sum", "count", "revenue", "customers", "top", "bottom", "show me the first", "show me total"],
    Intent.QUERY_SQL: ["run sql", "execute sql", "sql query"],
    Intent.QUERY_SUGGEST: ["suggest queries", "what can i ask", "example queries"],
    Intent.LINEAGE_SUMMARY: ["lineage", "lineage graph", "data flow", "pipeline graph", "dependency", "show me the lineage"],
    Intent.LINEAGE_IMPACT: ["impact", "what breaks", "downstream", "affected by"],
    Intent.LINEAGE_ROOT_CAUSE: ["root cause", "upstream", "where does", "source of", "origin"],
    Intent.ETL_LIST: ["pipelines", "list pipelines", "etl jobs", "show pipelines"],
    Intent.ETL_RUN: ["run pipeline", "trigger", "execute pipeline", "start pipeline"],
    Intent.ETL_STATUS: ["pipeline status", "job status", "is running"],
    Intent.ETL_GENERATE: ["generate transform", "create transform", "build pipeline"],
    Intent.QUALITY_SUITE: ["data quality", "quality check", "quality report", "health check", "quality checks", "data quality check"],
    Intent.QUALITY_VALIDATE: ["validate", "check rules", "run validations"],
    Intent.QUALITY_ANOMALIES: ["anomalies", "outliers", "unusual", "abnormal"],
    Intent.CONNECTOR_LIST: ["connectors", "connections", "data sources", "integrations"],
    Intent.CONNECTOR_TEST: ["test connection", "check connection", "ping"],
    Intent.CONNECTOR_DISCOVER: ["discover sources", "find sources", "auto discover"],
    Intent.PLATFORM_OVERVIEW: ["overview", "dashboard", "platform status", "360", "everything", "full picture", "summary"],
    Intent.FEDERATE_BRIDGE: ["federate", "federation", "bridge", "cross-endpoint", "cross endpoint", "synapse to pds", "synapse and pds", "hidden signal", "hidden signals", "link synapse", "connect synapse"],
    Intent.CAPABILITY_ANCHOR: ["outcome anchor", "outcome data", "clinical outcome", "what outcomes", "which cohorts", "dataset outcome", "trial outcome", "do we have os", "do we have pfs", "platinum sensitivity data", "publication for", "what was the outcome"],
    Intent.CAPABILITY_EFFICACY: ["predict survival", "predict outcome", "efficacy", "cox model", "cox ph", "hazard ratio", "survival model", "train a model", "predict os", "predict pfs", "predict platinum", "logistic regression", "concordance", "c-index", "auc", "efficacy predictor", "model outcomes", "reverse engineer"],
}


def classify_intent(text: str) -> Intent:
    """Classify user intent from natural language input."""
    text_lower = text.lower().strip()

    # Score each intent by keyword matches
    scores: dict[Intent, float] = {}
    for intent, keywords in _INTENT_KEYWORDS.items():
        score = 0
        for kw in keywords:
            if kw in text_lower:
                score += len(kw.split())  # weight multi-word matches higher
        if score > 0:
            scores[intent] = score

    if not scores:
        return Intent.UNKNOWN

    best = max(scores, key=scores.get)

    # Check if multiple high-scoring intents suggest multi-step
    top_scores = sorted(scores.values(), reverse=True)
    if len(top_scores) >= 2 and top_scores[1] >= top_scores[0] * 0.7:
        return Intent.MULTI_STEP

    return best


# ── Task Decomposition ───────────────────────────────────────────────────────

@dataclass
class AgentTask:
    """A single task in the execution DAG."""
    task_id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    agent_name: str = ""
    action: str = ""
    params: dict = field(default_factory=dict)
    depends_on: list[str] = field(default_factory=list)  # task_ids this depends on
    result: AgentResult | None = None
    status: str = "pending"


@dataclass
class ExecutionPlan:
    """DAG of agent tasks to execute."""
    plan_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    intent: Intent = Intent.UNKNOWN
    tasks: list[AgentTask] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def add_task(self, agent_name: str, action: str, params: dict = None, depends_on: list[str] = None) -> str:
        task = AgentTask(
            agent_name=agent_name,
            action=action,
            params=params or {},
            depends_on=depends_on or [],
        )
        self.tasks.append(task)
        return task.task_id

    def get_ready_tasks(self) -> list[AgentTask]:
        """Get tasks whose dependencies are all resolved."""
        completed = {t.task_id for t in self.tasks if t.status == "completed"}
        return [
            t for t in self.tasks
            if t.status == "pending" and all(dep in completed for dep in t.depends_on)
        ]

    def to_dict(self) -> dict:
        return {
            "plan_id": self.plan_id,
            "intent": self.intent.value,
            "tasks": [
                {
                    "task_id": t.task_id,
                    "agent": t.agent_name,
                    "action": t.action,
                    "status": t.status,
                    "depends_on": t.depends_on,
                }
                for t in self.tasks
            ],
        }


def decompose_task(intent: Intent, user_input: str, params: dict = None) -> ExecutionPlan:
    """Decompose an intent into an execution plan (DAG of agent tasks)."""
    plan = ExecutionPlan(intent=intent)
    params = params or {}

    if intent == Intent.CATALOG_DISCOVER:
        plan.add_task("catalog_agent", "discover")

    elif intent == Intent.CATALOG_DESCRIBE:
        plan.add_task("catalog_agent", "describe", {"table": params.get("table", "")})

    elif intent == Intent.CATALOG_SEARCH:
        plan.add_task("catalog_agent", "search", {"pattern": params.get("pattern", user_input)})

    elif intent == Intent.CATALOG_PROFILE:
        plan.add_task("catalog_agent", "profile", {"table": params.get("table", "")})

    elif intent == Intent.QUERY_NL:
        plan.add_task("query_agent", "nl_query", {"question": user_input})

    elif intent == Intent.QUERY_SQL:
        plan.add_task("query_agent", "execute_sql", {"sql": params.get("sql", "")})

    elif intent == Intent.QUERY_SUGGEST:
        plan.add_task("query_agent", "suggest_queries")

    elif intent == Intent.LINEAGE_SUMMARY:
        plan.add_task("lineage_agent", "summary")

    elif intent == Intent.LINEAGE_IMPACT:
        plan.add_task("lineage_agent", "impact_analysis", {"dataset": params.get("dataset", "")})

    elif intent == Intent.LINEAGE_ROOT_CAUSE:
        plan.add_task("lineage_agent", "root_cause", {"dataset": params.get("dataset", "")})

    elif intent == Intent.ETL_LIST:
        plan.add_task("etl_agent", "list")

    elif intent == Intent.ETL_RUN:
        plan.add_task("etl_agent", "run", {
            "pipeline": params.get("pipeline", ""),
            "inputs": params.get("inputs", []),
            "outputs": params.get("outputs", []),
        })

    elif intent == Intent.ETL_STATUS:
        plan.add_task("etl_agent", "status", {"pipeline": params.get("pipeline", "")})

    elif intent == Intent.ETL_GENERATE:
        plan.add_task("etl_agent", "generate_transform", params)

    elif intent == Intent.QUALITY_SUITE:
        plan.add_task("data_quality_agent", "suite")

    elif intent == Intent.QUALITY_VALIDATE:
        plan.add_task("data_quality_agent", "validate", {
            "table": params.get("table", ""),
            "rules": params.get("rules", []),
        })

    elif intent == Intent.QUALITY_ANOMALIES:
        plan.add_task("data_quality_agent", "anomalies", {
            "table": params.get("table", ""),
            "threshold": params.get("threshold", 3.0),
        })

    elif intent == Intent.CONNECTOR_LIST:
        plan.add_task("connector_agent", "list")

    elif intent == Intent.CONNECTOR_TEST:
        plan.add_task("connector_agent", "test", {"connector_type": params.get("connector_type", "duckdb")})

    elif intent == Intent.CONNECTOR_DISCOVER:
        plan.add_task("connector_agent", "discover")

    elif intent == Intent.PLATFORM_OVERVIEW:
        # Full 360 overview: parallel catalog + lineage + quality + connectors
        t1 = plan.add_task("catalog_agent", "discover")
        t2 = plan.add_task("lineage_agent", "summary")
        t3 = plan.add_task("data_quality_agent", "suite")
        t4 = plan.add_task("connector_agent", "list")
        t5 = plan.add_task("etl_agent", "list")

    elif intent == Intent.FEDERATE_BRIDGE:
        # Multi-hop cross-endpoint DAG:
        #   Agent 1 (Synapse) -> Agent 2 (PDS, depends on Synapse) ->
        #   Synthesis (depends on both). Orchestrator injects each dependency's
        #   output as dep_<agent_name>, so PDS receives Agent 1's payload and
        #   Synthesis receives both. Context preserved via AgentContext.fork.
        gene = params.get("gene", "KRAS")
        t1 = plan.add_task("synapse_agent", "query_gene", {"gene": gene})
        t2 = plan.add_task("pds_agent", "interrogate_biomarker", {"gene": gene}, depends_on=[t1])
        t3 = plan.add_task("synthesis_agent", "synthesize",
                           {"narrative": params.get("narrative", True)},
                           depends_on=[t1, t2])

    elif intent == Intent.MULTI_STEP:
        # Heuristic multi-step: catalog first, then query
        t1 = plan.add_task("catalog_agent", "discover")
        t2 = plan.add_task("query_agent", "nl_query", {"question": user_input}, depends_on=[t1])

    else:
        # Default: try query agent
        plan.add_task("query_agent", "nl_query", {"question": user_input})

    return plan


# ── Capability layer (outcome anchors + Efficacy Predictor) ──────────────────

def _capability_anchor_answer(user_input: str) -> dict:
    """Answer 'what outcomes / which cohorts / publication for X' from the anchor index."""
    from routers.capability import _load_index
    idx = _load_index()
    sources = idx.get("sources", [])
    q = user_input.lower()
    hit = None
    for s in sources:
        keys = [s["source_id"].lower(), s["name"].lower(), s.get("cancer_type", "").lower()]
        if any(k and k in q for k in keys):
            hit = s
            break
    if hit:
        pub = hit.get("publication", {})
        summary = (f"{hit['name']} ({hit['source_id']}): {hit['cohort_n']} patients, "
                   f"{hit['cancer_type']}, trial={'yes' if hit['is_trial'] else 'no'}. "
                   f"Outcomes: {', '.join(hit.get('outcome_vars', []))}. "
                   f"Signals: {', '.join(hit.get('signal_vars', []))}. "
                   f"Publication: {pub.get('title', 'n/a')} ({pub.get('doi', 'n/a')}). "
                   f"Efficacy-ready: {hit['efficacy_ready']}.")
        return {"results": hit, "summary": summary}
    lines = []
    for s in sources:
        lines.append(f"- {s['name']} ({s['source_id']}): {s['cohort_n']} pts, {s['cancer_type']}, "
                     f"outcomes={', '.join(s.get('outcome_vars', []))}, efficacy_ready={s['efficacy_ready']}")
    summary = (f"ZetaBridge has {len(sources)} outcome-anchored sources:\n" + "\n".join(lines))
    return {"results": {"n_sources": len(sources), "sources": sources}, "summary": summary}


def _capability_efficacy_answer(user_input: str, params: dict) -> dict:
    """Parse cohort/features/outcome from the prompt and run the Efficacy Predictor."""
    from routers.capability import EfficacyRequest, run_efficacy
    q = user_input.lower()
    cohort = params.get("cohort")
    if not cohort:
        if "britroc" in q or "brit" in q:
            cohort = "britroc"
        elif "spectrum" in q or "synapse" in q or "msk" in q:
            cohort = "spectrum"
        else:
            cohort = "britroc"
    analysis = params.get("analysis")
    if not analysis:
        if "platinum" in q or "sensitivity" in q or "resistance" in q:
            analysis = "platinum_sensitivity"
        elif "pfs" in q or "progression" in q:
            analysis = "pfs"
        else:
            analysis = "os"
    feats = params.get("features")
    if not feats:
        if cohort == "spectrum":
            feats = ["is_fbi"]
        else:
            cand = ["LST_score", "fraction_genome_altered", "CCNE1", "KRAS", "MYC", "age"]
            feats = [c for c in cand if c.lower() in q] or ["LST_score", "fraction_genome_altered"]
    try:
        res = run_efficacy(EfficacyRequest(cohort=cohort, analysis=analysis, features=feats,
                                           cv_folds=int(params.get("cv_folds", 5))))
    except Exception as e:
        return {"results": {"error": str(e)},
                "summary": f"Efficacy model failed: {e}. Try cohort=spectrum|britroc, analysis=os|pfs|platinum_sensitivity."}
    if res["model"] == "cox_ph":
        hr = res.get("hazard_ratios", {})
        hr_s = ", ".join(f"{k} HR={v}" for k, v in hr.items())
        summary = (f"Efficacy Predictor — {cohort} → {analysis} (Cox PH, n={res['n']}, events={res['events']}): "
                   f"CV concordance={res.get('cv_concordance_mean')}. {hr_s}. "
                   f"PH assumption ok={res.get('ph_assumption_ok')}. Discovery-only (single cohort).")
    else:
        summary = (f"Efficacy Predictor — {cohort} → {analysis} (logistic, n={res['n']}, events={res['events']}): "
                   f"CV AUC={res.get('cv_auc_mean')}. Discovery-only (single cohort).")
    return {"results": res, "summary": summary}


def _handle_capability_intent(intent: "Intent", user_input: str, params: dict) -> dict:
    """Route a capability intent to the anchor index or the Efficacy Predictor."""
    if intent == Intent.CAPABILITY_ANCHOR:
        return _capability_anchor_answer(user_input)
    return _capability_efficacy_answer(user_input, params or {})


# ── Orchestrator ─────────────────────────────────────────────────────────────

class Orchestrator:
    """Multi-agent orchestrator: routes, chains, and executes agent tasks.

    Key features:
      - DAG-based execution with dependency resolution
      - Parallel execution of independent tasks
      - Shared context propagation across agent chains
      - Full lineage tracking for every agent invocation
      - Benchmark aggregation across the execution plan
    """

    def __init__(self):
        self._agents: dict[str, BaseAgent] = {}
        self._execution_history: list[dict] = []

    def register_agent(self, agent: BaseAgent) -> None:
        self._agents[agent.name] = agent
        log.info("Registered agent: %s", agent.name)

    def get_agent(self, name: str) -> BaseAgent | None:
        return self._agents.get(name)

    def list_agents(self) -> list[dict]:
        return [
            {
                "name": a.name,
                "description": a.description,
                "capabilities": a.capabilities,
            }
            for a in self._agents.values()
        ]

    async def execute_plan(self, plan: ExecutionPlan, ctx: AgentContext) -> dict:
        """Execute a full plan with DAG resolution."""
        start = time.monotonic()
        plan_results: dict[str, AgentResult] = {}

        while True:
            ready = plan.get_ready_tasks()
            if not ready:
                # Check if all done or deadlocked
                pending = [t for t in plan.tasks if t.status == "pending"]
                if not pending:
                    break
                # Deadlock — deps can't be resolved
                for t in pending:
                    t.status = "failed"
                    t.result = AgentResult(
                        agent_name=t.agent_name,
                        status=AgentStatus.FAILED,
                        error="Deadlocked: unresolvable dependencies",
                    )
                break

            # Execute ready tasks in parallel
            async def _run_task(task: AgentTask) -> None:
                agent = self._agents.get(task.agent_name)
                if not agent:
                    task.status = "failed"
                    task.result = AgentResult(
                        agent_name=task.agent_name,
                        status=AgentStatus.FAILED,
                        error=f"Agent '{task.agent_name}' not registered",
                    )
                    return

                task.status = "running"
                task_data = {"action": task.action, **task.params}

                # Inject results from dependencies into task params
                for dep_id in task.depends_on:
                    dep_task = next((t for t in plan.tasks if t.task_id == dep_id), None)
                    if dep_task and dep_task.result:
                        task_data[f"dep_{dep_task.agent_name}"] = dep_task.result.output

                child_ctx = ctx.fork(parent_agent="orchestrator")
                result = await agent.run(child_ctx, task_data)
                task.result = result
                task.status = "completed"
                plan_results[task.task_id] = result

                # Propagate lineage chain back to parent context
                ctx.lineage_chain.extend(child_ctx.lineage_chain)

            await asyncio.gather(*[_run_task(t) for t in ready])

        elapsed = time.monotonic() - start

        # Aggregate results
        all_results = [t.result for t in plan.tasks if t.result]
        success = sum(1 for r in all_results if r.status == AgentStatus.SUCCESS)
        failed = sum(1 for r in all_results if r.status in (AgentStatus.FAILED, AgentStatus.TIMEOUT))

        execution_record = {
            "plan_id": plan.plan_id,
            "intent": plan.intent.value,
            "total_tasks": len(plan.tasks),
            "succeeded": success,
            "failed": failed,
            "total_latency_ms": round(elapsed * 1000, 2),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "plan": plan.to_dict(),
            "results": {
                tid: r.to_dict() for tid, r in plan_results.items()
            },
            "lineage_chain": ctx.lineage_chain,
            "benchmarks": [r.benchmark for r in all_results if r.benchmark],
        }

        self._execution_history.append(execution_record)
        return execution_record

    async def handle_copilot(self, user_input: str, ctx: AgentContext | None = None, params: dict = None) -> dict:
        """360 co-pilot entry point: classify → decompose → execute → respond."""
        if ctx is None:
            ctx = AgentContext()
        params = params or {}

        # Step 1: Classify intent
        intent = classify_intent(user_input)
        log.info("Co-pilot intent: %s for input: %s", intent.value, user_input[:80])

        # Capability layer: outcome-anchor + Efficacy Predictor, answered directly
        # from the byte-verified anchors (no agent decomposition needed).
        if intent in (Intent.CAPABILITY_ANCHOR, Intent.CAPABILITY_EFFICACY):
            cap = _handle_capability_intent(intent, user_input, params)
            cap["intent"] = intent.value
            cap["user_input"] = user_input
            return cap

        # Step 2: Decompose into execution plan
        plan = decompose_task(intent, user_input, params)
        log.info("Execution plan: %d tasks", len(plan.tasks))

        # Step 3: Execute the plan
        result = await self.execute_plan(plan, ctx)

        # Step 4: Format response
        result["intent"] = intent.value
        result["user_input"] = user_input

        # Add conversation context
        ctx.conversation_history.append({
            "role": "user",
            "content": user_input,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        ctx.conversation_history.append({
            "role": "assistant",
            "content": json.dumps(result, default=str)[:2000],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return result

    def get_execution_history(self, limit: int = 50) -> list[dict]:
        return self._execution_history[-limit:]

    def get_agent_stats(self) -> dict:
        """Aggregate performance stats across all agent executions."""
        stats: dict[str, dict] = {}
        for record in self._execution_history:
            for bench in record.get("benchmarks", []):
                agent = bench.get("agent", "unknown")
                if agent not in stats:
                    stats[agent] = {
                        "total_runs": 0,
                        "total_latency_ms": 0,
                        "avg_latency_ms": 0,
                        "min_latency_ms": float("inf"),
                        "max_latency_ms": 0,
                    }
                s = stats[agent]
                latency = bench.get("latency_ms", 0)
                s["total_runs"] += 1
                s["total_latency_ms"] += latency
                s["min_latency_ms"] = min(s["min_latency_ms"], latency)
                s["max_latency_ms"] = max(s["max_latency_ms"], latency)
                s["avg_latency_ms"] = round(s["total_latency_ms"] / s["total_runs"], 2)

        return stats


# ── Factory ──────────────────────────────────────────────────────────────────

def create_orchestrator() -> Orchestrator:
    """Create and configure the default orchestrator with all agents."""
    orch = Orchestrator()
    orch.register_agent(CatalogAgent())
    orch.register_agent(QueryAgent())
    orch.register_agent(LineageAgent())
    orch.register_agent(ETLAgent())
    orch.register_agent(DataQualityAgent())
    orch.register_agent(ConnectorAgent())
    # Federation bridge agents (Synapse<->PDS)
    orch.register_agent(SynapseAgent())
    orch.register_agent(PDSAgent())
    orch.register_agent(SynthesisAgent())
    return orch
