import { useEffect, useMemo, useState } from "react";
import { Link, useParams } from "wouter";
import { ArrowLeft, ExternalLink, Loader2, Network as NetworkIcon, Info } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ForceGraph, EndpointLegend } from "@/components/force-graph";
import { ENDPOINT_META, type EndpointCode, type GNode, type GEdge } from "@/lib/graphApi";
import {
  getSignalDetail,
  graphFocusHref,
  LAST_SIGNAL_SOURCE,
  type SignalDetailResult,
} from "@/lib/signalsApi";

function epColor(ep: EndpointCode): string {
  return ep && ep in ENDPOINT_META ? ENDPOINT_META[ep].color : "hsl(220,20%,55%)";
}

// value formatting for the attribute table
function fmtVal(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : v.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
  return String(v);
}

const PRIMARY_ATTR_ORDER = [
  "gene", "ae_term", "exp_drug", "ctrl_drug", "trial", "trial_id", "arm",
  "rate_ratio", "ror", "bridge_score", "consistency_score",
  "exp_rate", "ctrl_rate", "n_trials", "n_patients", "recurrence_pct",
  "severity", "signal_type", "feature_type", "interpretation",
];

export default function SignalDetail() {
  const params = useParams();
  const slug = decodeURIComponent((params as any).slug ?? "");
  const [detail, setDetail] = useState<SignalDetailResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!slug) return;
    setLoading(true);
    setNotFound(false);
    getSignalDetail(slug)
      .then((d) => {
        if (!d) setNotFound(true);
        else setDetail(d);
      })
      .catch(() => setNotFound(true))
      .finally(() => setLoading(false));
  }, [slug]);

  const { nodes, edges } = useMemo(() => {
    if (!detail) return { nodes: [] as GNode[], edges: [] as GEdge[] };
    const nodes: GNode[] = detail.connections.nodes.map((n) => ({
      id: n.id,
      name: n.name ?? n.id,
      label: n.labels?.[0] ?? "Node",
      labels: n.labels ?? [],
      endpoint: n.endpoint,
    }));
    const edges: GEdge[] = detail.connections.edges.map((e) => ({
      source: e.source,
      target: e.target,
      type: e.rel,
    }));
    return { nodes, edges };
  }, [detail]);

  const orderedAttrs = useMemo(() => {
    if (!detail) return [] as [string, unknown][];
    const a = detail.attrs ?? {};
    const seen = new Set<string>();
    const out: [string, unknown][] = [];
    for (const k of PRIMARY_ATTR_ORDER) {
      if (k in a && a[k] !== null && a[k] !== undefined) {
        out.push([k, a[k]]);
        seen.add(k);
      }
    }
    for (const [k, v] of Object.entries(a)) {
      if (!seen.has(k) && v !== null && v !== undefined && typeof v !== "object") out.push([k, v]);
    }
    return out;
  }, [detail]);

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32 text-muted-foreground">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading signal…
      </div>
    );
  }

  if (notFound || !detail) {
    return (
      <div className="p-6 max-w-2xl mx-auto">
        <Link href="/signals">
          <Button variant="ghost" size="sm" className="mb-4">
            <ArrowLeft className="w-4 h-4 mr-1.5" /> Back to Signal Intelligence
          </Button>
        </Link>
        <Card>
          <CardContent className="py-12 text-center text-muted-foreground text-sm">
            <Info className="w-6 h-6 mx-auto mb-3 opacity-50" />
            Signal <code className="text-xs bg-muted px-1.5 py-0.5 rounded">{slug}</code> was not found.
          </CardContent>
        </Card>
      </div>
    );
  }

  const live = LAST_SIGNAL_SOURCE === "live";

  return (
    <div className="p-6 max-w-[1400px] mx-auto space-y-5">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <Link href="/signals">
          <Button variant="ghost" size="sm">
            <ArrowLeft className="w-4 h-4 mr-1.5" /> Signal Intelligence
          </Button>
        </Link>
        <Badge
          variant="outline"
          className={`text-[10px] ${live ? "border-emerald-500/40 text-emerald-400" : "border-amber-500/40 text-amber-400"}`}
        >
          {live ? "live graph" : "bundled snapshot"}
        </Badge>
      </div>

      {/* title */}
      <div>
        <div className="flex items-center gap-2 flex-wrap">
          {(detail.labels ?? []).map((l) => (
            <Badge key={l} variant="secondary" className="text-[10px]">{l}</Badge>
          ))}
        </div>
        <h1 className="text-xl font-semibold mt-2">{detail.name ?? slug}</h1>
        <code className="text-[11px] text-muted-foreground break-all">{slug}</code>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
        {/* attributes */}
        <Card className="lg:col-span-2">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">Signal detail</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="rounded-lg border border-border/50 overflow-hidden">
              <table className="w-full text-xs">
                <tbody>
                  {orderedAttrs.map(([k, v], i) => (
                    <tr key={k} className={i % 2 ? "bg-muted/20" : ""}>
                      <td className="px-3 py-1.5 font-mono text-[10px] text-muted-foreground align-top whitespace-nowrap">
                        {k}
                      </td>
                      <td className="px-3 py-1.5 align-top">{fmtVal(v)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {detail.provenance?.session && (
              <p className="text-[10px] text-muted-foreground mt-2">
                Minted in session {String(detail.provenance.session)} · every value read from the graph.
              </p>
            )}
          </CardContent>
        </Card>

        {/* connecting subgraph */}
        <Card className="lg:col-span-3">
          <CardHeader className="pb-2 flex-row items-center justify-between space-y-0">
            <CardTitle className="text-sm flex items-center gap-1.5">
              <NetworkIcon className="w-4 h-4 text-primary" /> How it connects
            </CardTitle>
            <a href={graphFocusHref(slug)}>
              <Button variant="outline" size="sm" className="h-7 text-[11px]">
                Open in Graph Explorer <ExternalLink className="w-3 h-3 ml-1.5" />
              </Button>
            </a>
          </CardHeader>
          <CardContent>
            {nodes.length <= 1 ? (
              <div className="text-center py-16 text-muted-foreground text-xs">
                {live
                  ? "This signal has no additional 1-hop connections in the graph."
                  : "Connect the backend (live mode) to render this signal's exact connecting nodes and edges."}
              </div>
            ) : (
              <>
                <div className="h-[360px] rounded-lg border border-border/50 overflow-hidden">
                  <ForceGraph nodes={nodes} edges={edges} centerId={slug} highlightPath={nodes.map((n) => n.id)} />
                </div>
                <div className="flex items-center justify-between mt-2">
                  <EndpointLegend />
                  <span className="text-[10px] text-muted-foreground">
                    {nodes.length} nodes · {edges.length} edges
                  </span>
                </div>
              </>
            )}
            {/* connected node chips with deep-links */}
            {nodes.length > 1 && (
              <div className="flex flex-wrap gap-1.5 mt-3">
                {nodes
                  .filter((n) => n.id !== slug)
                  .map((n) => (
                    <a key={n.id} href={graphFocusHref(n.id)}>
                      <Badge
                        variant="outline"
                        className="text-[10px] cursor-pointer hover:border-primary/50"
                        style={{ borderColor: `${epColor(n.endpoint)}55` }}
                      >
                        <span className="truncate max-w-[180px]">{n.name}</span>
                      </Badge>
                    </a>
                  ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
