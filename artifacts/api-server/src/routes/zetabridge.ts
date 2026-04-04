import { Router } from "express";

const router = Router();

// ── Mock Data ──────────────────────────────────────────────────────────────

const AGENTS = [
  { name: "catalog_agent", description: "Discovers and manages table metadata across Iceberg, Delta, and DuckDB catalogs", capabilities: ["table_discovery", "schema_introspection", "catalog_sync", "metadata_enrichment"], status: "active" },
  { name: "query_agent", description: "Translates natural language to SQL and executes queries across federated engines", capabilities: ["nl_to_sql", "query_execution", "result_formatting", "query_optimization"], status: "active" },
  { name: "lineage_agent", description: "Tracks data lineage via OpenLineage events, builds dependency graphs", capabilities: ["lineage_tracking", "impact_analysis", "dependency_graph", "event_processing"], status: "active" },
  { name: "etl_agent", description: "Orchestrates data pipelines across Airbyte, dbt, and Spark workloads", capabilities: ["pipeline_orchestration", "transformation_management", "job_scheduling", "failure_recovery"], status: "active" },
  { name: "data_quality_agent", description: "Runs quality checks, validates schemas, and monitors data freshness", capabilities: ["quality_checks", "schema_validation", "freshness_monitoring", "anomaly_detection"], status: "active" },
  { name: "connector_agent", description: "Manages and health-checks 17 data connectors across the platform", capabilities: ["connector_management", "health_monitoring", "config_validation", "protocol_negotiation"], status: "active" },
];

const AGENT_STATS: Record<string, any> = {
  catalog_agent: { total_runs: 342, avg_latency_ms: 128, min_latency_ms: 45, max_latency_ms: 890 },
  query_agent: { total_runs: 1204, avg_latency_ms: 245, min_latency_ms: 67, max_latency_ms: 2100 },
  lineage_agent: { total_runs: 567, avg_latency_ms: 189, min_latency_ms: 52, max_latency_ms: 1450 },
  etl_agent: { total_runs: 203, avg_latency_ms: 1520, min_latency_ms: 320, max_latency_ms: 8900 },
  data_quality_agent: { total_runs: 891, avg_latency_ms: 312, min_latency_ms: 89, max_latency_ms: 3200 },
  connector_agent: { total_runs: 456, avg_latency_ms: 156, min_latency_ms: 34, max_latency_ms: 980 },
};

const TOOLS = [
  { name: "catalog_search", description: "Search tables by name, schema, or type", parameters: { query: "string", catalog: "string?" }, category: "catalog" },
  { name: "schema_inspect", description: "Get column definitions for a table", parameters: { table_fqn: "string" }, category: "catalog" },
  { name: "nl_to_sql", description: "Convert natural language question to SQL", parameters: { question: "string", context: "string?" }, category: "query" },
  { name: "execute_sql", description: "Execute SQL against DuckDB engine", parameters: { sql: "string", engine: "string?" }, category: "query" },
  { name: "lineage_lookup", description: "Find upstream/downstream dependencies", parameters: { dataset: "string", direction: "string?" }, category: "lineage" },
  { name: "lineage_graph", description: "Build full lineage DAG", parameters: {}, category: "lineage" },
  { name: "run_pipeline", description: "Trigger an ETL pipeline run", parameters: { pipeline_id: "string", params: "object?" }, category: "etl" },
  { name: "quality_check", description: "Run data quality assertions", parameters: { table: "string", checks: "string[]?" }, category: "quality" },
  { name: "freshness_check", description: "Check data freshness for a table", parameters: { table: "string", threshold_hours: "number?" }, category: "quality" },
  { name: "connector_health", description: "Check health status of a connector", parameters: { connector_name: "string" }, category: "connector" },
  { name: "list_connectors", description: "List all registered connectors", parameters: {}, category: "connector" },
  { name: "run_benchmark", description: "Run agent performance benchmark suite", parameters: { categories: "string[]?" }, category: "benchmark" },
];

