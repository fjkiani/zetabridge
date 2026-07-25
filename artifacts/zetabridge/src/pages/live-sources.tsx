/**
 * Live Extraction Console (Session 15) — route `/live`.
 *
 * The end-to-end VISIBLE proof that Zeta Bridge connects to the three source
 * systems and extracts real data on demand (Synapse / SAS Viya CAS / EGA).
 *
 * Each endpoint gets a card with:
 *   - a Connect button -> real health handshake (status + latency)
 *   - a Fetch action   -> real extraction (Synapse entity/table, SAS ADaM query,
 *                          EGA file listing)
 *   - a live results table / JSON, a source badge (cyan/gold/purple), latency,
 *     and an HONEST status chip (live / unreachable:<reason> / unconfigured).
 *
 * On `unreachable`/`unconfigured` we show the real reason and a clearly-labelled
 * "view the extracted equivalent in the Graph Explorer" deep-link — never a
 * silent swap to stale data. This surface is LIVE-ONLY by design.
 */

import { useEffect, useState } from "react";
import {
  Radio,
  Loader2,
  Play,
  Plug,
  Database,
  FileSearch,
  ExternalLink,
  AlertTriangle,
  CheckCircle2,
  CircleSlash,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ENDPOINT_META, type EndpointCode } from "@/lib/graphApi";
import {
  health,
  synapseEntity,
  synapseTable,
  sasCaslibs,
  sasAdam,
  egaFiles,
  egaFile,
  statusLabel,
  graphFocusHref,
  humanBytes,
  apiConfigured,
  BackendUnavailable,
  type Envelope,
  type HealthResult,
  type LiveStatus,
  type SourceCode,
  ENDPOINT_OF_SOURCE,
  type EgaListData,
  type TableData,
} from "@/lib/sourcesApi";

function epColor(ep: EndpointCode): string {
  return ep && ep in ENDPOINT_META ? ENDPOINT_META[ep].color : "hsl(220,20%,55%)";
}

// the "extracted equivalent" vault node id per endpoint (deep-link target)
const VAULT_NODE: Record<SourceCode, string> = {
  synapse: "vault:synapse_msk_spectrum",
  sas_cas: "vault:sas_pds",
  ega: "vault:ega_britroc",
};

