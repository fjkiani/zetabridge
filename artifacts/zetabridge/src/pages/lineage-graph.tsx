import { useEffect, useRef, useState } from "react";
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

export default function LineageGraph() {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const { data: graph } = useQuery({
    queryKey: ["/api/lineage/graph"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/lineage/graph"); return r.json(); },
  });

  const { data: stats } = useQuery({
    queryKey: ["/api/lineage/stats"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/lineage/stats"); return r.json(); },
  });

  useEffect(() => {
    if (!graph || !svgRef.current || !containerRef.current) return;

    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;

    // Clear previous
    d3.select(svgRef.current).selectAll("*").remove();

    const svg = d3.select(svgRef.current)
      .attr("width", width)
      .attr("height", height);

    // Zoom
    const g = svg.append("g");
    const zoom = d3.zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.3, 3])
      .on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);

    // Arrow marker
    svg.append("defs").append("marker")
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

    const nodes: GraphNode[] = graph.nodes.map((n: any) => ({ ...n }));
    const edges: GraphEdge[] = graph.edges.map((e: any) => ({ source: e.source, target: e.target }));

    const simulation = d3.forceSimulation<GraphNode>(nodes)
      .force("link", d3.forceLink<GraphNode, any>(edges).id(d => d.id).distance(120))
      .force("charge", d3.forceManyBody().strength(-300))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(35));

    // Edges
    const link = g.append("g")
      .selectAll("line")
      .data(edges)
      .join("line")
      .attr("class", "lineage-edge")
      .attr("stroke", "hsl(220, 16%, 28%)")
      .attr("stroke-width", 1.5)
      .attr("marker-end", "url(#arrowhead)");

    // Node groups
    const node = g.append("g")
      .selectAll<SVGGElement, GraphNode>("g")
      .data(nodes)
      .join("g")
      .attr("class", "lineage-node cursor-pointer")
      .call(d3.drag<SVGGElement, GraphNode>()
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
        })
      );

    // Node circles
    node.append("circle")
      .attr("r", 16)
      .attr("fill", d => d.type === "job" ? "hsl(193, 80%, 25%)" : "hsl(220, 20%, 18%)")
      .attr("stroke", d => d.type === "job" ? "hsl(193, 100%, 50%)" : "hsl(220, 16%, 35%)")
      .attr("stroke-width", 2);

    // Node icons (text-based)
    node.append("text")
      .attr("text-anchor", "middle")
      .attr("dominant-baseline", "central")
      .attr("font-size", "10px")
      .attr("fill", d => d.type === "job" ? "hsl(193, 100%, 70%)" : "hsl(210, 20%, 70%)")
      .text(d => d.type === "job" ? "⚙" : "▦");

    // Labels
    node.append("text")
      .attr("dy", 28)
      .attr("text-anchor", "middle")
      .attr("font-size", "10px")
      .attr("fill", "hsl(210, 20%, 70%)")
      .attr("font-family", "'DM Sans', sans-serif")
      .text(d => d.name.length > 18 ? d.name.slice(0, 16) + "…" : d.name);

    node.on("click", (_event: any, d: GraphNode) => {
      setSelectedNode(d);
    });

    simulation.on("tick", () => {
      link
        .attr("x1", (d: any) => d.source.x)
        .attr("y1", (d: any) => d.source.y)
        .attr("x2", (d: any) => d.target.x)
        .attr("y2", (d: any) => d.target.y);
      node.attr("transform", (d: any) => `translate(${d.x},${d.y})`);
    });

    // Initial zoom
    svg.call(zoom.transform, d3.zoomIdentity.translate(width * 0.1, height * 0.1).scale(0.8));

    return () => { simulation.stop(); };
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
      <div className="flex-1 relative" ref={containerRef}>
        <svg ref={svgRef} className="w-full h-full bg-background" data-testid="lineage-svg" />

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
