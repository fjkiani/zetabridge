import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Plug, CheckCircle2, Settings, ExternalLink } from "lucide-react";

interface Connection {
  name: string;
  description: string;
  protocol: string;
  status: "connected" | "configurable" | "disconnected";
  icon: string;
  color: string;
}

const connections: Connection[] = [
  {
    name: "Iceberg (Lakekeeper)",
    description: "Apache Iceberg REST catalog via Lakekeeper. Manages table metadata, snapshots, and schema evolution.",
    protocol: "REST Catalog API",
    status: "connected",
    icon: "❄️",
    color: "text-blue-400",
  },
  {
    name: "Delta (Unity Catalog OSS)",
    description: "Delta Lake tables managed through Unity Catalog open-source. Supports time travel and ACID transactions.",
    protocol: "Unity Catalog API",
    status: "connected",
    icon: "△",
    color: "text-amber-400",
  },
  {
    name: "Nessie (Git Versioning)",
    description: "Git-like versioning for data lakes. Branch, tag, and merge table metadata across environments.",
    protocol: "Nessie REST API v2",
    status: "connected",
    icon: "🔀",
    color: "text-purple-400",
  },
  {
    name: "DuckDB (Embedded)",
    description: "In-process analytical SQL engine for fast local queries. Used for the query workbench and ad-hoc analytics.",
    protocol: "Native Embedded",
    status: "connected",
    icon: "🦆",
    color: "text-emerald-400",
  },
  {
    name: "HuggingFace API (Arctic Text2SQL)",
    description: "Snowflake Arctic text-to-SQL model via HuggingFace Inference API. Powers natural language query translation.",
    protocol: "HuggingFace Inference API",
    status: "configurable",
    icon: "🤗",
    color: "text-yellow-400",
  },
  {
    name: "OpenLineage (Tracking)",
    description: "Collects lineage events from Airflow, dbt, Spark, and custom jobs. Powers the lineage graph visualization.",
    protocol: "OpenLineage HTTP API",
    status: "connected",
    icon: "📊",
    color: "text-cyan-400",
  },
];

function statusBadge(status: Connection["status"]) {
  if (status === "connected") {
    return (
      <Badge className="bg-emerald-500/10 text-emerald-400 border-emerald-500/20 gap-1">
        <CheckCircle2 className="w-3 h-3" />
        Connected
      </Badge>
    );
  }
  if (status === "configurable") {
    return (
      <Badge className="bg-amber-500/10 text-amber-400 border-amber-500/20 gap-1">
        <Settings className="w-3 h-3" />
        Configurable
      </Badge>
    );
  }
  return (
    <Badge variant="outline" className="text-muted-foreground gap-1">
      Disconnected
    </Badge>
  );
}

export default function Connections() {
  return (
    <div className="p-6 space-y-6" data-testid="page-connections">
      <div>
        <h1 className="text-xl font-semibold flex items-center gap-2">
          <Plug className="w-5 h-5 text-primary" />
          Connections
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">
          Services and integrations powering ZetaBridge
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {connections.map((conn) => (
          <Card
            key={conn.name}
            className="bg-card border-card-border"
            data-testid={`connection-${conn.name.split(" ")[0].toLowerCase()}`}
          >
            <CardContent className="p-4">
              <div className="flex items-start justify-between mb-3">
                <div className="flex items-center gap-2.5">
                  <span className="text-lg" role="img" aria-label={conn.name}>
                    {conn.icon}
                  </span>
                  <div>
                    <h3 className="text-sm font-semibold">{conn.name}</h3>
                    <span className="text-[11px] text-muted-foreground font-mono">
                      {conn.protocol}
                    </span>
                  </div>
                </div>
                {statusBadge(conn.status)}
              </div>
              <p className="text-xs text-muted-foreground leading-relaxed">
                {conn.description}
              </p>
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
