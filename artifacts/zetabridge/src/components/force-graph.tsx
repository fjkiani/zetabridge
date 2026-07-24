import { useEffect, useRef } from "react";
import * as d3 from "d3";
import { ENDPOINT_META, endpointOf, type EndpointCode, type GNode, type GEdge } from "@/lib/graphApi";

/**
 * Reusable endpoint-colored D3 force-directed graph.
 *
 * Generalized from the working `/lineage` renderer: same zoom/pan/drag/simulation
 * machinery, but nodes are colored by federation endpoint (A_MSK / B_SAS / C_EGA)
 * and it accepts a click handler + an optional highlighted path (for the Path
 * Finder). Rendering is pure D3 into an SVG ref; React only owns the container.
 */

export interface ForceGraphNode extends GNode {
  x?: number;
  y?: number;
  fx?: number | null;
  fy?: number | null;
}

type SimEdge = {
  source: string | ForceGraphNode;
  target: string | ForceGraphNode;
  type: string;
};

const NEUTRAL = "hsl(220, 20%, 45%)"; // nodes with no resolved endpoint
const EDGE_COLOR = "hsl(220, 16%, 28%)";
const HILITE = "hsl(45, 100%, 60%)";

function colorFor(ep: EndpointCode): string {
  return ep ? ENDPOINT_META[ep].color : NEUTRAL;
}

function labelText(d: ForceGraphNode): string {
  const n = d?.name ?? d?.id ?? "";
  return n.length > 20 ? `${n.slice(0, 18)}…` : n;
}

export interface ForceGraphProps {
  nodes: GNode[];
  edges: GEdge[];
  /** Ordered node-ids forming a highlighted path (Path Finder). */
  highlightPath?: string[];
  /** The currently selected / centered node-id (drawn larger with a ring). */
  centerId?: string | null;
  onNodeClick?: (node: GNode) => void;
  onNodeDoubleClick?: (node: GNode) => void;
}

