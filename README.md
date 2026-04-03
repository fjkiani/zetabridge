# ZetaBridge — Cloud-Native Open Data Platform

Multi-agent data platform with federated catalog, lineage tracking, NL→SQL, and a 360 co-pilot. Zero local install required.

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                      360 Co-Pilot API                           │
│  Intent Classification → Task Decomposition → Agent Dispatch    │
├────────┬──────────┬──────────┬────────┬───────────┬─────────────┤
│Catalog │  Query   │ Lineage  │  ETL   │  Quality  │ Connector   │
│ Agent  │  Agent   │  Agent   │ Agent  │  Agent    │   Agent     │
├────────┴──────────┴──────────┴────────┴───────────┴─────────────┤
│                    Tool Registry (8 tools)                       │
├─────────────┬──────────────┬──────────────┬─────────────────────┤
│  Catalog    │   Lineage    │    Query     │   Multi-Modal       │
│  (Postgres) │ (OpenLineage)│  (DuckDB)    │  (Vector + Blob)    │
├─────────────┴──────────────┴──────────────┴─────────────────────┤
│              17 OSS Connectors (Iceberg, Delta, dbt, ...)       │
└─────────────────────────────────────────────────────────────────┘
```

## What It Does

| Layer | Components | Replaces |
|-------|-----------|----------|
| **Catalog** | Federated metadata in Postgres | Gravitino + Lakekeeper + Unity Catalog |
| **Lineage** | OpenLineage event store + graph | Marquez |
| **Query** | NL→SQL (Arctic Text2SQL) + DuckDB | Manual SQL + Doris |
| **Agents** | 6 specialized agents + DAG orchestrator | Manual CLI/API calls |
| **Co-Pilot** | Conversational interface with memory | N/A |
| **Benchmarks** | 17 test scenarios, accuracy + latency | Manual testing |
| **Multi-Modal** | Structured (SQL) + Unstructured (vector) + Blob | Separate stores |
| **Connectors** | 17 OSS connectors with health checks | Manual configuration |

## Agents

| Agent | Description | Key Actions |
|-------|-------------|-------------|
| `catalog_agent` | Schema discovery, profiling, drift detection | discover, describe, search, profile, suggest_joins |
| `query_agent` | NL→SQL generation + execution | nl_query, execute_sql, suggest_queries, explain |
| `lineage_agent` | Impact analysis, root-cause tracing | summary, impact_analysis, root_cause, freshness |
| `etl_agent` | Pipeline orchestration | list, run, generate_transform, status |
| `data_quality_agent` | Validation, anomaly detection | suite, validate, completeness, anomalies |
| `connector_agent` | Source/sink management | list, test, status, discover |

## Connectors (17)

**Catalogs:** Gravitino, Lakekeeper, Nessie, Unity Catalog
**Lakes:** Delta Lake (delta-rs), Apache Iceberg (pyiceberg)
**Warehouses:** DuckDB, Neon Postgres
**ETL:** dbt-core, dlt, Airbyte, Apache Hop
**Lineage:** OpenLineage, Marquez
**Other:** S3/MinIO, Apache Kafka, OpenSearch

## API Endpoints (35)

### Core
```
GET  /api/health
GET  /api/platform/status
```

### Co-Pilot
```
POST /api/copilot/chat          { message, session_id?, params? }
GET  /api/copilot/sessions
GET  /api/copilot/sessions/:id/history
```

### Agents
```
POST /api/agents/execute         { agent, action, params? }
GET  /api/agents
GET  /api/agents/stats
GET  /api/agents/tools
GET  /api/agents/execution-history
```

### Catalog
```
POST /api/catalog/tables         Register a table
GET  /api/catalog/tables         List tables (filter by catalog/schema)
GET  /api/catalog/tables/:id     Get table detail
DELETE /api/catalog/tables/:id   Remove table
GET  /api/catalog/stats          Dashboard stats
```

### Lineage
```
POST /api/lineage/events         Emit OpenLineage event
GET  /api/lineage/events         List events
GET  /api/lineage/graph          D3-compatible graph
GET  /api/lineage/stats          Aggregate stats
```

### Query
```
POST /api/query/nl               NL→SQL + execute
POST /api/query/sql              Raw SQL on DuckDB
GET  /api/query/history          Query audit log
```

### Benchmarks
```
POST /api/benchmarks/run         Run suite (filter by tags/category)
GET  /api/benchmarks/results     All results
GET  /api/benchmarks/summary     Aggregated stats
```

### Connectors
```
GET  /api/connectors             All 17 connectors
GET  /api/connectors/stats       By category/status
GET  /api/connectors/active      Active only
GET  /api/connectors/health      Health check all
GET  /api/connectors/:category   Filter by category
```

### Multi-Modal Data
```
GET  /api/data/stats             Store stats
POST /api/data/documents         Add document
GET  /api/data/documents         List documents
POST /api/data/search            Vector similarity search
GET  /api/data/blobs             List blob objects
```

## Quick Start (Cloud — Zero Local Install)

### 1. Deploy Backend to Render

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy)

```yaml
# render.yaml is included — one-click deploy
```

**Required env vars:**
```
DATABASE_URL=postgresql://...   # Neon Postgres (free tier)
HF_API_TOKEN=hf_...            # HuggingFace (free tier)
```

### 2. Provision Neon Postgres (Free)

1. Go to [neon.tech](https://neon.tech)
2. Create project → copy connection string
3. Set as `DATABASE_URL` in Render

### 3. Get HuggingFace Token (Free)

1. Go to [huggingface.co/settings/tokens](https://huggingface.co/settings/tokens)
2. Create read token → set as `HF_API_TOKEN`

Tables, lineage events, and DuckDB data are auto-seeded on first boot.

## Project Structure

```
zetabridge-cloud/
├── backend/
│   ├── main.py                    # FastAPI app, 35 endpoints, seed data
│   ├── requirements.txt
│   ├── agents/
│   │   ├── base.py                # BaseAgent, ToolRegistry, AgentContext
│   │   ├── specialized.py         # 6 agents (1,329 lines)
│   │   └── orchestrator.py        # DAG planner, intent classifier
│   ├── benchmarks/
│   │   └── harness.py             # 17 test scenarios
│   ├── connectors/
│   │   └── registry.py            # 17 OSS connectors
│   ├── copilot/
│   │   └── copilot.py             # 360 co-pilot with session memory
│   └── multimodal/
│       └── data_layer.py          # Structured + Unstructured + Blob
├── dashboard/                     # React + Express frontend
│   ├── client/src/                # React pages (8 views)
│   ├── server/routes.ts           # Express API with mock data
│   └── dist/public/               # Built static output
├── render.yaml                    # One-click Render deploy
├── .env.example
└── README.md
```

## OSS Components Referenced

| Component | Protocol | License | Role |
|-----------|----------|---------|------|
| Apache Gravitino | REST Catalog API | Apache-2.0 | Federated metadata |
| Lakekeeper | Iceberg REST | Apache-2.0 | Iceberg catalog |
| Project Nessie | Nessie REST | Apache-2.0 | Versioned catalog |
| delta-rs | Delta Protocol | Apache-2.0 | Delta Lake tables |
| Apache Iceberg | Table Spec v2 | Apache-2.0 | Iceberg tables |
| DuckDB | Embedded SQL | MIT | Analytics engine |
| Neon Postgres | PG Wire | PostgreSQL | Metadata store |
| dbt-core | CLI/API | Apache-2.0 | SQL transforms |
| dlt | Python API | Apache-2.0 | Data loading |
| Airbyte | REST API | ELv2/MIT | 300+ connectors |
| Apache Hop | REST API | Apache-2.0 | Visual ETL |
| OpenLineage | HTTP API | Apache-2.0 | Lineage standard |
| Marquez | REST API | Apache-2.0 | Lineage storage |
| MinIO/S3 | S3 API | AGPL-3.0 | Object storage |
| Unity Catalog | REST API | Apache-2.0 | Data governance |
| Apache Kafka | Kafka Protocol | Apache-2.0 | Event streaming |
| OpenSearch | REST API | Apache-2.0 | Search engine |
| Arctic Text2SQL | HF Inference | Apache-2.0 | NL→SQL model |

## License

Apache-2.0
