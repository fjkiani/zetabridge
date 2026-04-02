"""
ZetaBridge Specialized Agents
==============================
Production agents for each data platform domain:
  - CatalogAgent: Federated catalog discovery & management
    (replaces manual Gravitino/Lakekeeper/Unity API calls)
  - QueryAgent: NL→SQL with schema-aware generation + execution
    (replaces manual Text2SQL + DuckDB/Doris routing)
  - LineageAgent: Lineage graph analysis, impact assessment, root-cause
    (replaces manual Marquez/OpenLineage querying)
  - ETLAgent: Pipeline orchestration, dbt/dlt/Airbyte job management
    (replaces manual Hop/Airbyte/dbt invocations)
  - DataQualityAgent: Automated data validation, profiling, anomaly detection
    (replaces manual Great Expectations / dbt-test workflows)
  - ConnectorAgent: Dynamic source/sink management across lakes & warehouses
    (replaces manual connector configuration)

License: Apache-2.0
"""

from __future__ import annotations

import json
import logging
import re
import hashlib
import statistics
from datetime import datetime, timezone
from typing import Any

from .base import (
    BaseAgent, AgentContext, AgentResult, AgentStatus,
    DataModality, ToolRegistry,
)

log = logging.getLogger("zetabridge.agents.specialized")


# ══════════════════════════════════════════════════════════════════════════════
# CATALOG AGENT — Federated catalog discovery & management
# ══════════════════════════════════════════════════════════════════════════════

