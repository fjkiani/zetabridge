import { useState } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiRequest, queryClient } from "@/lib/queryClient";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Send,
  Terminal,
  Clock,
  Table2,
  History,
  Loader2,
  Sparkles,
  Code2,
} from "lucide-react";

function SQLBlock({ sql }: { sql: string }) {
  // Basic SQL syntax highlighting
  const highlighted = sql
    .replace(/(SELECT|FROM|WHERE|ORDER BY|GROUP BY|LIMIT|AS|DESC|ASC|JOIN|ON|AND|OR|INSERT|UPDATE|DELETE|COUNT|SUM|AVG|MAX|MIN)\b/gi, '<span class="text-primary">$1</span>')
    .replace(/('.*?')/g, '<span class="text-emerald-400">$1</span>')
    .replace(/(\d+)/g, '<span class="text-amber-400">$1</span>');

  return (
    <div className="bg-[hsl(222,43%,6%)] rounded-lg border border-border/30 p-4 mt-2">
      <div className="flex items-center gap-2 mb-2">
        <Code2 className="w-3 h-3 text-muted-foreground" />
        <span className="text-[10px] text-muted-foreground uppercase tracking-wider font-semibold">Generated SQL</span>
      </div>
      <pre className="code-editor text-sm overflow-x-auto" dangerouslySetInnerHTML={{ __html: highlighted }} />
    </div>
  );
}

function ResultsTable({ results }: { results: any[] }) {
  if (!results || results.length === 0) return null;
  const keys = Object.keys(results[0]);
  return (
    <div className="rounded-lg border border-border/50 overflow-hidden mt-3">
      <div className="overflow-x-auto">
        <table className="w-full text-sm" data-testid="results-table">
          <thead>
            <tr className="bg-muted/20">
              {keys.map(k => (
                <th key={k} className="text-left px-3 py-2 text-[10px] font-semibold text-muted-foreground uppercase tracking-wider">
                  {k}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {results.map((row, i) => (
              <tr key={i} className="border-t border-border/30">
                {keys.map(k => (
                  <td key={k} className="px-3 py-2 text-xs tabular-nums text-foreground whitespace-nowrap">{String(row[k])}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export default function QueryWorkbench() {
  const [question, setQuestion] = useState("");
  const [track2Source, setTrack2Source] = useState("duckdb");
  const [result, setResult] = useState<any>(null);

  const { data: history } = useQuery({
    queryKey: ["/api/query/history"],
    queryFn: async () => { const r = await apiRequest("GET", "/api/query/history"); return r.json(); },
  });

  const nlMutation = useMutation({
    mutationFn: async (q: string) => {
      const r = await apiRequest("POST", "/api/query/nl", { question: q });
      return r.json();
    },
    onSuccess: (data) => {
      setResult(data);
      queryClient.invalidateQueries({ queryKey: ["/api/query/history"] });
    },
  });

  const track2Mutation = useMutation({
    mutationFn: async (payload: { question: string; source: string }) => {
      const r = await apiRequest("POST", "/api/query", payload);
      return r.json();
    },
    onSuccess: (data) => {
      setResult({
        sql: data.sql,
        engine: data.source || track2Source,
        results: data.rows ?? [],
        row_count: (data.rows ?? []).length,
        duration_ms: 0,
        error: data.error,
      });
    },
  });

  const handleSubmit = () => {
    if (!question.trim()) return;
    nlMutation.mutate(question.trim());
  };

  const handleTrack2Submit = () => {
    if (!question.trim()) return;
    track2Mutation.mutate({ question: question.trim(), source: track2Source });
  };

  return (
    <div className="p-6 space-y-6" data-testid="query-page">
      <div>
        <h1 className="text-xl font-bold text-foreground flex items-center gap-2">
          <Terminal className="w-5 h-5 text-primary" /> Query
        </h1>
        <p className="text-sm text-muted-foreground mt-0.5">Natural language to SQL</p>
      </div>

      {/* NL Input */}
      <Card className="border-border/50">
        <CardContent className="p-4">
          <div className="flex items-center gap-2">
            <div className="flex-1 relative">
              <Sparkles className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-primary/50" />
              <input
                type="text"
                value={question}
                onChange={(e) => setQuestion(e.target.value)}
                onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
                placeholder="Ask a question about your data..."
                className="w-full bg-background border border-border/50 rounded-lg pl-10 pr-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30"
                data-testid="query-input"
              />
            </div>
            <Button onClick={handleSubmit} disabled={nlMutation.isPending || !question.trim()} className="gap-2" data-testid="query-submit">
              {nlMutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : <Send className="w-4 h-4" />}
              Query
            </Button>
            <select
              value={track2Source}
              onChange={(e) => setTrack2Source(e.target.value)}
              className="text-xs bg-background border border-border/50 rounded-md px-2 py-2 text-foreground"
              aria-label="Engine for Track 2 API"
            >
              <option value="duckdb">duckdb</option>
              <option value="snowflake">snowflake</option>
              <option value="databricks">databricks</option>
              <option value="unified">unified</option>
            </select>
            <Button
              variant="outline"
              onClick={handleTrack2Submit}
              disabled={track2Mutation.isPending || !question.trim()}
              className="gap-2 shrink-0 text-xs"
              data-testid="query-track2-submit"
            >
              {track2Mutation.isPending ? <Loader2 className="w-4 h-4 animate-spin" /> : null}
              POST /api/query
            </Button>
          </div>
        </CardContent>
      </Card>

      {/* Results */}
      {result && (
        <div className="space-y-3" data-testid="query-result">
          {result.error ? (
            <p className="text-sm text-destructive" data-testid="query-error">{String(result.error)}</p>
          ) : null}
          <div className="flex items-center gap-3">
            <Badge variant="outline" className="text-[10px]">{result.engine}</Badge>
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <Table2 className="w-3 h-3" /> {result.row_count} rows
            </span>
            <span className="text-xs text-muted-foreground flex items-center gap-1">
              <Clock className="w-3 h-3" /> {result.duration_ms}ms
            </span>
          </div>
          <SQLBlock sql={result.sql} />
          <ResultsTable results={result.results} />
        </div>
      )}

      {/* Query History */}
      <Card className="border-border/50">
        <CardHeader className="pb-2">
          <CardTitle className="text-sm font-semibold flex items-center gap-2">
            <History className="w-4 h-4" /> Query History
          </CardTitle>
        </CardHeader>
        <CardContent className="pt-0">
          <div className="space-y-0" data-testid="query-history">
            {(history || []).map((h: any) => (
              <button
                key={h.id}
                onClick={() => { setQuestion(h.question); setResult({ sql: h.sql, engine: h.engine, row_count: h.row_count, duration_ms: h.duration_ms, results: [] }); }}
                className="w-full flex items-center justify-between py-2.5 border-b border-border/20 last:border-0 hover:bg-muted/20 px-2 rounded transition-colors text-left"
              >
                <div className="flex-1 min-w-0">
                  <p className="text-sm text-foreground truncate">{h.question}</p>
                  <p className="text-[10px] text-muted-foreground font-mono mt-0.5 truncate">{h.sql}</p>
                </div>
                <div className="flex items-center gap-2 shrink-0 ml-3">
                  <Badge variant="outline" className="text-[9px]">{h.engine}</Badge>
                  <span className="text-[10px] tabular-nums text-muted-foreground">{h.duration_ms}ms</span>
                </div>
              </button>
            ))}
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
