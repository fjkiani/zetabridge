import { useCallback, useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "wouter";
import { apiRequest } from "@/lib/queryClient";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Plug,
  Database,
  GitBranch,
  Cog,
  Waves,
  Search,
  HardDrive,
  Box,
  BarChart3,
  CheckCircle2,
  AlertCircle,
  MinusCircle,
  Plus,
  Download,
  ShieldAlert,
  Loader2,
  Link2,
  FileJson,
  Sparkles,
  Wrench,
  ArrowRight,
  ExternalLink,
} from "lucide-react";
import {
  ENDPOINT_META,
  searchNodes,
  LAST_SOURCE,
  type EndpointCode,
  type GNode,
} from "@/lib/graphApi";
import {
  getGaps,
  runSignalAgent,
  graphFocusHref,
  type GapItem,
  type AgentResponse,
} from "@/lib/signalsApi";

const categoryIcons: Record<string, any> = {
  data_lake: HardDrive,
  warehouse: BarChart3,
  etl: Cog,
  streaming: Waves,
  search: Search,
  catalog: Database,
  lineage: GitBranch,
  object_storage: Box,
  database: Database,
};

const categoryColors: Record<string, string> = {
  data_lake: "text-cyan-400 border-cyan-400/20 bg-cyan-400/5",
  warehouse: "text-violet-400 border-violet-400/20 bg-violet-400/5",
  etl: "text-amber-400 border-amber-400/20 bg-amber-400/5",
  streaming: "text-rose-400 border-rose-400/20 bg-rose-400/5",
  search: "text-orange-400 border-orange-400/20 bg-orange-400/5",
  catalog: "text-blue-400 border-blue-400/20 bg-blue-400/5",
  lineage: "text-emerald-400 border-emerald-400/20 bg-emerald-400/5",
  object_storage: "text-indigo-400 border-indigo-400/20 bg-indigo-400/5",
  database: "text-teal-400 border-teal-400/20 bg-teal-400/5",
};

const statusConfig: Record<string, { color: string; icon: any; label: string }> = {
  active: { color: "text-emerald-400 bg-emerald-400/10 border-emerald-400/30", icon: CheckCircle2, label: "Active" },
  configured: { color: "text-amber-400 bg-amber-400/10 border-amber-400/30", icon: AlertCircle, label: "Configured" },
  available: { color: "text-muted-foreground bg-muted/20 border-muted-foreground/20", icon: MinusCircle, label: "Available" },
};

// existing federation endpoints a new source can bridge into
const EP_TARGETS: { code: Exclude<EndpointCode, null>; anchorPrefix: string; anchorLabel: string }[] = [
  { code: "A_MSK", anchorPrefix: "biospecimen:msk:", anchorLabel: "MSK biospecimen" },
  { code: "A_MSK", anchorPrefix: "genomicfeature:msk:", anchorLabel: "MSK genomic feature" },
  { code: "B_SAS", anchorPrefix: "trial:sas:", anchorLabel: "SAS trial" },
  { code: "C_EGA", anchorPrefix: "ega:sample:", anchorLabel: "EGA sample" },
  { code: "C_EGA", anchorPrefix: "specimen:britroc1:", anchorLabel: "BriTROC specimen" },
];

interface CandidateEdge {
  source_id: string;
  target_id: string;
  rel_type: string;
  target_endpoint: EndpointCode;
}

interface MintProposal {
  _session: number;
  _kind: "connection_mint_proposal";
  _draft: true;
  _generated: string;
  _note: string;
  source: {
    name: string;
    id_prefix: string;
    endpoint_code: string;
    description: string;
  };
  bridge_rule: {
    relation: string;
    anchor_prefix: string;
    anchor_endpoint: EndpointCode;
    match_on: string;
  };
  candidate_nodes: { id: string; label: string }[];
  candidate_edges: CandidateEdge[];
  counts: { candidate_nodes: number; candidate_edges: number };
}

