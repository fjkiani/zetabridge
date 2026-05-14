import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiRequest, queryClient } from "@/lib/queryClient";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  Bot,
  Wrench,
  History,
  FlaskConical,
  Clock,
  CheckCircle2,
  XCircle,
  Loader2,
  Activity,
} from "lucide-react";

export default function AgentsPage() {
  const [benchmarkResult, setBenchmarkResult] = useState<any>(null);

  const { data: agents } = useQuery({
    queryKey: ["/api/agents"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/agents"); return r.json(); },
  });

  const { data: stats } = useQuery({
    queryKey: ["/api/agents/stats"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/agents/stats"); return r.json(); },
  });

  const { data: tools } = useQuery({
    queryKey: ["/api/agents/tools"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/agents/tools"); return r.json(); },
  });

  const { data: history } = useQuery({
    queryKey: ["/api/agents/execution-history"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/agents/execution-history"); return r.json(); },
  });

  const benchmarkMutation = useMutation({
    mutationFn: async () => {
      const r = await apiRequest("POST", "/api/benchmarks/run", {});
      return r.json();
    },
    onSuccess: (data) => setBenchmarkResult(data),
  });

  const categoryColors: Record<string, string> = {
    catalog: "text-cyan-400 border-cyan-400/30",
    query: "text-violet-400 border-violet-400/30",
    lineage: "text-emerald-400 border-emerald-400/30",
    etl: "text-amber-400 border-amber-400/30",
    quality: "text-rose-400 border-rose-400/30",
    connector: "text-blue-400 border-blue-400/30",
    search: "text-orange-400 border-orange-400/30",
  };

  return (
    <div className="p-6 space-y-6" data-testid="agents-page">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-foreground">Agents</h1>
          <p className="text-sm text-muted-foreground mt-0.5">AI agents powering the data platform</p>
        </div>
        <Button
          onClick={() => benchmarkMutation.mutate()}
          disabled={benchmarkMutation.isPending}
          className="gap-2"
          data-testid="run-benchmark-btn"
        >
          {benchmarkMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <FlaskConical className="w-4 h-4" />}
          Run Benchmark
        </Button>
      </div>

      <Tabs defaultValue="agents">
        <TabsList>
          <TabsTrigger value="agents" className="gap-1.5"><Bot className="w-3.5 h-3.5" />Agents</TabsTrigger>
          <TabsTrigger value="tools" className="gap-1.5"><Wrench className="w-3.5 h-3.5" />Tools</TabsTrigger>
          <TabsTrigger value="history" className="gap-1.5"><History className="w-3.5 h-3.5" />History</TabsTrigger>
        </TabsList>

        {/* Agents Tab */}
        <TabsContent value="agents" className="mt-4">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" data-testid="agents-grid">
            {(agents || []).map((agent: any) => {
              const s = stats?.[agent.name];
              return (
                <Card key={agent.name} className="border-border/50" data-testid={`agent-detail-${agent.name}`}>
                  <CardContent className="p-5">
                    <div className="flex items-start justify-between mb-3">
                      <div className="flex items-center gap-2">
                        <div className="p-2 rounded-lg bg-primary/10">
                          <Bot className="w-4 h-4 text-primary" />
                        </div>
                        <div>
                          <p className="text-sm font-semibold text-foreground">{agent.name.replace(/_/g, " ")}</p>
                          <span className="flex items-center gap-1 mt-0.5">
                            <span className="w-1.5 h-1.5 rounded-full bg-emerald-400" />
                            <span className="text-[10px] text-emerald-400">{agent.status}</span>
                          </span>
                        </div>
                      </div>
                    </div>
                    <p className="text-xs text-muted-foreground mb-3">{agent.description}</p>
                    <div className="flex flex-wrap gap-1 mb-3">
                      {agent.capabilities.map((c: string) => (
                        <Badge key={c} variant="outline" className="text-[10px] border-muted-foreground/20">
                          {c.replace(/_/g, " ")}
                        </Badge>
                      ))}
                    </div>
                    {s && (
                      <div className="grid grid-cols-3 gap-2 pt-3 border-t border-border/30">
                        <div>
                          <p className="text-xs font-bold tabular-nums">{s.total_runs}</p>
                          <p className="text-[10px] text-muted-foreground">runs</p>
                        </div>
                        <div>
                          <p className="text-xs font-bold tabular-nums">{s.avg_latency_ms}ms</p>
                          <p className="text-[10px] text-muted-foreground">avg latency</p>
                        </div>
                        <div>
                          <p className="text-xs font-bold tabular-nums">{s.max_latency_ms}ms</p>
                          <p className="text-[10px] text-muted-foreground">p99</p>
                        </div>
                      </div>
                    )}
                  </CardContent>
                </Card>
              );
            })}
          </div>
        </TabsContent>

        {/* Tools Tab */}
        <TabsContent value="tools" className="mt-4">
          <Card className="border-border/50">
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="tools-table">
                  <thead>
                    <tr className="border-b border-border/50">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Name</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Description</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Category</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(tools || []).map((tool: any) => (
                      <tr key={tool.name} className="border-b border-border/30 hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-3">
                          <code className="text-xs font-mono text-primary bg-primary/5 px-1.5 py-0.5 rounded">{tool.name}</code>
                        </td>
                        <td className="px-4 py-3 text-xs text-muted-foreground">{tool.description}</td>
                        <td className="px-4 py-3">
                          <Badge variant="outline" className={`text-[10px] ${categoryColors[tool.category] || ""}`}>
                            {tool.category}
                          </Badge>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </TabsContent>

        {/* History Tab */}
        <TabsContent value="history" className="mt-4">
          <Card className="border-border/50">
            <CardContent className="p-0">
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="history-table">
                  <thead>
                    <tr className="border-b border-border/50">
                      <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Timestamp</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Intent</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Tasks</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Succeeded</th>
                      <th className="text-left px-4 py-3 text-xs font-semibold text-muted-foreground uppercase tracking-wider">Latency</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(history || []).map((h: any) => (
                      <tr key={h.plan_id} className="border-b border-border/30 hover:bg-muted/20 transition-colors">
                        <td className="px-4 py-3 text-xs tabular-nums text-muted-foreground">
                          {new Date(h.timestamp).toLocaleTimeString()}
                        </td>
                        <td className="px-4 py-3">
                          <Badge variant="outline" className="text-[10px]">{h.intent.replace(/_/g, " ")}</Badge>
                        </td>
                        <td className="px-4 py-3 text-xs tabular-nums">{h.total_tasks}</td>
                        <td className="px-4 py-3">
                          <span className="flex items-center gap-1 text-xs">
                            {h.failed === 0 ? (
                              <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                            ) : (
                              <XCircle className="w-3 h-3 text-amber-400" />
                            )}
                            <span className="tabular-nums">{h.succeeded}/{h.total_tasks}</span>
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs tabular-nums flex items-center gap-1">
                          <Clock className="w-3 h-3 text-muted-foreground" />{h.total_latency_ms}ms
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Benchmark Results */}
      {benchmarkResult && (
        <Card className="border-primary/20" data-testid="benchmark-results">
          <CardHeader className="pb-3">
            <CardTitle className="text-sm font-semibold flex items-center gap-2">
              <FlaskConical className="w-4 h-4 text-primary" /> Benchmark Results
            </CardTitle>
          </CardHeader>
          <CardContent className="pt-0">
            <div className="grid grid-cols-4 gap-4 mb-4">
              <div>
                <p className="text-2xl font-bold tabular-nums text-primary">{Math.round(benchmarkResult.pass_rate * 100)}%</p>
                <p className="text-xs text-muted-foreground">Pass rate</p>
              </div>
              <div>
                <p className="text-2xl font-bold tabular-nums">{benchmarkResult.passed}</p>
                <p className="text-xs text-muted-foreground">Passed</p>
              </div>
              <div>
                <p className="text-2xl font-bold tabular-nums text-destructive">{benchmarkResult.failed}</p>
                <p className="text-xs text-muted-foreground">Failed</p>
              </div>
              <div>
                <p className="text-2xl font-bold tabular-nums">{benchmarkResult.avg_latency_ms}ms</p>
                <p className="text-xs text-muted-foreground">Avg latency</p>
              </div>
            </div>
            <div className="space-y-1">
              {benchmarkResult.results?.slice(0, 5).map((r: any) => (
                <div key={r.case} className="flex items-center justify-between py-1.5 text-xs">
                  <div className="flex items-center gap-2">
                    {r.passed ? <CheckCircle2 className="w-3 h-3 text-emerald-400" /> : <XCircle className="w-3 h-3 text-destructive" />}
                    <span className="text-foreground">{r.case}</span>
                  </div>
                  <span className="tabular-nums text-muted-foreground">{r.latency_ms}ms</span>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
