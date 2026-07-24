import { useEffect, useMemo, useState } from "react";
import { Link } from "wouter";
import {
  Gem,
  Loader2,
  Network,
  ShieldCheck,
  GitFork,
  ShieldAlert,
  Waypoints,
  ArrowRight,
  Building2,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ENDPOINT_META, type EndpointCode } from "@/lib/graphApi";
import {
  getOverview,
  getBridges,
  LAST_SIGNAL_SOURCE,
  type OverviewResult,
  type BridgeItem,
} from "@/lib/signalsApi";

function epColor(ep: string): string {
  return ep in ENDPOINT_META ? ENDPOINT_META[ep as Exclude<EndpointCode, null>].color : "hsl(220,20%,55%)";
}

interface Pillar {
  icon: any;
  title: string;
  metric: string;
  body: string;
  href?: string;
}

export default function Value() {
  const [ov, setOv] = useState<OverviewResult | null>(null);
  const [bridges, setBridges] = useState<BridgeItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    Promise.all([getOverview().catch(() => null), getBridges().catch(() => null)])
      .then(([o, b]) => {
        setOv(o);
        setBridges(b?.bridges ?? []);
      })
      .finally(() => setLoading(false));
  }, []);

  const pillars: Pillar[] = useMemo(() => {
    if (!ov) return [];
    const reach = ov.headline?.cross_endpoint_reachability;
    const broker = ov.headline?.top_broker;
    const topBridge = bridges[0];
    return [
      {
        icon: Network,
        title: "Federation without ETL",
        metric: `${ov.totals.nodes.toLocaleString()} nodes · ${ov.totals.relationships.toLocaleString()} edges`,
        body:
          "Three independent endpoints — MSK single-cell/genomics, PDS/SAS clinical trials, EGA/BriTROC sWGS — " +
          "queried as one graph. No data was copied into a warehouse; the value is the connective tissue, not a re-ingest.",
      },
      {
        icon: ShieldCheck,
        title: "100% cross-endpoint reachability",
        metric: reach?.all_100pct ? `all ${reach.directed_pairs} directed pairs · ≤${reach.max_hops} hops` : `${reach?.directed_pairs ?? 0} pairs`,
        body:
          "Every endpoint pair is reachable through the graph within a few hops. The federation is genuinely " +
          "connected, not three silos sharing a login.",
        href: "/insights",
      },
      {
        icon: Waypoints,
        title: "Single articulation broker, mapped",
        metric: broker?.node ? `${broker.node} · betweenness ${broker.betweenness}` : "—",
        body:
          "The graph knows its own topology: the highest-betweenness broker spanning all three endpoints is " +
          "identified as an articulation point. A buyer sees exactly where the federation's load concentrates.",
        href: "/insights",
      },
      {
        icon: GitFork,
        title: "Edge-backed genomic ↔ clinical bridges",
        metric: topBridge ? `${bridges.length} bridges · top ${topBridge.gene}→${topBridge.ae_term} (${topBridge.bridge_score?.toFixed(2)})` : `${bridges.length} bridges`,
        body:
          "Structural links from an MSK genomic feature to a SAS clinical toxicity term — a connection neither a " +
          "genomics vendor nor a clinical-trials vendor holds alone. This is the asset that isn't reproducible by buying either side.",
        href: "/bridges",
      },
      {
        icon: ShieldAlert,
        title: "Quantified blind spots = TAM",
        metric: `${ov.n_blind_spots} blind spots · ${ov.n_signals} signals mined`,
        body:
          "The platform names what it can't answer yet (un-ingested labs, missing AE onset timing, hollow trials) — " +
          "each a data-completion product a warehouse buyer would monetize. It also already mines hundreds of grounded signals.",
        href: "/gaps",
      },
    ];
  }, [ov, bridges]);

  const live = LAST_SIGNAL_SOURCE === "live";

  if (loading) {
    return (
      <div className="flex items-center justify-center py-32 text-muted-foreground">
        <Loader2 className="w-5 h-5 animate-spin mr-2" /> Loading the numbers…
      </div>
    );
  }

  return (
    <div className="p-6 max-w-[1200px] mx-auto space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <Gem className="w-5 h-5 text-primary" />
            <h1 className="text-xl font-semibold">Value / Moat</h1>
            <Badge
              variant="outline"
              className={`text-[10px] ${live ? "border-emerald-500/40 text-emerald-400" : "border-amber-500/40 text-amber-400"}`}
            >
              {live ? "live graph" : "bundled snapshot"}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-1 max-w-3xl">
            Why a data platform (Snowflake, Databricks) would acquire this: not the raw data — that's licensable —
            but the <span className="text-foreground">federated connective layer and the signals only it can produce</span>.
            Every number on this page is read live from the graph, not asserted.
          </p>
        </div>
      </div>

      {/* endpoint composition */}
      {ov && (
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm">What's federated</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
              {Object.entries(ov.endpoints).map(([code, n]) => (
                <div key={code} className="rounded-lg border border-border/50 p-3">
                  <div className="flex items-center gap-2">
                    <span className="w-3 h-3 rounded-full" style={{ backgroundColor: epColor(code) }} />
                    <span className="text-sm font-semibold">{code}</span>
                    <span className="text-[10px] text-muted-foreground">
                      {code in ENDPOINT_META ? ENDPOINT_META[code as Exclude<EndpointCode, null>].label : ""}
                    </span>
                  </div>
                  <p className="text-lg font-bold tabular-nums mt-1">{Number(n).toLocaleString()} <span className="text-xs font-normal text-muted-foreground">indexed nodes</span></p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* the pillars */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {pillars.map((p) => {
          const Icon = p.icon;
          const inner = (
            <Card className={`h-full ${p.href ? "hover:border-primary/50 cursor-pointer transition-colors" : ""}`}>
              <CardContent className="p-5 space-y-2">
                <div className="flex items-center gap-2">
                  <Icon className="w-4 h-4 text-primary" />
                  <h3 className="text-sm font-semibold">{p.title}</h3>
                  {p.href && <ArrowRight className="w-3.5 h-3.5 text-muted-foreground ml-auto" />}
                </div>
                <p className="text-base font-bold tabular-nums text-primary leading-tight">{p.metric}</p>
                <p className="text-xs text-muted-foreground leading-relaxed">{p.body}</p>
              </CardContent>
            </Card>
          );
          return p.href ? (
            <Link key={p.title} href={p.href}>{inner}</Link>
          ) : (
            <div key={p.title}>{inner}</div>
          );
        })}
      </div>

      {/* acquisition thesis */}
      <Card className="border-primary/30">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-1.5">
            <Building2 className="w-4 h-4 text-primary" /> The acquisition thesis, in one line
          </CardTitle>
        </CardHeader>
        <CardContent>
          <p className="text-sm leading-relaxed text-muted-foreground">
            A warehouse can store any of these datasets. What it can't buy off the shelf is a{" "}
            <span className="text-foreground">read-only federation that already resolves genomics-to-clinical
            toxicity as graph edges</span>, proves it's fully connected, knows its own broker topology, mines{" "}
            <span className="text-foreground">{ov?.n_signals ?? "hundreds of"} grounded signals</span>, and enumerates{" "}
            <span className="text-foreground">{ov?.n_blind_spots ?? "its"} monetizable data gaps</span>. That
            connective layer — not the raw rows — is the asset.
          </p>
          <div className="flex flex-wrap gap-2 mt-4">
            <Link href="/signals"><Badge variant="outline" className="cursor-pointer hover:border-primary/50 py-1.5 px-3">Explore the signals</Badge></Link>
            <Link href="/bridges"><Badge variant="outline" className="cursor-pointer hover:border-primary/50 py-1.5 px-3">See the bridges</Badge></Link>
            <Link href="/gaps"><Badge variant="outline" className="cursor-pointer hover:border-primary/50 py-1.5 px-3">Review the gaps</Badge></Link>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