export default function Connectors() {
  const { data: connectors } = useQuery({
    queryKey: ["/api/connectors"],
    queryFn: async () => {
      try {
        const r = await apiRequest("GET", "/api/connectors");
        const j = await r.json();
        return Array.isArray(j) ? j : [];
      } catch {
        return [];
      }
    },
  });

  const { data: stats } = useQuery({
    queryKey: ["/api/connectors/stats"],
    queryFn: async () => {
      try {
        const r = await apiRequest("GET", "/api/connectors/stats");
        return r.json();
      } catch {
        return null;
      }
    },
  });

  return (
    <div className="p-6 space-y-6" data-testid="connectors-page">
      <div className="flex items-start justify-between">
        <div>
          <h1 className="text-xl font-bold text-foreground flex items-center gap-2">
            <Plug className="w-5 h-5 text-primary" /> Connectors
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">
            Data-platform integration registry · define new source-to-graph bridges (draft proposals)
          </p>
        </div>
        <AddConnectionDialog />
      </div>

      {/* Draft-only banner */}
      <div className="flex items-start gap-2 rounded-md border border-amber-400/30 bg-amber-400/5 px-3 py-2">
        <ShieldAlert className="w-4 h-4 text-amber-400 shrink-0 mt-0.5" />
        <p className="text-xs text-muted-foreground">
          The graph is <span className="text-amber-400 font-medium">read-only</span> from the front-end. "Add a
          connection" defines candidate edges and exports an <span className="text-foreground">additive mint proposal</span>{" "}
          (JSON) for review — it never writes to the live graph. Applying a proposal is a separate, explicitly
          authenticated step.
        </p>
      </div>

      {/* Connection Guide — read-only FE copilot #2: blind-spot → fix */}
      <ConnectionGuide />

      {/* Stats Summary */}
      {stats && (
        <div className="flex flex-wrap items-center gap-3" data-testid="connector-stats">
          {typeof stats.total_connectors === "number" && (
            <Badge variant="outline" className="text-xs">
              {stats.total_connectors} total
            </Badge>
          )}
          {typeof stats.active === "number" && (
            <Badge variant="outline" className="text-xs text-emerald-400 border-emerald-400/30">
              <CheckCircle2 className="w-3 h-3 mr-1" /> {stats.active} active
            </Badge>
          )}
          {Object.entries(stats.by_category || {}).map(([cat, count]) => (
            <Badge key={cat} variant="outline" className={`text-[10px] ${categoryColors[cat] || ""}`}>
              {cat.replace(/_/g, " ")}: {String(count)}
            </Badge>
          ))}
        </div>
      )}

      {/* Connector Grid */}
      {(connectors || []).length > 0 ? (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3" data-testid="connector-grid">
          {(connectors || []).map((c: any) => {
            const CategoryIcon = categoryIcons[c.category] || Plug;
            const status = statusConfig[c.status] || statusConfig.available;
            const StatusIcon = status.icon;
            return (
              <Card key={c.name} className="border-border/50" data-testid={`connector-${c.name}`}>
                <CardContent className="p-4">
                  <div className="flex items-start justify-between mb-2">
                    <div className="flex items-center gap-2.5">
                      <div className={`p-2 rounded-lg border ${categoryColors[c.category] || "bg-muted/10"}`}>
                        <CategoryIcon className="w-4 h-4" />
                      </div>
                      <div>
                        <p className="text-sm font-semibold text-foreground">{c.display_name}</p>
                        <Badge variant="outline" className={`text-[9px] mt-0.5 ${categoryColors[c.category] || ""}`}>
                          {(c.category || "").replace(/_/g, " ")}
                        </Badge>
                      </div>
                    </div>
                    <Badge variant="outline" className={`text-[10px] ${status.color}`}>
                      <StatusIcon className="w-2.5 h-2.5 mr-1" />
                      {status.label}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground mb-3 line-clamp-2">{c.description}</p>
                  <div className="space-y-1.5 text-xs">
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Protocol</span>
                      <span className="text-foreground font-mono text-[11px]">{c.protocol}</span>
                    </div>
                    <div className="flex justify-between">
                      <span className="text-muted-foreground">Component</span>
                      <code className="text-[11px] text-primary bg-primary/5 px-1.5 py-0.5 rounded">{c.oss_component}</code>
                    </div>
                  </div>
                </CardContent>
              </Card>
            );
          })}
        </div>
      ) : (
        <div className="rounded-md border border-border/50 p-6 text-center">
          <p className="text-sm text-muted-foreground">
            No registry connectors returned by the backend. Use{" "}
            <span className="text-foreground font-medium">Add a connection</span> to define a new source-to-graph bridge
            and export a mint proposal.
          </p>
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Connection Guide — read-only front-end copilot (#2)
// Reads a blind spot (KBGap) the federation has quantified, asks the grounded
// gap_auditor agent to explain it, and points the user at the mint-proposal
// builder below as the additive fix affordance. Never writes to the graph.
// ---------------------------------------------------------------------------

// parse `#/connectors?gap=<slug>` deep-link (hash router, so read from hash)
function parseGapParam(): string | null {
  if (typeof window === "undefined") return null;
  const hash = window.location.hash || "";
  const qIdx = hash.indexOf("?");
  if (qIdx === -1) return null;
  const params = new URLSearchParams(hash.slice(qIdx + 1));
  const g = params.get("gap");
  return g ? decodeURIComponent(g) : null;
}

function ConnectionGuide() {
  const [gaps, setGaps] = useState<GapItem[]>([]);
  const [selected, setSelected] = useState<string>("");
  const [loadingGaps, setLoadingGaps] = useState(true);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AgentResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  // load the quantified blind spots (works offline via snapshot)
  useEffect(() => {
    let alive = true;
    (async () => {
      try {
        const r = await getGaps();
        if (!alive) return;
        setGaps(r.gaps);
        // preselect from deep-link, else first gap
        const fromUrl = parseGapParam();
        const match = fromUrl && r.gaps.find((g) => g.slug === fromUrl);
        setSelected(match ? match.slug : r.gaps[0]?.slug ?? "");
      } catch (e) {
        if (alive) setErr(`Could not load blind spots: ${String(e)}`);
      } finally {
        if (alive) setLoadingGaps(false);
      }
    })();
    return () => {
      alive = false;
    };
  }, []);

  const current = useMemo(() => gaps.find((g) => g.slug === selected) ?? null, [gaps, selected]);

  const explain = useCallback(async () => {
    setRunning(true);
    setErr(null);
    setResult(null);
    try {
      // grounded, server-side agent — rejects offline by design (no fabrication)
      const res = await runSignalAgent({ agent: "gap_auditor", action: "list_gaps" });
      setResult(res);
    } catch (e) {
      setErr(
        "The Connection Guide agent runs grounded Cypher on the live backend. " +
          "Set VITE_API_BASE + VITE_ZETA_API_KEY to enable it — blind-spot browsing works offline, live analysis does not.",
      );
    } finally {
      setRunning(false);
    }
  }, []);

  return (
    <Card className="border-primary/20 bg-primary/[0.03]" data-testid="connection-guide">
      <CardContent className="p-4 space-y-3">
        <div className="flex items-start gap-2">
          <div className="p-2 rounded-lg border border-primary/30 bg-primary/5 shrink-0">
            <Sparkles className="w-4 h-4 text-primary" />
          </div>
          <div className="min-w-0">
            <p className="text-sm font-semibold text-foreground flex items-center gap-2">
              Connection Guide
              <Badge variant="outline" className="text-[9px] text-primary border-primary/40">
                read-only copilot
              </Badge>
            </p>
            <p className="text-xs text-muted-foreground mt-0.5">
              Pick a blind spot the federation has already quantified — the guide explains what pharma got wrong and
              which connection closes it. Then draft an additive mint proposal below.
            </p>
          </div>
        </div>

        {loadingGaps ? (
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Loader2 className="w-3.5 h-3.5 animate-spin" /> loading quantified blind spots…
          </div>
        ) : gaps.length === 0 ? (
          <p className="text-xs text-muted-foreground">No blind spots available.</p>
        ) : (
          <>
            <div className="grid grid-cols-1 sm:grid-cols-[1fr_auto] gap-2 items-end">
              <div className="space-y-1 min-w-0">
                <label className="text-[11px] text-muted-foreground font-medium">Blind spot</label>
                <Select value={selected} onValueChange={setSelected}>
                  <SelectTrigger className="h-8 text-xs" data-testid="gap-select">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {gaps.map((g) => (
                      <SelectItem key={g.slug} value={g.slug}>
                        <span className="truncate">
                          {(g.name || g.slug).slice(0, 60)}
                          {g.gap_type ? ` · ${g.gap_type}` : ""}
                        </span>
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Button
                size="sm"
                className="h-8 text-xs"
                onClick={explain}
                disabled={running || !selected}
                data-testid="explain-gaps-btn"
              >
                {running ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Sparkles className="w-3.5 h-3.5 mr-1" />}
                Explain the gaps
              </Button>
            </div>

            {/* selected gap facts (grounded from getGaps, works offline) */}
            {current && (
              <div className="rounded-md border border-border/50 p-2.5 space-y-1.5">
                <div className="flex flex-wrap items-center gap-1.5">
                  {current.gap_type && (
                    <Badge variant="outline" className="text-[9px] text-amber-400 border-amber-400/40">
                      {current.gap_type}
                    </Badge>
                  )}
                  {current.current_tier && (
                    <Badge variant="outline" className="text-[9px]">
                      tier: {current.current_tier}
                    </Badge>
                  )}
                  <code className="text-[10px] text-muted-foreground truncate">{current.slug}</code>
                </div>
                {current.impact && (
                  <p className="text-[11px] text-muted-foreground">
                    <span className="text-foreground font-medium">Impact:</span> {current.impact}
                  </p>
                )}
                {current.closes_gap && (
                  <p className="text-[11px] text-muted-foreground flex items-start gap-1">
                    <Wrench className="w-3 h-3 text-emerald-400 shrink-0 mt-0.5" />
                    <span>
                      <span className="text-emerald-400 font-medium">Fix path:</span> {current.closes_gap}
                    </span>
                  </p>
                )}
              </div>
            )}

            {/* grounded agent narrative */}
            {result && (
              <div className="rounded-md border border-primary/20 bg-background/40 p-2.5 space-y-2">
                <div className="flex items-center gap-1.5">
                  <Badge variant="outline" className="text-[9px] text-primary border-primary/40">
                    gap_auditor
                  </Badge>
                  <Badge
                    variant="outline"
                    className={`text-[9px] ${
                      result.used_llm ? "text-violet-400 border-violet-400/40" : "text-muted-foreground"
                    }`}
                  >
                    {result.used_llm ? "LLM narration" : "deterministic"}
                  </Badge>
                </div>
                <p className="text-[11px] text-foreground/90 whitespace-pre-wrap">{result.summary}</p>
                {result.grounding?.length > 0 && (
                  <div>
                    <p className="text-[9px] uppercase tracking-wider text-muted-foreground mb-1">
                      Grounded in {result.grounding.length} graph node{result.grounding.length === 1 ? "" : "s"}
                    </p>
                    <div className="flex flex-wrap gap-1">
                      {result.grounding.slice(0, 10).map((id) => (
                        <a key={id} href={graphFocusHref(id)}>
                          <Badge
                            variant="outline"
                            className="text-[9px] font-mono cursor-pointer hover:border-primary/50"
                          >
                            <span className="truncate max-w-[160px]">{id}</span>
                            <ExternalLink className="w-2.5 h-2.5 ml-1 shrink-0" />
                          </Badge>
                        </a>
                      ))}
                    </div>
                  </div>
                )}
                <Link href="/gaps">
                  <span className="text-[10px] text-primary hover:underline inline-flex items-center gap-1">
                    see all blind spots <ArrowRight className="w-3 h-3" />
                  </span>
                </Link>
              </div>
            )}

            {err && (
              <div className="flex items-start gap-2 rounded-md border border-amber-400/30 bg-amber-400/5 px-2.5 py-2">
                <ShieldAlert className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
                <p className="text-[11px] text-muted-foreground">{err}</p>
              </div>
            )}

            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground pt-0.5">
              <ArrowRight className="w-3 h-3" /> Ready to close it? Use{" "}
              <span className="text-foreground font-medium">Add a connection</span> above to draft an additive mint
              proposal (never a live write).
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Add-a-connection dialog (draft mint proposal builder)
// ---------------------------------------------------------------------------
function AddConnectionDialog() {
  const [open, setOpen] = useState(false);
  const [step, setStep] = useState<1 | 2>(1);

  // source definition
  const [srcName, setSrcName] = useState("");
  const [srcPrefix, setSrcPrefix] = useState("");
  const [srcEndpoint, setSrcEndpoint] = useState("D_NEW");
  const [srcDesc, setSrcDesc] = useState("");

  // bridge rule
  const [relation, setRelation] = useState("RELATES_TO");
  const [anchorKey, setAnchorKey] = useState(`${EP_TARGETS[0].code}::${EP_TARGETS[0].anchorPrefix}`);
  const [matchOn, setMatchOn] = useState("shared identifier / manual mapping");

  // preview
  const [preview, setPreview] = useState<CandidateEdge[] | null>(null);
  const [anchorNodes, setAnchorNodes] = useState<GNode[]>([]);
  const [previewing, setPreviewing] = useState(false);
  const [source, setSource] = useState<"live" | "snapshot">(LAST_SOURCE);
  const [err, setErr] = useState<string | null>(null);

  const anchor = useMemo(() => {
    const [code, prefix] = anchorKey.split("::");
    return { code: code as Exclude<EndpointCode, null>, prefix };
  }, [anchorKey]);

  const validSource = srcName.trim() && srcPrefix.trim();

  const runPreview = useCallback(async () => {
    setPreviewing(true);
    setErr(null);
    try {
      // sample real anchor nodes from the target endpoint to show what the
      // new source would bridge into
      const anchors = await searchNodes({ prefix: anchor.prefix, limit: 8 });
      setSource(LAST_SOURCE);
      setAnchorNodes(anchors);
      if (anchors.length === 0) {
        setErr(`No existing nodes found under "${anchor.prefix}" to bridge into.`);
        setPreview([]);
        return;
      }
      // candidate edges: a placeholder new-source node id per anchor
      const edges: CandidateEdge[] = anchors.map((a, i) => ({
        source_id: `${srcPrefix.trim().replace(/:$/, "")}:${i + 1}`,
        target_id: a.id,
        rel_type: relation.trim() || "RELATES_TO",
        target_endpoint: a.endpoint,
      }));
      setPreview(edges);
      setStep(2);
    } catch (e) {
      setErr(`Preview failed: ${String(e)}`);
    } finally {
      setPreviewing(false);
    }
  }, [anchor.prefix, srcPrefix, relation]);

  const buildProposal = useCallback((): MintProposal => {
    return {
      _session: 13,
      _kind: "connection_mint_proposal",
      _draft: true,
      _generated: new Date().toISOString(),
      _note:
        "DRAFT additive mint proposal generated from the Zeta Bridge front-end. Read-only: this is NOT a live graph write. Review, then apply via an explicitly authenticated ingestion step.",
      source: {
        name: srcName.trim(),
        id_prefix: srcPrefix.trim().replace(/:$/, "") + ":",
        endpoint_code: srcEndpoint.trim() || "D_NEW",
        description: srcDesc.trim(),
      },
      bridge_rule: {
        relation: relation.trim() || "RELATES_TO",
        anchor_prefix: anchor.prefix,
        anchor_endpoint: anchor.code,
        match_on: matchOn.trim(),
      },
      candidate_nodes: anchorNodes.map((_, i) => ({
        id: `${srcPrefix.trim().replace(/:$/, "")}:${i + 1}`,
        label: srcName.trim().replace(/\s+/g, "") || "NewSourceNode",
      })),
      candidate_edges: preview ?? [],
      counts: { candidate_nodes: anchorNodes.length, candidate_edges: (preview ?? []).length },
    };
  }, [srcName, srcPrefix, srcEndpoint, srcDesc, relation, anchor, matchOn, anchorNodes, preview]);

  const download = useCallback(() => {
    const proposal = buildProposal();
    const blob = new Blob([JSON.stringify(proposal, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    const safe = (srcName.trim() || "new_source").toLowerCase().replace(/[^a-z0-9]+/g, "_");
    a.download = `mint_proposal_${safe}_s13.json`;
    a.click();
    URL.revokeObjectURL(url);
  }, [buildProposal, srcName]);

  const reset = () => {
    setStep(1);
    setPreview(null);
    setAnchorNodes([]);
    setErr(null);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(o) => {
        setOpen(o);
        if (!o) reset();
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm" className="h-8 text-xs" data-testid="add-connection-btn">
          <Plus className="w-3.5 h-3.5 mr-1" /> Add a connection
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="text-sm flex items-center gap-1.5">
            <Link2 className="w-4 h-4 text-primary" /> Add a connection · mint proposal
          </DialogTitle>
          <DialogDescription className="text-xs">
            Define a new source and how it bridges into the federated graph. Exports a draft additive proposal — never a
            live write.
          </DialogDescription>
        </DialogHeader>

        {step === 1 ? (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2">
              <Field label="Source name">
                <Input value={srcName} onChange={(e) => setSrcName(e.target.value)} placeholder="e.g. TCGA-OV" className="h-8 text-xs" data-testid="src-name" />
              </Field>
              <Field label="Endpoint code">
                <Input value={srcEndpoint} onChange={(e) => setSrcEndpoint(e.target.value)} placeholder="D_NEW" className="h-8 text-xs" data-testid="src-endpoint" />
              </Field>
            </div>
            <Field label="Node id-prefix">
              <Input value={srcPrefix} onChange={(e) => setSrcPrefix(e.target.value)} placeholder="e.g. sample:tcga_ov:" className="h-8 text-xs font-mono" data-testid="src-prefix" />
            </Field>
            <Field label="Description">
              <Textarea value={srcDesc} onChange={(e) => setSrcDesc(e.target.value)} placeholder="What this source contains and why it connects." className="text-xs min-h-[52px]" data-testid="src-desc" />
            </Field>

            <div className="h-px bg-border my-1" />

            <Field label="Bridge relation type">
              <Input value={relation} onChange={(e) => setRelation(e.target.value.toUpperCase().replace(/\s+/g, "_"))} placeholder="RELATES_TO" className="h-8 text-xs font-mono" data-testid="rel-type" />
            </Field>
            <Field label="Bridge into (existing anchor node type)">
              <Select value={anchorKey} onValueChange={setAnchorKey}>
                <SelectTrigger className="h-8 text-xs" data-testid="anchor-select">
                  <SelectValue />
                </SelectTrigger>
                <SelectContent>
                  {EP_TARGETS.map((t) => (
                    <SelectItem key={`${t.code}::${t.anchorPrefix}`} value={`${t.code}::${t.anchorPrefix}`}>
                      {t.code} · {t.anchorLabel} ({t.anchorPrefix})
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </Field>
            <Field label="Match on">
              <Input value={matchOn} onChange={(e) => setMatchOn(e.target.value)} className="h-8 text-xs" data-testid="match-on" />
            </Field>

            {err && <p className="text-xs text-destructive">{err}</p>}

            <DialogFooter>
              <Button size="sm" className="h-8 text-xs" disabled={!validSource || previewing} onClick={runPreview} data-testid="preview-btn">
                {previewing ? <Loader2 className="w-3.5 h-3.5 mr-1 animate-spin" /> : <Search className="w-3.5 h-3.5 mr-1" />}
                Preview candidate edges
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-xs text-muted-foreground">
                Bridging <code className="text-primary">{srcPrefix.replace(/:$/, "")}:</code> →{" "}
                <code style={{ color: ENDPOINT_META[anchor.code].color }}>{anchor.prefix}</code>
              </span>
              <Badge
                variant="outline"
                className={`text-[10px] ${source === "live" ? "text-emerald-400 border-emerald-400/40" : "text-amber-400 border-amber-400/40"}`}
              >
                {source === "live" ? "● live anchors" : "○ snapshot anchors"}
              </Badge>
            </div>

            <div className="rounded-md border border-border/50">
              <div className="px-2.5 py-1.5 border-b border-border/50 flex items-center justify-between">
                <span className="text-[11px] font-medium text-foreground">
                  {preview?.length ?? 0} candidate edges
                </span>
                <span className="text-[10px] text-muted-foreground">rel: {relation}</span>
              </div>
              <ScrollArea className="max-h-56">
                <div className="p-1.5 space-y-1">
                  {(preview ?? []).map((e, i) => (
                    <div key={i} className="text-[10px] font-mono flex items-center gap-1 p-1 rounded bg-muted/20">
                      <span className="text-foreground truncate max-w-[38%]">{e.source_id}</span>
                      <span className="text-amber-400 shrink-0">—{e.rel_type}→</span>
                      <span className="truncate max-w-[38%]" style={{ color: e.target_endpoint ? ENDPOINT_META[e.target_endpoint].color : undefined }}>
                        {e.target_id}
                      </span>
                    </div>
                  ))}
                </div>
              </ScrollArea>
            </div>

            <div className="flex items-start gap-2 rounded-md border border-amber-400/30 bg-amber-400/5 px-2.5 py-2">
              <ShieldAlert className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
              <p className="text-[11px] text-muted-foreground">
                These are <span className="text-foreground">candidate</span> edges only. Exporting produces a draft
                additive proposal ({" "}
                <code className="text-primary">_session: 13</code>, <code className="text-primary">_draft: true</code>).
                Nothing is written to the graph.
              </p>
            </div>

            {err && <p className="text-xs text-destructive">{err}</p>}

            <DialogFooter className="gap-2">
              <Button size="sm" variant="ghost" className="h-8 text-xs" onClick={() => setStep(1)}>
                Back
              </Button>
              <Button size="sm" className="h-8 text-xs" onClick={download} disabled={!preview || preview.length === 0} data-testid="export-proposal-btn">
                <Download className="w-3.5 h-3.5 mr-1" /> Export mint proposal
              </Button>
            </DialogFooter>

            <div className="flex items-center gap-1.5 text-[10px] text-muted-foreground">
              <FileJson className="w-3 h-3" /> downloads mint_proposal_&lt;source&gt;_s13.json
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1">
      <label className="text-[11px] text-muted-foreground font-medium">{label}</label>
      {children}
    </div>
  );
}
