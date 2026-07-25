import { useEffect, useRef, useState } from "react";
import { Link } from "wouter";
import {
  Sparkles,
  Send,
  Loader2,
  User,
  Bot,
  ExternalLink,
  GitFork,
  Zap,
  ShieldAlert,
  Database,
} from "lucide-react";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  runSignalAgent,
  graphFocusHref,
  apiConfigured,
  type AgentName,
  type AgentResponse,
} from "@/lib/signalsApi";

interface Turn {
  role: "user" | "assistant";
  text: string;
  agent?: AgentName;
  usedLlm?: boolean;
  grounding?: string[];
  findings?: any[];
  error?: boolean;
}

// map a free-text question to the right agent + task (deterministic router)
function routeQuestion(q: string): { agent: AgentName; action?: string; family?: string; label: string } {
  const s = q.toLowerCase();
  if (/(bridge|genom|connect|cross-endpoint|link|opportunit)/.test(s))
    return { agent: "bridge_hunter", label: "BridgeHunter" };
  if (/(gap|blind|missing|wrong|weak|absent|not ingest|can't answer|cannot answer)/.test(s))
    return { agent: "gap_auditor", label: "GapAuditor" };
  // family hints for the miner
  let family = "all";
  if (/ror|dispropor|pharmacovig/.test(s)) family = "pharmacovig";
  else if (/cross.?trial|reproduc|consisten|escalat/.test(s)) family = "cross_trial";
  else if (/outlier/.test(s)) family = "outlier";
  else if (/drug|rate ratio|\brr\b/.test(s)) family = "drug_ae";
  return { agent: "signal_miner", action: "rank", family, label: "SignalMiner" };
}

const SUGGESTIONS = [
  { q: "What are the strongest signals across the federation?", icon: Zap },
  { q: "How do MSK genomics connect to clinical toxicity?", icon: GitFork },
  { q: "What has pharma been getting wrong — where are the blind spots?", icon: ShieldAlert },
  { q: "Show the strongest ROR disproportionality signals", icon: Zap },
];

function GroundingChips({ ids }: { ids: string[] }) {
  if (!ids?.length) return null;
  const shown = ids.slice(0, 12);
  return (
    <div className="mt-2">
      <p className="text-[9px] uppercase tracking-wider text-muted-foreground mb-1">
        Grounded in {ids.length} graph node{ids.length === 1 ? "" : "s"}
      </p>
      <div className="flex flex-wrap gap-1">
        {shown.map((id) => (
          <a key={id} href={graphFocusHref(id)}>
            <Badge variant="outline" className="text-[9px] font-mono cursor-pointer hover:border-primary/50">
              <span className="truncate max-w-[180px]">{id}</span>
              <ExternalLink className="w-2.5 h-2.5 ml-1 shrink-0" />
            </Badge>
          </a>
        ))}
        {ids.length > shown.length && (
          <span className="text-[9px] text-muted-foreground self-center">+{ids.length - shown.length} more</span>
        )}
      </div>
    </div>
  );
}

// compact finding preview: top few named findings, each a slug deep-link
function FindingsPreview({ findings }: { findings: any[] }) {
  if (!findings?.length) return null;
  const items = findings.slice(0, 5);
  return (
    <div className="mt-2 space-y-1">
      {items.map((f, i) => {
        const slug = f.slug ?? f.path?.[1];
        const name = f.name ?? (f.gene && f.ae_term ? `${f.gene} → ${f.ae_term}` : slug);
        const metric =
          f.native_value ?? f.bridge_score ?? f.consistency_score ?? null;
        const metricName = f.native_metric ?? (f.bridge_score != null ? "bridge_score" : null);
        if (!slug && !name) return null;
        return (
          <div key={i} className="flex items-center justify-between gap-2 text-[11px] rounded border border-border/40 px-2 py-1">
            <span className="truncate">{name}</span>
            <div className="flex items-center gap-2 shrink-0">
              {metric != null && (
                <span className="tabular-nums text-primary font-semibold">
                  {metricName ? `${metricName}=` : ""}{typeof metric === "number" ? metric : String(metric)}
                </span>
              )}
              {slug && (
                <Link href={`/signals/${encodeURIComponent(slug)}`}>
                  <span className="text-muted-foreground hover:text-primary underline">detail</span>
                </Link>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

export default function Analyst() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, busy]);

  async function ask(q: string) {
    if (!q.trim() || busy) return;
    const route = routeQuestion(q);
    setTurns((t) => [...t, { role: "user", text: q }]);
    setInput("");
    setBusy(true);
    try {
      const res: AgentResponse = await runSignalAgent({
        agent: route.agent,
        action: route.action,
        family: route.family as any,
        limit: 8,
      });
      setTurns((t) => [
        ...t,
        {
          role: "assistant",
          text: res.summary,
          agent: route.agent,
          usedLlm: res.used_llm,
          grounding: res.grounding,
          findings: res.findings as any[],
        },
      ]);
    } catch (e) {
      setTurns((t) => [
        ...t,
        {
          role: "assistant",
          text:
            "The Insight Analyst runs grounded queries against the live backend, which isn't reachable right now. " +
            "Configure VITE_API_BASE + VITE_ZETA_API_KEY (or start the backend) to get live, graph-grounded answers. " +
            "You can still browse every signal offline from the Signal Intelligence hub.",
          error: true,
        },
      ]);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col h-full">
      {/* header */}
      <div className="px-6 py-4 border-b border-border/50 shrink-0">
        <div className="flex items-center gap-2">
          <Sparkles className="w-5 h-5 text-primary" />
          <h1 className="text-lg font-semibold">Insight Analyst</h1>
          <Badge variant="outline" className="text-[10px]">read-only</Badge>
          {!apiConfigured() && (
            <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-400">
              backend not configured
            </Badge>
          )}
        </div>
        <p className="text-xs text-muted-foreground mt-1 max-w-2xl">
          Ask about the strongest signals, how genomics connect to clinical toxicity, or what pharma
          missed. Every answer is <span className="text-foreground">grounded</span> — the exact graph
          nodes each claim came from are listed and deep-linked. Numbers are never invented.
        </p>
      </div>

      {/* conversation */}
      <div className="flex-1 overflow-y-auto px-6 py-4 space-y-4">
        {turns.length === 0 && (
          <div className="max-w-2xl mx-auto text-center pt-8 space-y-4">
            <Database className="w-8 h-8 mx-auto text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">Ask the federation a question.</p>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {SUGGESTIONS.map((s) => {
                const Icon = s.icon;
                return (
                  <button
                    key={s.q}
                    onClick={() => ask(s.q)}
                    disabled={busy}
                    className="text-left text-xs rounded-lg border border-border/50 hover:border-primary/50 px-3 py-2.5 transition-colors flex items-start gap-2 disabled:opacity-50"
                  >
                    <Icon className="w-3.5 h-3.5 text-primary mt-0.5 shrink-0" />
                    {s.q}
                  </button>
                );
              })}
            </div>
          </div>
        )}

        {turns.map((t, i) => (
          <div key={i} className={`flex gap-3 ${t.role === "user" ? "justify-end" : "justify-start"}`}>
            {t.role === "assistant" && (
              <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
                <Bot className="w-4 h-4 text-primary" />
              </div>
            )}
            <div className={`max-w-[80%] ${t.role === "user" ? "order-first" : ""}`}>
              <Card className={t.role === "user" ? "bg-primary/5 border-primary/20" : t.error ? "border-amber-500/30" : ""}>
                <CardContent className="p-3">
                  {t.role === "assistant" && t.agent && (
                    <div className="flex items-center gap-1.5 mb-1.5">
                      <Badge variant="secondary" className="text-[9px]">{t.agent}</Badge>
                      <Badge variant="outline" className={`text-[9px] ${t.usedLlm ? "border-violet-500/40 text-violet-400" : "border-muted-foreground/30 text-muted-foreground"}`}>
                        {t.usedLlm ? "LLM-narrated" : "deterministic"}
                      </Badge>
                    </div>
                  )}
                  <p className="text-sm leading-relaxed whitespace-pre-wrap">{t.text}</p>
                  {t.role === "assistant" && !t.error && (
                    <>
                      <FindingsPreview findings={t.findings ?? []} />
                      <GroundingChips ids={t.grounding ?? []} />
                    </>
                  )}
                </CardContent>
              </Card>
            </div>
            {t.role === "user" && (
              <div className="w-7 h-7 rounded-full bg-muted flex items-center justify-center shrink-0">
                <User className="w-4 h-4" />
              </div>
            )}
          </div>
        ))}

        {busy && (
          <div className="flex gap-3">
            <div className="w-7 h-7 rounded-full bg-primary/10 flex items-center justify-center shrink-0">
              <Bot className="w-4 h-4 text-primary" />
            </div>
            <Card>
              <CardContent className="p-3 flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="w-4 h-4 animate-spin" /> Querying the graph…
              </CardContent>
            </Card>
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* composer */}
      <div className="px-6 py-3 border-t border-border/50 shrink-0">
        <form
          onSubmit={(e) => {
            e.preventDefault();
            ask(input);
          }}
          className="flex gap-2"
        >
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask about the strongest signals, bridges, or blind spots…"
            disabled={busy}
            data-testid="analyst-input"
            className="flex-1 bg-muted/40 border border-border/50 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-primary/50 disabled:opacity-50"
          />
          <Button type="submit" disabled={busy || !input.trim()} size="sm" className="px-4">
            <Send className="w-4 h-4" />
          </Button>
        </form>
      </div>
    </div>
  );
}