const EXECUTION_HISTORY = [
  { plan_id: "exec-001", intent: "nl_query", session_id: "session-demo", user_input: "What is total revenue this month?", tasks_total: 3, succeeded: 3, failed: 0, total_latency_ms: 245, status: "success", timestamp: "2026-04-04T09:12:00Z" },
  { plan_id: "exec-002", intent: "catalog_discovery", session_id: "session-demo", user_input: "Show me all tables", tasks_total: 2, succeeded: 2, failed: 0, total_latency_ms: 89, status: "success", timestamp: "2026-04-04T09:11:30Z" },
  { plan_id: "exec-003", intent: "lineage_analysis", session_id: "session-demo", user_input: "Build lineage graph", tasks_total: 2, succeeded: 2, failed: 0, total_latency_ms: 312, status: "success", timestamp: "2026-04-04T09:10:45Z" },
  { plan_id: "exec-004", intent: "quality_check", session_id: "session-demo", user_input: "Check analytics tables", tasks_total: 5, succeeded: 4, failed: 1, total_latency_ms: 1890, status: "warning", timestamp: "2026-04-04T09:09:00Z" },
  { plan_id: "exec-005", intent: "connector_status", session_id: "session-demo", user_input: "Health check all connectors", tasks_total: 2, succeeded: 2, failed: 0, total_latency_ms: 67, status: "success", timestamp: "2026-04-04T09:08:20Z" },
  { plan_id: "exec-006", intent: "etl_pipeline", session_id: "session-demo", user_input: "Run raw to staging pipeline", tasks_total: 3, succeeded: 3, failed: 0, total_latency_ms: 8200, status: "success", timestamp: "2026-04-04T09:05:00Z" },
  { plan_id: "exec-007", intent: "nl_query", session_id: "session-demo", user_input: "Count all orders", tasks_total: 2, succeeded: 2, failed: 0, total_latency_ms: 142, status: "success", timestamp: "2026-04-04T09:03:10Z" },
  { plan_id: "exec-008", intent: "quality_check", session_id: "session-demo", user_input: "Check freshness of revenue_daily", tasks_total: 2, succeeded: 2, failed: 0, total_latency_ms: 234, status: "success", timestamp: "2026-04-04T09:00:00Z" },
];

const CATALOG_TABLES = [
  { id: 1, catalog_name: "lakehouse_iceberg", catalog_type: "Iceberg", schema_name: "raw", table_name: "customers", columns_json: JSON.stringify([{ name: "customer_id", type: "BIGINT", nullable: false }, { name: "email", type: "VARCHAR", nullable: false }, { name: "full_name", type: "VARCHAR", nullable: true }, { name: "signup_date", type: "DATE", nullable: false }, { name: "country", type: "VARCHAR", nullable: true }, { name: "lifetime_value", type: "DECIMAL(12,2)", nullable: true }]), properties_json: JSON.stringify({ format: "parquet", location: "s3://lakehouse/raw/customers", partitioned_by: "country" }) },
  { id: 2, catalog_name: "lakehouse_iceberg", catalog_type: "Iceberg", schema_name: "raw", table_name: "orders", columns_json: JSON.stringify([{ name: "order_id", type: "BIGINT", nullable: false }, { name: "customer_id", type: "BIGINT", nullable: false }, { name: "order_date", type: "TIMESTAMP", nullable: false }, { name: "total_amount", type: "DECIMAL(10,2)", nullable: false }, { name: "status", type: "VARCHAR", nullable: false }, { name: "payment_method", type: "VARCHAR", nullable: true }]), properties_json: JSON.stringify({ format: "parquet", location: "s3://lakehouse/raw/orders", partitioned_by: "order_date" }) },
  { id: 3, catalog_name: "lakehouse_iceberg", catalog_type: "Iceberg", schema_name: "staging", table_name: "stg_orders", columns_json: JSON.stringify([{ name: "order_id", type: "BIGINT", nullable: false }, { name: "customer_id", type: "BIGINT", nullable: false }, { name: "order_date", type: "DATE", nullable: false }, { name: "amount_usd", type: "DECIMAL(10,2)", nullable: false }, { name: "status", type: "VARCHAR", nullable: false }]), properties_json: JSON.stringify({ format: "parquet", location: "s3://lakehouse/staging/stg_orders", materialized: "true" }) },
  { id: 4, catalog_name: "lakehouse_delta", catalog_type: "Delta", schema_name: "analytics", table_name: "revenue_daily", columns_json: JSON.stringify([{ name: "date", type: "DATE", nullable: false }, { name: "total_revenue", type: "DECIMAL(14,2)", nullable: false }, { name: "order_count", type: "INTEGER", nullable: false }, { name: "avg_order_value", type: "DECIMAL(10,2)", nullable: false }, { name: "new_customers", type: "INTEGER", nullable: false }]), properties_json: JSON.stringify({ format: "delta", location: "s3://lakehouse/analytics/revenue_daily", retention_days: "30" }) },
  { id: 5, catalog_name: "lakehouse_delta", catalog_type: "Delta", schema_name: "analytics", table_name: "customer_segments", columns_json: JSON.stringify([{ name: "segment_id", type: "INTEGER", nullable: false }, { name: "segment_name", type: "VARCHAR", nullable: false }, { name: "customer_count", type: "INTEGER", nullable: false }, { name: "avg_ltv", type: "DECIMAL(12,2)", nullable: false }, { name: "churn_score", type: "FLOAT", nullable: true }]), properties_json: JSON.stringify({ format: "delta", location: "s3://lakehouse/analytics/customer_segments", z_ordered_by: "segment_id" }) },
  { id: 6, catalog_name: "analytics_duckdb", catalog_type: "DuckDB", schema_name: "reports", table_name: "monthly_summary", columns_json: JSON.stringify([{ name: "month", type: "VARCHAR", nullable: false }, { name: "revenue", type: "DECIMAL(14,2)", nullable: false }, { name: "orders", type: "INTEGER", nullable: false }, { name: "customers", type: "INTEGER", nullable: false }, { name: "churn_rate", type: "FLOAT", nullable: false }]), properties_json: JSON.stringify({ engine: "duckdb", database: ":memory:", updated_by: "dbt" }) },
  { id: 7, catalog_name: "analytics_duckdb", catalog_type: "DuckDB", schema_name: "reports", table_name: "top_products", columns_json: JSON.stringify([{ name: "product_id", type: "INTEGER", nullable: false }, { name: "product_name", type: "VARCHAR", nullable: false }, { name: "total_sales", type: "INTEGER", nullable: false }, { name: "revenue", type: "DECIMAL(12,2)", nullable: false }, { name: "avg_rating", type: "FLOAT", nullable: true }]), properties_json: JSON.stringify({ engine: "duckdb", database: ":memory:", updated_by: "dbt" }) },
];

