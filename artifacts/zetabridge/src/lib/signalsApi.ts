/**
 * Zeta Bridge signal-intelligence API layer (Session 14).
 *
 * Talks to the read-only `/api/signals/*` backend when reachable, and falls
 * back to the baked static snapshot (public/graph-snapshot/graph-snapshot.json,
 * schema_version >= 2) when the backend/Neo4j is unreachable or no API base is
 * configured. Every value surface (Signals hub, slug detail, Bridges, Gaps,
 * Value, Insight Analyst) imports from here so the data source is transparent.
 *
 * Auth: sends `X-Zeta-Api-Key` (VITE_ZETA_API_KEY). Neo4j credentials are NEVER
 * in the front-end — only the scoped graph API key.
 *
 * Grounding contract: every number surfaced here originates from a real node or
 * edge in the live graph (live path) or from the real export in the snapshot.
 * The `strength_derived` field is a clearly-labelled per-family min-max
 * normalization for cross-family ordering only — never a source value.
 */

import type { EndpointCode } from "./graphApi";
import { endpointOf, loadSnapshot } from "./graphApi";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
const API_KEY = (import.meta.env.VITE_ZETA_API_KEY as string | undefined) ?? "";

// ---------------------------------------------------------------------------
// types (mirror backend /api/signals/* response contracts)
// ---------------------------------------------------------------------------
export type SignalFamily =
  | "drug_ae"
  | "pharmacovig"
  | "genomic_bridge"
  | "cross_trial"
  | "outlier";

export const FAMILY_META: Record<
  SignalFamily,
  { label: string; short: string; metric: string; metricLabel: string; blurb: string }
> = {
  drug_ae: {
    label: "Drug → Adverse-Event enrichment",
    short: "Drug/AE",
    metric: "rate_ratio",
    metricLabel: "Rate ratio",
    blurb: "How much more often an AE occurs on the experimental drug vs control arm.",
  },
  pharmacovig: {
    label: "Pharmacovigilance disproportionality",
    short: "ROR",
    metric: "ror",
    metricLabel: "ROR",
    blurb: "Reporting Odds Ratio — classic disproportionality signal used by regulators.",
  },
  genomic_bridge: {
    label: "Genomic → clinical AE bridge",
    short: "Bridge",
    metric: "bridge_score",
    metricLabel: "Bridge score",
    blurb: "Edge-backed link from an MSK genomic feature to a SAS clinical toxicity term.",
  },
  cross_trial: {
    label: "Cross-trial escalation pattern",
    short: "Cross-trial",
    metric: "consistency_score",
    metricLabel: "Consistency",
    blurb: "How reproducibly an AE escalates across independent trials.",
  },
  outlier: {
    label: "Adverse-event outlier",
    short: "Outlier",
    metric: "rate_ratio",
    metricLabel: "Rate ratio",
    blurb: "Extreme AE outliers (e.g. present in the exp arm, absent in control).",
  },
};

export interface SignalItem {
  slug: string;
  family: SignalFamily | string;
  label: string;
  name: string;
  native_metric: string | null;
  native_value: number | null;
  strength_derived: number | null;
  endpoint: EndpointCode;
  session?: number | string | null;
  detail?: Record<string, unknown>;
  attrs?: Record<string, unknown>;
}

export interface TopSignalsResult {
  family: string;
  count: number;
  signals: SignalItem[];
}

export interface BridgeItem {
  slug: string;
  gene: string | null;
  ae_term: string | null;
  bridge_score: number | null;
  recurrence_pct: number | null;
  strength_derived?: number | null;
  gene_node: { id: string; name: string | null; endpoint: EndpointCode };
  ae_node: { id: string; name: string | null; endpoint: EndpointCode };
  path: string[];
}

export interface BridgesResult {
  count: number;
  bridges: BridgeItem[];
}

export interface GapItem {
  slug: string;
  name: string | null;
  gap_type: string | null;
  current_tier: string | null;
  impact: string | null;
  closes_gap: string | null;
  session?: number | string | null;
  attrs?: Record<string, unknown>;
}

export interface GapsResult {
  count: number;
  gaps: GapItem[];
}

export interface OverviewResult {
  totals: { nodes: number; relationships: number };
  endpoints: Record<string, number>;
  counts_by_family: Record<string, number>;
  n_signals: number;
  n_blind_spots: number;
  headline: {
    cross_endpoint_reachability: { directed_pairs: number; all_100pct: boolean; max_hops: number };
    top_broker: {
      node: string | null;
      betweenness: number | null;
      degree?: number | null;
      endpoint_span?: string[] | null;
      kind?: string | null;
    } | null;
  };
}

