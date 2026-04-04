"""
ZetaBridge Benchmark Harness
==============================
Comprehensive benchmark framework for agent performance:
  - Latency profiling per agent and per tool call
  - Accuracy scoring for NL→SQL and data quality checks
  - Cost estimation (token usage, API calls)
  - Comparative benchmarks across agent configurations
  - Built-in test scenarios for demo

OSS alignment:
  - Inspired by LangSmith/Braintrust evaluation patterns
  - Zero external dependencies

License: Apache-2.0
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import statistics
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from agents.base import AgentContext, AgentStatus
from agents.orchestrator import Orchestrator, classify_intent, decompose_task

log = logging.getLogger("zetabridge.benchmarks")


@dataclass
class BenchmarkCase:
    """A single benchmark test case."""
    name: str
    category: str  # latency | accuracy | throughput | e2e
    input_text: str
    expected_intent: str | None = None
    expected_contains: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)
    max_latency_ms: float = 5000
    tags: list[str] = field(default_factory=list)


@dataclass
class BenchmarkResult:
    """Result of running a single benchmark case."""
    case_name: str
    category: str
    passed: bool
    latency_ms: float
    details: dict = field(default_factory=dict)
    error: str | None = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict:
        return {
            "case": self.case_name,
            "category": self.category,
            "passed": self.passed,
            "latency_ms": self.latency_ms,
            "details": self.details,
            "error": self.error,
            "timestamp": self.timestamp,
        }


# ── Built-in Test Scenarios ──────────────────────────────────────────────────

DEMO_SCENARIOS: list[BenchmarkCase] = [
    # Intent classification accuracy
    BenchmarkCase(
        name="intent_catalog_discover",
        category="accuracy",
        input_text="Show me all tables in the catalog",
        expected_intent="catalog_discover",
        tags=["intent", "catalog"],
    ),
    BenchmarkCase(
        name="intent_query_nl",
        category="accuracy",
        input_text="How many customers do we have?",
        expected_intent="query_nl",
        tags=["intent", "query"],
    ),
    BenchmarkCase(
        name="intent_lineage_summary",
        category="accuracy",
        input_text="Show me the data lineage graph",
        expected_intent="lineage_summary",
        tags=["intent", "lineage"],
    ),
    BenchmarkCase(
        name="intent_quality_suite",
        category="accuracy",
        input_text="Run a data quality check on all tables",
        expected_intent="quality_suite",
        tags=["intent", "quality"],
    ),
    BenchmarkCase(
        name="intent_etl_list",
        category="accuracy",
        input_text="List all ETL pipelines",
        expected_intent="etl_list",
        tags=["intent", "etl"],
    ),
    BenchmarkCase(
        name="intent_connector_list",
        category="accuracy",
        input_text="What data connectors are available?",
        expected_intent="connector_list",
        tags=["intent", "connector"],
    ),
    BenchmarkCase(
        name="intent_platform_overview",
        category="accuracy",
        input_text="Give me a 360 overview of the entire platform",
        expected_intent="platform_overview",
        tags=["intent", "overview"],
    ),
    BenchmarkCase(
        name="intent_impact_analysis",
        category="accuracy",
        input_text="What would be impacted if raw.events changes?",
        expected_intent="lineage_impact",
        tags=["intent", "lineage"],
    ),

    # E2E agent execution
    BenchmarkCase(
        name="e2e_catalog_discover",
        category="e2e",
        input_text="Show me all tables in the catalog",
        expected_contains=["total_tables", "catalogs"],
        max_latency_ms=3000,
        tags=["e2e", "catalog"],
    ),
    BenchmarkCase(
        name="e2e_query_revenue",
        category="e2e",
        input_text="Show me total revenue by region",
        expected_contains=["sql", "results"],
        max_latency_ms=5000,
        tags=["e2e", "query"],
    ),
    BenchmarkCase(
        name="e2e_lineage_summary",
        category="e2e",
        input_text="Show me the lineage graph summary",
        expected_contains=["total_nodes", "jobs", "datasets"],
        max_latency_ms=3000,
        tags=["e2e", "lineage"],
    ),
    BenchmarkCase(
        name="e2e_quality_suite",
        category="e2e",
        input_text="Run quality checks on all tables",
        expected_contains=["score", "grade"],
        max_latency_ms=5000,
        tags=["e2e", "quality"],
    ),
    BenchmarkCase(
        name="e2e_connector_list",
        category="e2e",
        input_text="List all available data connectors",
        expected_contains=["connectors", "active"],
        max_latency_ms=2000,
        tags=["e2e", "connector"],
    ),
    BenchmarkCase(
        name="e2e_etl_pipelines",
        category="e2e",
        input_text="Show me all ETL pipelines",
        expected_contains=["pipelines", "total"],
        max_latency_ms=3000,
        tags=["e2e", "etl"],
    ),
    BenchmarkCase(
        name="e2e_platform_overview",
        category="e2e",
        input_text="Give me a full platform overview",
        expected_contains=["succeeded"],
        max_latency_ms=10000,
        tags=["e2e", "overview"],
    ),

    # Latency benchmarks
    BenchmarkCase(
        name="latency_intent_classification",
        category="latency",
        input_text="What is the average revenue per product?",
        max_latency_ms=10,  # should be instant
        tags=["latency", "intent"],
    ),
    BenchmarkCase(
        name="latency_task_decomposition",
        category="latency",
        input_text="Give me a 360 overview of everything",
        max_latency_ms=10,
        tags=["latency", "decomposition"],
    ),
]


# ── Benchmark Runner ─────────────────────────────────────────────────────────

class BenchmarkHarness:
    """Runs benchmark suites against the orchestrator."""

    def __init__(self, orchestrator: Orchestrator):
        self.orchestrator = orchestrator
        self.results: list[BenchmarkResult] = []

    async def run_suite(
        self,
        scenarios: list[BenchmarkCase] | None = None,
        tags: list[str] | None = None,
        category: str | None = None,
    ) -> dict:
        """Run a full benchmark suite."""
        cases = scenarios or DEMO_SCENARIOS

        # Filter by tags/category
        if tags:
            cases = [c for c in cases if any(t in c.tags for t in tags)]
        if category:
            cases = [c for c in cases if c.category == category]

        log.info("Running %d benchmark cases", len(cases))
        start = time.monotonic()
        results = []

        for case in cases:
            result = await self._run_case(case)
            results.append(result)
            self.results.append(result)

        elapsed = time.monotonic() - start
        passed = sum(1 for r in results if r.passed)
        failed = len(results) - passed

        # Compute stats
        latencies = [r.latency_ms for r in results]
        by_category: dict[str, list] = {}
        for r in results:
            by_category.setdefault(r.category, []).append(r)

        category_stats = {}
        for cat, cat_results in by_category.items():
            cat_latencies = [r.latency_ms for r in cat_results]
            category_stats[cat] = {
                "total": len(cat_results),
                "passed": sum(1 for r in cat_results if r.passed),
                "failed": sum(1 for r in cat_results if not r.passed),
                "avg_latency_ms": round(statistics.mean(cat_latencies), 2),
                "p50_latency_ms": round(statistics.median(cat_latencies), 2),
                "p95_latency_ms": round(sorted(cat_latencies)[int(len(cat_latencies) * 0.95)] if cat_latencies else 0, 2),
                "max_latency_ms": round(max(cat_latencies), 2),
            }

        return {
            "suite_id": f"bench_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}",
            "total_cases": len(results),
            "passed": passed,
            "failed": failed,
            "pass_rate": round(passed / max(len(results), 1) * 100, 1),
            "total_latency_ms": round(elapsed * 1000, 2),
            "avg_latency_ms": round(statistics.mean(latencies), 2) if latencies else 0,
            "p50_latency_ms": round(statistics.median(latencies), 2) if latencies else 0,
            "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2) if latencies else 0,
            "by_category": category_stats,
            "results": [r.to_dict() for r in results],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    async def _run_case(self, case: BenchmarkCase) -> BenchmarkResult:
        """Run a single benchmark case."""
        start = time.monotonic()

        try:
            if case.category == "accuracy":
                return await self._run_accuracy_case(case, start)
            elif case.category == "latency":
                return await self._run_latency_case(case, start)
            elif case.category == "e2e":
                return await self._run_e2e_case(case, start)
            else:
                return BenchmarkResult(
                    case_name=case.name, category=case.category,
                    passed=False, latency_ms=0,
                    error=f"Unknown benchmark category: {case.category}",
                )
        except Exception as exc:
            elapsed = (time.monotonic() - start) * 1000
            return BenchmarkResult(
                case_name=case.name, category=case.category,
                passed=False, latency_ms=round(elapsed, 2),
                error=str(exc),
            )

    async def _run_accuracy_case(self, case: BenchmarkCase, start: float) -> BenchmarkResult:
        """Test intent classification accuracy."""
        intent = classify_intent(case.input_text)
        elapsed = (time.monotonic() - start) * 1000
        passed = intent.value == case.expected_intent

        return BenchmarkResult(
            case_name=case.name, category="accuracy",
            passed=passed, latency_ms=round(elapsed, 2),
            details={
                "input": case.input_text,
                "expected": case.expected_intent,
                "actual": intent.value,
            },
        )

    async def _run_latency_case(self, case: BenchmarkCase, start: float) -> BenchmarkResult:
        """Test operation latency against threshold."""
        if "intent" in case.tags:
            classify_intent(case.input_text)
        elif "decomposition" in case.tags:
            intent = classify_intent(case.input_text)
            decompose_task(intent, case.input_text)

        elapsed = (time.monotonic() - start) * 1000
        passed = elapsed <= case.max_latency_ms

        return BenchmarkResult(
            case_name=case.name, category="latency",
            passed=passed, latency_ms=round(elapsed, 2),
            details={
                "threshold_ms": case.max_latency_ms,
                "actual_ms": round(elapsed, 2),
            },
        )

    async def _run_e2e_case(self, case: BenchmarkCase, start: float) -> BenchmarkResult:
        """Full end-to-end test through the orchestrator."""
        ctx = AgentContext()
        result = await self.orchestrator.handle_copilot(
            case.input_text, ctx, case.params,
        )
        elapsed = (time.monotonic() - start) * 1000

        # Check expected fields in result
        result_str = json.dumps(result, default=str)
        missing = [
            field for field in case.expected_contains
            if field not in result_str
        ]
        passed = len(missing) == 0 and elapsed <= case.max_latency_ms

        return BenchmarkResult(
            case_name=case.name, category="e2e",
            passed=passed, latency_ms=round(elapsed, 2),
            details={
                "expected_fields": case.expected_contains,
                "missing_fields": missing,
                "under_latency_threshold": elapsed <= case.max_latency_ms,
                "threshold_ms": case.max_latency_ms,
                "tasks_executed": result.get("total_tasks", 0),
                "tasks_succeeded": result.get("succeeded", 0),
            },
        )

    def get_results(self) -> list[dict]:
        return [r.to_dict() for r in self.results]

    def get_summary(self) -> dict:
        if not self.results:
            return {"message": "No benchmarks run yet"}

        passed = sum(1 for r in self.results if r.passed)
        latencies = [r.latency_ms for r in self.results]

        return {
            "total_runs": len(self.results),
            "passed": passed,
            "failed": len(self.results) - passed,
            "pass_rate": round(passed / len(self.results) * 100, 1),
            "avg_latency_ms": round(statistics.mean(latencies), 2),
            "p50_latency_ms": round(statistics.median(latencies), 2),
            "p95_latency_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2) if latencies else 0,
        }