const LINEAGE_NODES = [
  { id: "ds-customers", type: "dataset", name: "customers", namespace: "lakehouse_iceberg.raw" },
  { id: "ds-orders", type: "dataset", name: "orders", namespace: "lakehouse_iceberg.raw" },
  { id: "job-stg-orders", type: "job", name: "stg_orders_transform", namespace: "zetabridge" },
  { id: "ds-stg-orders", type: "dataset", name: "stg_orders", namespace: "lakehouse_iceberg.staging" },
  { id: "job-revenue-daily", type: "job", name: "revenue_daily_agg", namespace: "zetabridge" },
  { id: "ds-revenue-daily", type: "dataset", name: "revenue_daily", namespace: "lakehouse_delta.analytics" },
  { id: "job-segments", type: "job", name: "customer_segments_ml", namespace: "zetabridge" },
  { id: "ds-segments", type: "dataset", name: "customer_segments", namespace: "lakehouse_delta.analytics" },
  { id: "job-monthly", type: "job", name: "monthly_summary_rollup", namespace: "zetabridge" },
  { id: "ds-monthly", type: "dataset", name: "monthly_summary", namespace: "analytics_duckdb.reports" },
  { id: "job-products", type: "job", name: "top_products_view", namespace: "zetabridge" },
  { id: "ds-products", type: "dataset", name: "top_products", namespace: "analytics_duckdb.reports" },
];

const LINEAGE_EDGES = [
  { source: "ds-customers", target: "job-stg-orders" },
  { source: "ds-orders", target: "job-stg-orders" },
  { source: "job-stg-orders", target: "ds-stg-orders" },
  { source: "ds-stg-orders", target: "job-revenue-daily" },
  { source: "job-revenue-daily", target: "ds-revenue-daily" },
  { source: "ds-customers", target: "job-segments" },
  { source: "job-segments", target: "ds-segments" },
  { source: "ds-revenue-daily", target: "job-monthly" },
  { source: "ds-segments", target: "job-monthly" },
  { source: "job-monthly", target: "ds-monthly" },
  { source: "ds-stg-orders", target: "job-products" },
  { source: "job-products", target: "ds-products" },
];

