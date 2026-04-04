import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScrollArea } from "@/components/ui/scroll-area";
import {
  Database,
  FolderOpen,
  Table2,
  ChevronDown,
  ChevronRight,
  Columns3,
  Hash,
  Type,
  ToggleLeft,
} from "lucide-react";

interface TableEntry {
  id: number;
  catalog_name: string;
  catalog_type: string;
  schema_name: string;
  table_name: string;
  columns: { name: string; type: string; nullable: boolean }[];
  properties: Record<string, string>;
  fqn: string;
}

function TreeNode({ icon: Icon, label, badge, isActive, depth, expanded, onClick, children }: any) {
  return (
    <div>
      <button
        onClick={onClick}
        className={`w-full flex items-center gap-2 px-2 py-1.5 rounded-md text-[13px] transition-colors hover:bg-muted/50 ${
          isActive ? "bg-primary/10 text-primary" : "text-foreground"
        }`}
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
      >
        {children ? (
          expanded ? <ChevronDown className="w-3 h-3 text-muted-foreground shrink-0" /> : <ChevronRight className="w-3 h-3 text-muted-foreground shrink-0" />
        ) : (
          <span className="w-3 shrink-0" />
        )}
        <Icon className="w-3.5 h-3.5 shrink-0 text-muted-foreground" />
        <span className="truncate">{label}</span>
        {badge && (
          <Badge variant="outline" className="text-[9px] ml-auto shrink-0 border-muted-foreground/20">
            {badge}
          </Badge>
        )}
      </button>
    </div>
  );
}

const typeColorMap: Record<string, string> = {
  Iceberg: "text-cyan-400 bg-cyan-400/10 border-cyan-400/20",
  Delta: "text-violet-400 bg-violet-400/10 border-violet-400/20",
  DuckDB: "text-amber-400 bg-amber-400/10 border-amber-400/20",
};

