import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Database,
  Bot,
  Plug,
  ShieldCheck,
  Activity,
  Clock,
  GitBranch,
  ArrowRight,
} from "lucide-react";
import { Link } from "wouter";
import { safeRender } from "@/lib/utils";

function KPICard({ icon: Icon, label, value, sub, color }: { icon: any; label: string; value: string; sub?: string; color: string }) {
  return (
    <Card className="border-border/50" data-testid={`kpi-${label.toLowerCase().replace(/\s/g, "-")}`}>
      <CardContent className="p-4">
        <div className="flex items-start justify-between">
          <div>
            <p className="text-xs font-medium text-muted-foreground uppercase tracking-wider">{label}</p>
            <p className="text-2xl font-bold tabular-nums mt-1" style={{ color }}>{value}</p>
            {sub && <p className="text-xs text-muted-foreground mt-0.5">{sub}</p>}
          </div>
          <div className="p-2 rounded-lg" style={{ backgroundColor: `${color}15` }}>
            <Icon className="w-4 h-4" style={{ color }} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function DashboardHome() {
  const { data: platform, isLoading } = useQuery({
    queryKey: ["/api/platform/status"],
    queryFn: async () => {
      const res = await apiRequest("GET", "/api/platform/status");
      return res.json();
    },
  });

  const { data: agents } = useQuery({
    queryKey: ["/api/agents"],
    queryFn: async () => {
      const res = await apiRequest("GET", "/api/agents");
      return res.json();
    },
  });

  const { data: history } = useQuery({
    queryKey: ["/api/agents/execution-history"],
    queryFn: async () => {
      const res = await apiRequest("GET", "/api/agents/execution-history");
      return res.json();
    },
  });

  const { data: lineageGraph } = useQuery({
    queryKey: ["/api/lineage/graph"],
    queryFn: async () => {
      const res = await apiRequest("GET", "/api/lineage/graph");
      return res.json();
    },
  });

  if (isLoading) {
    return (
      <div className="p-6 space-y-6">
        <div className="grid grid-cols-4 gap-4">
          {[1, 2, 3, 4].map(i => <Skeleton key={i} className="h-24 rounded-xl" />)}
        </div>
        <div className="grid grid-cols-3 gap-4">
          {[1, 2, 3, 4, 5, 6].map(i => <Skeleton key={i} className="h-32 rounded-xl" />)}
        </div>
      </div>
    );
  }

  const teal = "hsl(193, 100%, 50%)";

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-xl font-bold text-foreground">Overview</h1>
        <p className="text-sm text-muted-foreground mt-0.5">ZetaBridge Cloud platform status</p>
      </div>

      {/* KPI Row */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4" data-testid="kpi-row">
        <KPICard icon={Database} label="Total Tables" value={String(platform?.data_stores?.tables ?? 7)} sub="3 catalogs" color={teal} />
        <KPICard icon={Bot} label="Active Agents" value={String(platform?.agents?.active ?? 6)} sub={`${platform?.tools?.total ?? 12} tools`} color="#a78bfa" />
        <KPICard icon={Plug} label="Connectors" value={String(platform?.connectors?.total ?? 17)} sub={`${platform?.connectors?.active ?? 15} active`} color="#34d399" />
        <KPICard icon={ShieldCheck} label="Quality Score" value="A" sub={`${Math.round((platform?.benchmark_summary?.pass_rate ?? 0.88) * 100)}% pass rate`} color="#fbbf24" />
      </div>

      {/* Agent Status Grid */}
      <div>
        <div className="flex items-center justify-between mb-3">
          <h2 className="text-sm font-semibold text-foreground">Agent Status</h2>
          <Link href="/agents">
            <span className="text-xs text-primary hover:underline cursor-pointer flex items-center gap-1">
              View all <ArrowRight className="w-3 h-3" />
            </span>
          </Link>
        </div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3" data-testid="agent-grid">
          {(agents || []).map((agent: any) => (
            <Card key={agent.name} className="border-border/50" data-testid={`agent-card-${agent.name}`}>
              <CardContent className="p-4">
                <div className="flex items-start justify-between mb-2">
                  <p className="text-sm font-semibold text-foreground">{agent.name.replace(/_/g, " ")}</p>
                  <span className="flex items-center gap-1">
                    <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse" />
                    <span className="text-[10px] text-emerald-400 font-medium">active</span>
                  </span>
                </div>
                <p className="text-xs text-muted-foreground line-clamp-2">{agent.description}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      </div>

      {/* Bottom row: Recent Activity + Lineage Preview */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Recent Co-pilot Activity */}
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold">Recent Activity</CardTitle>
              <Link href="/copilot">
                <span className="text-xs text-primary hover:underline cursor-pointer">Open Co-Pilot</span>
              </Link>
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="space-y-2" data-testid="recent-activity">
              {(history || []).slice(0, 5).map((h: any, i: number) => (
                <div key={h.plan_id} className="flex items-center justify-between py-2 border-b border-border/30 last:border-0">
                  <div className="flex items-center gap-3">
                    <div className="p-1.5 rounded-md bg-primary/10">
                      <Activity className="w-3 h-3 text-primary" />
                    </div>
                    <div>
                      <p className="text-xs font-medium text-foreground">{h.intent.replace(/_/g, " ")}</p>
                      <p className="text-[10px] text-muted-foreground">{h.total_tasks} tasks · {h.succeeded} succeeded</p>
                    </div>
                  </div>
                  <div className="text-right">
                    <p className="text-xs tabular-nums text-muted-foreground flex items-center gap-1">
                      <Clock className="w-3 h-3" /> {h.total_latency_ms}ms
                    </p>
                  </div>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>

        {/* Mini Lineage Preview */}
        <Card className="border-border/50">
          <CardHeader className="pb-3">
            <div className="flex items-center justify-between">
              <CardTitle className="text-sm font-semibold">Lineage Preview</CardTitle>
              <Link href="/lineage">
                <span className="text-xs text-primary hover:underline cursor-pointer">Full graph</span>
              </Link>
            </div>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="flex items-center gap-6 mb-4">
              <div className="flex items-center gap-2">
                <GitBranch className="w-4 h-4 text-primary" />
                <div>
                  <p className="text-lg font-bold tabular-nums">{lineageGraph?.nodes?.length ?? 0}</p>
                  <p className="text-[10px] text-muted-foreground">Nodes</p>
                </div>
              </div>
              <div>
                <p className="text-lg font-bold tabular-nums">{lineageGraph?.edges?.length ?? 0}</p>
                <p className="text-[10px] text-muted-foreground">Edges</p>
              </div>
              <div>
                <p className="text-lg font-bold tabular-nums">{lineageGraph?.nodes?.filter((n: any) => n.type === "job").length ?? 0}</p>
                <p className="text-[10px] text-muted-foreground">Jobs</p>
              </div>
              <div>
                <p className="text-lg font-bold tabular-nums">{lineageGraph?.nodes?.filter((n: any) => n.type === "dataset").length ?? 0}</p>
                <p className="text-[10px] text-muted-foreground">Datasets</p>
              </div>
            </div>
            {/* Mini node preview */}
            <div className="flex flex-wrap gap-1.5" data-testid="lineage-mini-preview">
              {(lineageGraph?.nodes || []).slice(0, 12).map((node: any) => (
                <Badge
                  key={node.id}
                  variant="outline"
                  className={`text-[10px] ${
                    node.type === "job"
                      ? "border-primary/40 text-primary"
                      : "border-muted-foreground/30 text-muted-foreground"
                  }`}
                >
                  {safeRender(node.name ?? node.label ?? node.id)}
                </Badge>
              ))}
              {(lineageGraph?.nodes?.length ?? 0) > 12 && (
                <Badge variant="outline" className="text-[10px] border-muted-foreground/20 text-muted-foreground">
                  +{(lineageGraph?.nodes?.length ?? 0) - 12} more
                </Badge>
              )}
            </div>
          </CardContent>
        </Card>
      </div>
    </div>
  );
}