export interface SignalDetailResult {
  slug: string;
  labels: string[];
  name: string | null;
  attrs: Record<string, unknown>;
  provenance: { session?: number | string | null; mint_planner?: unknown };
  connections: {
    nodes: { id: string; name: string | null; labels: string[]; endpoint: EndpointCode }[];
    edges: { rel: string; source: string; target: string }[];
  };
}

export type AgentName = "signal_miner" | "bridge_hunter" | "gap_auditor";

export interface AgentResponse {
  agent: string;
  action?: string;
  summary: string;
  findings: unknown[];
  grounding: string[];
  used_llm: boolean;
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

/** Whether the last data call came from the live backend. Pages can show a badge. */
export let LAST_SIGNAL_SOURCE: "live" | "snapshot" = "snapshot";

export function apiConfigured(): boolean {
  return Boolean(API_BASE);
}

// ---------------------------------------------------------------------------
// snapshot helpers (schema_version >= 2 signal layer)
// ---------------------------------------------------------------------------
function snapFamilies(snap: any): Record<string, any> {
  return snap?.signals?.families ?? {};
}

function snapSignalToItem(s: any): SignalItem {
  return {
    slug: s.slug,
    family: s.family,
    label: s.label,
    name: s.name,
    native_metric: s.native_metric ?? null,
    native_value: s.native_value ?? null,
    strength_derived: s.strength_derived ?? null,
    endpoint: (s.endpoint ?? endpointOf(s.slug)) as EndpointCode,
    session: s.session ?? null,
    detail: s.detail ?? {},
    attrs: s.detail ?? {},
  };
}

// round-robin interleave for "all" (mirrors backend ordering intent)
function interleaveAll(families: Record<string, any>, limit: number): SignalItem[] {
  const lists = Object.values(families).map((f: any) => (f.signals ?? []).map(snapSignalToItem));
  const merged: SignalItem[] = [];
  let idx = 0;
  let progressed = true;
  while (progressed && merged.length < 10000) {
    progressed = false;
    for (const lst of lists) {
      if (idx < lst.length) {
        merged.push(lst[idx]);
        progressed = true;
      }
    }
    idx += 1;
  }
  return merged.slice(0, limit);
}

// ---------------------------------------------------------------------------
// public API (live-first, snapshot fallback)
// ---------------------------------------------------------------------------
export async function getOverview(): Promise<OverviewResult> {
  try {
    const raw = await apiFetch("/api/signals/overview");
    LAST_SIGNAL_SOURCE = "live";
    return raw;
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) throw e;
    const snap = await loadSnapshot();
    LAST_SIGNAL_SOURCE = "snapshot";
    if (snap.overview) return snap.overview;
    // minimal reconstruction if an older snapshot slipped through
    const fam = snapFamilies(snap);
    const counts: Record<string, number> = {};
    let n = 0;
    for (const [k, v] of Object.entries<any>(fam)) {
      counts[k] = v.total ?? 0;
      n += v.total ?? 0;
    }
    return {
      totals: { nodes: snap.totals?.nodes ?? 0, relationships: snap.totals?.edges ?? 0 },
      endpoints: Object.fromEntries(
        Object.entries<any>(snap.endpoints ?? {}).map(([k, v]) => [k, v.node_count ?? 0]),
      ),
      counts_by_family: counts,
      n_signals: n,
      n_blind_spots: snap.gaps?.count ?? 0,
      headline: {
        cross_endpoint_reachability: { directed_pairs: 0, all_100pct: false, max_hops: 0 },
        top_broker: null,
      },
    };
  }
}

export async function getTopSignals(
  family: SignalFamily | "all" = "all",
  limit = 24,
): Promise<TopSignalsResult> {
  try {
    const raw = await apiFetch(`/api/signals/top?family=${encodeURIComponent(family)}&limit=${limit}`);
    LAST_SIGNAL_SOURCE = "live";
    return raw;
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) throw e;
    const snap = await loadSnapshot();
    LAST_SIGNAL_SOURCE = "snapshot";
    const fam = snapFamilies(snap);
    if (family === "all") {
      const total = Object.values<any>(fam).reduce((a, f) => a + (f.total ?? 0), 0);
      return { family: "all", count: total, signals: interleaveAll(fam, limit) };
    }
    const f = fam[family];
    if (!f) return { family, count: 0, signals: [] };
    return { family, count: f.total ?? 0, signals: (f.signals ?? []).slice(0, limit).map(snapSignalToItem) };
  }
}

