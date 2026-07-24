import { useEffect, useMemo, useState } from "react";
import { Link } from "wouter";
import { GitFork, Loader2, ArrowRight, ExternalLink, Dna, Activity } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ForceGraph, EndpointLegend } from "@/components/force-graph";
import { ENDPOINT_META, type EndpointCode, type GNode, type GEdge } from "@/lib/graphApi";
import {
  getBridges,
  graphFocusHref,
  LAST_SIGNAL_SOURCE,
  apiConfigured,
  type BridgeItem,
} from "@/lib/signalsApi";

function epColor(ep: EndpointCode): string {
  return ep && ep in ENDPOINT_META ? ENDPOINT_META[ep].color : "hsl(220,20%,55%)";
}

function BridgeRow({ b }: { b: BridgeItem }) {
  const score = b.bridge_score ?? 0;
  return (
    <Link href={`/signals/${encodeURIComponent(b.slug)}`}>
      <div
        className="grid grid-cols-[1fr_auto_1fr_auto] items-center gap-3 px-4 py-3 rounded-lg border border-border/50 hover:border-primary/50 cursor-pointer transition-colors"
        data-testid={`bridge-row-${b.slug}`}
      >
        {/* gene side (A_MSK) */}
        <div className="flex items-center gap-2 min-w-0">
          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: epColor(b.gene_node.endpoint) }} />
          <div className="min-w-0">
            <p className="text-sm font-semibold truncate flex items-center gap-1">
              <Dna className="w-3.5 h-3.5 text-muted-foreground" /> {b.gene}
            </p>
            <p className="text-[10px] text-muted-foreground truncate">{b.gene_node.endpoint} · genomic feature</p>
          </div>
        </div>

        {/* connector */}
        <ArrowRight className="w-4 h-4 text-primary shrink-0" />

        {/* AE side (B_SAS) */}
        <div className="flex items-center gap-2 min-w-0">
          <span className="w-2.5 h-2.5 rounded-full shrink-0" style={{ backgroundColor: epColor(b.ae_node.endpoint) }} />
          <div className="min-w-0">
            <p className="text-sm font-semibold truncate flex items-center gap-1">
              <Activity className="w-3.5 h-3.5 text-muted-foreground" /> {b.ae_term}
            </p>
            <p className="text-[10px] text-muted-foreground truncate">{b.ae_node.endpoint} · clinical AE term</p>
          </div>
        </div>

        {/* score */}
        <div className="text-right">
          <p className="text-[9px] uppercase tracking-wider text-muted-foreground">bridge score</p>
          <p className="text-lg font-bold tabular-nums text-primary leading-none">{score.toFixed(3)}</p>
          {b.recurrence_pct != null && (
            <p className="text-[9px] text-muted-foreground">{b.recurrence_pct}% recur.</p>
          )}
        </div>
      </div>
    </Link>
  );
}

