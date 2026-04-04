import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
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
  Activity,
  CheckCircle2,
  AlertCircle,
  MinusCircle,
} from "lucide-react";

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

export default function Connectors() {
  const { data: connectors } = useQuery({
    queryKey: ["/api/connectors"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/connectors"); return r.json(); },
  });

  const { data: stats } = useQuery({
    queryKey: ["/api/connectors/stats"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/connectors/stats"); return r.json(); },
  });

  return (
    <div className="p-6 space-y-6" data-testid="connectors-page">
      <div>
        <h1 className="text-xl font-bold text-foreground flex items-center gap-2">
          <Plug className="w-5 h-5 text-primary" /> Connectors
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">Data platform integration registry</p>
      </div>

      {/* Stats Summary */}
      {stats && (
        <div className="flex flex-wrap items-center gap-3" data-testid="connector-stats">
          <Badge variant="outline" className="text-xs">
            {stats.total_connectors} total
          </Badge>
          <Badge variant="outline" className="text-xs text-emerald-400 border-emerald-400/30">
            <CheckCircle2 className="w-3 h-3 mr-1" /> {stats.active} active
          </Badge>
          {Object.entries(stats.by_category || {}).map(([cat, count]) => (
            <Badge key={cat} variant="outline" className={`text-[10px] ${categoryColors[cat] || ""}`}>
              {cat.replace(/_/g, " ")}: {String(count)}
            </Badge>
          ))}
        </div>
      )}

      {/* Connector Grid */}
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
                        {c.category.replace(/_/g, " ")}
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
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">License</span>
                    <span className="text-foreground text-[11px]">{c.license}</span>
                  </div>
                </div>
              </CardContent>
            </Card>
          );
        })}
      </div>
    </div>
  );
}