export async function getBridges(): Promise<BridgesResult> {
  try {
    const raw = await apiFetch("/api/signals/bridges");
    LAST_SIGNAL_SOURCE = "live";
    return raw;
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) throw e;
    const snap = await loadSnapshot();
    LAST_SIGNAL_SOURCE = "snapshot";
    const b = snap.bridges;
    if (!b) return { count: 0, bridges: [] };
    return { count: b.total ?? b.bridges?.length ?? 0, bridges: b.bridges ?? [] };
  }
}

export async function getGaps(): Promise<GapsResult> {
  try {
    const raw = await apiFetch("/api/signals/gaps");
    LAST_SIGNAL_SOURCE = "live";
    return raw;
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) throw e;
    const snap = await loadSnapshot();
    LAST_SIGNAL_SOURCE = "snapshot";
    const g = snap.gaps;
    if (!g) return { count: 0, gaps: [] };
    return { count: g.count ?? g.gaps?.length ?? 0, gaps: g.gaps ?? [] };
  }
}

export async function getSignalDetail(slug: string): Promise<SignalDetailResult | null> {
  try {
    const raw = await apiFetch(`/api/signals/${encodeURIComponent(slug)}`);
    LAST_SIGNAL_SOURCE = "live";
    return raw;
  } catch (e) {
    if (!(e instanceof BackendUnavailable)) {
      if (String(e).startsWith("404")) return null;
      throw e;
    }
    // snapshot fallback: reconstruct detail from the bundled signal/bridge lists.
    const snap = await loadSnapshot();
    LAST_SIGNAL_SOURCE = "snapshot";
    // signal families
    for (const f of Object.values<any>(snapFamilies(snap))) {
      const hit = (f.signals ?? []).find((s: any) => s.slug === slug);
      if (hit) {
        return {
          slug,
          labels: [hit.label],
          name: hit.name,
          attrs: hit.detail ?? {},
          provenance: { session: hit.session ?? null },
          connections: { nodes: [], edges: [] },
        };
      }
    }
    // bridge
    const bhit = (snap.bridges?.bridges ?? []).find((b: any) => b.slug === slug);
    if (bhit) {
      const nodes = [
        { id: bhit.gene_node.id, name: bhit.gene_node.name, labels: ["ZetaGenomicFeature"], endpoint: bhit.gene_node.endpoint },
        { id: bhit.slug, name: `${bhit.gene} → ${bhit.ae_term}`, labels: ["GenomicAEBridge"], endpoint: endpointOf(bhit.slug) },
        { id: bhit.ae_node.id, name: bhit.ae_node.name, labels: ["ZetaAdverseEventTerm"], endpoint: bhit.ae_node.endpoint },
      ];
      const edges = [
        { rel: "HAS_BRIDGE_SCORE", source: bhit.gene_node.id, target: bhit.slug },
        { rel: "BRIDGES_TO_AE", source: bhit.slug, target: bhit.ae_node.id },
      ];
      return {
        slug,
        labels: ["GenomicAEBridge"],
        name: `${bhit.gene} → ${bhit.ae_term}`,
        attrs: { gene: bhit.gene, ae_term: bhit.ae_term, bridge_score: bhit.bridge_score, recurrence_pct: bhit.recurrence_pct },
        provenance: {},
        connections: { nodes, edges },
      };
    }
    return null;
  }
}

/**
 * Call a backend signal agent. Read-only. Requires the live backend (agents run
 * grounded Cypher server-side); when the backend is unavailable this rejects so
 * the UI can show a "connect the backend for live agent analysis" state rather
 * than fabricate a response.
 */
export async function runSignalAgent(body: {
  agent: AgentName;
  action?: string;
  family?: SignalFamily | "all";
  slug?: string;
  limit?: number;
}): Promise<AgentResponse> {
  const raw = await apiFetch("/api/signals/agent", { method: "POST", body: JSON.stringify(body) });
  LAST_SIGNAL_SOURCE = "live";
  return raw;
}

/** Deep-link into the Graph Explorer focused on a node id. */
export function graphFocusHref(id: string): string {
  return `#/graph?focus=${encodeURIComponent(id)}`;
}
