/**
 * Zeta Bridge graph API layer.
 *
 * Talks to the read-only `/api/graph/*` backend when reachable, and falls back
 * to a baked static snapshot (public/graph-snapshot/graph-snapshot.json) when
 * the backend/Neo4j is unreachable or no API base is configured. Every graph
 * page imports from here so the data source is transparent to the UI.
 *
 * Auth: sends `X-Zeta-Api-Key` (VITE_ZETA_API_KEY). The Neo4j credentials are
 * NEVER in the front-end — only the scoped graph API key.
 *
 * The backend and the snapshot use slightly different node shapes; both are
 * normalized into the canonical `GNode` here.
 */

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
const API_KEY = (import.meta.env.VITE_ZETA_API_KEY as string | undefined) ?? "";
const SNAPSHOT_URL = `${import.meta.env.BASE_URL ?? "/"}graph-snapshot/graph-snapshot.json`.replace(/\/+/g, "/");

export type EndpointCode = "A_MSK" | "B_SAS" | "C_EGA" | null;

export const ENDPOINT_META: Record<
  Exclude<EndpointCode, null>,
  { label: string; short: string; color: string }
> = {
  A_MSK: { label: "MSK SPECTRUM / Synapse", short: "MSK", color: "hsl(193, 100%, 50%)" },
  B_SAS: { label: "PDS / SAS clinical trials", short: "SAS", color: "hsl(45, 100%, 55%)" },
  C_EGA: { label: "EGA / BriTROC HGSOC", short: "EGA", color: "hsl(280, 70%, 62%)" },
};

/** Canonical node shape used across all graph pages. */
export interface GNode {
  id: string;
  name: string;
  label: string;
  labels: string[];
  type?: string | null;
  endpoint: EndpointCode;
  degree?: number;
  relSummary?: Record<string, number>;
  attributesJson?: string;
  props?: Record<string, unknown>;
}

export interface GEdge {
  source: string;
  target: string;
  type: string;
}

export interface GSubgraph {
  center?: string;
  hops?: number;
  nodes: GNode[];
  edges: GEdge[];
}

export interface GPath {
  node_ids: string[];
  rel_types: string[];
  hops: number;
  source_endpoint: EndpointCode;
  target_endpoint: EndpointCode;
}

export interface GSchema {
  labels: { label: string; count: number }[];
  relationship_types: { type: string; count: number }[];
  endpoints: Record<string, { prefixes: string[]; node_count: number }>;
  totals: { nodes: number; edges: number };
  live: boolean;
}

// ---------------------------------------------------------------------------
// id-prefix -> endpoint (mirrors backend ENDPOINT_PREFIXES)
// ---------------------------------------------------------------------------
const ENDPOINT_PREFIXES: Record<Exclude<EndpointCode, null>, string[]> = {
  A_MSK: ["genomicfeature:msk:", "biospecimen:msk:", "cohort:msk", "vault:synapse"],
  B_SAS: ["patient:sas:", "trial:sas:", "arm:sas:", "clinical_table:sas:", "trial_design:sas:", "vault:sas"],
  C_EGA: ["ega:file:", "ega:sample:", "ega:dataset:", "specimen:britroc1:", "cohort:britroc", "vault:ega"],
};

export function endpointOf(id: string | null | undefined): EndpointCode {
  if (!id) return null;
  for (const code of Object.keys(ENDPOINT_PREFIXES) as Exclude<EndpointCode, null>[]) {
    if (ENDPOINT_PREFIXES[code].some((p) => id.startsWith(p))) return code;
  }
  // EGA / BriTROC HGSOC live-graph ids (Session 16): EGA accessions
  // (EGAD dataset · EGAF file · EGAN sample · EGAR run · EGAS study …) and
  // Subject ids namespaced as "<NAMESPACE>:<subject_id>" (JBLAB / PATIENT_INT).
  // The live nodes also carry an explicit `endpoint` property that normNode
  // prefers; this id fallback keeps snapshot/id-only code paths coloring EGA.
  if (/^EGA[A-Z]\d/i.test(id) || /^(JBLAB|PATIENT_INT):/i.test(id)) return "C_EGA";
  return null;
}

// ---------------------------------------------------------------------------
// node normalization: accept both live (`_labels`/`_type`/`_endpoint`) and
// snapshot (`labels`/`label`/`endpoint`) shapes.
// ---------------------------------------------------------------------------
function normNode(raw: any): GNode {
  const id = String(raw.id ?? "");
  const labels: string[] = raw._labels ?? raw.labels ?? [];
  const label = raw.label ?? labels.find((l: string) => !["Entity", "ZetaVault", "Resource"].includes(l)) ?? labels[0] ?? raw._type ?? raw.type ?? "Node";
  return {
    id,
    name: String(raw.name ?? id),
    label,
    labels,
    type: raw._type ?? raw.type ?? null,
    endpoint: (raw._endpoint ?? raw.endpoint ?? endpointOf(id)) as EndpointCode,
    degree: raw._degree ?? raw.degree,
    relSummary: raw._rel_summary ?? raw.relSummary,
    attributesJson: raw.attributes_json ?? raw.attributesJson,
    props: raw.props ?? undefined,
  };
}

