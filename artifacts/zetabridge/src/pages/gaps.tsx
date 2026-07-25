import { useEffect, useMemo, useState } from "react";
import { Link } from "wouter";
import { ShieldAlert, Loader2, Wrench, AlertTriangle, Database } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { getGaps, LAST_SIGNAL_SOURCE, apiConfigured, type GapItem } from "@/lib/signalsApi";

// group gaps by type for a scannable feed
const TYPE_LABEL: Record<string, string> = {
  data_harmonization: "Data harmonization",
  data_extraction: "Data extraction",
  structural: "Structural / schema",
  data_ingestion: "Un-ingested data",
  coverage: "Coverage",
};

function tierColor(tier: string | null): string {
  const t = (tier ?? "").toLowerCase();
  if (t.includes("0") || t.includes("absent") || t.includes("missing")) return "border-red-500/40 text-red-400";
  if (t.includes("1") || t.includes("partial")) return "border-amber-500/40 text-amber-400";
  return "border-muted-foreground/30 text-muted-foreground";
}

function GapCard({ g }: { g: GapItem }) {
  return (
    <Card className="hover:border-primary/40 transition-colors" data-testid={`gap-card-${g.slug}`}>
      <CardContent className="p-4 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="flex items-center gap-2 flex-wrap">
            <AlertTriangle className="w-4 h-4 text-amber-400 shrink-0" />
            {g.gap_type && (
              <Badge variant="secondary" className="text-[10px]">
                {TYPE_LABEL[g.gap_type] ?? g.gap_type}
              </Badge>
            )}
            {g.current_tier && (
              <Badge variant="outline" className={`text-[10px] ${tierColor(g.current_tier)}`}>
                tier: {g.current_tier}
              </Badge>
            )}
          </div>
        </div>
        <p className="text-sm font-medium leading-snug">{g.name}</p>
        {g.impact && <p className="text-xs text-muted-foreground leading-relaxed">{g.impact}</p>}
        {g.closes_gap && (
          <p className="text-[11px] text-muted-foreground leading-relaxed">
            <span className="text-foreground/80 font-medium">Fix path:</span> {g.closes_gap}
          </p>
        )}
        <div className="flex items-center justify-between pt-1">
          <code className="text-[10px] text-muted-foreground">{g.slug}</code>
          <Link href={`/connectors?gap=${encodeURIComponent(g.slug)}`}>
            <Button size="sm" variant="outline" className="h-7 text-[11px]">
              <Wrench className="w-3 h-3 mr-1.5" /> Draft a fix proposal
            </Button>
          </Link>
        </div>
      </CardContent>
    </Card>
  );
}

export default function Gaps() {
  const [gaps, setGaps] = useState<GapItem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    getGaps()
      .then((r) => setGaps(r.gaps))
      .catch(() => setGaps([]))
      .finally(() => setLoading(false));
  }, []);

  const live = LAST_SIGNAL_SOURCE === "live";

  return (
    <div className="p-6 max-w-[1200px] mx-auto space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="flex items-center gap-2">
            <ShieldAlert className="w-5 h-5 text-primary" />
            <h1 className="text-xl font-semibold">Blind Spots</h1>
            <Badge
              variant="outline"
              className={`text-[10px] ${live ? "border-emerald-500/40 text-emerald-400" : "border-amber-500/40 text-amber-400"}`}
            >
              {live ? "live graph" : "bundled snapshot"}
            </Badge>
          </div>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            What the data <span className="text-foreground">can't answer yet</span> — the concrete gaps
            pharma has been missing, quantified from the graph itself. Each is a monetizable data-completion
            opportunity a buyer's platform would close. Every gap is a real{" "}
            <code className="text-[10px]">KBGap</code>/<code className="text-[10px]">ZetaKBGap</code> node.
          </p>
        </div>
        {!apiConfigured() && (
          <Badge variant="outline" className="text-[10px] border-amber-500/40 text-amber-400">offline snapshot</Badge>
        )}
      </div>

      {loading ? (
        <div className="flex items-center justify-center py-24 text-muted-foreground">
          <Loader2 className="w-5 h-5 animate-spin mr-2" /> Auditing blind spots…
        </div>
      ) : (
        <>
          <div className="flex items-center gap-3 text-xs text-muted-foreground">
            <Database className="w-4 h-4" />
            {gaps.length} blind spots identified across the federation
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            {gaps.map((g) => (
              <GapCard key={g.slug} g={g} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
