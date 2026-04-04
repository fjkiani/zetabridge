import { useState, useRef, useEffect } from "react";
import { useQuery, useMutation } from "@tanstack/react-query";
import { apiRequest } from "@/lib/queryClient";
import { safeRender } from "@/lib/utils";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Send,
  Bot,
  User,
  Clock,
  Zap,
  ChevronRight,
  Sparkles,
  Loader2,
} from "lucide-react";

interface ChatMessage {
  role: "user" | "assistant";
  content: string;
  intent?: string;
  agents_used?: string[];
  latency_ms?: number;
  tasks?: number;
  suggestions?: string[];
  data?: any;
  render_type?: string;
}

function DataTable({ data }: { data: any[] }) {
  if (!data || data.length === 0) return null;
  const keys = Object.keys(data[0]);
  return (
    <div className="rounded-lg border border-border/50 overflow-hidden mt-3">
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="bg-muted/30">
              {keys.map((k) => (
                <th key={k} className="text-left px-3 py-2 font-semibold text-muted-foreground uppercase tracking-wider text-[10px]">
                  {k}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {data.map((row, i) => (
              <tr key={i} className="border-t border-border/30">
                {keys.map((k) => (
                  <td key={k} className="px-3 py-2 tabular-nums text-foreground whitespace-nowrap">
                    {String(row[k])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ExecutionMeta({ intent, agents, latency, tasks }: { intent?: string; agents?: string[]; latency?: number; tasks?: number }) {
  if (!intent) return null;
  return (
    <div className="flex flex-wrap items-center gap-2 mt-3">
      <Badge variant="outline" className="text-[10px] border-primary/30 text-primary">
        <Zap className="w-2.5 h-2.5 mr-1" />{intent.replace(/_/g, " ")}
      </Badge>
      {agents?.map((a) => (
        <Badge key={a} variant="outline" className="text-[10px] border-muted-foreground/30 text-muted-foreground">
          <Bot className="w-2.5 h-2.5 mr-1" />{a.replace(/_/g, " ")}
        </Badge>
      ))}
      {latency != null && (
        <Badge variant="outline" className="text-[10px] border-muted-foreground/30 text-muted-foreground tabular-nums">
          <Clock className="w-2.5 h-2.5 mr-1" />{latency}ms
        </Badge>
      )}
      {tasks != null && (
        <Badge variant="outline" className="text-[10px] border-muted-foreground/30 text-muted-foreground tabular-nums">
          {tasks} tasks
        </Badge>
      )}
    </div>
  );
}

function messageText(content: unknown): string {
  return typeof content === "string" ? content : safeRender(content);
}

function MessageBubble({ msg, onSuggestionClick }: { msg: ChatMessage; onSuggestionClick: (s: string) => void }) {
  const isUser = msg.role === "user";
  const body = messageText(msg.content);

  return (
    <div className={`flex gap-3 ${isUser ? "justify-end" : "justify-start"}`} data-testid={`msg-${msg.role}`}>
      {!isUser && (
        <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center shrink-0 mt-1">
          <Sparkles className="w-3.5 h-3.5 text-primary" />
        </div>
      )}
      <div className={`max-w-[75%] ${isUser ? "order-first" : ""}`}>
        <div
          className={`rounded-2xl px-4 py-3 text-sm ${
            isUser
              ? "bg-primary text-primary-foreground rounded-br-md"
              : "bg-card border border-border/50 text-foreground rounded-bl-md"
          }`}
        >
          {isUser ? (
            <p>{body}</p>
          ) : (
            <>
              <p className="whitespace-pre-wrap leading-relaxed">{body.replace(/\*\*(.*?)\*\*/g, "$1")}</p>
              {Array.isArray(msg.data) && msg.data.length > 0 && msg.render_type === "table" && (
                <DataTable data={msg.data} />
              )}
            </>
          )}
        </div>
        {!isUser && <ExecutionMeta intent={msg.intent} agents={msg.agents_used} latency={msg.latency_ms} tasks={msg.tasks} />}
        {!isUser && msg.suggestions && msg.suggestions.length > 0 && (
          <div className="flex flex-wrap gap-1.5 mt-2">
            {msg.suggestions.map((s) => (
              <button
                key={s}
                onClick={() => onSuggestionClick(s)}
                className="text-[11px] px-3 py-1.5 rounded-full border border-primary/30 text-primary hover:bg-primary/10 transition-colors"
                data-testid={`suggestion-chip`}
              >
                <ChevronRight className="w-2.5 h-2.5 inline mr-1" />{s}
              </button>
            ))}
          </div>
        )}
      </div>
      {isUser && (
        <div className="w-7 h-7 rounded-lg bg-muted flex items-center justify-center shrink-0 mt-1">
          <User className="w-3.5 h-3.5 text-muted-foreground" />
        </div>
      )}
    </div>
  );
}

export default function CoPilot() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [seeded, setSeeded] = useState(false);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Load pre-seeded conversation
  const { data: sessionHistory } = useQuery({
    queryKey: ["/api/copilot/sessions/session-demo/history"],
    queryFn: async () => {
      const res = await apiRequest("GET", "/api/copilot/sessions/session-demo/history");
      return res.json();
    },
  });

  useEffect(() => {
    if (sessionHistory && !seeded) {
      const parsed: ChatMessage[] = [];
      for (const turn of sessionHistory) {
        if (turn.role === "user") {
          parsed.push({ role: "user", content: turn.content });
        } else {
          try {
            const data = JSON.parse(turn.content);
            parsed.push({
              role: "assistant",
              content: messageText(data.summary),
              intent: data.intent,
              agents_used: data.agents_used,
              latency_ms: turn.latency_ms,
              tasks: data.agents_used?.length ? data.agents_used.length + 1 : 2,
              suggestions: data.suggestions,
              data: data.data,
              render_type: data.render_type,
            });
          } catch {
            parsed.push({ role: "assistant", content: turn.content });
          }
        }
      }
      setMessages(parsed);
      setSeeded(true);
    }
  }, [sessionHistory, seeded]);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  const chatMutation = useMutation({
    mutationFn: async (message: string) => {
      const res = await apiRequest("POST", "/api/copilot/chat", { message, session_id: "session-demo" });
      return res.json();
    },
    onSuccess: (data) => {
      const resp = data.response ?? {};
      setMessages((prev) => [
        ...prev,
        {
          role: "assistant",
          content: messageText(resp.summary),
          intent: resp.intent,
          agents_used: resp.agents_used,
          latency_ms: data.execution?.latency_ms,
          tasks: data.execution?.tasks,
          suggestions: Array.isArray(resp.suggestions) ? resp.suggestions : undefined,
          data: resp.data,
          render_type: resp.render_type,
        },
      ]);
    },
  });

  const handleSend = (text?: string) => {
    const message = text || input.trim();
    if (!message) return;
    setMessages((prev) => [...prev, { role: "user", content: message }]);
    setInput("");
    chatMutation.mutate(message);
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  return (
    <div className="h-full flex flex-col" data-testid="copilot-page">
      {/* Header */}
      <div className="border-b border-border px-6 py-3 shrink-0">
        <div className="flex items-center gap-2">
          <Sparkles className="w-4 h-4 text-primary" />
          <h1 className="text-sm font-semibold text-foreground">Co-Pilot</h1>
          <Badge variant="outline" className="text-[10px] ml-2">Multi-Agent</Badge>
        </div>
        <p className="text-xs text-muted-foreground mt-0.5">Natural language interface to your entire data platform</p>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto overscroll-contain px-6 py-4 space-y-4" data-testid="chat-messages">
        {messages.length === 0 && !sessionHistory && (
          <div className="flex flex-col items-center justify-center h-full text-center">
            <div className="w-12 h-12 rounded-2xl bg-primary/10 flex items-center justify-center mb-4">
              <Sparkles className="w-6 h-6 text-primary" />
            </div>
            <h2 className="text-base font-semibold text-foreground">ZetaBridge Co-Pilot</h2>
            <p className="text-sm text-muted-foreground mt-1 max-w-md">
              Ask about your data catalog, run queries, check quality, or explore lineage.
            </p>
          </div>
        )}
        {messages.map((msg, i) => (
          <MessageBubble key={i} msg={msg} onSuggestionClick={(s) => handleSend(s)} />
        ))}
        {chatMutation.isPending && (
          <div className="flex gap-3">
            <div className="w-7 h-7 rounded-lg bg-primary/10 flex items-center justify-center shrink-0">
              <Loader2 className="w-3.5 h-3.5 text-primary animate-spin" />
            </div>
            <div className="bg-card border border-border/50 rounded-2xl rounded-bl-md px-4 py-3">
              <div className="flex items-center gap-2">
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse delay-100" />
                <div className="w-1.5 h-1.5 rounded-full bg-primary animate-pulse delay-200" />
                <span className="text-xs text-muted-foreground ml-1">Orchestrating agents…</span>
              </div>
            </div>
          </div>
        )}
        <div ref={messagesEndRef} />
      </div>

      {/* Input */}
      <div className="border-t border-border px-6 py-3 shrink-0">
        <div className="flex items-center gap-2 max-w-4xl mx-auto">
          <div className="flex-1 relative">
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about your data platform..."
              className="w-full bg-card border border-border/50 rounded-xl px-4 py-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 focus:border-primary/50"
              data-testid="copilot-input"
            />
          </div>
          <Button
            size="icon"
            className="h-11 w-11 rounded-xl"
            onClick={() => handleSend()}
            disabled={!input.trim() || chatMutation.isPending}
            data-testid="copilot-send"
          >
            <Send className="w-4 h-4" />
          </Button>
        </div>
      </div>
    </div>
  );
}