const CONNECTORS = [
  { name: "apache_iceberg", display_name: "Apache Iceberg", category: "data_lake", description: "Open table format for huge analytic datasets with ACID transactions and schema evolution.", protocol: "REST / S3", oss_component: "Iceberg REST Catalog", license: "Apache-2.0", status: "active" },
  { name: "delta_lake", display_name: "Delta Lake", category: "data_lake", description: "Open-source storage framework that enables building a lakehouse architecture with Spark.", protocol: "Delta Protocol", oss_component: "delta-rs", license: "Apache-2.0", status: "active" },
  { name: "apache_duckdb", display_name: "DuckDB", category: "database", description: "In-process SQL OLAP database management system for analytical queries.", protocol: "SQL/JDBC", oss_component: "duckdb", license: "MIT", status: "active" },
  { name: "apache_kafka", display_name: "Apache Kafka", category: "streaming", description: "Distributed event streaming platform for high-performance data pipelines.", protocol: "Kafka Protocol", oss_component: "kafka-python", license: "Apache-2.0", status: "active" },
  { name: "apache_spark", display_name: "Apache Spark", category: "etl", description: "Unified analytics engine for large-scale data processing with built-in ML.", protocol: "Spark Connect", oss_component: "PySpark", license: "Apache-2.0", status: "active" },
  { name: "apache_flink", display_name: "Apache Flink", category: "streaming", description: "Stateful computations over unbounded and bounded data streams.", protocol: "REST API", oss_component: "PyFlink", license: "Apache-2.0", status: "configured" },
  { name: "dbt_core", display_name: "dbt Core", category: "etl", description: "Data transformation tool for analytics engineering with SQL-based transforms.", protocol: "CLI / REST", oss_component: "dbt-core", license: "Apache-2.0", status: "active" },
  { name: "airbyte", display_name: "Airbyte", category: "etl", description: "Open-source data integration platform with 300+ connectors.", protocol: "Airbyte Protocol", oss_component: "airbyte-api", license: "MIT/ELv2", status: "active" },
  { name: "trino", display_name: "Trino", category: "warehouse", description: "Distributed SQL query engine for federated queries across multiple data sources.", protocol: "JDBC/REST", oss_component: "trino-python-client", license: "Apache-2.0", status: "configured" },
  { name: "apache_hudi", display_name: "Apache Hudi", category: "data_lake", description: "Streaming data lake platform with upserts, deletes, and incremental processing.", protocol: "Hudi API", oss_component: "hudi-spark", license: "Apache-2.0", status: "available" },
  { name: "elasticsearch", display_name: "Elasticsearch", category: "search", description: "Distributed search and analytics engine for full-text search and log analytics.", protocol: "REST/HTTP", oss_component: "elasticsearch-py", license: "Apache-2.0", status: "configured" },
  { name: "minio", display_name: "MinIO", category: "object_storage", description: "High-performance object storage compatible with Amazon S3 API.", protocol: "S3 API", oss_component: "minio-py", license: "AGPL-3.0", status: "active" },
  { name: "apache_gravitino", display_name: "Apache Gravitino", category: "catalog", description: "Unified metadata layer for data lakes, warehouses, and catalogs.", protocol: "REST API", oss_component: "gravitino-client", license: "Apache-2.0", status: "available" },
  { name: "openlineage", display_name: "OpenLineage", category: "lineage", description: "Open standard for lineage metadata collection with Marquez-compatible API.", protocol: "HTTP/REST", oss_component: "openlineage-python", license: "Apache-2.0", status: "active" },
  { name: "apache_atlas", display_name: "Apache Atlas", category: "catalog", description: "Scalable data governance and metadata framework for Hadoop ecosystem.", protocol: "REST API", oss_component: "apache-atlas", license: "Apache-2.0", status: "available" },
  { name: "nessie", display_name: "Project Nessie", category: "catalog", description: "Transactional catalog for Apache Iceberg and Delta Lake with Git-like semantics.", protocol: "REST API", oss_component: "pynessie", license: "Apache-2.0", status: "configured" },
  { name: "redpanda", display_name: "Redpanda", category: "streaming", description: "Kafka-compatible streaming data platform without JVM overhead.", protocol: "Kafka Protocol", oss_component: "kafka-python", license: "BSL 1.1", status: "available" },
];