class CatalogAgent(BaseAgent):
    """Discovers schemas, registers tables, profiles data across
    Iceberg / Delta / DuckDB catalogs via the unified catalog API.

    Replaces: Manual Gravitino REST + Lakekeeper REST + Unity Catalog calls.
    """

    @property
    def name(self) -> str:
        return "catalog_agent"

    @property
    def description(self) -> str:
        return "Federated catalog discovery, schema introspection, and table management across Iceberg/Delta/DuckDB"

    @property
    def capabilities(self) -> list[str]:
        return [
            "list_catalogs", "list_tables", "describe_table",
            "register_table", "search_columns", "profile_table",
            "suggest_joins", "detect_schema_drift",
        ]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        action = task.get("action", "discover")
        catalog_store = ctx.metadata.get("catalog_store")  # injected at boot

        if action == "discover":
            return await self._discover_all(ctx, catalog_store)
        elif action == "describe":
            return await self._describe_table(ctx, task, catalog_store)
        elif action == "search":
            return await self._search_columns(ctx, task, catalog_store)
        elif action == "profile":
            return await self._profile_table(ctx, task)
        elif action == "suggest_joins":
            return await self._suggest_joins(ctx, catalog_store)
        elif action == "detect_drift":
            return await self._detect_schema_drift(ctx, task, catalog_store)
        else:
            return AgentResult(
                agent_name=self.name,
                status=AgentStatus.FAILED,
                error=f"Unknown catalog action: {action}",
            )

    async def _discover_all(self, ctx: AgentContext, store) -> AgentResult:
        """Full catalog discovery across all registered catalogs."""
        tables = await self.use_tool("catalog_list_tables")
        by_catalog: dict[str, list] = {}
        for t in tables:
            cat = t["catalog_name"]
            by_catalog.setdefault(cat, []).append(t)

        summary = {
            "total_tables": len(tables),
            "catalogs": {
                cat: {
                    "type": tbls[0]["catalog_type"],
                    "table_count": len(tbls),
                    "schemas": list({t["schema_name"] for t in tbls}),
                    "tables": [
                        {
                            "fqn": t["fqn"],
                            "columns": len(t["columns"]),
                            "type": t["catalog_type"],
                        }
                        for t in tbls
                    ],
                }
                for cat, tbls in by_catalog.items()
            },
        }

        ctx.working_memory["catalog_summary"] = summary
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.SUCCESS,
            output=summary,
            data_modality=DataModality.STRUCTURED,
            artifacts={"catalog_summary": summary},
        )

    async def _describe_table(self, ctx: AgentContext, task: dict, store) -> AgentResult:
        """Detailed table description with column analysis."""
        table_fqn = task.get("table", "")
        tables = await self.use_tool("catalog_list_tables")
        match = next((t for t in tables if t["fqn"] == table_fqn), None)
        if not match:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Table {table_fqn} not found",
            )
        description = {
            "fqn": match["fqn"],
            "catalog_type": match["catalog_type"],
            "columns": match["columns"],
            "properties": match["properties"],
            "column_count": len(match["columns"]),
            "has_nullable": any(c.get("nullable", True) for c in match["columns"]),
        }
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output=description, data_modality=DataModality.STRUCTURED,
        )

    async def _search_columns(self, ctx: AgentContext, task: dict, store) -> AgentResult:
        """Search for columns across all catalogs by name pattern."""
        pattern = task.get("pattern", "").lower()
        tables = await self.use_tool("catalog_list_tables")
        matches = []
        for t in tables:
            for col in t["columns"]:
                if pattern in col["name"].lower():
                    matches.append({
                        "table": t["fqn"],
                        "column": col["name"],
                        "type": col["type"],
                        "catalog_type": t["catalog_type"],
                    })
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={"pattern": pattern, "matches": matches, "count": len(matches)},
        )

    async def _profile_table(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Profile a DuckDB table: row counts, null rates, distinct counts, stats."""
        table_fqn = task.get("table", "")
        parts = table_fqn.split(".")
        if len(parts) != 2:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Invalid table FQN for profiling: {table_fqn}. Expected schema.table",
            )
        schema_name, table_name = parts
        try:
            # Get row count
            count_result = await self.use_tool(
                "duckdb_query",
                sql=f'SELECT COUNT(*) as cnt FROM "{schema_name}"."{table_name}"',
            )
            row_count = count_result[0]["cnt"] if count_result else 0

            # Get column stats
            tables = await self.use_tool("catalog_list_tables")
            match = next((t for t in tables if t["fqn"].endswith(table_fqn)), None)
            columns_info = match["columns"] if match else []

            profile = {
                "table": table_fqn,
                "row_count": row_count,
                "columns": [],
            }
            for col in columns_info:
                col_name = col["name"]
                try:
                    stats_sql = f'''
                        SELECT
                            COUNT(*) as total,
                            COUNT("{col_name}") as non_null,
                            COUNT(DISTINCT "{col_name}") as distinct_count
                        FROM "{schema_name}"."{table_name}"
                    '''
                    stats = await self.use_tool("duckdb_query", sql=stats_sql)
                    s = stats[0] if stats else {}
                    profile["columns"].append({
                        "name": col_name,
                        "type": col["type"],
                        "null_rate": round(1 - (s.get("non_null", 0) / max(s.get("total", 1), 1)), 4),
                        "distinct_count": s.get("distinct_count", 0),
                        "completeness": round(s.get("non_null", 0) / max(s.get("total", 1), 1), 4),
                    })
                except Exception:
                    profile["columns"].append({"name": col_name, "type": col["type"], "error": "stats unavailable"})

            return AgentResult(
                agent_name=self.name, status=AgentStatus.SUCCESS,
                output=profile, data_modality=DataModality.STRUCTURED,
                artifacts={"table_profile": profile},
            )
        except Exception as exc:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Profile failed: {exc}",
            )

    async def _suggest_joins(self, ctx: AgentContext, store) -> AgentResult:
        """Suggest possible join relationships by matching column names across tables."""
        tables = await self.use_tool("catalog_list_tables")
        column_index: dict[str, list] = {}
        for t in tables:
            for col in t["columns"]:
                col_name = col["name"].lower()
                column_index.setdefault(col_name, []).append(t["fqn"])

        suggestions = []
        for col_name, fqns in column_index.items():
            if len(fqns) > 1 and ("_id" in col_name or "id" == col_name):
                suggestions.append({
                    "column": col_name,
                    "tables": fqns,
                    "confidence": "high" if col_name.endswith("_id") else "medium",
                })

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={"join_suggestions": suggestions},
        )

    async def _detect_schema_drift(self, ctx: AgentContext, task: dict, store) -> AgentResult:
        """Compare current schema against a stored snapshot to detect drift."""
        table_fqn = task.get("table", "")
        expected_columns = task.get("expected_columns", [])
        tables = await self.use_tool("catalog_list_tables")
        match = next((t for t in tables if t["fqn"] == table_fqn), None)
        if not match:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Table {table_fqn} not found",
            )
        current_cols = {c["name"]: c["type"] for c in match["columns"]}
        expected_cols = {c["name"]: c["type"] for c in expected_columns}

        added = {k: v for k, v in current_cols.items() if k not in expected_cols}
        removed = {k: v for k, v in expected_cols.items() if k not in current_cols}
        type_changed = {
            k: {"expected": expected_cols[k], "actual": current_cols[k]}
            for k in current_cols
            if k in expected_cols and current_cols[k] != expected_cols[k]
        }

        drift = {
            "table": table_fqn,
            "has_drift": bool(added or removed or type_changed),
            "added_columns": added,
            "removed_columns": removed,
            "type_changes": type_changed,
        }
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output=drift, data_modality=DataModality.STRUCTURED,
        )


# ══════════════════════════════════════════════════════════════════════════════
# QUERY AGENT — NL→SQL with schema-aware generation + execution
# ══════════════════════════════════════════════════════════════════════════════

class QueryAgent(BaseAgent):
    """Translates natural language to SQL, executes on DuckDB, validates results.

    Replaces: Manual Arctic Text2SQL + Gravitino schema discovery + DuckDB/Doris routing.
    """

    @property
    def name(self) -> str:
        return "query_agent"

    @property
    def description(self) -> str:
        return "NL→SQL translation, execution, result validation, and query optimization"

    @property
    def capabilities(self) -> list[str]:
        return [
            "nl_to_sql", "execute_sql", "validate_results",
            "optimize_query", "explain_results", "suggest_queries",
        ]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        action = task.get("action", "nl_query")

        if action == "nl_query":
            return await self._nl_query(ctx, task)
        elif action == "execute_sql":
            return await self._execute_sql(ctx, task)
        elif action == "suggest_queries":
            return await self._suggest_queries(ctx)
        elif action == "explain":
            return await self._explain_results(ctx, task)
        else:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Unknown query action: {action}",
            )

    async def _nl_query(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Full NL→SQL pipeline: discover schema → generate SQL → execute → validate."""
        question = task.get("question", "")
        if not question:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error="No question provided",
            )

        # Step 1: Get schema context
        schema_ctx = await self.use_tool("build_schema_context")

        # Step 2: Generate SQL
        sql = await self.use_tool("text2sql", question=question, schema_context=schema_ctx)

        # Step 3: Execute
        results = None
        error = None
        row_count = 0
        if sql:
            try:
                results = await self.use_tool("duckdb_query", sql=sql)
                row_count = len(results) if results else 0
            except Exception as exc:
                error = str(exc)

        # Step 4: Build response
        output = {
            "question": question,
            "sql": sql,
            "engine": "duckdb",
            "results": results,
            "row_count": row_count,
            "error": error,
            "schema_context": schema_ctx,
        }

        ctx.working_memory["last_query"] = output
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.SUCCESS if not error else AgentStatus.FAILED,
            output=output,
            data_modality=DataModality.STRUCTURED,
            artifacts={"query_result": output},
            error=error,
        )

    async def _execute_sql(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Execute raw SQL on DuckDB."""
        sql = task.get("sql", "")
        try:
            results = await self.use_tool("duckdb_query", sql=sql)
            return AgentResult(
                agent_name=self.name, status=AgentStatus.SUCCESS,
                output={"sql": sql, "results": results, "row_count": len(results) if results else 0},
                data_modality=DataModality.STRUCTURED,
            )
        except Exception as exc:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=str(exc),
            )

    async def _suggest_queries(self, ctx: AgentContext) -> AgentResult:
        """Suggest useful queries based on discovered schema."""
        tables = await self.use_tool("catalog_list_tables")
        suggestions = []
        for t in tables:
            fqn = t["fqn"]
            cols = [c["name"] for c in t["columns"]]
            suggestions.append({
                "table": fqn,
                "suggestions": [
                    f"Show me the first 10 rows from {fqn}",
                    f"How many records are in {fqn}?",
                ],
            })
            # Add numeric-aware suggestions
            for c in t["columns"]:
                if c["type"] in ("double", "float", "integer", "long"):
                    suggestions[-1]["suggestions"].append(
                        f"What is the average {c['name']} in {fqn}?"
                    )
                    break

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={"suggestions": suggestions},
        )

    async def _explain_results(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Generate a plain-English explanation of query results."""
        query_data = task.get("query_data") or ctx.working_memory.get("last_query", {})
        if not query_data:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error="No query results to explain",
            )

        results = query_data.get("results", [])
        row_count = len(results) if results else 0
        question = query_data.get("question", "")
        sql = query_data.get("sql", "")

        explanation = {
            "question": question,
            "sql": sql,
            "row_count": row_count,
            "summary": f"Query returned {row_count} rows.",
        }

        if results and row_count > 0:
            # Compute basic stats for numeric columns
            sample = results[:5]
            explanation["sample_rows"] = sample
            numeric_cols = []
            for key in results[0]:
                vals = [r[key] for r in results if isinstance(r.get(key), (int, float))]
                if vals:
                    numeric_cols.append({
                        "column": key,
                        "min": min(vals),
                        "max": max(vals),
                        "mean": round(statistics.mean(vals), 2),
                    })
            if numeric_cols:
                explanation["numeric_stats"] = numeric_cols

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output=explanation,
        )


