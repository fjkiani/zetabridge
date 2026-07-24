/**
 * Opportunity board (Session 15) — route `/opportunities`.
 *
 * Fuses the strongest signals + genomic→clinical bridges + blind-spot gaps
 * (all from the existing read-only `/api/signals` layer) into a single ranked
 * list of concrete opportunities. Each card states, in plain language:
 *   - the finding (e.g. "BRCA1 → Neutropenia bridge spanning MSK ↔ SAS")
 *   - the supporting numbers (native metric + value)
 *   - the blind-spot it could close
 *   - a plain "why this is worth money" line
 * and deep-links to the signal detail, bridges, gaps, and the Live Console so a
 * viewer can pull the live source record behind it.
 *
 * Every value carries a ProvenanceBadge (endpoint + live/snapshot + grounding).
 */

import { useEffect, useMemo, useState } from "react";
import { Link } from "wouter";
import {
  Gem,
  Loader2,
  GitFork,
  Radar,
  ShieldAlert,
  ArrowRight,
  Radio,
  TrendingUp,
  DollarSign,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ProvenanceBadge } from "@/components/provenance-badge";
import { type EndpointCode } from "@/lib/graphApi";
import {
  getTopSignals,
  getBridges,
  getGaps,
  graphFocusHref,
  LAST_SIGNAL_SOURCE,
  apiConfigured,
  FAMILY_META,
  type SignalItem,
  type BridgeItem,
  type GapItem,
  type SignalFamily,
} from "@/lib/signalsApi";

type Kind = "bridge" | "signal" | "gap";

interface Opportunity {
  id: string;
  kind: Kind;
  rank: number; // 0..1 normalized strength for ordering
  title: string;
  finding: string;
  metricLabel: string;
  metricValue: string;
  blindSpot: string;
  whyMoney: string;
  endpoints: EndpointCode[];
  grounding: string;
  href: string; // deep-link to the detail surface
  liveHref?: string; // optional deep-link into the live console equivalent
}

function num(v: number | null | undefined, digits = 2): string {
  return v == null ? "—" : v.toFixed(digits);
}

// The pipeline uses 999.0 as an HONEST sentinel for "AE present in the
// experimental arm but ABSENT in control" (rate ratio is division-by-zero, so
// it is capped). Showing a bare "999.00" would read as a measured statistic, so
// we render the sentinel truthfully instead.
const RR_SENTINEL = 999.0;
function isSentinel(metric: string | null | undefined, v: number | null | undefined): boolean {
  return (metric === "rate_ratio") && v != null && v >= RR_SENTINEL;
}
function metricDisplay(metric: string | null | undefined, v: number | null | undefined): {
  label: string;
  value: string;
} {
  if (isSentinel(metric, v)) {
    return { label: "Ctrl arm = 0", value: "exp-only" };
  }
  return { label: "", value: num(v, 2) };
}