const BENCHMARK_CASES = [
  { case: "catalog_table_lookup", category: "catalog", passed: true, latency_ms: 89, details: "7 tables resolved in 89ms" },
  { case: "schema_introspection", category: "catalog", passed: true, latency_ms: 112, details: "Column metadata returned correctly" },
  { case: "nl_to_sql_revenue", category: "query", passed: true, latency_ms: 245, details: "SQL generated and validated" },
  { case: "nl_to_sql_customers", category: "query", passed: true, latency_ms: 189, details: "Correct table join inferred" },
  { case: "duckdb_execution", category: "query", passed: true, latency_ms: 67, details: "3 rows returned in 67ms" },
  { case: "lineage_graph_build", category: "lineage", passed: true, latency_ms: 312, details: "8 nodes, 9 edges resolved" },
  { case: "lineage_impact_analysis", category: "lineage", passed: true, latency_ms: 201, details: "Upstream dependencies traced" },
  { case: "quality_check_schema", category: "quality", passed: true, latency_ms: 445, details: "Schema validated across 4 tables" },
  { case: "quality_check_nulls", category: "quality", passed: false, latency_ms: 567, details: "2 null avg_rating values in top_products", error: "Null check failed" },
  { case: "freshness_check", category: "quality", passed: true, latency_ms: 234, details: "All tables within 4-hour threshold" },
  { case: "connector_kafka_health", category: "connector", passed: true, latency_ms: 45, details: "Consumer lag: 0" },
  { case: "connector_minio_health", category: "connector", passed: true, latency_ms: 67, details: "Bucket access confirmed" },
  { case: "etl_pipeline_dbt", category: "etl", passed: true, latency_ms: 3200, details: "5 models materialized" },
  { case: "copilot_catalog_intent", category: "copilot", passed: true, latency_ms: 156, details: "Intent classified correctly" },
  { case: "copilot_nl_query_intent", category: "copilot", passed: true, latency_ms: 178, details: "Query routed to query_agent" },
  { case: "copilot_quality_intent", category: "copilot", passed: true, latency_ms: 143, details: "Quality check dispatched" },
  { case: "copilot_fallback_response", category: "copilot", passed: true, latency_ms: 112, details: "Graceful fallback with suggestions" },
];

const COPILOT_SESSION: Record<string, any[]> = {
  "session-demo": [
    { turn_id: "turn-001", role: "user", content: "Show me all available tables in the catalog", intent: "catalog_discovery", timestamp: "2026-04-02T16:00:00Z", latency_ms: 0 },
    {
      turn_id: "turn-002", role: "assistant",
      content: JSON.stringify({ summary: "Found **7 tables** across 3 catalogs (lakehouse_iceberg, lakehouse_delta, analytics_duckdb). Your data platform covers raw ingestion, staging transforms, and analytics reporting.", data: [{ catalog: "lakehouse_iceberg", schema: "raw", table: "customers", type: "Iceberg", columns: 6 }, { catalog: "lakehouse_iceberg", schema: "raw", table: "orders", type: "Iceberg", columns: 6 }, { catalog: "lakehouse_iceberg", schema: "staging", table: "stg_orders", type: "Iceberg", columns: 5 }, { catalog: "lakehouse_delta", schema: "analytics", table: "revenue_daily", type: "Delta", columns: 5 }, { catalog: "lakehouse_delta", schema: "analytics", table: "customer_segments", type: "Delta", columns: 5 }, { catalog: "analytics_duckdb", schema: "reports", table: "monthly_summary", type: "DuckDB", columns: 5 }, { catalog: "analytics_duckdb", schema: "reports", table: "top_products", type: "DuckDB", columns: 5 }], intent: "catalog_discovery", agents_used: ["catalog_agent"], suggestions: ["Show me the columns in the customers table", "What's the revenue trend this month?", "Run a quality check on all tables"], render_type: "table" }),
      intent: "catalog_discovery", timestamp: "2026-04-02T16:00:02Z", latency_ms: 342,
    },
    { turn_id: "turn-003", role: "user", content: "What's the total revenue this month and how does it compare to last month?", intent: "nl_query", timestamp: "2026-04-02T16:01:00Z", latency_ms: 0 },
    {
      turn_id: "turn-004", role: "assistant",
      content: JSON.stringify({ summary: "March 2026 total revenue is **$1,284,500** — up 11.1% from February ($1,156,200). Order count increased from 7,893 to 8,412 (+6.6%), with average order value rising to $152.70. Customer base grew by 5.2% to 3,201 active customers.", data: [{ month: "2026-03", revenue: "$1,284,500", orders: 8412, customers: 3201, churn_rate: "3.2%" }, { month: "2026-02", revenue: "$1,156,200", orders: 7893, customers: 3042, churn_rate: "4.1%" }, { month: "2026-01", revenue: "$1,092,800", orders: 7234, customers: 2891, churn_rate: "3.8%" }], intent: "nl_query", agents_used: ["query_agent", "catalog_agent"], suggestions: ["Break down revenue by customer segment", "Show the top 10 customers by lifetime value", "What's the churn trend?"], render_type: "table" }),
      intent: "nl_query", timestamp: "2026-04-02T16:01:03Z", latency_ms: 512,
    },
  ],
};