function StatusChip({ status, error }: { status: LiveStatus; error?: string | null }) {
  const label = statusLabel(status, error);
  if (status === "live") {
    return (
      <Badge variant="outline" className="gap-1 text-emerald-400 border-emerald-400/40">
        <CheckCircle2 className="w-3 h-3" /> {label}
      </Badge>
    );
  }
  if (status === "unconfigured") {
    return (
      <Badge variant="outline" className="gap-1 text-muted-foreground">
        <CircleSlash className="w-3 h-3" /> {label}
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="gap-1 text-amber-400 border-amber-400/40">
      <AlertTriangle className="w-3 h-3" /> {label}
    </Badge>
  );
}

function LatencyPill({ ms }: { ms: number | null | undefined }) {
  if (ms == null) return null;
  return (
    <span className="text-[10px] font-mono text-muted-foreground tabular-nums">
      {ms.toFixed(0)} ms
    </span>
  );
}

/** Render a live rows table generically from an envelope's TableData. */
function RowsTable({ data }: { data: TableData }) {
  const cols = data.columns ?? [];
  const rows = data.rows ?? [];
  return (
    <div className="overflow-auto max-h-72 rounded-md border border-border/50">
      <table className="w-full text-[11px]">
        <thead className="sticky top-0 bg-muted/60 backdrop-blur">
          <tr>
            {cols.map((c) => (
              <th key={c} className="text-left px-2 py-1.5 font-semibold whitespace-nowrap">
                {c}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-border/40">
              {cols.map((c) => (
                <td key={c} className="px-2 py-1 font-mono whitespace-nowrap max-w-[220px] truncate">
                  {String((r as Record<string, unknown>)[c] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[10px] text-muted-foreground px-2 py-1">
        {rows.length} row{rows.length === 1 ? "" : "s"} · {cols.length} columns (live)
      </p>
    </div>
  );
}

function EgaTable({ data }: { data: EgaListData }) {
  return (
    <div className="overflow-auto max-h-72 rounded-md border border-border/50">
      <table className="w-full text-[11px]">
        <thead className="sticky top-0 bg-muted/60 backdrop-blur">
          <tr>
            <th className="text-left px-2 py-1.5 font-semibold">accession</th>
            <th className="text-left px-2 py-1.5 font-semibold">size</th>
            <th className="text-left px-2 py-1.5 font-semibold">checksum</th>
            <th className="text-left px-2 py-1.5 font-semibold">locations</th>
          </tr>
        </thead>
        <tbody>
          {(data.files ?? []).map((f) => (
            <tr key={f.accession_id ?? Math.random()} className="border-t border-border/40">
              <td className="px-2 py-1 font-mono" title={f.accession_id ?? undefined}>
                {f.accession_id}
              </td>
              <td className="px-2 py-1 font-mono tabular-nums">{humanBytes(f.filesize)}</td>
              <td
                className="px-2 py-1 font-mono text-muted-foreground truncate max-w-[220px]"
                title={`${f.checksum_type}:${f.checksum}`}
              >
                {f.checksum_type}:{f.checksum}
              </td>
              <td className="px-2 py-1 font-mono" title={(f.locations ?? []).join(", ")}>
                {(f.locations ?? []).join(", ")}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      <p className="text-[10px] text-muted-foreground px-2 py-1">
        {data.n_files ?? 0} files in {data.dataset} (live · metadata only)
      </p>
    </div>
  );
}

/** Result panel that honestly renders any envelope (live data OR typed error). */
function ResultPanel({ env, source }: { env: Envelope | null; source: SourceCode }) {
  if (!env) return null;
  if (env.status === "live") {
    const action = env.action;
    return (
      <div className="mt-3 space-y-2">
        <div className="flex items-center gap-2">
          <StatusChip status={env.status} error={env.error} />
          <LatencyPill ms={env.latency_ms} />
          {env.grounding && Object.keys(env.grounding).length > 0 && (
            <span className="text-[10px] font-mono text-muted-foreground truncate">
              {Object.entries(env.grounding)
                .map(([k, v]) => `${k}=${v}`)
                .join(" · ")}
            </span>
          )}
        </div>
        {action === "list_files" ? (
          <EgaTable data={env.data as unknown as EgaListData} />
        ) : action === "query_table" || action === "query_adam" ? (
          <RowsTable data={env.data as unknown as TableData} />
        ) : (
          <pre className="text-[11px] font-mono bg-muted/40 rounded-md p-3 overflow-auto max-h-72">
            {JSON.stringify(env.data, null, 2)}
          </pre>
        )}
      </div>
    );
  }
  // honest non-live: show reason + extracted-equivalent deep-link
  return (
    <div className="mt-3 space-y-2">
      <div className="flex items-center gap-2">
        <StatusChip status={env.status} error={env.error} />
        <LatencyPill ms={env.latency_ms} />
      </div>
      {env.error && (
        <p className="text-[11px] text-muted-foreground font-mono break-all">{env.error}</p>
      )}
      <a href={graphFocusHref(VAULT_NODE[source])}>
        <Button variant="outline" size="sm" className="gap-1.5 text-xs">
          <ExternalLink className="w-3.5 h-3.5" />
          View the extracted equivalent in Graph Explorer
        </Button>
      </a>
    </div>
  );
}

interface CardState {
  loading: boolean;
  env: Envelope | null;
  err: string | null;
}
const IDLE: CardState = { loading: false, env: null, err: null };

function useCall() {
  const [state, setState] = useState<CardState>(IDLE);
  async function run(fn: () => Promise<Envelope>) {
    setState({ loading: true, env: null, err: null });
    try {
      const env = await fn();
      setState({ loading: false, env, err: null });
    } catch (e) {
      const msg =
        e instanceof BackendUnavailable
          ? `Backend unreachable — start the Zeta Bridge API and set VITE_API_BASE. (${e.message})`
          : String(e instanceof Error ? e.message : e);
      setState({ loading: false, env: null, err: msg });
    }
  }
  return { state, run };
}

function SourceCard({
  source,
  icon: Icon,
  title,
  subtitle,
  healthEntry,
  children,
}: {
  source: SourceCode;
  icon: typeof Radio;
  title: string;
  subtitle: string;
  healthEntry?: HealthResult["endpoints"][number];
  children: React.ReactNode;
}) {
  const ep = ENDPOINT_OF_SOURCE[source];
  return (
    <Card className="flex flex-col" data-testid={`source-card-${source}`}>
      <CardHeader className="pb-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span
              className="w-8 h-8 rounded-md flex items-center justify-center shrink-0"
              style={{ backgroundColor: `${epColor(ep)}22` }}
            >
              <Icon className="w-4 h-4" style={{ color: epColor(ep) }} />
            </span>
            <div className="min-w-0">
              <CardTitle className="text-sm truncate">{title}</CardTitle>
              <p className="text-[11px] text-muted-foreground truncate">{subtitle}</p>
            </div>
          </div>
          <Badge variant="outline" className="shrink-0 font-mono text-[10px]">
            {ep}
          </Badge>
        </div>
        {healthEntry && (
          <div className="flex items-center gap-2 pt-1">
            <StatusChip status={healthEntry.status} error={healthEntry.error} />
            <LatencyPill ms={healthEntry.latency_ms} />
            <span className="text-[10px] text-muted-foreground">
              {healthEntry.configured ? "configured" : "no server-side creds"}
            </span>
          </div>
        )}
      </CardHeader>
      <CardContent className="flex-1 space-y-3">{children}</CardContent>
    </Card>
  );
}

export default function LiveSources() {
  const [h, setH] = useState<HealthResult | null>(null);
  const [hLoading, setHLoading] = useState(false);
  const [hErr, setHErr] = useState<string | null>(null);

  const configured = apiConfigured();

  async function refreshHealth() {
    setHLoading(true);
    setHErr(null);
    try {
      setH(await health());
    } catch (e) {
      setHErr(
        e instanceof BackendUnavailable
          ? `Backend unreachable — start the Zeta Bridge API and set VITE_API_BASE.`
          : String(e instanceof Error ? e.message : e),
      );
    } finally {
      setHLoading(false);
    }
  }

  useEffect(() => {
    if (configured) refreshHealth();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const entryOf = (src: SourceCode) => h?.endpoints.find((e) => e.source === src);

  // per-card call state + inputs
  const syn = useCall();
  const [synId, setSynId] = useState("syn25569736");
  const sas = useCall();
  const [caslib, setCaslib] = useState("CASUSER");
  const [table, setTable] = useState("ADAE");
  const ega = useCall();
  const [dataset, setDataset] = useState("EGAD00001011049");

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      {/* header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Radio className="w-6 h-6 text-primary" /> Live Extraction Console
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Connect to the three source systems and extract real data on demand. Every result is a
            live source response with its latency and grounding — or an honest typed error. Nothing
            here is a snapshot.
          </p>
        </div>
        <Button onClick={refreshHealth} disabled={hLoading || !configured} variant="outline" className="gap-2">
          {hLoading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Plug className="w-4 h-4" />}
          Handshake all
        </Button>
      </div>

      {!configured && (
        <Card className="border-amber-400/40">
          <CardContent className="py-4 flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
            <div className="text-sm">
              <p className="font-semibold">No backend configured.</p>
              <p className="text-muted-foreground">
                Set <span className="font-mono">VITE_API_BASE</span> (and{" "}
                <span className="font-mono">VITE_ZETA_API_KEY</span>) to point this console at a
                running Zeta Bridge API. The live console is intentionally live-only — it does not
                fall back to a snapshot.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {hErr && (
        <Card className="border-amber-400/40">
          <CardContent className="py-3 text-sm text-amber-300 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" /> {hErr}
          </CardContent>
        </Card>
      )}

      {h && (
        <p className="text-xs text-muted-foreground">
          {h.any_live ? (
            <span className="text-emerald-400 font-semibold">At least one endpoint is live.</span>
          ) : (
            <span>No endpoints are live right now — set the server-side source credentials.</span>
          )}{" "}
          EGA metadata is public, so C_EGA is live without any credentials.
        </p>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        {/* --- Synapse (A_MSK) --- */}
        <SourceCard
          source="synapse"
          icon={Database}
          title="Synapse — MSK SPECTRUM"
          subtitle="A_MSK · synapseclient (JWT, server-side)"
          healthEntry={entryOf("synapse")}
        >
          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground">synID (entity)</label>
            <div className="flex gap-2">
              <Input
                value={synId}
                onChange={(e) => setSynId(e.target.value)}
                className="h-8 text-xs font-mono"
                data-testid="input-syn-id"
              />
              <Button
                size="sm"
                className="gap-1 shrink-0"
                disabled={syn.state.loading || !configured}
                onClick={() => syn.run(() => synapseEntity(synId))}
                data-testid="btn-syn-entity"
              >
                {syn.state.loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <FileSearch className="w-3.5 h-3.5" />}
                Entity
              </Button>
              <Button
                size="sm"
                variant="secondary"
                className="gap-1 shrink-0"
                disabled={syn.state.loading || !configured}
                onClick={() => syn.run(() => synapseTable(synId, 25))}
                data-testid="btn-syn-table"
              >
                <Play className="w-3.5 h-3.5" /> Table
              </Button>
            </div>
          </div>
          {syn.state.err && <p className="text-[11px] text-amber-400">{syn.state.err}</p>}
          <ResultPanel env={syn.state.env} source="synapse" />
        </SourceCard>

        {/* --- SAS Viya CAS (B_SAS) --- */}
        <SourceCard
          source="sas_cas"
          icon={Database}
          title="SAS Viya CAS — PDS"
          subtitle="B_SAS · swat (mpmprodvdmml…, server-side)"
          healthEntry={entryOf("sas_cas")}
        >
          <div className="space-y-2">
            <Button
              size="sm"
              variant="outline"
              className="gap-1 w-full"
              disabled={sas.state.loading || !configured}
              onClick={() => sas.run(() => sasCaslibs())}
              data-testid="btn-sas-caslibs"
            >
              {sas.state.loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Plug className="w-3.5 h-3.5" />}
              List caslibs
            </Button>
            <div className="grid grid-cols-2 gap-2">
              <Input
                value={caslib}
                onChange={(e) => setCaslib(e.target.value)}
                placeholder="caslib"
                className="h-8 text-xs font-mono"
                data-testid="input-caslib"
              />
              <Input
                value={table}
                onChange={(e) => setTable(e.target.value)}
                placeholder="table"
                className="h-8 text-xs font-mono"
                data-testid="input-table"
              />
            </div>
            <Button
              size="sm"
              className="gap-1 w-full"
              disabled={sas.state.loading || !configured}
              onClick={() => sas.run(() => sasAdam(caslib, table, 20))}
              data-testid="btn-sas-adam"
            >
              <Play className="w-3.5 h-3.5" /> Query ADaM ({caslib}.{table})
            </Button>
          </div>
          {sas.state.err && <p className="text-[11px] text-amber-400">{sas.state.err}</p>}
          <ResultPanel env={sas.state.env} source="sas_cas" />
        </SourceCard>

        {/* --- EGA (C_EGA) --- */}
        <SourceCard
          source="ega"
          icon={FileSearch}
          title="EGA — BriTROC HGSOC"
          subtitle="C_EGA · public metadata API (no creds)"
          healthEntry={entryOf("ega")}
        >
          <div className="space-y-2">
            <label className="text-[11px] text-muted-foreground">dataset accession</label>
            <div className="flex gap-2">
              <Input
                value={dataset}
                onChange={(e) => setDataset(e.target.value)}
                className="h-8 text-xs font-mono"
                data-testid="input-dataset"
              />
              <Button
                size="sm"
                className="gap-1 shrink-0"
                disabled={ega.state.loading || !configured}
                onClick={() => ega.run(() => egaFiles(dataset, 10))}
                data-testid="btn-ega-files"
              >
                {ega.state.loading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Play className="w-3.5 h-3.5" />}
                List files
              </Button>
            </div>
            <p className="text-[10px] text-muted-foreground">
              Listing + metadata only. Controlled-access patient sequence bytes are never fetched.
            </p>
          </div>
          {ega.state.err && <p className="text-[11px] text-amber-400">{ega.state.err}</p>}
          <ResultPanel env={ega.state.env} source="ega" />
        </SourceCard>
      </div>
    </div>
  );
}
