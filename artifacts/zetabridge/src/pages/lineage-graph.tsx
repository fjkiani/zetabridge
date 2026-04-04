import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import * as d3 from "d3";
import {
  GitBranch,
  Workflow,
  Database as DatabaseIcon,
  Activity,
  X,
  Loader2,
} from "lucide-react";

interface GraphNode {
  id: string;
  type: "job" | "dataset";
  name: string;
  namespace: string;
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

interface GraphEdge {
  source: string | GraphNode;
  target: string | GraphNode;
}

/** Backend legacy returns `label` not `name`; Marquez may use other shapes. */
function normalizeLineageGraph(raw: unknown): { nodes: GraphNode[]; edges: GraphEdge[] } {
  const empty = { nodes: [] as GraphNode[], edges: [] as GraphEdge[] };
  if (!raw || typeof raw !== "object") return empty;

  const r = raw as Record<string, unknown>;
  let rawNodes: unknown[] = Array.isArray(r.nodes) ? (r.nodes as unknown[]) : [];
  let rawEdges: unknown[] = Array.isArray(r.edges) ? (r.edges as unknown[]) : [];

  if (rawNodes.length === 0 && Array.isArray(r.graph)) {
    const g = r.graph as unknown[];
    const maybeVertices = g.filter(
      (x) => x && typeof x === "object" && (x as { id?: string }).id != null,
    );
    if (maybeVertices.length > 0) rawNodes = maybeVertices;
  }

  const nodes: GraphNode[] = rawNodes
    .filter((n): n is Record<string, unknown> => !!n && typeof n === "object")
    .map((n) => {
      const id = String(n.id ?? "").trim();
      if (!id) return null;
      const type: "job" | "dataset" = n.type === "job" ? "job" : "dataset";
      const name =
        String(n.name ?? n.label ?? n.dataset ?? n.id ?? "unknown").trim() || id;
      const namespace = String(n.namespace ?? n.jobNamespace ?? "");
      return { id, type, name, namespace };
    })
    .filter((n): n is GraphNode => n !== null);

  const idSet = new Set(nodes.map((n) => n.id));
  const edges: GraphEdge[] = rawEdges
    .filter((e): e is Record<string, unknown> => !!e && typeof e === "object")
    .map((e) => {
      const s = e.source;
      const t = e.target;
      const src = typeof s === "string" ? s : (s as GraphNode | undefined)?.id;
      const tgt = typeof t === "string" ? t : (t as GraphNode | undefined)?.id;
      return { source: String(src ?? ""), target: String(tgt ?? "") };
    })
    .filter((e) => e.source && e.target && idSet.has(e.source) && idSet.has(e.target));

  return { nodes, edges };
}

function labelText(d: GraphNode): string {
  const n = d?.name ?? "";
  return n.length > 18 ? `${n.slice(0, 16)}…` : n;
}

export default function LineageGraph() {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const { data: graphRaw, isPending: graphPending, isError: graphError } = useQuery({
    queryKey: ["/api/lineage/graph"],
    queryFn: async () => {
      const r = await apiRequest("GET", "/api/lineage/graph");
      return r.json();
    },
  });

  const { data: stats } = useQuery({
    queryKey: ["/api/lineage/stats"],
    queryFn: async () => {
      const r = await apiRequest("GET", "/api/lineage/stats");
      return r.json();
    },
  });

  const graph = useMemo(() => normalizeLineageGraph(graphRaw), [graphRaw]);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;

    d3.select(svgRef.current).selectAll("*").remove();

    if (graph.nodes.length === 0) return;

    const nodes = graph.nodes.map((n) => ({ ...n }));
    const edges = graph.edges.map((e) => ({ ...e }));

    const svg = d3.select(svgRef.current).attr("width", width).attr("height", height);

    const g = svg.append("g");
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 3])
      .on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);

    svg
      .append("defs")
      .append("marker")
      .attr("id", "arrowhead")
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 22)
      .attr("refY", 0)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-5L10,0L0,5")
      .attr("fill", "hsl(220, 16%, 28%)");

    const simulation = d3
      .forceSimulation<GraphNode>(nodes)
      .force("link", d3.forceLink<GraphNode, GraphEdge>(edges).id((d) => d.id).distance(120))
      .force("charge", d3.forceManyBody().strength(-300))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(35));

    const link = g
      .append("g")
      .selectAll("line")
      .data(edges)
      .join("line")
      .attr("class", "lineage-edge")
      .attr("stroke", "hsl(220, 16%, 28%)")
      .attr("stroke-width", 1.5)
      .attr("marker-end", "url(#arrowhead)");

    const node = g
      .append("g")
      .selectAll<SVGGElement, GraphNode>("g")
      .data(nodes)
      .join("g")
      .attr("class", "lineage-node cursor-pointer")
      .call(
        d3
          .drag<SVGGElement, GraphNode>()
          .on("start", (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart();
            d.fx = d.x;
            d.fy = d.y;
          })
          .on("drag", (event, d) => {
            d.fx = event.x;
            d.fy = event.y;
          })
          .on("end", (event, d) => {
            if (!event.active) simulation.alphaTarget(0);
            d.fx = null;
            d.fy = null;
          }),
      );

    node
      .append("circle")
      .attr("r", 16)
      .attr("fill", (d) => (d?.type === "job" ? "hsl(193, 80%, 25%)" : "hsl(220, 20%, 18%)"))
      .attr("stroke", (d) => (d?.type === "job" ? "hsl(193, 100%, 50%)" : "hsl(220, 16%, 35%)"))
      .attr("stroke-width", 2);

    node
      .append("text")
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .attr("font-size", "10px")
      .attr("fill", (d) => (d?.type === "job" ? "hsl(193, 100%, 70%)" : "hsl(210, 20%, 70%)"))
      .text((d) => (d?.type === "job" ? "⚙" : "▦"));

    node
      .append("text")
      .attr("dy", 28)
      .attr("text-anchor", "middle")
      .attr("font-size", "10px")
      .attr("fill", "hsl(210, 20%, 70%)")
      .attr("font-family", "'DM Sans', sans-serif")
      .text((d) => labelText(d));

    node.on("click", (_event: unknown, d: GraphNode) => {
      setSelectedNode(d);
    });

    simulation.on("tick", () => {
      link
        .attr("x1", (d: GraphEdge & { source: GraphNode; target: GraphNode }) => d.source?.x ?? 0)
        .attr("y1", (d: GraphEdge & { source: GraphNode; target: GraphNode }) => d.source?.y ?? 0)
        .attr("x2", (d: GraphEdge & { source: GraphNode; target: GraphNode }) => d.target?.x ?? 0)
        .attr("y2", (d: GraphEdge & { source: GraphNode; target: GraphNode }) => d.target?.y ?? 0);
      node.attr("transform", (d: GraphNode) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    svg.call(zoom.transform, d3.zoomIdentity.translate(width * 0.1, height * 0.1).scale(0.8));

    return () => {
      simulation.stop();
    };
  }, [graph]);

  return (
    <div className="h-full flex flex-col" data-testid="lineage-page">
      {/* Header + Stats */}
      <div className="border-b border-border px-6 py-3 shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <GitBranch className="w-4 h-4 text-primary" /> Data Lineage
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">OpenLineage-powered dependency graph</p>
          </div>
          <div className="flex items-center gap-4" data-testid="lineage-stats">
            <StatBadge icon={Activity} label="Events" value={stats?.total_events ?? 0} />
            <StatBadge icon={Workflow} label="Jobs" value={stats?.unique_jobs ?? 0} />
            <StatBadge icon={DatabaseIcon} label="Datasets" value={stats?.unique_datasets ?? 0} />
          </div>
        </div>
        {/* Legend */}
        <div className="flex items-center gap-4 mt-2">
          <div className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full border-2 border-primary bg-primary/20" />
            <span className="text-[10px] text-muted-foreground">Jobs</span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-3 h-3 rounded-full border-2 border-muted-foreground/50 bg-muted/30" />
            <span className="text-[10px] text-muted-foreground">Datasets</span>
          </div>
        </div>
      </div>

      {/* Graph */}
      <div className="flex-1 relative min-h-[280px]" ref={containerRef}>
        <svg ref={svgRef} className="w-full h-full bg-background" data-testid="lineage-svg" />

        {graphPending && (
          <div
            className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background/85 text-muted-foreground text-sm"
            data-testid="lineage-loading"
          >
            <Loader2 className="w-5 h-5 animate-spin text-primary" />
            <p>Loading lineage graph…</p>
          </div>
        )}

        {!graphPending && graphError && (
          <div
            className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-destructive"
            data-testid="lineage-error"
          >
            Could not load lineage graph. Check the API and try again.
          </div>
        )}

        {!graphPending && !graphError && graph.nodes.length === 0 && (
          <div
            className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-muted-foreground"
            data-testid="lineage-empty"
          >
            No lineage data yet. Run a query first so jobs and datasets appear here.
          </div>
        )}

        {/* Selected node detail */}
        {selectedNode && (
          <div className="absolute top-4 right-4 w-64" data-testid="node-detail">
            <Card className="border-border/50">
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-xs font-semibold">{selectedNode.type === "job" ? "Job" : "Dataset"}</CardTitle>
                  <button onClick={() => setSelectedNode(null)} className="text-muted-foreground hover:text-foreground">
                    <X className="w-3 h-3" />
                  </button>
                </div>
              </CardHeader>
              <CardContent className="pt-0 space-y-2">
                <div>
                  <p className="text-xs text-muted-foreground">Name</p>
                  <p className="text-sm font-medium text-foreground">{selectedNode.name}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">Namespace</p>
                  <p className="text-sm font-mono text-foreground">{selectedNode.namespace}</p>
                </div>
                <div>
                  <p className="text-xs text-muted-foreground">ID</p>
                  <p className="text-[10px] font-mono text-muted-foreground break-all">{selectedNode.id}</p>
                </div>
                <Badge variant="outline" className={`text-[10px] ${selectedNode.type === "job" ? "border-primary/40 text-primary" : "border-muted-foreground/30"}`}>
                  {selectedNode.type}
                </Badge>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}

function StatBadge({ icon: Icon, label, value }: { icon: any; label: string; value: number }) {
  return (
    <div className="flex items-center gap-1.5">
      <Icon className="w-3.5 h-3.5 text-muted-foreground" />
      <span className="text-xs font-bold tabular-nums text-foreground">{value}</span>
      <span className="text-[10px] text-muted-foreground">{label}</span>
    </div>
  );
}