// ---- fuse the three families into ranked opportunities ---------------------
function buildOpportunities(
  signals: SignalItem[],
  bridges: BridgeItem[],
  gaps: GapItem[],
): Opportunity[] {
  const out: Opportunity[] = [];

  // bridges: cross-endpoint MSK genomic -> SAS clinical AE links (highest value)
  for (const b of bridges.slice(0, 12)) {
    const score = b.bridge_score ?? 0;
    out.push({
      id: `bridge:${b.slug}`,
      kind: "bridge",
      rank: 0.6 + 0.4 * Math.min(1, score), // bridges weighted high (cross-source)
      title: `${b.gene ?? "gene"} → ${b.ae_term ?? "adverse event"}`,
      finding: `Genomic feature ${b.gene ?? "?"} (MSK) links to clinical toxicity "${
        b.ae_term ?? "?"
      }" (SAS) via an edge-backed bridge${
        b.recurrence_pct != null ? `, recurring in ${b.recurrence_pct}% of the cohort` : ""
      }.`,
      metricLabel: "Bridge score",
      metricValue: num(score, 3),
      blindSpot: `MSK sees the ${
        b.gene ?? "genomic"
      } driver; SAS sees "${
        b.ae_term ?? "the toxicity"
      }" on treatment — neither source alone connects them. This edge exists only in the federated graph.`,
      whyMoney: `A ${b.gene ?? "genotype"}→${
        b.ae_term ?? "toxicity"
      } link is a candidate biomarker: it flags which patients may hit this dose-limiting AE before dosing — directly de-risking a trial arm.`,
      endpoints: [b.gene_node?.endpoint ?? "A_MSK", b.ae_node?.endpoint ?? "B_SAS"],
      grounding: `${b.gene_node?.id ?? b.slug} → ${b.ae_node?.id ?? ""}`,
      href: `/signals/${encodeURIComponent(b.slug)}`,
      liveHref: "#/live",
    });
  }

  // signals: strongest per-family disproportionality / enrichment
  for (const s of signals.slice(0, 12)) {
    const strength = s.strength_derived ?? 0;
    const fam = (s.family as SignalFamily) in FAMILY_META ? (s.family as SignalFamily) : null;
    const meta = fam ? FAMILY_META[fam] : null;
    const sentinel = isSentinel(s.native_metric, s.native_value);
    const md = metricDisplay(s.native_metric, s.native_value);
    // per-signal specific finding: the signal name already carries the concrete
    // drug→AE (or "ctrl=0 rate=x") detail, so we lead with it, not boilerplate.
    const specific = s.name ?? s.label ?? s.slug;
    const finding = sentinel
      ? `${specific}. The event appears in the experimental arm but is absent from control (rate ratio is undefined → the pipeline caps it), so the raw metric is a sentinel, not a measured ratio — the underlying arm rate is in the name.`
      : `${specific}. ${meta?.blurb ?? "A statistically flagged safety/efficacy signal."}${
          s.native_value != null
            ? ` Measured ${s.native_metric ?? "value"} = ${num(s.native_value, 2)}.`
            : ""
        }`;
    out.push({
      id: `signal:${s.slug}`,
      kind: "signal",
      rank: 0.3 + 0.4 * Math.min(1, strength),
      title: specific,
      finding,
      metricLabel: md.label || meta?.metricLabel || s.native_metric || "value",
      metricValue: md.value,
      blindSpot: `This ${
        meta?.short ?? "signal"
      } lives in one warehouse's tables (${s.endpoint}); ranked and cross-linked here it becomes searchable evidence rather than a buried row.`,
      whyMoney: sentinel
        ? "A control-absent AE is a clean safety flag — the kind of exp-arm-only toxicity that drives label warnings and dose-limiting decisions."
        : "Disproportionality/enrichment signals are what pharmacovigilance teams pay to surface early — each one is a potential label change or safety action.",
      endpoints: [s.endpoint],
      grounding: s.slug,
      href: `/signals/${encodeURIComponent(s.slug)}`,
    });
  }

  // gaps: blind spots the federation could close
  for (const g of gaps.slice(0, 8)) {
    out.push({
      id: `gap:${g.slug}`,
      kind: "gap",
      rank: 0.25 + (g.impact === "high" ? 0.25 : g.impact === "medium" ? 0.12 : 0.05),
      title: g.name ?? g.slug,
      finding: `${g.gap_type ?? "Blind spot"}${
        g.current_tier ? ` (currently ${g.current_tier})` : ""
      } — ${g.closes_gap ?? "closing this connects an isolated island of data."}`,
      metricLabel: "Impact",
      metricValue: g.impact ?? "—",
      blindSpot: g.closes_gap ?? "An un-linked region of the federated graph.",
      whyMoney:
        "Each closed blind spot adds a queryable join no competitor holding only one source can answer — that asymmetry is the moat.",
      endpoints: [],
      grounding: g.slug,
      href: "/gaps",
    });
  }

  return out.sort((a, b) => b.rank - a.rank);
}

const KIND_META: Record<Kind, { label: string; icon: typeof Gem; color: string }> = {
  bridge: { label: "Cross-source bridge", icon: GitFork, color: "hsl(280, 70%, 62%)" },
  signal: { label: "Safety / efficacy signal", icon: Radar, color: "hsl(193, 100%, 50%)" },
  gap: { label: "Blind spot to close", icon: ShieldAlert, color: "hsl(45, 100%, 55%)" },
};