const QUERY_HISTORY = [
  { id: 1, question: "What is the total revenue this month?", generated_sql: "SELECT SUM(total_revenue) AS total FROM revenue_daily WHERE date >= '2026-04-01'", engine: "duckdb", row_count: 1, duration_ms: 142, created_at: "2026-04-04T09:00:00Z" },
  { id: 2, question: "Show me the top 10 customers by lifetime value", generated_sql: "SELECT customer_id, full_name, lifetime_value FROM customers ORDER BY lifetime_value DESC LIMIT 10", engine: "duckdb", row_count: 10, duration_ms: 89, created_at: "2026-04-04T08:45:00Z" },
  { id: 3, question: "How many orders were placed last week?", generated_sql: "SELECT COUNT(*) AS order_count FROM orders WHERE order_date >= '2026-03-26'", engine: "duckdb", row_count: 1, duration_ms: 67, created_at: "2026-04-04T08:30:00Z" },
];

// ── NL Query Handler ──────────────────────────────────────────────────────

function handleNLQuery(question: string): { sql: string; results: any[]; engine: string } {
  const q = question.toLowerCase();
  if (q.includes("revenue") || q.includes("sales") || q.includes("money")) {
    return { sql: "SELECT date, total_revenue, order_count FROM revenue_daily ORDER BY date DESC LIMIT 5", engine: "duckdb", results: [{ date: "2026-04-01", total_revenue: 48250.0, order_count: 312 }, { date: "2026-03-31", total_revenue: 52180.5, order_count: 341 }, { date: "2026-03-30", total_revenue: 39420.0, order_count: 278 }, { date: "2026-03-29", total_revenue: 44100.75, order_count: 295 }, { date: "2026-03-28", total_revenue: 51320.0, order_count: 338 }] };
  }
  if (q.includes("customer") || q.includes("user")) {
    return { sql: "SELECT customer_id, full_name, email, lifetime_value FROM customers ORDER BY lifetime_value DESC LIMIT 5", engine: "duckdb", results: [{ customer_id: 1042, full_name: "Sarah Chen", email: "sarah.chen@example.com", lifetime_value: 24850.0 }, { customer_id: 2891, full_name: "James Wilson", email: "j.wilson@corp.io", lifetime_value: 18930.5 }, { customer_id: 503, full_name: "Maria Garcia", email: "mgarcia@mail.com", lifetime_value: 16240.0 }] };
  }
  if (q.includes("order") || q.includes("purchase")) {
    return { sql: "SELECT order_id, customer_id, order_date, total_amount, status FROM orders ORDER BY order_date DESC LIMIT 5", engine: "duckdb", results: [{ order_id: 98234, customer_id: 1042, order_date: "2026-04-02", total_amount: 189.99, status: "completed" }, { order_id: 98233, customer_id: 503, order_date: "2026-04-02", total_amount: 45.50, status: "completed" }, { order_id: 98232, customer_id: 2891, order_date: "2026-04-01", total_amount: 312.00, status: "shipped" }] };
  }
  return { sql: "SELECT * FROM monthly_summary ORDER BY month DESC LIMIT 3", engine: "duckdb", results: [{ month: "2026-03", revenue: 1284500.0, orders: 8412, customers: 3201 }, { month: "2026-02", revenue: 1156200.0, orders: 7893, customers: 3042 }, { month: "2026-01", revenue: 1092800.0, orders: 7234, customers: 2891 }] };
}

