import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Activity, AlertTriangle, CheckCircle2, Loader2 } from "lucide-react";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
const API_KEY = (import.meta.env.VITE_ZETA_API_KEY as string | undefined) ?? "";

interface Cohort {
  cohort: string;
  n: number;
  cancer: string;
  features: string[];
  targets: string[];
}

interface EfficacyResult {
  cohort: string;
  analysis: string;
  model: string;
  n: number;
  events: number;
  cv_concordance_mean: number | null;
  cv_concordance_folds: number[];
  hazard_ratios: Record<string, number>;
  p_values: Record<string, number>;
  ph_test_p: Record<string, number>;
  ph_assumption_ok: boolean | null;
  discovery_only: boolean;
  note: string;
}

async function apiFetch(path: string, init?: RequestInit) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(API_KEY ? { "X-Zeta-Api-Key": API_KEY } : {}),
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    const txt = await res.text().catch(() => "");
    throw new Error(`${res.status}: ${txt.slice(0, 200)}`);
  }
  return res.json();
}

export default function Survival() {
  const [cohorts, setCohorts] = useState<Cohort[]>([]);
  const [cohort, setCohort] = useState<string>("britroc");
  const [analysis, setAnalysis] = useState<string>("os");
  const [features, setFeatures] = useState<string[]>([]);
  const [result, setResult] = useState<EfficacyResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [cohortsErr, setCohortsErr] = useState<string | null>(null);

  useEffect(() => {
    apiFetch("/api/capability/cohorts")
      .then((d) => setCohorts(d.cohorts ?? []))
      .catch((e) => setCohortsErr(String(e)));
  }, []);

  const active = cohorts.find((c) => c.cohort === cohort);

  useEffect(() => {
    if (active) setFeatures(active.features.slice(0, 3));
  }, [cohort, cohorts]);

  const toggleFeature = (f: string) =>
    setFeatures((prev) => (prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f]));

  const run = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const d = await apiFetch("/api/capability/efficacy", {
        method: "POST",
        body: JSON.stringify({ cohort, analysis, features, cv_folds: 5 }),
      });
      setResult(d);
    } catch (e) {
      setError(String(e));
    } finally {
      setLoading(false);
    }
  };

  const hrs = result ? Object.entries(result.hazard_ratios) : [];
  const maxLogHR = hrs.length
    ? Math.max(...hrs.map(([f]) => Math.abs(Math.log(result.hazard_ratios[f]))), 1)
    : 1;

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      <div>
        <h1 className="text-2xl font-semibold flex items-center gap-2">
          <Activity className="h-6 w-6 text-primary" /> Survival / Cox Models
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          Cox proportional-hazards and efficacy models across the federated cohorts.
          Discovery-only — single-cohort cross-validation, no external validation.
        </p>
      </div>

      {cohortsErr && (
        <Card className="border-destructive">
          <CardContent className="pt-4 text-sm text-destructive">
            Could not load cohorts: {cohortsErr}
          </CardContent>
        </Card>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Model configuration</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div>
            <div className="text-xs font-medium text-muted-foreground mb-2">Cohort</div>
            <div className="flex flex-wrap gap-2">
              {cohorts.map((c) => (
                <Button
                  key={c.cohort}
                  variant={cohort === c.cohort ? "default" : "outline"}
                  size="sm"
                  onClick={() => setCohort(c.cohort)}
                >
                  {c.cohort} <span className="ml-1 text-xs opacity-70">n={c.n}</span>
                </Button>
              ))}
            </div>
            {active && <div className="text-xs text-muted-foreground mt-1">{active.cancer}</div>}
          </div>

          <div>
            <div className="text-xs font-medium text-muted-foreground mb-2">Endpoint</div>
            <div className="flex gap-2">
              {(active?.targets ?? ["os", "pfs"]).map((t) => (
                <Button
                  key={t}
                  variant={analysis === t ? "default" : "outline"}
                  size="sm"
                  onClick={() => setAnalysis(t)}
                >
                  {t}
                </Button>
              ))}
            </div>
          </div>

          <div>
            <div className="text-xs font-medium text-muted-foreground mb-2">Features</div>
            <div className="flex flex-wrap gap-2">
              {(active?.features ?? []).map((f) => (
                <Badge
                  key={f}
                  variant={features.includes(f) ? "default" : "outline"}
                  className="cursor-pointer"
                  onClick={() => toggleFeature(f)}
                >
                  {f}
                </Badge>
              ))}
            </div>
          </div>

          <Button onClick={run} disabled={loading || features.length === 0}>
            {loading ? <Loader2 className="h-4 w-4 animate-spin mr-2" /> : null}
            Run Cox model
          </Button>
        </CardContent>
      </Card>

      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      {result && (
        <>
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            <Stat label="Patients" value={String(result.n)} />
            <Stat label="Events" value={String(result.events)} />
            <Stat
              label="CV concordance"
              value={result.cv_concordance_mean != null ? result.cv_concordance_mean.toFixed(3) : "—"}
            />
            <Stat
              label="PH assumption"
              value={
                result.ph_assumption_ok == null ? "—" : result.ph_assumption_ok ? "OK" : "Violated"
              }
              icon={
                result.ph_assumption_ok == null ? undefined : result.ph_assumption_ok ? (
                  <CheckCircle2 className="h-4 w-4 text-green-500" />
                ) : (
                  <AlertTriangle className="h-4 w-4 text-amber-500" />
                )
              }
            />
          </div>

          <Card>
            <CardHeader>
              <CardTitle className="text-base">Hazard ratios</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              {hrs.map(([f, hr]) => {
                const logHR = Math.log(hr);
                const width = (Math.abs(logHR) / maxLogHR) * 50;
                const p = result.p_values[f];
                return (
                  <div key={f} className="space-y-1">
                    <div className="flex justify-between text-sm">
                      <span className="font-mono">{f}</span>
                      <span>
                        HR {hr.toFixed(2)} <span className="text-muted-foreground">p={p?.toFixed(3)}</span>
                      </span>
                    </div>
                    <div className="relative h-5 bg-muted rounded">
                      <div className="absolute left-1/2 top-0 bottom-0 w-px bg-border" />
                      <div
                        className={`absolute top-1 bottom-1 rounded ${hr >= 1 ? "bg-red-400/70" : "bg-blue-400/70"}`}
                        style={{
                          left: hr >= 1 ? "50%" : `${50 - width}%`,
                          width: `${width}%`,
                        }}
                      />
                    </div>
                  </div>
                );
              })}
              <p className="text-xs text-muted-foreground pt-2">{result.note}</p>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function Stat({ label, value, icon }: { label: string; value: string; icon?: React.ReactNode }) {
  return (
    <Card>
      <CardContent className="pt-4">
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="text-xl font-semibold flex items-center gap-2 mt-1">
          {value} {icon}
        </div>
      </CardContent>
    </Card>
  );
}
