/**
 * Zeta Bridge LIVE source-extraction API layer (Session 15).
 *
 * Talks to the authenticated `/api/sources/*` backend, which invokes the three
 * LIVE source systems on demand:
 *   A_MSK -> Synapse      B_SAS -> SAS Viya CAS      C_EGA -> EGA
 *
 * This client is deliberately LIVE-ONLY — there is NO snapshot fallback. A live
 * extraction console must be live: if the backend is unreachable we surface that
 * honestly, and the UI offers a clearly-labelled "view the extracted equivalent
 * in the Graph Explorer" deep-link instead of silently swapping in stale data.
 *
 * Auth: sends `X-Zeta-Api-Key` (VITE_ZETA_API_KEY). The raw SOURCE credentials
 * (Synapse JWT, SAS token, EGA creds) are NEVER in the front-end — they live
 * server-side; the caller only ever holds the one scoped Zeta key.
 *
 * Honesty contract (enforced server-side, mirrored in these types): every
 * response is the uniform gateway envelope. `data` is populated IFF
 * `status === "live"`; on `unreachable`/`unconfigured`, `data` is null and
 * `error` carries a typed reason. Rows are never fabricated.
 */

import type { EndpointCode } from "./graphApi";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
const API_KEY = (import.meta.env.VITE_ZETA_API_KEY as string | undefined) ?? "";

// ---------------------------------------------------------------------------
// types (mirror backend SourceGateway envelope + /api/sources responses)
// ---------------------------------------------------------------------------
export type SourceCode = "synapse" | "sas_cas" | "ega";
export type LiveStatus = "live" | "unreachable" | "unconfigured";

/** Map a source code to its endpoint code (shared colors/labels via ENDPOINT_META). */
export const ENDPOINT_OF_SOURCE: Record<SourceCode, Exclude<EndpointCode, null>> = {
  synapse: "A_MSK",
  sas_cas: "B_SAS",
  ega: "C_EGA",
};

export interface Envelope<T = unknown> {
  endpoint: Exclude<EndpointCode, null>;
  source: SourceCode;
  status: LiveStatus;
  action: string;
  latency_ms: number;
  data: T | null;
  error: string | null;
  grounding: Record<string, unknown>;
}

export interface HealthEndpoint {
  endpoint: Exclude<EndpointCode, null>;
  source: SourceCode;
  status: LiveStatus;
  configured: boolean;
  latency_ms: number;
  error: string | null;
}

export interface HealthResult {
  endpoints: HealthEndpoint[];
  any_live: boolean;
}

// endpoint-specific data payload shapes (as returned live)
export interface EgaFile {
  accession_id: string | null;
  filesize: number | null;
  extension: string | null;
  checksum: string | null;
  checksum_type: string | null;
  locations: string[] | null;
  has_report: boolean | null;
}
export interface EgaListData {
  dataset: string;
  n_files: number | null;
  files: EgaFile[];
}

export interface TableData {
  columns: string[];
  n_rows: number;
  rows: Record<string, unknown>[];
  caslib?: string;
  table?: string;
}

export interface CaslibData {
  n_caslibs: number;
  caslibs: string[];
}

export interface SynapseEntityData {
  id: string | null;
  name: string | null;
  concreteType: string | null;
  parentId: string | null;
  createdOn: string | null;
  modifiedOn: string | null;
  versionNumber: number | null;
}

// ---------------------------------------------------------------------------
// low-level fetch (live-only; typed unreachable signalling)
// ---------------------------------------------------------------------------
/** Raised when the backend itself can't be reached / isn't configured. */
export class BackendUnavailable extends Error {}

export function apiConfigured(): boolean {
  return Boolean(API_BASE);
}

async function apiFetch<T>(path: string): Promise<T> {
  if (!API_BASE) throw new BackendUnavailable("No backend configured (VITE_API_BASE unset).");
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, {
      headers: { ...(API_KEY ? { "X-Zeta-Api-Key": API_KEY } : {}) },
    });
  } catch (e) {
    throw new BackendUnavailable(String(e));
  }
  if (res.status === 502 || res.status === 503 || res.status === 504) {
    throw new BackendUnavailable(`Backend ${res.status}`);
  }
  if (res.status === 401) {
    throw new Error("401: missing or invalid X-Zeta-Api-Key (set VITE_ZETA_API_KEY).");
  }
  if (!res.ok) {
    const txt = (await res.text().catch(() => "")) || res.statusText;
    throw new Error(`${res.status}: ${txt}`);
  }
  return res.json() as Promise<T>;
}

// ---------------------------------------------------------------------------
// public API (all LIVE — no snapshot fallback by design)
// ---------------------------------------------------------------------------
export function health(): Promise<HealthResult> {
  return apiFetch<HealthResult>("/api/sources/health");
}

export function synapseWhoami(): Promise<Envelope> {
  return apiFetch<Envelope>("/api/sources/synapse/whoami");
}

export function synapseEntity(synId: string): Promise<Envelope<SynapseEntityData>> {
  return apiFetch(`/api/sources/synapse/entity/${encodeURIComponent(synId)}`);
}

export function synapseTable(synId: string, limit = 50): Promise<Envelope<TableData>> {
  return apiFetch(`/api/sources/synapse/table/${encodeURIComponent(synId)}?limit=${limit}`);
}

export function sasCaslibs(): Promise<Envelope<CaslibData>> {
  return apiFetch("/api/sources/sas/caslibs");
}

export function sasAdam(caslib: string, table: string, limit = 50): Promise<Envelope<TableData>> {
  const qs = new URLSearchParams({ caslib, table, limit: String(limit) });
  return apiFetch(`/api/sources/sas/adam?${qs.toString()}`);
}

export function egaFiles(dataset = "EGAD00001011049", limit = 50): Promise<Envelope<EgaListData>> {
  const qs = new URLSearchParams({ dataset, limit: String(limit) });
  return apiFetch(`/api/sources/ega/files?${qs.toString()}`);
}

export function egaFile(fileId: string): Promise<Envelope> {
  return apiFetch(`/api/sources/ega/file/${encodeURIComponent(fileId)}`);
}

// ---------------------------------------------------------------------------
// small helpers for the UI
// ---------------------------------------------------------------------------
/** Human-readable, honest status label incl. the typed reason. */
export function statusLabel(status: LiveStatus, error?: string | null): string {
  if (status === "live") return "live";
  if (status === "unconfigured") return "unconfigured";
  // unreachable: show the typed reason prefix (before the first ':')
  const reason = (error ?? "").split(":")[0]?.trim();
  return reason ? `unreachable: ${reason}` : "unreachable";
}

/** Deep-link into the Graph Explorer focused on a node id (the extracted equivalent). */
export function graphFocusHref(id: string): string {
  return `#/graph?focus=${encodeURIComponent(id)}`;
}

/** Format a byte count compactly (e.g. 795045329 -> "795.0 MB"). */
export function humanBytes(n: number | null | undefined): string {
  if (n == null) return "—";
  const units = ["B", "KB", "MB", "GB", "TB"];
  let v = n;
  let u = 0;
  while (v >= 1024 && u < units.length - 1) {
    v /= 1024;
    u += 1;
  }
  return `${v.toFixed(u === 0 ? 0 : 1)} ${units[u]}`;
}