export default function Bridges() {
  const [bridges, setBridges] = useState<BridgeItem[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getBridges()
      .then((r) => {
        setBridges(r.bridges);
        setCount(r.count);
      })
      .catch(() => setBridges([]))
      .finally(() => setLoading(false));
  }, []);

  // build a small D3 graph of the top bridges (gene -> bridge -> AE)
  const graph = useMemo(() => {
    const nodeMap = new Map<string, GNode>();
    const edges: GEdge[] = [];
    for (const b of bridges.slice(0, 8)) {
      const g = b.gene_node;
      const ae = b.ae_node;
      if (!nodeMap.has(g.id)) nodeMap.set(g.id, { id: g.id, name: b.gene ?? g.name ?? g.id, label: "ZetaGenomicFeature", labels: ["ZetaGenomicFeature"], endpoint: g.endpoint });
      if (!nodeMap.has(ae.id)) nodeMap.set(ae.id, { id: ae.id, name: b.ae_term ?? ae.name ?? ae.id, label: "ZetaAdverseEventTerm", labels: ["ZetaAdverseEventTerm"], endpoint: ae.endpoint });
      if (!nodeMap.has(b.slug)) nodeMap.set(b.slug, { id: b.slug, name: `${b.gene}→${b.ae_term}`, label: "GenomicAEBridge", labels: ["GenomicAEBridge"], endpoint: "A_MSK" });
      edges.push({ source: g.id, target: b.slug, type: "HAS_BRIDGE_SCORE" });
      edges.push({ source: b.slug, target: ae.id, type: "BRIDGES_TO_AE" });
    }
    return { nodes: [...nodeMap.values()], edges };
  }, [bridges]);

  const live = LAST_SIGNAL_SOURCE === "live";
  const top = bridges[0];

  return (
    <div className="p-6 max-w-[1400px] mx-auto space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <GitFork className="w-5 h-5 text-primary" />
            <h1 className="text-xl font-semibold">Genomic ↔ Clinical Bridges</h1>
            <Badge
              variant="outline"
              className={`text-[10px] ${live ? "border-emerald-500/40 text-emerald-400" : "border-amber-500/40 text-amber-400"}`}
            >
              {live ? "live graph" : "bundled snapshot"}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            The crown jewel of federation: edge-backed links from an{" "}
            <span style={{ color: epColor("A_MSK") }}>MSK genomic feature</span> to a{" "}
            <span style={{ color: epColor("B_SAS") }}>SAS clinical toxicity term</span>, resolved
            end-to-end through the graph (<code className="text-[10px]">ZetaGenomicFeature
            —HAS_BRIDGE_SCORE→ GenomicAEBridge —BRIDGES_TO_AE→ ZetaAdverseEventTerm</code>). No single
            vendor holds both sides — this is the connection they'd acquire.
          </p>
        </div>
        {!apiConfigured() && (
          <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-400">offline snapshot</Badge>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> Resolving bridges…
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-5 gap-5">
          {/* ranked list */}
          <div className="lg:col-span-3 space-y-2">
            <div className="flex items-center justify-between">
              <p className="text-xs text-muted-foreground">
                {count} bridges · ranked by bridge score
              </p>
              <EndpointLegend />
            </div>
            {bridges.map((b) => (
              <BridgeRow key={b.slug} b={b} />
            ))}
          </div>

          {/* graph + spotlight */}
          <div className="lg:col-span-2 space-y-4">
            {top && (
              <Card className="border-primary/30">
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm">Strongest bridge</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2">
                  <p className="text-lg font-bold">
                    {top.gene} <span className="text-primary">→</span> {top.ae_term}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    Bridge score <span className="text-foreground font-semibold tabular-nums">{top.bridge_score?.toFixed(3)}</span>
                    {top.recurrence_pct != null && <> · {top.gene} recurs in {top.recurrence_pct}% of HGSOC samples</>}.
                  </p>
                  <div className="flex gap-2 pt-1">
                    <Link href={`/signals/${encodeURIComponent(top.slug)}`}>
                      <Button size="sm" variant="outline" className="h-7 text-[11px]">Detail</Button>
                    </Link>
                    <a href={graphFocusHref(top.gene_node.id)}>
                      <Button size="sm" variant="outline" className="h-7 text-[11px]">
                        Graph <ExternalLink className="w-3 h-3 ml-1" />
                      </Button>
                    </a>
                  </div>
                </CardContent>
              </Card>
            )}
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-sm">Top-8 bridge network</CardTitle>
              </CardHeader>
              <CardContent>
                <div className="h-[320px] rounded-lg border border-border/50 overflow-hidden">
                  {graph.nodes.length > 0 ? (
                    <ForceGraph nodes={graph.nodes} edges={graph.edges} />
                  ) : (
                    <div className="flex items-center justify-center h-full text-muted-foreground text-xs">
                      No bridge data.
                    </div>
                  )}
                </div>
              </CardContent>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
