import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { safeRender } from "@/lib/utils";
import { apiRequest } from "@/lib/queryClient";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  FlaskConical,
  Play,
  CheckCircle2,
  XCircle,
  Clock,
  Loader2,
  TrendingUp,
  Target,
  Timer,
  BarChart3,
} from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts";

const categoryColors: Record<string, string> = {
  catalog: "#22d3ee",
  query: "#a78bfa",
  lineage: "#34d399",
  quality: "#fb923c",
  connector: "#60a5fa",
  etl: "#fbbf24",
  copilot: "#f472b6",
};

export default function Benchmarks() {
  const [benchmarkData, setBenchmarkData] = useState<any>(null);

  const { data: summary } = useQuery({
    queryKey: ["/api/benchmarks/summary"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/benchmarks/summary"); return r.json(); },
  });

  const { data: results } = useQuery({
    queryKey: ["/api/benchmarks/results"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/benchmarks/results"); return r.json(); },
  });

  const runMutation = useMutation({
    mutationFn: async () => {
      const r = await apiRequest("POST", "/api/benchmarks/run", {});
      return r.json();
    },
    onSuccess: (data) => setBenchmarkData(data),
  });

  const activeResults = benchmarkData?.results || results || [];
  const activeSummary = benchmarkData || summary;

  // Build chart data by category
  const chartData: { category: string; latency: number }[] = [];
  if (activeResults.length > 0) {
    const byCat: Record<string, number[]> = {};
    activeResults.forEach((r: any) => {
      if (!byCat[r.category]) byCat[r.category] = [];
      byCat[r.category].push(r.latency_ms);
    });
    Object.entries(byCat).forEach(([cat, latencies]) => {
      chartData.push({ category: cat, latency: Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length) });
    });
  }

  return (
    <div className="p-6 space-y-6" data-testid="benchmarks-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-foreground flex items-center gap-2">
            <FlaskConical className="w-5 h-5 text-primary" /> Benchmarks
          </h1>
          <p className="text-sm text-muted-foreground mt-0.5">Agent performance evaluation suite</p>
        </div>
        <Button onClick={() => runMutation.mutate()} disabled={runMutation.isPending} className="gap-2" data-testid="run-benchmark">
          {runMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
          Run Benchmark
        </Button>
      </div>

      {/* Summary KPIs */}
      {activeSummary && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4" data-testid="benchmark-kpis">
          <Card className="border-border/50">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <Target className="w-4 h-4 text-primary" />
                <span className="text-xs text-muted-foreground">Pass Rate</span>
              </div>
              <p className="text-2xl font-bold tabular-nums text-primary">
                {Math.round((activeSummary.pass_rate ?? 0) * 100)}%
              </p>
              {/* Pass rate gauge */}
              <div className="w-full h-2 bg-muted/30 rounded-full mt-2 overflow-hidden">
                <div
                  className="h-full bg-primary rounded-full transition-all duration-500"
                  style={{ width: `${(activeSummary.pass_rate ?? 0) * 100}%` }}
                />
              </div>
            </CardContent>
          </Card>
          <Card className="border-border/50">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                <span className="text-xs text-muted-foreground">Passed</span>
              </div>
              <p className="text-2xl font-bold tabular-nums text-emerald-400">{activeSummary.passed}</p>
              <p className="text-xs text-muted-foreground mt-1">of {(activeSummary.passed ?? 0) + (activeSummary.failed ?? 0)} cases</p>
            </CardContent>
          </Card>
          <Card className="border-border/50">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <Timer className="w-4 h-4 text-amber-400" />
                <span className="text-xs text-muted-foreground">Avg Latency</span>
              </div>
              <p className="text-2xl font-bold tabular-nums">{activeSummary.avg_latency_ms}ms</p>
              <p className="text-xs text-muted-foreground mt-1">p50: {activeSummary.p50_latency_ms}ms · p95: {activeSummary.p95_latency_ms}ms</p>
            </CardContent>
          </Card>
          <Card className="border-border/50">
            <CardContent className="p-4">
              <div className="flex items-center gap-2 mb-1">
                <XCircle className="w-4 h-4 text-destructive" />
                <span className="text-xs text-muted-foreground">Failed</span>
              </div>
              <p className="text-2xl font-bold tabular-nums text-destructive">{activeSummary.failed}</p>
              <p className="text-xs text-muted-foreground mt-1">issues to investigate</p>
            </CardContent>
          </Card>
        </div>
      )}

      {/* Latency by Category Chart */}
      {chartData.length > 0 && (
        <Card className="border-border/50">
          <CardHeader className="pb-2">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <BarChart3 className="w-4 h-4" /> Latency by Category
            </CardTitle>
          </CardHeader>
          <CardContent>
            <div className="h-64" data-testid="latency-chart">
              <ResponsiveContainer width="100%" height="100%">
                <BarChart data={chartData} margin={{ top: 4, right: 8, left: 0, bottom: 4 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="hsl(220, 16%, 16%)" />
                  <XAxis
                    dataKey="category"
                    tick={{ fill: "hsl(215, 14%, 55%)", fontSize: 11 }}
                    axisLine={{ stroke: "hsl(220, 16%, 16%)" }}
                    tickLine={false}
                  />
                  <YAxis
                    tick={{ fill: "hsl(215, 14%, 55%)", fontSize: 11 }}
                    axisLine={{ stroke: "hsl(220, 16%, 16%)" }}
                    tickLine={false}
                    unit="ms"
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "hsl(222, 35%, 11%)",
                      border: "1px solid hsl(220, 16%, 16%)",
                      borderRadius: "8px",
                      fontSize: "12px",
                      color: "hsl(210, 20%, 92%)",
                    }}
                    formatter={(value: number) => [`${value}ms`, "Avg Latency"]}
                  />
                  <Bar dataKey="latency" radius={[4, 4, 0, 0]}>
                    {chartData.map((entry) => (
                      <Cell key={entry.category} fill={categoryColors[entry.category] || "#22d3ee"} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Results Table */}
      <Card className="border-border/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold">Test Results</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <div className="overflow-x-auto">
            <table className="w-full text-sm" data-testid="benchmark-results-table">
              <thead>
                <tr className="border-b border-border/50">
                  <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Status</th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Test Case</th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Category</th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Latency</th>
                  <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Details</th>
                </tr>
              </thead>
              <tbody>
                {activeResults.map((r: any) => (
                  <tr key={r.case} className="border-b border-border/30 hover:bg-muted/20 transition-colors">
                    <td className="px-4 py-3">
                      {r.passed ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-400" />
                      ) : (
                        <XCircle className="w-4 h-4 text-destructive" />
                      )}
                    </td>
                    <td className="px-4 py-3 text-xs font-medium text-foreground">{r.case}</td>
                    <td className="px-4 py-3">
                      <Badge
                        variant="outline"
                        className="text-[10px]"
                        style={{ color: categoryColors[r.category], borderColor: `${categoryColors[r.category]}40` }}
                      >
                        {r.category}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-xs tabular-nums text-muted-foreground flex items-center gap-1">
                      <Clock className="w-3 h-3" /> {r.latency_ms}ms
                    </td>
                    <td className="px-4 py-3 text-xs text-muted-foreground max-w-[200px] truncate">
                      {r.error ? (
                        <span className="text-destructive">{safeRender(r.error)}</span>
                      ) : (
                        safeRender(r.details)
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