function handleCopilotChat(message: string): any {
  const q = (message || "").toLowerCase();
  if (q.includes("table") || q.includes("catalog") || q.includes("schema")) {
    return { summary: "Found **7 tables** across 3 catalogs (lakehouse_iceberg, lakehouse_delta, analytics_duckdb).", data: [{ catalog: "lakehouse_iceberg", schema: "raw", table: "customers", type: "Iceberg" }, { catalog: "lakehouse_iceberg", schema: "raw", table: "orders", type: "Iceberg" }, { catalog: "lakehouse_delta", schema: "analytics", table: "revenue_daily", type: "Delta" }], intent: "catalog_discovery", agents_used: ["catalog_agent"], suggestions: ["Show columns for customers table", "What type of catalog is lakehouse_iceberg?", "Run quality check"], render_type: "table", tasks: 3 };
  }
  if (q.includes("revenue") || q.includes("sales")) {
    return { summary: "March 2026 revenue is **$1,284,500**, up 11.1% from February. 8,412 orders from 3,201 customers.", data: [{ month: "2026-03", revenue: "$1,284,500", orders: 8412 }, { month: "2026-02", revenue: "$1,156,200", orders: 7893 }], intent: "nl_query", agents_used: ["query_agent", "catalog_agent"], suggestions: ["Break down by segment", "Show daily trend", "Compare to Q1 last year"], render_type: "table", tasks: 4 };
  }
  if (q.includes("quality") || q.includes("check") || q.includes("validation")) {
    return { summary: "Quality check complete. Overall score: **A** (96.5%). 2 minor issues found.", data: [{ table: "revenue_daily", score: "A+", issues: 0 }, { table: "customer_segments", score: "A", issues: 1 }, { table: "monthly_summary", score: "A+", issues: 0 }, { table: "top_products", score: "B+", issues: 2 }], intent: "quality_check", agents_used: ["data_quality_agent", "catalog_agent"], suggestions: ["Fix null ratings", "Show schema drift details", "Schedule daily checks"], render_type: "table", tasks: 5 };
  }
  if (q.includes("lineage") || q.includes("dependency") || q.includes("upstream")) {
    return { summary: "Lineage graph has 12 nodes and 12 edges. The revenue_daily table is derived from stg_orders → raw.orders + raw.customers.", intent: "lineage_analysis", agents_used: ["lineage_agent"], suggestions: ["Show impact of changing customers table", "View full lineage graph", "Which jobs use revenue_daily?"], render_type: "text", tasks: 2 };
  }
  if (q.includes("agent") || q.includes("benchmark") || q.includes("performance")) {
    return { summary: "6 agents are active. Latest benchmark run: 16/17 tests passed (94.1% pass rate). catalog_agent is the fastest (128ms avg).", intent: "agent_status", agents_used: ["connector_agent"], suggestions: ["Run a fresh benchmark", "Show agent execution history", "Which agent has the highest latency?"], render_type: "text", tasks: 1 };
  }
  return { summary: "I can help you explore your data platform. You can ask about tables, query your data with natural language, check lineage, run quality checks, or monitor agents.", intent: "general", agents_used: ["catalog_agent"], suggestions: ["Show me all tables", "What's the revenue this month?", "Run a quality check", "Show the lineage graph"], render_type: "text", tasks: 1 };
}

// ── Routes ──────────────────────────────────────────────────────────────────

router.get("/platform/status", (_req, res) => {
  const passed = BENCHMARK_CASES.filter(b => b.passed).length;
  res.json({
    agents: { total: AGENTS.length, active: AGENTS.filter(a => a.status === "active").length },
    tools: { total: TOOLS.length },
    connectors: { total: CONNECTORS.length, active: CONNECTORS.filter(c => c.status === "active").length },
    data_stores: { tables: CATALOG_TABLES.length, catalogs: 3 },
    lineage: { nodes: LINEAGE_NODES.length, edges: LINEAGE_EDGES.length },
    benchmark_summary: { total: BENCHMARK_CASES.length, passed, failed: BENCHMARK_CASES.length - passed, pass_rate: passed / BENCHMARK_CASES.length },
  });
});

// Catalog
router.get("/catalog/tables", (_req, res) => {
  res.json(CATALOG_TABLES.map(e => ({ ...e, columns: JSON.parse(e.columns_json), properties: JSON.parse(e.properties_json), fqn: `${e.catalog_name}.${e.schema_name}.${e.table_name}` })));
});

router.get("/catalog/stats", (_req, res) => {
  const byType: Record<string, number> = {};
  CATALOG_TABLES.forEach(e => { byType[e.catalog_type] = (byType[e.catalog_type] || 0) + 1; });
  const catalogs = [...new Set(CATALOG_TABLES.map(e => e.catalog_name))];
  res.json({ total_tables: CATALOG_TABLES.length, catalogs: catalogs.length, schemas: 5, by_type: byType, catalog_names: catalogs });
});