function normEdge(raw: any): GEdge {
  return {
    source: String(raw.source ?? raw.start ?? ""),
    target: String(raw.target ?? raw.end ?? ""),
    type: String(raw.type ?? raw.rel ?? "REL"),
  };
}

// ---------------------------------------------------------------------------
// low-level fetch with API key + snapshot fallback signalling
// ---------------------------------------------------------------------------
class BackendUnavailable extends Error {}

async function apiFetch(path: string, init?: RequestInit): Promise<any> {
  if (!API_BASE) throw new BackendUnavailable("No VITE_API_BASE configured");
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: {
        ...(init?.body ? { "Content-Type": "application/json" } : {}),
        ...(API_KEY ? { "X-Zeta-Api-Key": API_KEY } : {}),
        ...(init?.headers ?? {}),
      },
    });
  } catch (e) {
    throw new BackendUnavailable(String(e));
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    throw new BackendUnavailable(`Backend ${res.status}`);
  }
  if (!res.ok) {
    const txt = (await res.text().catch(() => "")) || res.statusText;
    throw new Error(`${res.status}: ${txt}`);
  }
  return res.json();
}

// snapshot cache
let _snapshot: any | null = null;
export async function loadSnapshot(): Promise<any> {
  if (_snapshot) return _snapshot;
  const res = await fetch(SNAPSHOT_URL);
  if (!res.ok) throw new Error(`Snapshot unavailable (${res.status})`);
  _snapshot = await res.json();
  return _snapshot;
}

/** Whether the last data call came from the live backend. Pages can show a badge. */
export let LAST_SOURCE: "live" | "snapshot" = "snapshot";

function allSnapshotNodes(snap: any): GNode[] {
  const out: GNode[] = [];
  const seen = new Set<string>();
  const push = (arr: any[]) => {
    for (const r of arr ?? []) {
      const n = normNode(r);
      if (n.id && !seen.has(n.id)) {
        seen.add(n.id);
        out.push(n);
      }
    }
  };
  for (const k of Object.keys(snap.seed_nodes ?? {})) push(snap.seed_nodes[k]);
  push(snap.s12_nodes ?? []);
  return out;
}

// ---------------------------------------------------------------------------
// public API (live-first, snapshot fallback)
// ---------------------------------------------------------------------------
export async function getSchema(): Promise<GSchema> {
  try {
    const raw = await apiFetch("/api/graph/schema");
    const health = await apiFetch("/api/graph/health").catch(() => null);
    LAST_SOURCE = "live";
    const endpoints: GSchema["endpoints"] = {};
    for (const [code, prefixes] of Object.entries(raw.endpoint_prefixes ?? {})) {
      endpoints[code] = { prefixes: prefixes as string[], node_count: 0 };
    }
    return {
      labels: Object.entries(raw.label_counts ?? {}).map(([label, count]) => ({ label, count: count as number })).sort((a, b) => b.count - a.count),
      relationship_types: Object.entries(raw.relationship_counts ?? {}).map(([type, count]) => ({ type, count: count as number })).sort((a, b) => b.count - a.count),
      endpoints,
      totals: { nodes: health?.nodes ?? 0, edges: health?.relationships ?? 0 },
      live: true,
    };
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) throw e;
    const snap = await loadSnapshot();
    LAST_SOURCE = "snapshot";
    return {
      labels: snap.schema.labels,
      relationship_types: snap.schema.relationship_types,
      endpoints: snap.endpoints,
      totals: snap.totals,
      live: false,
    };
  }
}

export async function searchNodes(params: {
  prefix?: string;
  label?: string;
  type?: string;
  name_contains?: string;
  limit?: number;
}): Promise<GNode[]> {
  try {
    const raw = await apiFetch("/api/graph/search", { method: "POST", body: JSON.stringify(params) });
    LAST_SOURCE = "live";
    return (raw.nodes ?? []).map(normNode);
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) throw e;
    const snap = await loadSnapshot();
    LAST_SOURCE = "snapshot";
    let nodes = allSnapshotNodes(snap);
    if (params.prefix) nodes = nodes.filter((n) => n.id.startsWith(params.prefix!));
    if (params.label) nodes = nodes.filter((n) => n.labels.includes(params.label!) || n.label === params.label);
    if (params.name_contains) {
      const q = params.name_contains.toLowerCase();
      nodes = nodes.filter((n) => n.name.toLowerCase().includes(q) || n.id.toLowerCase().includes(q));
    }
    return nodes.slice(0, params.limit ?? 50);
  }
}

