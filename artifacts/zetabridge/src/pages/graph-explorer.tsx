import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Network,
  Search,
  X,
  Loader2,
  Radar,
  Info,
  Trash2,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ForceGraph, EndpointLegend } from "@/components/force-graph";
import {
  ENDPOINT_META,
  endpointOf,
  searchNodes,
  getNeighbors,
  getNode,
  findPaths,
  getSchema,
  LAST_SOURCE,
  type EndpointCode,
  type GNode,
  type GEdge,
  type GPath,
} from "@/lib/graphApi";

type Mode = "explore" | "paths";

function SourceBadge({ live }: { live: boolean }) {
  return (
    <Badge
      variant="outline"
      className={`text-[10px] ${live ? "text-emerald-400 border-emerald-400/40" : "text-amber-400 border-amber-400/40"}`}
      data-testid="source-badge"
    >
      {live ? "● live graph" : "○ snapshot"}
    </Badge>
  );
}

function EndpointTag({ ep }: { ep: EndpointCode }) {
  if (!ep) return <Badge variant="outline" className="text-[9px] text-muted-foreground">—</Badge>;
  const m = ENDPOINT_META[ep];
  return (
    <Badge variant="outline" className="text-[9px]" style={{ color: m.color, borderColor: `${m.color}66` }}>
      {ep} · {m.short}
    </Badge>
  );
}

