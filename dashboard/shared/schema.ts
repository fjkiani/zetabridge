import { sqliteTable, text, integer } from "drizzle-orm/sqlite-core";
import { createInsertSchema } from "drizzle-zod";
import { z } from "zod";

// ── Catalog Entries ──
export const catalogEntries = sqliteTable("catalog_entries", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  metalake: text("metalake").notNull().default("zetabridge"),
  catalog_name: text("catalog_name").notNull(),
  catalog_type: text("catalog_type").notNull(), // "Iceberg" | "Delta" | "DuckDB"
  schema_name: text("schema_name").notNull(),
  table_name: text("table_name").notNull(),
  columns_json: text("columns_json").notNull().default("[]"),
  properties_json: text("properties_json").notNull().default("{}"),
  created_at: text("created_at").notNull(),
});

export const insertCatalogEntrySchema = createInsertSchema(catalogEntries).omit({
  id: true,
});
export type InsertCatalogEntry = z.infer<typeof insertCatalogEntrySchema>;
export type CatalogEntry = typeof catalogEntries.$inferSelect;

// Column type for JSON parsing
export interface ColumnDef {
  name: string;
  type: string;
  nullable: boolean;
}

// ── Lineage Events ──
export const lineageEvents = sqliteTable("lineage_events", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  run_id: text("run_id").notNull(),
  job_namespace: text("job_namespace").notNull(),
  job_name: text("job_name").notNull(),
  event_type: text("event_type").notNull(), // "START" | "COMPLETE" | "FAIL"
  inputs_json: text("inputs_json").notNull().default("[]"),
  outputs_json: text("outputs_json").notNull().default("[]"),
  facets_json: text("facets_json").notNull().default("{}"),
  event_time: text("event_time").notNull(),
});

export const insertLineageEventSchema = createInsertSchema(lineageEvents).omit({
  id: true,
});
export type InsertLineageEvent = z.infer<typeof insertLineageEventSchema>;
export type LineageEvent = typeof lineageEvents.$inferSelect;

// ── Query Logs ──
export const queryLogs = sqliteTable("query_logs", {
  id: integer("id").primaryKey({ autoIncrement: true }),
  question: text("question").notNull(),
  generated_sql: text("generated_sql").notNull(),
  engine: text("engine").notNull().default("duckdb"),
  row_count: integer("row_count").notNull().default(0),
  duration_ms: integer("duration_ms").notNull().default(0),
  error: text("error"),
  created_at: text("created_at").notNull(),
});

export const insertQueryLogSchema = createInsertSchema(queryLogs).omit({
  id: true,
});
export type InsertQueryLog = z.infer<typeof insertQueryLogSchema>;
export type QueryLog = typeof queryLogs.$inferSelect;

// ── Graph types for lineage visualization ──
export interface LineageNode {
  id: string;
  type: "job" | "dataset";
  name: string;
  namespace: string;
}

export interface LineageEdge {
  source: string;
  target: string;
}

export interface LineageGraph {
  nodes: LineageNode[];
  edges: LineageEdge[];
}