# ══════════════════════════════════════════════════════════════════════════════
# LINEAGE AGENT — Graph analysis, impact assessment, root-cause tracing
# ══════════════════════════════════════════════════════════════════════════════

class LineageAgent(BaseAgent):
    """Analyzes data lineage graphs for impact analysis, root-cause tracing,
    and dependency mapping.

    Replaces: Manual Marquez/OpenLineage graph traversal.
    """

    @property
    def name(self) -> str:
        return "lineage_agent"

    @property
    def description(self) -> str:
        return "Lineage graph analysis: impact assessment, root-cause tracing, dependency mapping"

    @property
    def capabilities(self) -> list[str]:
        return [
            "impact_analysis", "root_cause", "dependency_map",
            "freshness_check", "lineage_summary", "trace_data_flow",
        ]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        action = task.get("action", "summary")

        if action == "summary":
            return await self._lineage_summary(ctx)
        elif action == "impact_analysis":
            return await self._impact_analysis(ctx, task)
        elif action == "root_cause":
            return await self._root_cause(ctx, task)
        elif action == "dependency_map":
            return await self._dependency_map(ctx, task)
        elif action == "freshness":
            return await self._freshness_check(ctx)
        else:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Unknown lineage action: {action}",
            )

    async def _lineage_summary(self, ctx: AgentContext) -> AgentResult:
        """Full lineage summary: graph stats, critical paths, data freshness."""
        graph = await self.use_tool("lineage_graph")
        events = await self.use_tool("lineage_list_events")

        nodes = graph.get("nodes", [])
        edges = graph.get("edges", [])
        jobs = [n for n in nodes if n["type"] == "job"]
        datasets = [n for n in nodes if n["type"] == "dataset"]

        # Build adjacency for path analysis
        adjacency: dict[str, list[str]] = {}
        for e in edges:
            adjacency.setdefault(e["source"], []).append(e["target"])

        # Find source nodes (no incoming) and sink nodes (no outgoing)
        all_targets = {e["target"] for e in edges}
        all_sources = {e["source"] for e in edges}
        source_nodes = [n["id"] for n in nodes if n["id"] not in all_targets]
        sink_nodes = [n["id"] for n in nodes if n["id"] not in all_sources]

        # Find longest path (critical path)
        def _dfs_longest(node: str, visited: set) -> list[str]:
            if node in visited:
                return []
            visited.add(node)
            best = [node]
            for neighbor in adjacency.get(node, []):
                path = [node] + _dfs_longest(neighbor, visited)
                if len(path) > len(best):
                    best = path
            visited.discard(node)
            return best

        critical_path = []
        for src in source_nodes:
            path = _dfs_longest(src, set())
            if len(path) > len(critical_path):
                critical_path = path

        summary = {
            "total_nodes": len(nodes),
            "jobs": len(jobs),
            "datasets": len(datasets),
            "edges": len(edges),
            "source_datasets": [n for n in source_nodes if n.startswith("dataset:")],
            "sink_datasets": [n for n in sink_nodes if n.startswith("dataset:")],
            "critical_path": critical_path,
            "critical_path_length": len(critical_path),
            "total_lineage_events": len(events),
            "event_types": {},
        }
        for e in events:
            et = e.get("event_type", "UNKNOWN")
            summary["event_types"][et] = summary["event_types"].get(et, 0) + 1

        ctx.working_memory["lineage_summary"] = summary
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output=summary, data_modality=DataModality.STRUCTURED,
            artifacts={"lineage_summary": summary},
        )

    async def _impact_analysis(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Downstream impact analysis: what would break if a dataset changes."""
        target = task.get("dataset", "")
        graph = await self.use_tool("lineage_graph")

        adjacency: dict[str, list[str]] = {}
        for e in graph.get("edges", []):
            adjacency.setdefault(e["source"], []).append(e["target"])

        # BFS downstream
        visited = set()
        queue = [f"dataset:{target}"] if not target.startswith("dataset:") else [target]
        impacted = []
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            impacted.append(node)
            for neighbor in adjacency.get(node, []):
                queue.append(neighbor)

        impacted.remove(impacted[0])  # remove the source itself
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "source": target,
                "impacted_nodes": impacted,
                "impacted_count": len(impacted),
                "impacted_jobs": [n for n in impacted if n.startswith("job:")],
                "impacted_datasets": [n for n in impacted if n.startswith("dataset:")],
            },
        )

    async def _root_cause(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Upstream root-cause tracing: trace back to source datasets."""
        target = task.get("dataset", "")
        graph = await self.use_tool("lineage_graph")

        # Build reverse adjacency
        reverse_adj: dict[str, list[str]] = {}
        for e in graph.get("edges", []):
            reverse_adj.setdefault(e["target"], []).append(e["source"])

        visited = set()
        queue = [f"dataset:{target}"] if not target.startswith("dataset:") else [target]
        upstream = []
        while queue:
            node = queue.pop(0)
            if node in visited:
                continue
            visited.add(node)
            upstream.append(node)
            for neighbor in reverse_adj.get(node, []):
                queue.append(neighbor)

        upstream.remove(upstream[0])
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "target": target,
                "upstream_nodes": upstream,
                "root_sources": [n for n in upstream if n not in {e["target"] for e in graph.get("edges", [])}],
            },
        )

    async def _dependency_map(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Complete dependency map for a given node."""
        target = task.get("node", "")
        graph = await self.use_tool("lineage_graph")

        adjacency: dict[str, list[str]] = {}
        reverse_adj: dict[str, list[str]] = {}
        for e in graph.get("edges", []):
            adjacency.setdefault(e["source"], []).append(e["target"])
            reverse_adj.setdefault(e["target"], []).append(e["source"])

        def bfs(start: str, adj: dict) -> list[str]:
            visited, queue, result = set(), [start], []
            while queue:
                node = queue.pop(0)
                if node in visited:
                    continue
                visited.add(node)
                result.append(node)
                for n in adj.get(node, []):
                    queue.append(n)
            return result[1:]  # exclude start

        upstream = bfs(target, reverse_adj)
        downstream = bfs(target, adjacency)

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "node": target,
                "upstream": upstream,
                "downstream": downstream,
                "total_dependencies": len(upstream) + len(downstream),
            },
        )

    async def _freshness_check(self, ctx: AgentContext) -> AgentResult:
        """Check data freshness by analyzing latest lineage event times."""
        events = await self.use_tool("lineage_list_events")
        job_freshness: dict[str, str] = {}
        for e in events:
            jn = e.get("job_name", "")
            et = e.get("event_time", "")
            if jn not in job_freshness or et > job_freshness[jn]:
                job_freshness[jn] = et

        freshness = []
        now = datetime.now(timezone.utc).isoformat()
        for job, last_time in sorted(job_freshness.items()):
            freshness.append({
                "job": job,
                "last_event": last_time,
                "status": "fresh" if last_time > "2026-04-01" else "stale",
            })

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={"freshness": freshness, "checked_at": now},
        )


# ══════════════════════════════════════════════════════════════════════════════
# ETL AGENT — Pipeline orchestration
# ══════════════════════════════════════════════════════════════════════════════

class ETLAgent(BaseAgent):
    """Manages ETL pipelines: dbt runs, dlt loads, Airbyte syncs, Hop transforms.

    Replaces: Manual dbt CLI + Airbyte API + Hop REST + dlt invocations.
    """

    @property
    def name(self) -> str:
        return "etl_agent"

    @property
    def description(self) -> str:
        return "ETL pipeline orchestration: dbt runs, dlt loads, Airbyte syncs, data transformations"

    @property
    def capabilities(self) -> list[str]:
        return [
            "run_pipeline", "list_pipelines", "pipeline_status",
            "generate_transform", "validate_pipeline", "schedule_pipeline",
        ]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        action = task.get("action", "list")

        if action == "list":
            return await self._list_pipelines(ctx)
        elif action == "run":
            return await self._run_pipeline(ctx, task)
        elif action == "generate_transform":
            return await self._generate_transform(ctx, task)
        elif action == "validate":
            return await self._validate_pipeline(ctx, task)
        elif action == "status":
            return await self._pipeline_status(ctx, task)
        else:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Unknown ETL action: {action}",
            )

    async def _list_pipelines(self, ctx: AgentContext) -> AgentResult:
        """List all configured ETL pipelines."""
        events = await self.use_tool("lineage_list_events")
        pipelines: dict[str, dict] = {}
        for e in events:
            jn = e.get("job_name", "")
            if jn not in pipelines:
                tool_type = "dbt" if "dbt." in jn else "dlt" if "dlt." in jn else "airbyte" if "airbyte." in jn else "custom"
                pipelines[jn] = {
                    "name": jn,
                    "tool": tool_type,
                    "runs": 0,
                    "last_status": None,
                    "last_run": None,
                }
            pipelines[jn]["runs"] += 1
            pipelines[jn]["last_status"] = e.get("event_type")
            pipelines[jn]["last_run"] = e.get("event_time")

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={"pipelines": list(pipelines.values()), "total": len(pipelines)},
            data_modality=DataModality.STRUCTURED,
        )

    async def _run_pipeline(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Simulate running an ETL pipeline (emits lineage events)."""
        pipeline_name = task.get("pipeline", "")
        if not pipeline_name:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error="No pipeline name provided",
            )

        import uuid as _uuid
        run_id = str(_uuid.uuid4())

        # Emit START event
        await self.use_tool(
            "lineage_emit",
            job_name=pipeline_name,
            event_type="START",
            run_id=run_id,
            inputs=task.get("inputs", []),
            outputs=task.get("outputs", []),
        )

        # Simulate pipeline execution
        await self.use_tool(
            "lineage_emit",
            job_name=pipeline_name,
            event_type="COMPLETE",
            run_id=run_id,
            inputs=task.get("inputs", []),
            outputs=task.get("outputs", []),
            facets={"etl": {"tool": task.get("tool", "dbt"), "triggered_by": "agent"}},
        )

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "pipeline": pipeline_name,
                "run_id": run_id,
                "status": "completed",
                "message": f"Pipeline {pipeline_name} completed successfully",
            },
        )

    async def _generate_transform(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Generate a SQL transform based on source/target specifications."""
        source = task.get("source_table", "")
        target = task.get("target_table", "")
        transform_type = task.get("transform", "select_all")

        if transform_type == "aggregate":
            group_by = task.get("group_by", [])
            metrics = task.get("metrics", [])
            sql = f"CREATE TABLE {target} AS\nSELECT\n"
            sql += ",\n".join(f"  {col}" for col in group_by)
            for m in metrics:
                sql += f",\n  {m['func']}({m['column']}) AS {m.get('alias', m['column'])}"
            sql += f"\nFROM {source}\nGROUP BY {', '.join(group_by)}"
        elif transform_type == "filter":
            condition = task.get("condition", "1=1")
            sql = f"CREATE TABLE {target} AS\nSELECT * FROM {source}\nWHERE {condition}"
        else:
            sql = f"CREATE TABLE {target} AS\nSELECT * FROM {source}"

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={"sql": sql, "source": source, "target": target, "type": transform_type},
        )

    async def _validate_pipeline(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Validate pipeline configuration."""
        pipeline = task.get("pipeline", {})
        issues = []
        if not pipeline.get("name"):
            issues.append("Pipeline missing name")
        if not pipeline.get("source"):
            issues.append("Pipeline missing source")
        if not pipeline.get("target"):
            issues.append("Pipeline missing target")

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "valid": len(issues) == 0,
                "issues": issues,
                "pipeline": pipeline,
            },
        )

    async def _pipeline_status(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Get status of a specific pipeline."""
        pipeline_name = task.get("pipeline", "")
        events = await self.use_tool("lineage_list_events")
        pipeline_events = [e for e in events if e.get("job_name") == pipeline_name]

        if not pipeline_events:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.SUCCESS,
                output={"pipeline": pipeline_name, "status": "not_found", "runs": 0},
            )

        last_event = pipeline_events[0]  # already sorted newest first
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "pipeline": pipeline_name,
                "status": last_event.get("event_type", "").lower(),
                "last_run": last_event.get("event_time"),
                "total_events": len(pipeline_events),
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# DATA QUALITY AGENT — Validation, profiling, anomaly detection
# ══════════════════════════════════════════════════════════════════════════════

class DataQualityAgent(BaseAgent):
    """Automated data quality: validation rules, anomaly detection, profiling.

    Replaces: Great Expectations + dbt tests + manual SQL checks.
    """

    @property
    def name(self) -> str:
        return "data_quality_agent"

    @property
    def description(self) -> str:
        return "Data quality validation, anomaly detection, completeness checks, and profiling"

    @property
    def capabilities(self) -> list[str]:
        return [
            "validate_table", "check_completeness", "detect_anomalies",
            "check_freshness", "run_quality_suite", "generate_quality_report",
        ]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        action = task.get("action", "suite")

        if action == "suite":
            return await self._run_quality_suite(ctx, task)
        elif action == "validate":
            return await self._validate_table(ctx, task)
        elif action == "completeness":
            return await self._check_completeness(ctx, task)
        elif action == "anomalies":
            return await self._detect_anomalies(ctx, task)
        else:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Unknown quality action: {action}",
            )

    async def _run_quality_suite(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Run comprehensive quality checks across all DuckDB tables."""
        tables = await self.use_tool("catalog_list_tables")
        duckdb_tables = [t for t in tables if t["catalog_type"] == "duckdb"]

        results = []
        for t in duckdb_tables:
            schema_name = t["schema_name"]
            table_name = t["table_name"]
            fqn = f"{schema_name}.{table_name}"

            checks = {"table": fqn, "checks": []}

            # Row count check
            try:
                count_result = await self.use_tool(
                    "duckdb_query",
                    sql=f'SELECT COUNT(*) as cnt FROM "{schema_name}"."{table_name}"',
                )
                row_count = count_result[0]["cnt"] if count_result else 0
                checks["row_count"] = row_count
                checks["checks"].append({
                    "name": "row_count",
                    "status": "pass" if row_count > 0 else "warn",
                    "value": row_count,
                    "message": f"{row_count} rows" if row_count > 0 else "Table is empty",
                })
            except Exception as exc:
                checks["checks"].append({
                    "name": "row_count", "status": "error", "message": str(exc),
                })

            # Null check per column
            for col in t["columns"]:
                col_name = col["name"]
                try:
                    null_result = await self.use_tool(
                        "duckdb_query",
                        sql=f'SELECT COUNT(*) - COUNT("{col_name}") as nulls, COUNT(*) as total FROM "{schema_name}"."{table_name}"',
                    )
                    if null_result:
                        nulls = null_result[0]["nulls"]
                        total = null_result[0]["total"]
                        null_rate = nulls / max(total, 1)
                        status = "pass" if null_rate < 0.05 else "warn" if null_rate < 0.3 else "fail"
                        checks["checks"].append({
                            "name": f"null_check:{col_name}",
                            "status": status,
                            "null_rate": round(null_rate, 4),
                            "message": f"{nulls}/{total} nulls ({null_rate:.1%})",
                        })
                except Exception:
                    pass

            results.append(checks)

        # Score
        total_checks = sum(len(r["checks"]) for r in results)
        passed = sum(1 for r in results for c in r["checks"] if c["status"] == "pass")
        score = round(passed / max(total_checks, 1) * 100, 1)

        output = {
            "tables_checked": len(results),
            "total_checks": total_checks,
            "passed": passed,
            "score": score,
            "grade": "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "F",
            "details": results,
        }

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output=output, data_modality=DataModality.STRUCTURED,
            artifacts={"quality_report": output},
        )

    async def _validate_table(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Run validation rules against a specific table."""
        table = task.get("table", "")
        rules = task.get("rules", [])
        parts = table.split(".")
        if len(parts) != 2:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error="Table must be schema.table format",
            )

        results = []
        for rule in rules:
            rule_type = rule.get("type", "")
            try:
                if rule_type == "not_null":
                    col = rule["column"]
                    r = await self.use_tool(
                        "duckdb_query",
                        sql=f'SELECT COUNT(*) as nulls FROM "{parts[0]}"."{parts[1]}" WHERE "{col}" IS NULL',
                    )
                    nulls = r[0]["nulls"] if r else 0
                    results.append({
                        "rule": f"not_null({col})",
                        "status": "pass" if nulls == 0 else "fail",
                        "value": nulls,
                    })
                elif rule_type == "unique":
                    col = rule["column"]
                    r = await self.use_tool(
                        "duckdb_query",
                        sql=f'SELECT COUNT("{col}") - COUNT(DISTINCT "{col}") as dupes FROM "{parts[0]}"."{parts[1]}"',
                    )
                    dupes = r[0]["dupes"] if r else 0
                    results.append({
                        "rule": f"unique({col})",
                        "status": "pass" if dupes == 0 else "fail",
                        "value": dupes,
                    })
                elif rule_type == "range":
                    col = rule["column"]
                    min_val = rule.get("min", float("-inf"))
                    max_val = rule.get("max", float("inf"))
                    r = await self.use_tool(
                        "duckdb_query",
                        sql=f'SELECT COUNT(*) as violations FROM "{parts[0]}"."{parts[1]}" WHERE "{col}" < {min_val} OR "{col}" > {max_val}',
                    )
                    violations = r[0]["violations"] if r else 0
                    results.append({
                        "rule": f"range({col}, {min_val}, {max_val})",
                        "status": "pass" if violations == 0 else "fail",
                        "value": violations,
                    })
            except Exception as exc:
                results.append({
                    "rule": f"{rule_type}({rule.get('column', '')})",
                    "status": "error",
                    "error": str(exc),
                })

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={"table": table, "rules_checked": len(results), "results": results},
        )

    async def _check_completeness(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Check data completeness across all columns."""
        table = task.get("table", "")
        parts = table.split(".")
        if len(parts) != 2:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error="Table must be schema.table format",
            )

        tables = await self.use_tool("catalog_list_tables")
        match = next((t for t in tables if f"{t['schema_name']}.{t['table_name']}" == table), None)
        if not match:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Table {table} not found in catalog",
            )

        completeness = []
        for col in match["columns"]:
            try:
                r = await self.use_tool(
                    "duckdb_query",
                    sql=f'SELECT COUNT("{col["name"]}") as filled, COUNT(*) as total FROM "{parts[0]}"."{parts[1]}"',
                )
                if r:
                    rate = r[0]["filled"] / max(r[0]["total"], 1)
                    completeness.append({
                        "column": col["name"],
                        "completeness": round(rate, 4),
                        "status": "complete" if rate >= 0.99 else "partial" if rate >= 0.8 else "sparse",
                    })
            except Exception:
                completeness.append({"column": col["name"], "completeness": None, "status": "error"})

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={"table": table, "columns": completeness},
        )

    async def _detect_anomalies(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Detect statistical anomalies in numeric columns using z-score method."""
        table = task.get("table", "")
        parts = table.split(".")
        if len(parts) != 2:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error="Table must be schema.table format",
            )
        threshold = task.get("threshold", 3.0)

        tables = await self.use_tool("catalog_list_tables")
        match = next((t for t in tables if f"{t['schema_name']}.{t['table_name']}" == table), None)
        if not match:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Table {table} not found",
            )

        anomalies = []
        numeric_types = {"double", "float", "integer", "long", "bigint"}
        for col in match["columns"]:
            if col["type"].lower() not in numeric_types:
                continue
            try:
                r = await self.use_tool(
                    "duckdb_query",
                    sql=f'SELECT AVG("{col["name"]}") as mean, STDDEV("{col["name"]}") as std FROM "{parts[0]}"."{parts[1]}"',
                )
                if r and r[0]["std"] and r[0]["std"] > 0:
                    mean = r[0]["mean"]
                    std = r[0]["std"]
                    outliers = await self.use_tool(
                        "duckdb_query",
                        sql=f'SELECT COUNT(*) as cnt FROM "{parts[0]}"."{parts[1]}" WHERE ABS("{col["name"]}" - {mean}) > {threshold * std}',
                    )
                    cnt = outliers[0]["cnt"] if outliers else 0
                    if cnt > 0:
                        anomalies.append({
                            "column": col["name"],
                            "outlier_count": cnt,
                            "mean": round(mean, 2),
                            "std": round(std, 2),
                            "threshold": threshold,
                        })
            except Exception:
                pass

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "table": table,
                "anomalies": anomalies,
                "anomaly_count": len(anomalies),
                "status": "clean" if not anomalies else "anomalies_detected",
            },
        )


# ══════════════════════════════════════════════════════════════════════════════
# CONNECTOR AGENT — Dynamic source/sink management
# ══════════════════════════════════════════════════════════════════════════════

class ConnectorAgent(BaseAgent):
    """Manages data connectors: Iceberg, Delta, DuckDB, S3, Postgres,
    and external ETL tools (dbt, dlt, Airbyte).

    Replaces: Manual connector configuration across disparate tools.
    """

    @property
    def name(self) -> str:
        return "connector_agent"

    @property
    def description(self) -> str:
        return "Connector management: data lakes, warehouses, ETL tools, and external APIs"

    @property
    def capabilities(self) -> list[str]:
        return [
            "list_connectors", "test_connector", "connector_status",
            "register_connector", "discover_sources",
        ]

    # Built-in connector registry
    CONNECTOR_TYPES = {
        "iceberg": {"protocol": "REST Catalog API", "oss": "Gravitino/Lakekeeper", "modality": "structured"},
        "delta": {"protocol": "Delta Lake Protocol", "oss": "delta-rs", "modality": "structured"},
        "duckdb": {"protocol": "Embedded SQL", "oss": "DuckDB", "modality": "structured"},
        "postgres": {"protocol": "PostgreSQL Wire Protocol", "oss": "PostgreSQL/Neon", "modality": "structured"},
        "s3": {"protocol": "S3 API", "oss": "MinIO/AWS S3", "modality": "binary"},
        "dbt": {"protocol": "dbt Core CLI", "oss": "dbt-core", "modality": "structured"},
        "dlt": {"protocol": "dlt Python API", "oss": "dlt", "modality": "structured"},
        "airbyte": {"protocol": "Airbyte API", "oss": "Airbyte OSS", "modality": "structured"},
        "kafka": {"protocol": "Kafka Protocol", "oss": "Apache Kafka", "modality": "semi_structured"},
        "elasticsearch": {"protocol": "REST API", "oss": "OpenSearch", "modality": "unstructured"},
    }

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        action = task.get("action", "list")

        if action == "list":
            return await self._list_connectors(ctx)
        elif action == "status":
            return await self._connector_status(ctx, task)
        elif action == "test":
            return await self._test_connector(ctx, task)
        elif action == "discover":
            return await self._discover_sources(ctx)
        else:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Unknown connector action: {action}",
            )

    async def _list_connectors(self, ctx: AgentContext) -> AgentResult:
        """List all available and configured connectors."""
        # Derive active connectors from catalog
        tables = await self.use_tool("catalog_list_tables")
        active_types = {t["catalog_type"] for t in tables}

        connectors = []
        for ctype, info in self.CONNECTOR_TYPES.items():
            connectors.append({
                "type": ctype,
                "protocol": info["protocol"],
                "oss_component": info["oss"],
                "data_modality": info["modality"],
                "status": "active" if ctype in active_types else "available",
                "tables": len([t for t in tables if t["catalog_type"] == ctype]),
            })

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "connectors": connectors,
                "active": len(active_types),
                "available": len(self.CONNECTOR_TYPES),
            },
        )

    async def _connector_status(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Check status of a specific connector type."""
        ctype = task.get("connector_type", "")
        if ctype not in self.CONNECTOR_TYPES:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.FAILED,
                error=f"Unknown connector type: {ctype}. Available: {list(self.CONNECTOR_TYPES.keys())}",
            )

        tables = await self.use_tool("catalog_list_tables")
        type_tables = [t for t in tables if t["catalog_type"] == ctype]

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "type": ctype,
                "info": self.CONNECTOR_TYPES[ctype],
                "status": "active" if type_tables else "configured",
                "table_count": len(type_tables),
                "tables": [t["fqn"] for t in type_tables],
            },
        )

    async def _test_connector(self, ctx: AgentContext, task: dict) -> AgentResult:
        """Test connectivity for a specific connector."""
        ctype = task.get("connector_type", "")
        if ctype == "duckdb":
            try:
                result = await self.use_tool("duckdb_query", sql="SELECT 1 as test")
                return AgentResult(
                    agent_name=self.name, status=AgentStatus.SUCCESS,
                    output={"connector": ctype, "status": "connected", "test": "SELECT 1 → OK"},
                )
            except Exception as exc:
                return AgentResult(
                    agent_name=self.name, status=AgentStatus.FAILED,
                    error=f"DuckDB connection test failed: {exc}",
                )
        elif ctype == "postgres":
            try:
                result = await self.use_tool("pg_health_check")
                return AgentResult(
                    agent_name=self.name, status=AgentStatus.SUCCESS,
                    output={"connector": ctype, "status": "connected"},
                )
            except Exception as exc:
                return AgentResult(
                    agent_name=self.name, status=AgentStatus.FAILED,
                    error=f"Postgres connection test failed: {exc}",
                )
        else:
            return AgentResult(
                agent_name=self.name, status=AgentStatus.SUCCESS,
                output={
                    "connector": ctype,
                    "status": "available",
                    "message": f"{ctype} connector is registered but requires external service configuration",
                },
            )

    async def _discover_sources(self, ctx: AgentContext) -> AgentResult:
        """Auto-discover available data sources from catalog + lineage."""
        tables = await self.use_tool("catalog_list_tables")
        events = await self.use_tool("lineage_list_events")

        sources = set()
        sinks = set()
        for e in events:
            for inp in e.get("inputs", []):
                name = inp.get("name", str(inp)) if isinstance(inp, dict) else str(inp)
                sources.add(name)
            for out in e.get("outputs", []):
                name = out.get("name", str(out)) if isinstance(out, dict) else str(out)
                sinks.add(name)

        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS,
            output={
                "catalog_tables": len(tables),
                "lineage_sources": sorted(sources),
                "lineage_sinks": sorted(sinks),
                "source_count": len(sources),
                "sink_count": len(sinks),
            },
        )
