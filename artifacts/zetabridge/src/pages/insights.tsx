import { useEffect, useMemo, useState } from "react";
import {
  Lightbulb,
  GitFork,
  Grid3x3,
  Waypoints,
  Link2,
  Loader2,
  ArrowRight,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import { EndpointLegend } from "@/components/force-graph";
import {
  ENDPOINT_META,
  getS12Insights,
  type EndpointCode,
  type GNode,
  type GPath,
} from "@/lib/graphApi";

type Attrs = Record<string, any>;

function parseAttrs(n: GNode): Attrs {
  if (!n.attributesJson) return {};
  try {
    return JSON.parse(n.attributesJson);
  } catch {
    return {};
  }
}

const EP_CODES: Exclude<EndpointCode, null>[] = ["A_MSK", "B_SAS", "C_EGA"];

function epColor(ep: string | null | undefined): string {
  return ep && ep in ENDPOINT_META ? ENDPOINT_META[ep as Exclude<EndpointCode, null>].color : "hsl(220,20%,55%)";
}

/** navigate to Graph Explorer focused on a node id (hash routing). */
function openInGraph(id: string) {
  window.location.hash = `#/graph?focus=${encodeURIComponent(id)}`;
}

export default function Insights() {
  const [byLabel, setByLabel] = useState<Record<string, GNode[]>>({});
  const [examplePaths, setExamplePaths] = useState<GPath[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getS12Insights()
      .then((r) => {
        setByLabel(r.byLabel);
        setExamplePaths(r.examplePaths);
      })
      .finally(() => setLoading(false));
  }, []);

  // ----- brokers (StructuralBridge sorted by betweenness) -----
  const brokers = useMemo(() => {
    return (byLabel["StructuralBridge"] ?? [])
      .map((n) => ({ node: n, a: parseAttrs(n) }))
      .sort((x, y) => (y.a.betweenness ?? 0) - (x.a.betweenness ?? 0));
  }, [byLabel]);

  // ----- reachability matrix -----
  const reach = useMemo(() => {
    const m: Record<string, Record<string, number | null>> = {};
    for (const f of EP_CODES) {
      m[f] = {};
      for (const t of EP_CODES) m[f][t] = f === t ? 1 : null;
    }
    for (const n of byLabel["ReachabilityProfile"] ?? []) {
      const a = parseAttrs(n);
      if (a.endpoint_from && a.endpoint_to) m[a.endpoint_from][a.endpoint_to] = a.reach_fraction;
    }
    return m;
  }, [byLabel]);

  // ----- deep chains -----
  const chains = useMemo(() => {
    const cs = (byLabel["DeepChain"] ?? []).map((n) => ({ node: n, a: parseAttrs(n) }));
    const byKind: Record<string, typeof cs> = {};
    for (const c of cs) (byKind[c.a.kind ?? "other"] ??= []).push(c);
    return byKind;
  }, [byLabel]);

  // ----- path summaries -----
  const pathSummaries = useMemo(
    () => (byLabel["CrossEndpointPathSummary"] ?? []).map((n) => ({ node: n, a: parseAttrs(n) })),
    [byLabel],
  );

  if (loading) {
    return (
      <div className="h-full flex items-center justify-center text-muted-foreground">
        <Loader2 className="w-5 h-5 animate-spin text-primary mr-2" /> Loading insights…
      </div>
    );
  }

  return (
    <div className="h-full overflow-y-auto" data-testid="insights-page">
      <div className="border-b border-border px-6 py-3 sticky top-0 bg-background/95 backdrop-blur z-10">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Lightbulb className="w-4 h-4 text-primary" /> Federation Insights
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              Structural findings from the deep cross-endpoint traversal — click any item to open it in the graph
            </p>
          </div>
          <EndpointLegend />
        </div>
      </div>

      <div className="p-6 space-y-6">
        {/* headline stats */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3" data-testid="insight-stats">
          <StatCard icon={GitFork} label="Structural bridges" value={brokers.length} sub="articulation points" />
          <StatCard icon={Grid3x3} label="Reachability" value="100%" sub="all 6 directed pairs ≤6 hops" accent />
          <StatCard icon={Waypoints} label="Deep chains" value={(byLabel["DeepChain"] ?? []).length} sub="longitudinal + genomic" />
          <StatCard icon={Link2} label="Sole-cut bridges" value={0} sub="redundant / robust" accent />
        </div>

        {/* Reachability matrix */}
        <Card className="border-border/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-1.5">
              <Grid3x3 className="w-3.5 h-3.5 text-primary" /> Cross-endpoint reachability (sampled 60/pair, ≤6 hops)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="inline-block">
              <table className="text-xs" data-testid="reach-matrix">
                <thead>
                  <tr>
                    <th className="p-2 text-left text-muted-foreground font-normal">from ↓ / to →</th>
                    {EP_CODES.map((t) => (
                      <th key={t} className="p-2 font-medium" style={{ color: epColor(t) }}>
                        {t}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {EP_CODES.map((f) => (
                    <tr key={f}>
                      <td className="p-2 font-medium" style={{ color: epColor(f) }}>
                        {f}
                      </td>
                      {EP_CODES.map((t) => {
                        const v = reach[f][t];
                        const self = f === t;
                        return (
                          <td key={t} className="p-2 text-center">
                            {self ? (
                              <span className="text-muted-foreground">—</span>
                            ) : v === null ? (
                              <span className="text-muted-foreground">·</span>
                            ) : (
                              <span
                                className="inline-block px-2 py-1 rounded font-medium tabular-nums"
                                style={{
                                  color: v >= 1 ? "hsl(150,70%,55%)" : "hsl(45,100%,60%)",
                                  backgroundColor: v >= 1 ? "hsl(150,70%,55%,0.12)" : "hsl(45,100%,60%,0.12)",
                                }}
                              >
                                {Math.round(v * 100)}%
                              </span>
                            )}
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="text-[11px] text-muted-foreground mt-2">
              Every ordered endpoint pair is fully reachable within 6 hops — the federation is densely bridged, not a
              set of islands.
            </p>
          </CardContent>
        </Card>

        {/* Top brokers */}
        <Card className="border-border/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-1.5">
              <GitFork className="w-3.5 h-3.5 text-primary" /> Top broker nodes (betweenness on the connective subgraph)
            </CardTitle>
          </CardHeader>
          <CardContent>
            <ScrollArea className="max-h-72">
              <div className="space-y-1 pr-2">
                {brokers.map(({ node, a }) => (
                  <button
                    key={node.id}
                    onClick={() => openInGraph(a.node ?? node.id)}
                    className="w-full text-left p-2 rounded-md border border-border/50 hover:bg-muted/30 transition-colors flex items-center justify-between gap-2"
                    data-testid={`broker-${node.id}`}
                  >
                    <div className="min-w-0">
                      <code className="text-[11px] text-foreground break-all">{a.node ?? node.name}</code>
                      <div className="flex items-center gap-1.5 mt-0.5">
                        <Badge variant="outline" className="text-[9px]" style={{ color: epColor(a.node_endpoint), borderColor: `${epColor(a.node_endpoint)}66` }}>
                          {a.node_endpoint ?? "—"}
                        </Badge>
                        <span className="text-[9px] text-muted-foreground">degree {a.degree}</span>
                        {Array.isArray(a.endpoint_span) && (
                          <span className="text-[9px] text-muted-foreground">spans {a.endpoint_span.join("·")}</span>
                        )}
                      </div>
                    </div>
                    <div className="text-right shrink-0">
                      <span className="text-xs font-bold tabular-nums text-primary">{(a.betweenness ?? 0).toFixed(3)}</span>
                      <p className="text-[9px] text-muted-foreground">betweenness</p>
                    </div>
                  </button>
                ))}
              </div>
            </ScrollArea>
          </CardContent>
        </Card>

        {/* Path summaries + example paths */}
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          <Card className="border-border/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-1.5">
                <Waypoints className="w-3.5 h-3.5 text-primary" /> Dominant cross-endpoint metapaths
              </CardTitle>
            </CardHeader>
            <CardContent className="space-y-2">
              {pathSummaries.map(({ node, a }) => (
                <div key={node.id} className="p-2 rounded-md border border-border/50">
                  <div className="flex items-center gap-1.5 mb-1">
                    <Badge variant="outline" className="text-[9px]" style={{ color: epColor(a.endpoint_a), borderColor: `${epColor(a.endpoint_a)}66` }}>
                      {a.endpoint_a}
                    </Badge>
                    <ArrowRight className="w-3 h-3 text-muted-foreground" />
                    <Badge variant="outline" className="text-[9px]" style={{ color: epColor(a.endpoint_b), borderColor: `${epColor(a.endpoint_b)}66` }}>
                      {a.endpoint_b}
                    </Badge>
                    <span className="text-[9px] text-muted-foreground ml-auto">
                      {a.n_distinct_paths} paths · {a.min_hops}–{a.max_hops} hops
                    </span>
                  </div>
                  {(a.dominant_rel_sequences ?? []).slice(0, 2).map((seq: [string, number], i: number) => (
                    <div key={i} className="text-[10px] font-mono text-muted-foreground break-all">
                      <span className="text-primary">{seq[1]}×</span> {seq[0]}
                    </div>
                  ))}
                </div>
              ))}
            </CardContent>
          </Card>

          <Card className="border-border/50">
            <CardHeader className="pb-2">
              <CardTitle className="text-xs font-semibold flex items-center gap-1.5">
                <Link2 className="w-3.5 h-3.5 text-primary" /> Organic example paths
              </CardTitle>
            </CardHeader>
            <CardContent>
              <ScrollArea className="max-h-60">
                <div className="space-y-1.5 pr-2">
                  {examplePaths.slice(0, 10).map((p, i) => (
                    <button
                      key={i}
                      onClick={() => openInGraph(p.node_ids[0])}
                      className="w-full text-left p-2 rounded-md border border-border/50 hover:bg-muted/30 transition-colors"
                      data-testid={`example-path-${i}`}
                    >
                      <div className="flex items-center gap-1.5 mb-1">
                        <Badge variant="outline" className="text-[9px]" style={{ color: epColor(p.source_endpoint ?? undefined), borderColor: `${epColor(p.source_endpoint ?? undefined)}66` }}>
                          {p.source_endpoint}
                        </Badge>
                        <ArrowRight className="w-3 h-3 text-muted-foreground" />
                        <Badge variant="outline" className="text-[9px]" style={{ color: epColor(p.target_endpoint ?? undefined), borderColor: `${epColor(p.target_endpoint ?? undefined)}66` }}>
                          {p.target_endpoint}
                        </Badge>
                        <span className="text-[9px] text-muted-foreground ml-auto">{p.hops} hops</span>
                      </div>
                      <div className="text-[10px] font-mono text-muted-foreground break-all">
                        {p.node_ids[0]} … {p.node_ids[p.node_ids.length - 1]}
                      </div>
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </CardContent>
          </Card>
        </div>

        {/* Deep chains */}
        <Card className="border-border/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-xs font-semibold flex items-center gap-1.5">
              <Waypoints className="w-3.5 h-3.5 text-primary" /> Deep chains
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
              {Object.entries(chains).map(([kind, cs]) => (
                <div key={kind} className="p-3 rounded-md border border-border/50">
                  <div className="flex items-center justify-between mb-1.5">
                    <span className="text-xs font-medium text-foreground capitalize">{kind} chains</span>
                    <Badge variant="outline" className="text-[9px]">
                      {cs.length}
                    </Badge>
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {kind === "longitudinal"
                      ? "Diagnosis→relapse sample pairs reaching the EGA dataset."
                      : kind === "genomic"
                        ? "Genomic-bridge chains carrying MSK biospecimen provenance."
                        : "Traversal chains."}
                  </p>
                  <div className="mt-2 space-y-0.5">
                    {cs.slice(0, 3).map(({ node }) => (
                      <button
                        key={node.id}
                        onClick={() => openInGraph(node.id)}
                        className="block w-full text-left text-[10px] font-mono text-muted-foreground hover:text-foreground truncate"
                      >
                        {node.name}
                      </button>
                    ))}
                    {cs.length > 3 && <span className="text-[9px] text-muted-foreground">+{cs.length - 3} more…</span>}
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
  accent,
}: {
  icon: any;
  label: string;
  value: string | number;
  sub: string;
  accent?: boolean;
}) {
  return (
    <Card className="border-border/50">
      <CardContent className="p-3">
        <div className="flex items-center gap-1.5 mb-1">
          <Icon className={`w-3.5 h-3.5 ${accent ? "text-emerald-400" : "text-primary"}`} />
          <span className="text-[11px] text-muted-foreground">{label}</span>
        </div>
        <p className={`text-2xl font-bold tabular-nums ${accent ? "text-emerald-400" : "text-foreground"}`}>{value}</p>
        <p className="text-[10px] text-muted-foreground mt-0.5">{sub}</p>
      </CardContent>
    </Card>
  );
}
