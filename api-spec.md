# ZetaBridge API Spec for Dashboard

## Phase 2 APIs (existing)
- GET /api/health → { status, service, version, phase, agents, tools, timestamp }
- GET /api/catalog/tables → [{ id, metalake, catalog_name, catalog_type, schema_name, table_name, columns, properties, fqn, created_at }]
- GET /api/catalog/stats → { total_tables, catalogs, schemas, by_type, catalog_names }
- GET /api/lineage/events → [{ id, run_id, job_name, event_type, inputs, outputs, facets, event_time }]
- GET /api/lineage/graph → { nodes: [{ id, label, type }], edges: [{ source, target, type }] }
- GET /api/lineage/stats → { total_events, unique_jobs, unique_datasets, by_type }
- POST /api/query/nl → { question, sql, engine, results, row_count, duration_ms, error }
- POST /api/query/sql → { sql, columns, results, row_count }
- GET /api/query/history → [{ id, question, sql, engine, row_count, duration_ms, error, created_at }]

## Phase 3 APIs (NEW — Agentic Layer)
- POST /api/copilot/chat { message, session_id?, params? } → { session_id, turn_id, intent, response: { summary, data, intent, agents_used, suggestions, render_type }, execution: { plan_id, tasks, succeeded, failed, latency_ms }, lineage: [...], benchmarks: [...] }
- GET /api/copilot/sessions → [{ session_id, turns, created_at, last_activity }]
- GET /api/copilot/sessions/{id}/history → [{ turn_id, role, content, intent, timestamp, latency_ms }]
- POST /api/agents/execute { agent, action, params? } → { agent_name, status, output, error, data_modality, artifacts, benchmark, sub_results }
- GET /api/agents → [{ name, description, capabilities }]
- GET /api/agents/stats → { [agent_name]: { total_runs, avg_latency_ms, min_latency_ms, max_latency_ms } }
- GET /api/agents/tools → [{ name, description, parameters, category }]
- GET /api/agents/execution-history → [{ plan_id, intent, total_tasks, succeeded, failed, total_latency_ms, timestamp }]
- POST /api/benchmarks/run { tags?, category? } → { suite_id, total_cases, passed, failed, pass_rate, total_latency_ms, avg_latency_ms, p50_latency_ms, p95_latency_ms, by_category, results }
- GET /api/benchmarks/results → [{ case, category, passed, latency_ms, details, error, timestamp }]
- GET /api/benchmarks/summary → { total_runs, passed, failed, pass_rate, avg_latency_ms, p50_latency_ms, p95_latency_ms }
- GET /api/connectors → [{ name, display_name, category, protocol, oss_component, license, description, capabilities, data_modalities, status, health, last_check }]
- GET /api/connectors/stats → { total_connectors, by_category, by_status, active, modalities_supported }
- GET /api/connectors/active → [...]
- GET /api/connectors/health → [{ name, display_name, status, category, healthy, message, checked_at }]
- GET /api/data/stats → { structured: { tables, table_count }, unstructured: { documents }, binary: { objects } }
- POST /api/data/documents { content, metadata? } → { id, status }
- GET /api/data/documents → [{ id, content_preview, metadata, has_embedding, created_at }]
- POST /api/data/search { query, top_k? } → [{ id, content, metadata, score }]
- GET /api/platform/status → { agents, tools, connectors, data_stores, benchmark_summary }
