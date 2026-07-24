import { useEffect, useMemo, useState } from "react";
import { Link } from "wouter";
import {
  Activity,
  Loader2,
  ArrowRight,
  Radar,
  ShieldAlert,
  GitFork,
  Layers,
  Zap,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { ENDPOINT_META, type EndpointCode } from "@/lib/graphApi";
import {
  getOverview,
  getTopSignals,
  FAMILY_META,
  LAST_SIGNAL_SOURCE,
  apiConfigured,
  type SignalFamily,
  type SignalItem,
  type OverviewResult,
} from "@/lib/signalsApi";

const FAMILY_TABS: { key: SignalFamily | "all"; label: string; icon: any }[] = [
  { key: "all", label: "All families", icon: Layers },
  { key: "genomic_bridge", label: "Genomic↔Clinical", icon: GitFork },
  { key: "drug_ae", label: "Drug/AE", icon: Zap },
  { key: "pharmacovig", label: "Pharmacovig (ROR)", icon: ShieldAlert },
  { key: "cross_trial", label: "Cross-trial", icon: Radar },
  { key: "outlier", label: "Outliers", icon: Activity },
];

function epColor(ep: EndpointCode): string {
  return ep && ep in ENDPOINT_META ? ENDPOINT_META[ep].color : "hsl(220,20%,55%)";
}

function fmtMetric(v: number | null): string {
  if (v === null || v === undefined) return "—";
  if (v >= 100) return v.toFixed(0);
  if (v >= 10) return v.toFixed(1);
  return v.toFixed(3).replace(/0+$/, "").replace(/\.$/, "");
}

function SourceBadge() {
  const live = LAST_SIGNAL_SOURCE === "live";
  return (
    <Badge
      variant="outline"
      className={`text-[10px] ${live ? "border-emerald-500/40 text-emerald-400" : "border-amber-500/40 text-amber-400"}`}
    >
      {live ? "live graph" : "bundled snapshot"}
    </Badge>
  );
}

function SignalCard({ s }: { s: SignalItem }) {
  const fam = (s.family as SignalFamily) in FAMILY_META ? FAMILY_META[s.family as SignalFamily] : null;
  const d = s.detail ?? {};
  const strength = s.strength_derived ?? 0;
  return (
    <Link href={`/signals/${encodeURIComponent(s.slug)}`}>
      <Card
        className="cursor-pointer hover:border-primary/50 transition-colors h-full"
        data-testid={`signal-card-${s.slug}`}
      >
        <CardContent className="p-4 space-y-3">
          <div className="flex items-start justify-between gap-2">
            <div className="min-w-0">
              <div className="flex items-center gap-1.5 mb-1">
                <Badge variant="secondary" className="text-[10px]">
                  {fam?.short ?? s.family}
                </Badge>
                {s.endpoint && (
                  <span
                    className="text-[10px] px-1.5 py-0.5 rounded"
                    style={{ color: epColor(s.endpoint), border: `1px solid ${epColor(s.endpoint)}55` }}
                  >
                    {s.endpoint}
                  </span>
                )}
              </div>
              <p className="text-sm font-medium leading-tight truncate">{s.name}</p>
            </div>
            <ArrowRight className="w-4 h-4 text-muted-foreground shrink-0 mt-0.5" />
          </div>

          {/* native metric — the SOURCE value */}
          <div className="flex items-end justify-between">
            <div>
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">
                {fam?.metricLabel ?? s.native_metric}
              </p>
              <p className="text-xl font-bold tabular-nums text-primary leading-none">
                {fmtMetric(s.native_value)}
              </p>
            </div>
            {typeof d.interpretation === "string" && (
              <p className="text-[10px] text-muted-foreground max-w-[55%] text-right leading-tight line-clamp-2">
                {String(d.interpretation)}
              </p>
            )}
          </div>

          {/* derived strength — clearly labelled */}
          <div>
            <div className="flex items-center justify-between mb-1">
              <span className="text-[9px] uppercase tracking-wider text-muted-foreground">
                strength <span className="opacity-60">(derived, cross-family)</span>
              </span>
              <span className="text-[9px] tabular-nums text-muted-foreground">
                {(strength * 100).toFixed(0)}%
              </span>
            </div>
            <Progress value={strength * 100} className="h-1" />
          </div>
        </CardContent>
      </Card>
    </Link>
  );
}

export default function SignalsHub() {
  const [overview, setOverview] = useState<OverviewResult | null>(null);
  const [family, setFamily] = useState<SignalFamily | "all">("all");
  const [signals, setSignals] = useState<SignalItem[]>([]);
  const [count, setCount] = useState(0);
  const [loading, setLoading] = useState(true);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    getOverview().then(setOverview).catch(() => {}).finally(() => setTick((t) => t + 1));
  }, []);

  useEffect(() => {
    setLoading(true);
    getTopSignals(family, 24)
      .then((r) => {
        setSignals(r.signals);
        setCount(r.count);
      })
      .catch(() => setSignals([]))
      .finally(() => {
        setLoading(false);
        setTick((t) => t + 1);
      });
  }, [family]);

  const headlineStats = useMemo(() => {
    if (!overview) return [];
    const reach = overview.headline?.cross_endpoint_reachability;
    return [
      { label: "Federated nodes", value: overview.totals.nodes.toLocaleString() },
      { label: "Relationships", value: overview.totals.relationships.toLocaleString() },
      { label: "Signals mined", value: overview.n_signals.toLocaleString() },
      { label: "Blind spots", value: overview.n_blind_spots.toLocaleString() },
      {
        label: "Cross-endpoint reach",
        value: reach?.all_100pct ? `100% ≤${reach.max_hops} hops` : `${reach?.directed_pairs ?? 0} pairs`,
      },
    ];
  }, [overview]);

  return (
    <div className="p-6 max-w-[1400px] mx-auto space-y-6">
      {/* header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <Radar className="w-5 h-5 text-primary" />
            <h1 className="text-xl font-semibold">Signal Intelligence</h1>
            <SourceBadge />
          </div>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            Every signal below is mined from the live federated graph and ranked by its own{" "}
            <span className="text-foreground">native metric</span> (rate ratio, ROR, bridge score,
            consistency). The <span className="text-foreground">strength</span> bar is a derived,
            clearly-labelled normalization used only to order families on one scale. Click any card
            to drill into the exact nodes and edges it connects.
          </p>
        </div>
        {!apiConfigured() && (
          <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-400">
            offline — showing bundled snapshot
          </Badge>
        )}
      </div>

      {/* headline value strip */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3">
        {headlineStats.map((s) => (
          <Card key={s.label}>
            <CardContent className="p-3">
              <p className="text-[10px] uppercase tracking-wider text-muted-foreground">{s.label}</p>
              <p className="text-lg font-bold tabular-nums">{s.value}</p>
            </CardContent>
          </Card>
        ))}
      </div>

      {/* quick links to the connected views */}
      <div className="flex flex-wrap gap-2">
        <Link href="/bridges">
          <Badge variant="outline" className="cursor-pointer hover:border-primary/50 py-1.5 px-3">
            <GitFork className="w-3 h-3 mr-1.5" /> Genomic ↔ Clinical bridges
          </Badge>
        </Link>
        <Link href="/gaps">
          <Badge variant="outline" className="cursor-pointer hover:border-primary/50 py-1.5 px-3">
            <ShieldAlert className="w-3 h-3 mr-1.5" /> Blind spots (what pharma missed)
          </Badge>
        </Link>
        <Link href="/analyst">
          <Badge variant="outline" className="cursor-pointer hover:border-primary/50 py-1.5 px-3">
            <Zap className="w-3 h-3 mr-1.5" /> Ask the Insight Analyst
          </Badge>
        </Link>
        <Link href="/value">
          <Badge variant="outline" className="cursor-pointer hover:border-primary/50 py-1.5 px-3">
            <Layers className="w-3 h-3 mr-1.5" /> Value / Moat
          </Badge>
        </Link>
      </div>

      {/* family tabs */}
      <div className="flex flex-wrap gap-1.5 border-b border-border/50 pb-3">
        {FAMILY_TABS.map((t) => {
          const Icon = t.icon;
          const active = family === t.key;
          return (
            <button
              key={t.key}
              onClick={() => setFamily(t.key)}
              data-testid={`family-tab-${t.key}`}
              className={`flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors ${
                active ? "bg-primary/10 text-primary" : "text-muted-foreground hover:text-foreground hover:bg-muted/50"
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              {t.label}
            </button>
          );
        })}
      </div>

      {/* family blurb */}
      {family !== "all" && FAMILY_META[family] && (
        <p className="text-xs text-muted-foreground -mt-2">
          <span className="text-foreground font-medium">{FAMILY_META[family].label}.</span>{" "}
          {FAMILY_META[family].blurb} Ranked by {FAMILY_META[family].metricLabel}. Showing top{" "}
          {Math.min(24, count)} of {count}.
        </p>
      )}

      {/* signal grid */}
      {loading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> Mining signals…
        </div>
      ) : signals.length === 0 ? (
        <div className="text-center py-24 text-muted-foreground text-sm">No signals in this family.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {signals.map((s) => (
            <SignalCard key={s.slug} s={s} />
          ))}
        </div>
      )}
    </div>
  );
}