export default function CatalogExplorer() {
  const [selectedTable, setSelectedTable] = useState<TableEntry | null>(null);
  const [expandedNodes, setExpandedNodes] = useState<Set<string>>(new Set(["lakehouse_iceberg", "lakehouse_delta", "analytics_duckdb"]));

  const { data: tables } = useQuery<TableEntry[]>({
    queryKey: ["/api/catalog/tables"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/catalog/tables"); return r.json(); },
  });

  const { data: stats } = useQuery({
    queryKey: ["/api/catalog/stats"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/catalog/stats"); return r.json(); },
  });

  const toggleNode = (key: string) => {
    setExpandedNodes(prev => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key); else next.add(key);
      return next;
    });
  };

  // Build tree structure
  const tree: Record<string, Record<string, TableEntry[]>> = {};
  (tables || []).forEach(t => {
    if (!tree[t.catalog_name]) tree[t.catalog_name] = {};
    if (!tree[t.catalog_name][t.schema_name]) tree[t.catalog_name][t.schema_name] = [];
    tree[t.catalog_name][t.schema_name].push(t);
  });

  return (
    <div className="h-full flex" data-testid="catalog-page">
      {/* Tree Panel */}
      <div className="w-[280px] border-r border-border flex flex-col shrink-0">
        <div className="px-4 py-3 border-b border-border">
          <h2 className="text-sm font-semibold text-foreground">Catalog</h2>
          <div className="flex gap-3 mt-2">
            {stats?.by_type && Object.entries(stats.by_type).map(([type, count]) => (
              <Badge key={type} variant="outline" className={`text-[10px] ${typeColorMap[type] || ""}`}>
                {type}: {String(count)}
              </Badge>
            ))}
          </div>
        </div>
        <ScrollArea className="flex-1">
          <div className="py-2 px-2" data-testid="catalog-tree">
            {Object.entries(tree).map(([catalog, schemas]) => {
              const catalogType = (tables || []).find(t => t.catalog_name === catalog)?.catalog_type || "";
              return (
                <div key={catalog}>
                  <TreeNode
                    icon={Database}
                    label={catalog}
                    badge={catalogType}
                    depth={0}
                    expanded={expandedNodes.has(catalog)}
                    onClick={() => toggleNode(catalog)}
                    children={true}
                  />
                  {expandedNodes.has(catalog) && Object.entries(schemas).map(([schema, schemaTables]) => {
                    const schemaKey = `${catalog}.${schema}`;
                    return (
                      <div key={schemaKey}>
                        <TreeNode
                          icon={FolderOpen}
                          label={schema}
                          depth={1}
                          expanded={expandedNodes.has(schemaKey)}
                          onClick={() => toggleNode(schemaKey)}
                          children={true}
                        />
                        {expandedNodes.has(schemaKey) && schemaTables.map(table => (
                          <TreeNode
                            key={table.fqn}
                            icon={Table2}
                            label={table.table_name}
                            badge={`${table.columns.length} cols`}
                            depth={2}
                            isActive={selectedTable?.fqn === table.fqn}
                            onClick={() => setSelectedTable(table)}
                          />
                        ))}
                      </div>
                    );
                  })}
                </div>
              );
            })}
          </div>
        </ScrollArea>
      </div>

      {/* Detail Panel */}
      <div className="flex-1 overflow-y-auto overscroll-contain p-6">
        {!selectedTable ? (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <Database className="w-10 h-10 text-muted-foreground/30 mb-3" />
            <p className="text-sm text-muted-foreground">Select a table to view details</p>
            <p className="text-xs text-muted-foreground/60 mt-1">{stats?.total_tables ?? 0} tables across {stats?.catalogs ?? 0} catalogs</p>
          </div>
        ) : (
          <div className="space-y-4 max-w-3xl" data-testid="table-detail">
            <div>
              <div className="flex items-center gap-2 mb-1">
                <h2 className="text-lg font-bold text-foreground">{selectedTable.table_name}</h2>
                <Badge className={`text-[10px] ${typeColorMap[selectedTable.catalog_type] || ""}`}>
                  {selectedTable.catalog_type}
                </Badge>
              </div>
              <p className="text-xs text-muted-foreground font-mono">{selectedTable.fqn}</p>
            </div>

            {/* Columns */}
            <Card className="border-border/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold flex items-center gap-1.5">
                  <Columns3 className="w-3.5 h-3.5" /> Columns ({selectedTable.columns.length})
                </CardTitle>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="space-y-0">
                  {selectedTable.columns.map((col, i) => (
                    <div key={col.name} className={`flex items-center justify-between py-2 ${i > 0 ? "border-t border-border/20" : ""}`}>
                      <div className="flex items-center gap-2">
                        <Hash className="w-3 h-3 text-muted-foreground/50" />
                        <span className="text-sm font-medium text-foreground">{col.name}</span>
                      </div>
                      <div className="flex items-center gap-2">
                        <Badge variant="outline" className="text-[10px] font-mono border-muted-foreground/20">
                          {col.type}
                        </Badge>
                        {col.nullable && (
                          <Badge variant="outline" className="text-[10px] border-amber-400/20 text-amber-400">nullable</Badge>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>

            {/* Properties */}
            <Card className="border-border/50">
              <CardHeader className="pb-2">
                <CardTitle className="text-xs font-semibold">Properties</CardTitle>
              </CardHeader>
              <CardContent className="pt-0">
                <div className="space-y-1">
                  {Object.entries(selectedTable.properties).map(([key, val]) => (
                    <div key={key} className="flex items-center justify-between py-1.5 text-xs">
                      <span className="text-muted-foreground">{key}</span>
                      <code className="text-foreground font-mono bg-muted/30 px-1.5 py-0.5 rounded text-[11px]">{String(val)}</code>
                    </div>
                  ))}
                </div>
              </CardContent>
            </Card>
          </div>
        )}
      </div>
    </div>
  );
}