function OpportunityCard({ o, index }: { o: Opportunity; index: number }) {
  const meta = KIND_META[o.kind];
  const Icon = meta.icon;
  const prov = LAST_SIGNAL_SOURCE; // "live" | "snapshot"
  return (
    <Card
      className="flex flex-col border-l-4"
      style={{ borderLeftColor: meta.color }}
      data-testid={`opportunity-${o.id}`}
    >
      <CardHeader className="pb-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-xs font-mono text-muted-foreground shrink-0">#{index + 1}</span>
            <span
              className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
              style={{ backgroundColor: `${meta.color}22` }}
            >
              <Icon className="w-4 h-4" style={{ color: meta.color }} />
            </span>
            <div className="min-w-0">
              <CardTitle className="text-sm truncate">{o.title}</CardTitle>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {meta.label}
              </p>
            </div>
          </div>
          <div className="text-right shrink-0">
            <p className="text-[9px] uppercase tracking-wider text-muted-foreground">
              {o.metricLabel}
            </p>
            <p className="text-lg font-bold tabular-nums leading-none" style={{ color: meta.color }}>
              {o.metricValue}
            </p>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5 pt-1">
          {o.endpoints
            .filter((e): e is Exclude<EndpointCode, null> => Boolean(e))
            .map((e, i) => (
              <ProvenanceBadge key={`${e}-${i}`} endpoint={e} provenance={prov} handle={o.grounding} />
            ))}
          {o.endpoints.filter(Boolean).length === 0 && (
            <ProvenanceBadge endpoint={null} provenance={prov} handle={o.grounding} />
          )}
        </div>
      </CardHeader>
      <CardContent className="flex-1 flex flex-col gap-2.5">
        <p className="text-xs text-foreground/90 leading-relaxed">{o.finding}</p>
        <div className="rounded-md bg-muted/40 p-2.5 space-y-1.5">
          <p className="text-[11px] flex items-start gap-1.5">
            <ShieldAlert className="w-3.5 h-3.5 text-amber-400 shrink-0 mt-0.5" />
            <span>
              <span className="font-semibold">Blind spot: </span>
              <span className="text-muted-foreground">{o.blindSpot}</span>
            </span>
          </p>
          <p className="text-[11px] flex items-start gap-1.5">
            <DollarSign className="w-3.5 h-3.5 text-emerald-400 shrink-0 mt-0.5" />
            <span>
              <span className="font-semibold">Why it's worth money: </span>
              <span className="text-muted-foreground">{o.whyMoney}</span>
            </span>
          </p>
        </div>
        <div className="flex items-center gap-2 mt-auto pt-1">
          <Link href={o.href}>
            <Button size="sm" variant="outline" className="gap-1 text-xs" data-testid={`open-${o.id}`}>
              Open <ArrowRight className="w-3.5 h-3.5" />
            </Button>
          </Link>
          {o.liveHref && (
            <a href={o.liveHref}>
              <Button size="sm" variant="ghost" className="gap-1 text-xs">
                <Radio className="w-3.5 h-3.5 text-emerald-400" /> Pull live
              </Button>
            </a>
          )}
          {o.grounding && (
            <a href={graphFocusHref(o.grounding.split(" ")[0])} className="ml-auto">
              <Button size="sm" variant="ghost" className="gap-1 text-[11px] text-muted-foreground">
                <GitFork className="w-3 h-3" /> trace
              </Button>
            </a>
          )}
        </div>
      </CardContent>
    </Card>
  );
}

export default function Opportunities() {
  const [signals, setSignals] = useState<SignalItem[]>([]);
  const [bridges, setBridges] = useState<BridgeItem[]>([]);
  const [gaps, setGaps] = useState<GapItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);
  const [source, setSource] = useState<"live" | "snapshot">("snapshot");

  useEffect(() => {
    let cancelled = false;
    (async () => {
      setLoading(true);
      setErr(null);
      try {
        const [s, b, g] = await Promise.all([
          getTopSignals("all", 24),
          getBridges(),
          getGaps(),
        ]);
        if (cancelled) return;
        setSignals(s.signals ?? []);
        setBridges(b.bridges ?? []);
        setGaps(g.gaps ?? []);
        setSource(LAST_SIGNAL_SOURCE);
      } catch (e) {
        if (!cancelled) setErr(String(e instanceof Error ? e.message : e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const opportunities = useMemo(
    () => buildOpportunities(signals, bridges, gaps),
    [signals, bridges, gaps],
  );

  const counts = useMemo(() => {
    const c = { bridge: 0, signal: 0, gap: 0 } as Record<Kind, number>;
    for (const o of opportunities) c[o.kind] += 1;
    return c;
  }, [opportunities]);

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Gem className="w-6 h-6 text-primary" /> Opportunity Board
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            The strongest cross-source bridges, safety/efficacy signals, and blind spots — ranked,
            each with the finding, the supporting numbers, the gap it closes, and why it's worth
            money. Every value traces back to a real graph node or a live source read.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <Badge variant="outline" className="gap-1.5">
            <TrendingUp className="w-3.5 h-3.5" /> {opportunities.length} opportunities
          </Badge>
          <Badge variant="outline" className={source === "live" ? "text-emerald-400 border-emerald-400/40" : ""}>
            {source === "live" ? "live backend" : "snapshot"}
          </Badge>
        </div>
      </div>

      {!apiConfigured() && (
        <p className="text-xs text-muted-foreground">
          Showing the pre-extracted snapshot. Connect a backend to rank against the live graph and
          pull the live source record behind each card.
        </p>
      )}

      {err && (
        <Card className="border-amber-400/40">
          <CardContent className="py-3 text-sm text-amber-300">{err}</CardContent>
        </Card>
      )}

      {loading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground gap-2">
          <Loader2 className="w-5 h-5 animate-spin" /> Ranking opportunities…
        </div>
      ) : (
        <>
          <div className="flex items-center gap-4 text-xs text-muted-foreground">
            <span className="flex items-center gap-1.5">
              <GitFork className="w-3.5 h-3.5" style={{ color: KIND_META.bridge.color }} />
              {counts.bridge} bridges
            </span>
            <span className="flex items-center gap-1.5">
              <Radar className="w-3.5 h-3.5" style={{ color: KIND_META.signal.color }} />
              {counts.signal} signals
            </span>
            <span className="flex items-center gap-1.5">
              <ShieldAlert className="w-3.5 h-3.5" style={{ color: KIND_META.gap.color }} />
              {counts.gap} blind spots
            </span>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {opportunities.map((o, i) => (
              <OpportunityCard key={o.id} o={o} index={i} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