export function ForceGraph({
  nodes,
  edges,
  highlightPath,
  centerId,
  onNodeClick,
  onNodeDoubleClick,
}: ForceGraphProps) {
  const svgRef = useRef<SVGSVGElement>(null);
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!svgRef.current || !containerRef.current) return;
    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;

    d3.select(svgRef.current).selectAll("*").remove();
    if (nodes.length === 0) return;

    const simNodes: ForceGraphNode[] = nodes.map((n) => ({ ...n }));
    const idSet = new Set(simNodes.map((n) => n.id));
    const simEdges: SimEdge[] = edges
      .filter((e) => idSet.has(e.source) && idSet.has(e.target))
      .map((e) => ({ source: e.source, target: e.target, type: e.type }));

    // path edge lookup ("a|b") for highlighting
    const pathEdgeKeys = new Set<string>();
    const pathNodeSet = new Set(highlightPath ?? []);
    if (highlightPath && highlightPath.length > 1) {
      for (let i = 0; i < highlightPath.length - 1; i++) {
        pathEdgeKeys.add(`${highlightPath[i]}|${highlightPath[i + 1]}`);
        pathEdgeKeys.add(`${highlightPath[i + 1]}|${highlightPath[i]}`);
      }
    }

    const svg = d3.select(svgRef.current).attr("width", width).attr("height", height);
    const g = svg.append("g");
    const zoom = d3
      .zoom<SVGSVGElement, unknown>()
      .scaleExtent([0.2, 4])
      .on("zoom", (event) => g.attr("transform", event.transform));
    svg.call(zoom);

    svg
      .append("defs")
      .append("marker")
      .attr("id", "fg-arrow")
      .attr("viewBox", "0 -5 10 10")
      .attr("refX", 20)
      .attr("refY", 0)
      .attr("markerWidth", 6)
      .attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
      .attr("d", "M0,-5L10,0L0,5")
      .attr("fill", EDGE_COLOR);

    const simulation = d3
      .forceSimulation<ForceGraphNode>(simNodes)
      .force(
        "link",
        d3
          .forceLink<ForceGraphNode, SimEdge>(simEdges)
          .id((d) => d.id)
          .distance(110),
      )
      .force("charge", d3.forceManyBody().strength(-280))
      .force("center", d3.forceCenter(width / 2, height / 2))
      .force("collision", d3.forceCollide().radius(30));

    const link = g
      .append("g")
      .selectAll("line")
      .data(simEdges)
      .join("line")
      .attr("stroke", (d) => {
        const s = typeof d.source === "string" ? d.source : d.source.id;
        const t = typeof d.target === "string" ? d.target : d.target.id;
        return pathEdgeKeys.has(`${s}|${t}`) ? HILITE : EDGE_COLOR;
      })
      .attr("stroke-width", (d) => {
        const s = typeof d.source === "string" ? d.source : d.source.id;
        const t = typeof d.target === "string" ? d.target : d.target.id;
        return pathEdgeKeys.has(`${s}|${t}`) ? 3 : 1.3;
      })
      .attr("marker-end", "url(#fg-arrow)");

    // edge relationship labels (only for highlighted path to avoid clutter)
    const edgeLabel = g
      .append("g")
      .selectAll("text")
      .data(simEdges.filter((d) => {
        const s = typeof d.source === "string" ? d.source : d.source.id;
        const t = typeof d.target === "string" ? d.target : d.target.id;
        return pathEdgeKeys.has(`${s}|${t}`);
      }))
      .join("text")
      .attr("font-size", "9px")
      .attr("fill", HILITE)
      .attr("text-anchor", "middle")
      .attr("font-family", "monospace")
      .text((d) => d.type);

    const node = g
      .append("g")
      .selectAll<SVGGElement, ForceGraphNode>("g")
      .data(simNodes)
      .join("g")
      .attr("class", "cursor-pointer")
      .call(
        d3
          .drag<SVGGElement, ForceGraphNode>()
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
      .attr("r", (d) => (d.id === centerId ? 18 : pathNodeSet.has(d.id) ? 15 : 12))
      .attr("fill", (d) => {
        const c = colorFor(d.endpoint ?? endpointOf(d.id));
        return d3.color(c)?.copy({ opacity: 0.28 })?.toString() ?? c;
      })
      .attr("stroke", (d) => {
        if (pathNodeSet.has(d.id) || d.id === centerId) return HILITE;
        return colorFor(d.endpoint ?? endpointOf(d.id));
      })
      .attr("stroke-width", (d) => (pathNodeSet.has(d.id) || d.id === centerId ? 3 : 2));

    node
      .append("text")
      .attr("dy", 26)
      .attr("text-anchor", "middle")
      .attr("font-size", "10px")
      .attr("fill", "hsl(210, 20%, 72%)")
      .attr("font-family", "'DM Sans', sans-serif")
      .text((d) => labelText(d));

    node.append("title").text((d) => `${d.label} • ${d.id}`);

    if (onNodeClick) node.on("click", (_e, d) => onNodeClick(d));
    if (onNodeDoubleClick) node.on("dblclick", (_e, d) => onNodeDoubleClick(d));

    simulation.on("tick", () => {
      link
        .attr("x1", (d) => (typeof d.source === "string" ? 0 : d.source.x ?? 0))
        .attr("y1", (d) => (typeof d.source === "string" ? 0 : d.source.y ?? 0))
        .attr("x2", (d) => (typeof d.target === "string" ? 0 : d.target.x ?? 0))
        .attr("y2", (d) => (typeof d.target === "string" ? 0 : d.target.y ?? 0));
      edgeLabel
        .attr("x", (d) => {
          const s = typeof d.source === "string" ? 0 : d.source.x ?? 0;
          const t = typeof d.target === "string" ? 0 : d.target.x ?? 0;
          return (s + t) / 2;
        })
        .attr("y", (d) => {
          const s = typeof d.source === "string" ? 0 : d.source.y ?? 0;
          const t = typeof d.target === "string" ? 0 : d.target.y ?? 0;
          return (s + t) / 2 - 4;
        });
      node.attr("transform", (d) => `translate(${d.x ?? 0},${d.y ?? 0})`);
    });

    svg.call(zoom.transform, d3.zoomIdentity.translate(width * 0.05, height * 0.05).scale(0.85));

    return () => {
      simulation.stop();
    };
  }, [nodes, edges, highlightPath, centerId, onNodeClick, onNodeDoubleClick]);

  return (
    <div className="w-full h-full relative" ref={containerRef}>
      <svg ref={svgRef} className="w-full h-full bg-background" data-testid="force-graph-svg" />
    </div>
  );
}

/** Small reusable endpoint legend. */
export function EndpointLegend() {
  return (
    <div className="flex items-center gap-4">
      {(Object.keys(ENDPOINT_META) as Exclude<EndpointCode, null>[]).map((code) => (
        <div key={code} className="flex items-center gap-1.5">
          <span
            className="w-3 h-3 rounded-full border-2"
            style={{ borderColor: ENDPOINT_META[code].color, backgroundColor: `${ENDPOINT_META[code].color}33` }}
          />
          <span className="text-[10px] text-muted-foreground">
            {code} · {ENDPOINT_META[code].short}
          </span>
        </div>
      ))}
    </div>
  );
}