// Lineage
router.get("/lineage/graph", (_req, res) => {
  res.json({ nodes: LINEAGE_NODES, edges: LINEAGE_EDGES });
});

router.get("/lineage/stats", (_req, res) => {
  const jobs = LINEAGE_NODES.filter(n => n.type === "job");
  const datasets = LINEAGE_NODES.filter(n => n.type === "dataset");
  res.json({ total_events: 34, unique_jobs: jobs.length, unique_datasets: datasets.length, by_type: { START: 12, COMPLETE: 12, FAIL: 2, RUNNING: 8 } });
});

// Agents
router.get("/agents", (_req, res) => res.json(AGENTS));
router.get("/agents/stats", (_req, res) => res.json(AGENT_STATS));
router.get("/agents/tools", (_req, res) => res.json(TOOLS));
router.get("/agents/execution-history", (_req, res) => res.json(EXECUTION_HISTORY));

router.post("/benchmarks/run", (_req, res) => {
  const passed = BENCHMARK_CASES.filter(b => b.passed).length;
  const totalLatency = BENCHMARK_CASES.reduce((s, b) => s + b.latency_ms, 0);
  res.json({ passed, failed: BENCHMARK_CASES.length - passed, total: BENCHMARK_CASES.length, pass_rate: passed / BENCHMARK_CASES.length, avg_latency_ms: Math.round(totalLatency / BENCHMARK_CASES.length), results: BENCHMARK_CASES });
});

// Benchmarks
router.get("/benchmarks/summary", (_req, res) => {
  const passed = BENCHMARK_CASES.filter(b => b.passed).length;
  const totalLatency = BENCHMARK_CASES.reduce((s, b) => s + b.latency_ms, 0);
  res.json({ passed, failed: BENCHMARK_CASES.length - passed, total: BENCHMARK_CASES.length, pass_rate: passed / BENCHMARK_CASES.length, avg_latency_ms: Math.round(totalLatency / BENCHMARK_CASES.length) });
});

router.get("/benchmarks/results", (_req, res) => res.json(BENCHMARK_CASES));

// Query
router.get("/query/history", (_req, res) => res.json(QUERY_HISTORY));

router.post("/query/nl", (req, res) => {
  const { question } = req.body as { question?: string };
  if (!question) return res.status(400).json({ error: "question is required" });
  const result = handleNLQuery(question);
  const duration_ms = Math.floor(Math.random() * 200) + 50;
  QUERY_HISTORY.unshift({ id: QUERY_HISTORY.length + 1, question, generated_sql: result.sql, engine: result.engine, row_count: result.results.length, duration_ms, created_at: new Date().toISOString() });
  res.json({ sql: result.sql, results: result.results, engine: result.engine, duration_ms, row_count: result.results.length });
});

// Connectors
router.get("/connectors", (_req, res) => res.json(CONNECTORS));
router.get("/connectors/stats", (_req, res) => {
  const byCategory: Record<string, number> = {};
  CONNECTORS.forEach(c => { byCategory[c.category] = (byCategory[c.category] || 0) + 1; });
  res.json({ total_connectors: CONNECTORS.length, active: CONNECTORS.filter(c => c.status === "active").length, configured: CONNECTORS.filter(c => c.status === "configured").length, available: CONNECTORS.filter(c => c.status === "available").length, by_category: byCategory });
});

// Copilot
router.get("/copilot/sessions/:sessionId/history", (req, res) => {
  const { sessionId } = req.params;
  const turns = COPILOT_SESSION[sessionId] || [];
  res.json(turns);
});

router.post("/copilot/chat", (req, res) => {
  const { message, session_id } = req.body as { message?: string; session_id?: string };
  if (!message) return res.status(400).json({ error: "message is required" });
  const sid = session_id || "session-demo";
  const latency = Math.floor(Math.random() * 400) + 100;
  const result = handleCopilotChat(message);
  if (!COPILOT_SESSION[sid]) COPILOT_SESSION[sid] = [];
  COPILOT_SESSION[sid].push({ turn_id: `turn-${Date.now()}`, role: "user", content: message, timestamp: new Date().toISOString(), latency_ms: 0 });
  COPILOT_SESSION[sid].push({ turn_id: `turn-${Date.now() + 1}`, role: "assistant", ...result, timestamp: new Date().toISOString(), latency_ms: latency });
  res.json({ session_id: sid, ...result, latency_ms: latency });
});

export default router;