export async function getNode(id: string): Promise<GNode | null> {
  try {
    const raw = await apiFetch(`/api/graph/node/${encodeURIComponent(id)}`);
    LAST_SOURCE = "live";
    return normNode(raw);
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) {
      if (String(e).startsWith("404")) return null;
      throw e;
    }
    const snap = await loadSnapshot();
    LAST_SOURCE = "snapshot";
    return allSnapshotNodes(snap).find((n) => n.id === id) ?? null;
  }
}

export async function getNeighbors(params: {
  id: string;
  hops?: number;
  rel_types?: string[];
  direction?: "in" | "out" | "both";
  cap?: number;
}): Promise<GSubgraph> {
  try {
    const raw = await apiFetch("/api/graph/neighbors", { method: "POST", body: JSON.stringify(params) });
    LAST_SOURCE = "live";
    return {
      center: raw.center,
      hops: raw.hops,
      nodes: (raw.nodes ?? []).map(normNode),
      edges: (raw.edges ?? []).map(normEdge),
    };
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) throw e;
    const snap = await loadSnapshot();
    LAST_SOURCE = "snapshot";
    // build a local neighborhood from snapshot seed/s12 edges
    const allEdges: GEdge[] = [
      ...(snap.seed_edges ?? []).map(normEdge),
      ...(snap.s12_edges ?? []).map(normEdge),
    ];
    const nodeMap = new Map(allSnapshotNodes(snap).map((n) => [n.id, n]));
    const keepNodeIds = new Set<string>([params.id]);
    const edges: GEdge[] = [];
    for (const e of allEdges) {
      if (e.source === params.id || e.target === params.id) {
        edges.push(e);
        keepNodeIds.add(e.source);
        keepNodeIds.add(e.target);
      }
    }
    const nodes = [...keepNodeIds].map((nid) => nodeMap.get(nid) ?? normNode({ id: nid }));
    return { center: params.id, hops: params.hops ?? 1, nodes, edges };
  }
}

export async function findPaths(params: {
  source_id: string;
  target_id?: string;
  target_prefix?: string;
  max_hops?: number;
  k?: number;
}): Promise<{ count: number; paths: GPath[]; source: string; target: string }> {
  try {
    const raw = await apiFetch("/api/graph/paths", { method: "POST", body: JSON.stringify(params) });
    LAST_SOURCE = "live";
    return raw;
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) throw e;
    const snap = await loadSnapshot();
    LAST_SOURCE = "snapshot";
    // serve precomputed example paths that match the source/target intent
    let paths: GPath[] = snap.example_paths ?? [];
    paths = paths.filter((p) => p.node_ids[0]?.startsWith(params.source_id) || p.node_ids[0] === params.source_id || endpointOf(params.source_id) === p.source_endpoint);
    if (params.target_prefix) paths = paths.filter((p) => p.node_ids[p.node_ids.length - 1]?.startsWith(params.target_prefix!));
    return { count: paths.length, paths: paths.slice(0, params.k ?? 10), source: params.source_id, target: params.target_id ?? params.target_prefix ?? "" };
  }
}

/** S12 minted nodes grouped by label — used by the Insights page. Snapshot-backed. */
export async function getS12Insights(): Promise<{
  byLabel: Record<string, GNode[]>;
  edges: GEdge[];
  examplePaths: GPath[];
  live: boolean;
}> {
  const snap = await loadSnapshot();
  const byLabel: Record<string, GNode[]> = {};
  for (const raw of snap.s12_nodes ?? []) {
    const n = normNode(raw);
    (byLabel[n.label] ??= []).push(n);
  }
  return {
    byLabel,
    edges: (snap.s12_edges ?? []).map(normEdge),
    examplePaths: snap.example_paths ?? [],
    live: false,
  };
}

export function apiConfigured(): boolean {
  return Boolean(API_BASE);
}

// ---------------------------------------------------------------------------
// read-only Cypher (live-only; no snapshot fallback by design)
// ---------------------------------------------------------------------------
export interface CypherResult {
  columns: string[];
  rows: Record<string, unknown>[];
  row_count: number;
}

/**
 * Run a read-only Cypher query against the live backend `/api/graph/cypher`.
 * The backend enforces a read-only guard + row cap; write/DDL is rejected 403.
 * Live-only: if the backend is unreachable this throws (pages surface it
 * honestly rather than swapping in stale snapshot rows).
 */
export async function runCypher(
  cypher: string,
  params?: Record<string, unknown>,
  cap?: number,
): Promise<CypherResult> {
  const raw = await apiFetch("/api/graph/cypher", {
    method: "POST",
    body: JSON.stringify({ cypher, params: params ?? null, cap: cap ?? null }),
  });
  LAST_SOURCE = "live";
  return raw as CypherResult;
}
