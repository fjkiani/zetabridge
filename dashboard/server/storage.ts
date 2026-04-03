import {
  type CatalogEntry,
  type InsertCatalogEntry,
  type LineageEvent,
  type InsertLineageEvent,
  type QueryLog,
  type InsertQueryLog,
  type LineageGraph,
  type LineageNode,
  type LineageEdge,
  catalogEntries,
  lineageEvents,
  queryLogs,
} from "@shared/schema";
import { drizzle } from "drizzle-orm/better-sqlite3";
import Database from "better-sqlite3";
import { eq, desc, sql } from "drizzle-orm";

const sqlite = new Database("data.db");
sqlite.pragma("journal_mode = WAL");

export const db = drizzle(sqlite);

export interface IStorage {
  // Catalog
  getCatalogEntries(): CatalogEntry[];
  getCatalogEntry(id: number): CatalogEntry | undefined;
  createCatalogEntry(entry: InsertCatalogEntry): CatalogEntry;
  deleteCatalogEntry(id: number): void;
  getCatalogStats(): { totalTables: number; activeCatalogs: number };

  // Lineage
  getLineageEvents(): LineageEvent[];
  createLineageEvent(event: InsertLineageEvent): LineageEvent;
  getLineageGraph(): LineageGraph;
  getLineageStats(): { totalEvents: number };

  // Query
  getQueryHistory(): QueryLog[];
  createQueryLog(log: InsertQueryLog): QueryLog;
}

export class DatabaseStorage implements IStorage {
  // ── Catalog ──
  getCatalogEntries(): CatalogEntry[] {
    return db.select().from(catalogEntries).all();
  }

  getCatalogEntry(id: number): CatalogEntry | undefined {
    return db.select().from(catalogEntries).where(eq(catalogEntries.id, id)).get();
  }

  createCatalogEntry(entry: InsertCatalogEntry): CatalogEntry {
    return db.insert(catalogEntries).values(entry).returning().get();
  }

  deleteCatalogEntry(id: number): void {
    db.delete(catalogEntries).where(eq(catalogEntries.id, id)).run();
  }

  getCatalogStats(): { totalTables: number; activeCatalogs: number } {
    const tables = db.select().from(catalogEntries).all();
    const catalogs = new Set(tables.map((t) => t.catalog_name));
    return { totalTables: tables.length, activeCatalogs: catalogs.size };
  }

  // ── Lineage ──
  getLineageEvents(): LineageEvent[] {
    return db.select().from(lineageEvents).orderBy(desc(lineageEvents.event_time)).all();
  }

  createLineageEvent(event: InsertLineageEvent): LineageEvent {
    return db.insert(lineageEvents).values(event).returning().get();
  }

  getLineageGraph(): LineageGraph {
    const events = db.select().from(lineageEvents).all();
    const nodesMap = new Map<string, LineageNode>();
    const edgesSet = new Set<string>();
    const edges: LineageEdge[] = [];

    for (const evt of events) {
      // Add job node
      const jobId = `job:${evt.job_namespace}.${evt.job_name}`;
      if (!nodesMap.has(jobId)) {
        nodesMap.set(jobId, {
          id: jobId,
          type: "job",
          name: evt.job_name,
          namespace: evt.job_namespace,
        });
      }

      // Add input datasets and edges
      const inputs: Array<{ namespace: string; name: string }> = JSON.parse(evt.inputs_json || "[]");
      for (const inp of inputs) {
        const dsId = `dataset:${inp.namespace}.${inp.name}`;
        if (!nodesMap.has(dsId)) {
          nodesMap.set(dsId, {
            id: dsId,
            type: "dataset",
            name: inp.name,
            namespace: inp.namespace,
          });
        }
        const edgeKey = `${dsId}->${jobId}`;
        if (!edgesSet.has(edgeKey)) {
          edgesSet.add(edgeKey);
          edges.push({ source: dsId, target: jobId });
        }
      }

      // Add output datasets and edges
      const outputs: Array<{ namespace: string; name: string }> = JSON.parse(evt.outputs_json || "[]");
      for (const out of outputs) {
        const dsId = `dataset:${out.namespace}.${out.name}`;
        if (!nodesMap.has(dsId)) {
          nodesMap.set(dsId, {
            id: dsId,
            type: "dataset",
            name: out.name,
            namespace: out.namespace,
          });
        }
        const edgeKey = `${jobId}->${dsId}`;
        if (!edgesSet.has(edgeKey)) {
          edgesSet.add(edgeKey);
          edges.push({ source: jobId, target: dsId });
        }
      }
    }

    return { nodes: Array.from(nodesMap.values()), edges };
  }

  getLineageStats(): { totalEvents: number } {
    const all = db.select().from(lineageEvents).all();
    return { totalEvents: all.length };
  }

  // ── Query ──
  getQueryHistory(): QueryLog[] {
    return db.select().from(queryLogs).orderBy(desc(queryLogs.created_at)).all();
  }

  createQueryLog(log: InsertQueryLog): QueryLog {
    return db.insert(queryLogs).values(log).returning().get();
  }
}

export const storage = new DatabaseStorage();