export default function GraphExplorer() {
  const [mode, setMode] = useState<Mode>("explore");

  // shared canvas state
  const [nodes, setNodes] = useState<GNode[]>([]);
  const [edges, setEdges] = useState<GEdge[]>([]);
  const [centerId, setCenterId] = useState<string | null>(null);
  const [selected, setSelected] = useState<GNode | null>(null);
  const [source, setSource] = useState<"live" | "snapshot">(LAST_SOURCE);
  const [busy, setBusy] = useState(false);
  const [errMsg, setErrMsg] = useState<string | null>(null);

  // search controls
  const [query, setQuery] = useState("");
  const [prefixFilter, setPrefixFilter] = useState<string>("any");
  const [results, setResults] = useState<GNode[]>([]);
  const [searching, setSearching] = useState(false);

  // labels for the filter dropdown
  const [labelOptions, setLabelOptions] = useState<string[]>([]);

  // path finder controls
  const [pathTarget, setPathTarget] = useState<Exclude<EndpointCode, null>>("C_EGA");
  const [maxHops, setMaxHops] = useState(5);
  const [paths, setPaths] = useState<GPath[]>([]);
  const [activePathIdx, setActivePathIdx] = useState(0);

  const focusNode = useCallback(async (id: string, hops = 1) => {
    setBusy(true);
    setErrMsg(null);
    try {
      const [sub, detail] = await Promise.all([
        getNeighbors({ id, hops, direction: "both", cap: 200 }),
        getNode(id),
      ]);
      setNodes(sub.nodes);
      setEdges(sub.edges);
      setCenterId(id);
      setSelected(detail);
      setSource(LAST_SOURCE);
      if (sub.nodes.length <= 1) setErrMsg("This node has no loaded neighbors in the current data source.");
    } catch (e) {
      setErrMsg(`Could not expand node: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    getSchema()
      .then((s) => {
        setLabelOptions(s.labels.slice(0, 40).map((l) => l.label));
        setSource(LAST_SOURCE);
      })
      .catch(() => {});
  }, []);

  // deep-link: #/graph?focus=<id> (from the Insights page) auto-expands a node
  useEffect(() => {
    const hash = window.location.hash; // e.g. "#/graph?focus=ega:dataset:..."
    const qIdx = hash.indexOf("?");
    if (qIdx === -1) return;
    const params = new URLSearchParams(hash.slice(qIdx + 1));
    const focus = params.get("focus");
    if (focus) {
      setQuery(focus);
      void focusNode(focus, 1);
    }
  }, [focusNode]);

  const endpointPrefixes = useMemo(
    () => ({
      A_MSK: "genomicfeature:msk:",
      B_SAS: "trial:sas:",
      C_EGA: "ega:",
    }),
    [],
  );

  const runSearch = useCallback(async () => {
    setSearching(true);
    setErrMsg(null);
    try {
      const params: Parameters<typeof searchNodes>[0] = { limit: 40 };
      if (query.trim()) params.name_contains = query.trim();
      if (prefixFilter !== "any") {
        if (prefixFilter.startsWith("prefix:")) params.prefix = prefixFilter.slice(7);
        else params.label = prefixFilter;
      }
      const r = await searchNodes(params);
      setResults(r);
      setSource(LAST_SOURCE);
      if (r.length === 0) setErrMsg("No nodes matched. Try a broader term or clear the filter.");
    } catch (e) {
      setErrMsg(`Search failed: ${String(e)}`);
    } finally {
      setSearching(false);
    }
  }, [query, prefixFilter]);

  const expandMore = useCallback(async () => {
    if (!centerId) return;
    await focusNode(centerId, 2);
  }, [centerId, focusNode]);

  const renderPath = useCallback(async (p: GPath) => {
    const pathNodes: GNode[] = p.node_ids.map((id) => ({
      id,
      name: id,
      label: "Node",
      labels: [],
      endpoint: endpointOf(id),
    }));
    const pathEdges: GEdge[] = [];
    for (let i = 0; i < p.node_ids.length - 1; i++) {
      pathEdges.push({ source: p.node_ids[i], target: p.node_ids[i + 1], type: p.rel_types[i] ?? "REL" });
    }
    setNodes(pathNodes);
    setEdges(pathEdges);
    setCenterId(p.node_ids[0]);
  }, []);

  const runPaths = useCallback(async () => {
    if (!centerId) {
      setErrMsg("Pick a source node first (search, then click a result).");
      return;
    }
    setBusy(true);
    setErrMsg(null);
    try {
      const r = await findPaths({
        source_id: centerId,
        target_prefix: endpointPrefixes[pathTarget],
        max_hops: maxHops,
        k: 12,
      });
      setPaths(r.paths);
      setActivePathIdx(0);
      setSource(LAST_SOURCE);
      if (r.paths.length === 0) {
        setErrMsg(`No path found from this node to ${pathTarget} within ${maxHops} hops.`);
      } else {
        await renderPath(r.paths[0]);
      }
    } catch (e) {
      setErrMsg(`Path search failed: ${String(e)}`);
    } finally {
      setBusy(false);
    }
  }, [centerId, pathTarget, maxHops, endpointPrefixes, renderPath]);

  const activePath = paths[activePathIdx];
  const highlightPath = mode === "paths" && activePath ? activePath.node_ids : undefined;

  return (
    <div className="h-full flex flex-col" data-testid="graph-explorer-page">
      {/* Header */}
      <div className="border-b border-border px-6 py-3 shrink-0">
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-sm font-semibold text-foreground flex items-center gap-2">
              <Network className="w-4 h-4 text-primary" /> Graph Explorer
            </h1>
            <p className="text-xs text-muted-foreground mt-0.5">
              Search the federated graph, expand neighborhoods, and trace cross-endpoint opportunities
            </p>
          </div>
          <div className="flex items-center gap-3">
            <EndpointLegend />
            <SourceBadge live={source === "live"} />
          </div>
        </div>
        {/* Mode tabs */}
        <div className="flex items-center gap-1 mt-3">
          <Button
            size="sm"
            variant={mode === "explore" ? "default" : "ghost"}
            className="h-7 text-xs"
            onClick={() => setMode("explore")}
            data-testid="tab-explore"
          >
            <Search className="w-3 h-3 mr-1" /> Explore
          </Button>
          <Button
            size="sm"
            variant={mode === "paths" ? "default" : "ghost"}
            className="h-7 text-xs"
            onClick={() => setMode("paths")}
            data-testid="tab-paths"
          >
            <Radar className="w-3 h-3 mr-1" /> Path Finder
          </Button>
        </div>
      </div>

      <div className="flex-1 flex min-h-0">
        {/* Left control panel */}
        <div className="w-72 border-r border-border shrink-0 flex flex-col">
          {mode === "explore" ? (
            <div className="p-3 space-y-3">
              <div className="space-y-2">
                <label className="text-[11px] text-muted-foreground font-medium">Search nodes</label>
                <div className="flex gap-1.5">
                  <Input
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && runSearch()}
                    placeholder="name or id contains…"
                    className="h-8 text-xs"
                    data-testid="search-input"
                  />
                  <Button size="sm" className="h-8 px-2" onClick={runSearch} disabled={searching} data-testid="search-btn">
                    {searching ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Search className="w-3.5 h-3.5" />}
                  </Button>
                </div>
                <Select value={prefixFilter} onValueChange={setPrefixFilter}>
                  <SelectTrigger className="h-8 text-xs" data-testid="filter-select">
                    <SelectValue placeholder="Filter" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="any">Any endpoint / label</SelectItem>
                    <SelectItem value="prefix:genomicfeature:msk:">A_MSK · genomic features</SelectItem>
                    <SelectItem value="prefix:biospecimen:msk:">A_MSK · biospecimens</SelectItem>
                    <SelectItem value="prefix:trial:sas:">B_SAS · trials</SelectItem>
                    <SelectItem value="prefix:patient:sas:">B_SAS · patients</SelectItem>
                    <SelectItem value="prefix:ega:">C_EGA · files/samples/datasets</SelectItem>
                    <SelectItem value="prefix:specimen:britroc1:">C_EGA · BriTROC specimens</SelectItem>
                    {labelOptions.map((l) => (
                      <SelectItem key={l} value={l}>
                        label: {l}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>

              <div className="flex items-center justify-between">
                <span className="text-[11px] text-muted-foreground">
                  {results.length > 0 ? `${results.length} results` : "results"}
                </span>
                {results.length > 0 && (
                  <button
                    className="text-[10px] text-muted-foreground hover:text-foreground flex items-center gap-1"
                    onClick={() => setResults([])}
                  >
                    <Trash2 className="w-3 h-3" /> clear
                  </button>
                )}
              </div>

              <ScrollArea className="h-[calc(100vh-320px)]">
                <div className="space-y-1 pr-2">
                  {results.map((n) => (
                    <button
                      key={n.id}
                      onClick={() => focusNode(n.id, 1)}
                      className={`w-full text-left p-2 rounded-md border transition-colors ${
                        centerId === n.id
                          ? "border-primary/50 bg-primary/5"
                          : "border-border/50 hover:bg-muted/30"
                      }`}
                      data-testid={`result-${n.id}`}
                    >
                      <div className="flex items-center justify-between gap-1">
                        <span className="text-xs font-medium text-foreground truncate">{n.name}</span>
                        <EndpointTag ep={n.endpoint} />
                      </div>
                      <div className="flex items-center gap-1.5 mt-0.5">
                        <Badge variant="outline" className="text-[9px] text-muted-foreground">
                          {n.label}
                        </Badge>
                        {typeof n.degree === "number" && (
                          <span className="text-[9px] text-muted-foreground">deg {n.degree}</span>
                        )}
                      </div>
                    </button>
                  ))}
                </div>
              </ScrollArea>
            </div>
          ) : (
            <div className="p-3 space-y-3">
              <div className="rounded-md border border-border/50 p-2.5 bg-muted/10">
                <p className="text-[11px] text-muted-foreground">Source node</p>
                {centerId ? (
                  <p className="text-xs font-mono text-foreground break-all mt-0.5">{centerId}</p>
                ) : (
                  <p className="text-xs text-amber-400 mt-0.5">
                    Go to Explore, search, and click a node to set the source.
                  </p>
                )}
              </div>

              <div className="space-y-1.5">
                <label className="text-[11px] text-muted-foreground font-medium">Target endpoint</label>
                <Select value={pathTarget} onValueChange={(v) => setPathTarget(v as Exclude<EndpointCode, null>)}>
                  <SelectTrigger className="h-8 text-xs" data-testid="path-target-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="A_MSK">A_MSK · MSK SPECTRUM</SelectItem>
                    <SelectItem value="B_SAS">B_SAS · SAS trials</SelectItem>
                    <SelectItem value="C_EGA">C_EGA · EGA / BriTROC</SelectItem>
                  </SelectContent>
                </Select>
              </div>

              <div className="space-y-1.5">
                <label className="text-[11px] text-muted-foreground font-medium">Max hops: {maxHops}</label>
                <input
                  type="range"
                  min={1}
                  max={6}
                  value={maxHops}
                  onChange={(e) => setMaxHops(Number(e.target.value))}
                  className="w-full accent-primary"
                  data-testid="max-hops-slider"
                />
              </div>

              <Button
                size="sm"
                className="w-full h-8 text-xs"
                onClick={runPaths}
                disabled={busy || !centerId}
                data-testid="find-paths-btn"
              >
                {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin mr-1" /> : <Radar className="w-3.5 h-3.5 mr-1" />}
                Find cross-endpoint paths
              </Button>

              {paths.length > 0 && (
                <div className="space-y-1.5">
                  <span className="text-[11px] text-muted-foreground">{paths.length} paths ranked by hops</span>
                  <ScrollArea className="h-[calc(100vh-460px)]">
                    <div className="space-y-1 pr-2">
                      {paths.map((p, i) => (
                        <button
                          key={i}
                          onClick={() => {
                            setActivePathIdx(i);
                            renderPath(p);
                          }}
                          className={`w-full text-left p-2 rounded-md border transition-colors ${
                            i === activePathIdx ? "border-primary/50 bg-primary/5" : "border-border/50 hover:bg-muted/30"
                          }`}
                          data-testid={`path-${i}`}
                        >
                          <div className="flex items-center justify-between">
                            <span className="text-xs font-medium text-foreground">Path {i + 1}</span>
                            <Badge variant="outline" className="text-[9px]">
                              {p.hops} hops
                            </Badge>
                          </div>
                          <div className="flex items-center gap-1 mt-1">
                            <EndpointTag ep={p.source_endpoint} />
                            <span className="text-[9px] text-muted-foreground">→</span>
                            <EndpointTag ep={p.target_endpoint} />
                          </div>
                        </button>
                      ))}
                    </div>
                  </ScrollArea>
                </div>
              )}
            </div>
          )}
        </div>

        {/* Center canvas */}
        <div className="flex-1 relative min-h-0">
          <ForceGraph
            nodes={nodes}
            edges={edges}
            centerId={centerId}
            highlightPath={highlightPath}
            onNodeClick={(n) => setSelected(n)}
            onNodeDoubleClick={(n) => focusNode(n.id, 1)}
          />

          {busy && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-background/70 text-muted-foreground text-sm">
              <Loader2 className="w-5 h-5 animate-spin text-primary" />
              <p>Querying graph…</p>
            </div>
          )}

          {!busy && nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center px-6 text-center text-sm text-muted-foreground">
              {mode === "explore"
                ? "Search a node on the left, then click a result to expand its neighborhood."
                : "Set a source node in Explore, choose a target endpoint, then Find paths."}
            </div>
          )}

          {errMsg && (
            <div className="absolute bottom-4 left-1/2 -translate-x-1/2 px-3 py-1.5 rounded-md bg-destructive/15 border border-destructive/40 text-xs text-destructive max-w-[80%] text-center">
              {errMsg}
            </div>
          )}

          {/* Path relationship chain overlay */}
          {mode === "paths" && activePath && (
            <div className="absolute bottom-4 left-4 right-4 rounded-md border border-border/50 bg-card/90 backdrop-blur px-3 py-2" data-testid="path-chain">
              <div className="flex items-center gap-1 flex-wrap text-[11px]">
                {activePath.node_ids.map((id, i) => (
                  <span key={id} className="flex items-center gap-1">
                    <code
                      className="px-1.5 py-0.5 rounded text-[10px]"
                      style={{
                        color: endpointOf(id) ? ENDPOINT_META[endpointOf(id)!].color : "hsl(220,20%,60%)",
                        backgroundColor: endpointOf(id) ? `${ENDPOINT_META[endpointOf(id)!].color}1a` : "transparent",
                      }}
                    >
                      {id}
                    </code>
                    {i < activePath.rel_types.length && (
                      <span className="text-[9px] text-amber-400 font-mono">—{activePath.rel_types[i]}→</span>
                    )}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right detail panel */}
        {selected && (
          <div className="w-72 border-l border-border shrink-0" data-testid="node-detail-panel">
            <Card className="border-0 rounded-none h-full">
              <CardHeader className="pb-2">
                <div className="flex items-center justify-between">
                  <CardTitle className="text-xs font-semibold flex items-center gap-1.5">
                    <Info className="w-3.5 h-3.5 text-primary" /> Node detail
                  </CardTitle>
                  <button onClick={() => setSelected(null)} className="text-muted-foreground hover:text-foreground">
                    <X className="w-3.5 h-3.5" />
                  </button>
                </div>
              </CardHeader>
              <CardContent className="pt-0 space-y-3">
                <div>
                  <p className="text-[10px] text-muted-foreground">Name</p>
                  <p className="text-sm font-medium text-foreground break-words">{selected.name}</p>
                </div>
                <div className="flex items-center gap-1.5 flex-wrap">
                  <EndpointTag ep={selected.endpoint} />
                  <Badge variant="outline" className="text-[9px]">
                    {selected.label}
                  </Badge>
                  {typeof selected.degree === "number" && (
                    <Badge variant="outline" className="text-[9px] text-muted-foreground">
                      degree {selected.degree}
                    </Badge>
                  )}
                </div>
                <div>
                  <p className="text-[10px] text-muted-foreground">ID</p>
                  <p className="text-[10px] font-mono text-muted-foreground break-all">{selected.id}</p>
                </div>
                {selected.labels.length > 0 && (
                  <div>
                    <p className="text-[10px] text-muted-foreground mb-1">Labels</p>
                    <div className="flex flex-wrap gap-1">
                      {selected.labels.map((l) => (
                        <Badge key={l} variant="outline" className="text-[9px]">
                          {l}
                        </Badge>
                      ))}
                    </div>
                  </div>
                )}
                {selected.relSummary && Object.keys(selected.relSummary).length > 0 && (
                  <div>
                    <p className="text-[10px] text-muted-foreground mb-1">Relationships</p>
                    <div className="space-y-0.5">
                      {Object.entries(selected.relSummary)
                        .sort((a, b) => b[1] - a[1])
                        .slice(0, 12)
                        .map(([t, c]) => (
                          <div key={t} className="flex items-center justify-between text-[10px]">
                            <code className="text-primary">{t}</code>
                            <span className="text-muted-foreground tabular-nums">{c}</span>
                          </div>
                        ))}
                    </div>
                  </div>
                )}
                <div className="flex gap-1.5 pt-1">
                  <Button size="sm" variant="outline" className="h-7 text-[11px] flex-1" onClick={() => focusNode(selected.id, 1)}>
                    Expand 1-hop
                  </Button>
                  <Button size="sm" variant="outline" className="h-7 text-[11px] flex-1" onClick={expandMore}>
                    2-hop
                  </Button>
                </div>
                {mode === "explore" && (
                  <Button
                    size="sm"
                    className="w-full h-7 text-[11px]"
                    onClick={() => {
                      setCenterId(selected.id);
                      setMode("paths");
                    }}
                  >
                    <Radar className="w-3 h-3 mr-1" /> Use as path source
                  </Button>
                )}
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
