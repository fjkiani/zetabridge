/**
 * EGA / BriTROC HGSOC dashboard (Session 16) — route `/ega`.
 *
 * The curated, LIVE view of the C_EGA (BriTROC-1 high-grade serous ovarian
 * cancer) work loaded into the production Neo4j Aura graph: the 8-dataset
 * catalog, the subject x assay multimodal matrix, the pullable inventory and
 * byte-verification status, and a per-subject Subject->Sample->File lineage
 * drill-down.
 *
 * Everything on this page is read LIVE from the graph via `/api/graph/cypher`
 * (read-only, guarded server-side). There is NO snapshot fallback here by
 * design: if the backend is unreachable we say so honestly rather than showing
 * stale numbers. All EGA nodes are scoped by the `endpoint = 'C_EGA'` property.
 */

import { useCallback, useEffect, useState } from "react";
import {
  Dna,
  Loader2,
  RefreshCw,
  ShieldCheck,
  ShieldAlert,
  Database,
  Layers,
  Users,
  FileArchive,
  HardDrive,
  GitBranch,
  Lightbulb,
  AlertTriangle,
  CheckCircle2,
} from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { runCypher, apiConfigured, ENDPOINT_META } from "@/lib/graphApi";
import { humanBytes } from "@/lib/sourcesApi";

const EGA = ENDPOINT_META.C_EGA.color; // purple accent

// --- read-only Cypher (scoped to endpoint='C_EGA'; verified live) ----------
const Q_CATALOG = `MATCH (d:Dataset {endpoint:'C_EGA'})
RETURN d.id AS id, d.assay AS assay, d.file_type AS file_type, d.n_files AS n_files,
       d.total_gb AS total_gb, d.total_bytes AS total_bytes, d.size_tier AS size_tier,
       d.byte_verified AS byte_verified
ORDER BY d.total_gb DESC`;
const Q_SUBJECTS = `MATCH (s:Subject {endpoint:'C_EGA'})
RETURN count(s) AS n, sum(CASE WHEN s.multimodal THEN 1 ELSE 0 END) AS multimodal,
       max(s.n_assays) AS max_assays`;
const Q_FILES = `MATCH (f:File {endpoint:'C_EGA'})
RETURN count(f) AS n, sum(f.filesize) AS bytes,
       sum(CASE WHEN f.pullable THEN 1 ELSE 0 END) AS pullable`;
const Q_SAMPLES = `MATCH (sm:Sample {endpoint:'C_EGA'})
RETURN sm.sample_class AS klass, count(*) AS n ORDER BY n DESC`;
const Q_PROFILED = `MATCH (:Subject {endpoint:'C_EGA'})-[:PROFILED_BY]->(d:Dataset)
RETURN d.assay AS assay, count(*) AS n_subjects ORDER BY n_subjects DESC`;
const Q_NASSAYS = `MATCH (s:Subject {endpoint:'C_EGA'})
RETURN s.n_assays AS n_assays, count(s) AS n_subjects ORDER BY n_assays`;
const Q_LINEAGE_SAMPLES = `MATCH (s:Subject {id:$sid})-[:HAS_SAMPLE]->(sm:Sample)
OPTIONAL MATCH (sm)-[:HAS_FILE]->(f:File)
RETURN sm.id AS sample, sm.sample_class AS klass, count(f) AS n_files
ORDER BY n_files DESC, sample`;
const Q_LINEAGE_ASSAYS = `MATCH (s:Subject {id:$sid})-[:PROFILED_BY]->(d:Dataset)
RETURN d.id AS dataset, d.assay AS assay ORDER BY assay`;

type Row = Record<string, any>;

const TIER_COLOR: Record<string, string> = {
  small: "text-slate-400 border-slate-400/40",
  medium: "text-sky-400 border-sky-400/40",
  large: "text-amber-400 border-amber-400/40",
  huge: "text-rose-400 border-rose-400/40",
};

function num(v: any): number {
  const n = typeof v === "number" ? v : Number(v);
  return Number.isFinite(n) ? n : 0;
}

function StatCard({
  icon: Icon,
  label,
  value,
  sub,
}: {
  icon: typeof Database;
  label: string;
  value: string;
  sub?: string;
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-center gap-2 text-muted-foreground">
          <span
            className="w-7 h-7 rounded-md flex items-center justify-center shrink-0"
            style={{ backgroundColor: `${EGA}22` }}
          >
            <Icon className="w-4 h-4" style={{ color: EGA }} />
          </span>
          <span className="text-[11px] uppercase tracking-wide">{label}</span>
        </div>
        <div className="mt-2 text-2xl font-bold tabular-nums">{value}</div>
        {sub && <div className="text-[11px] text-muted-foreground mt-0.5">{sub}</div>}
      </CardContent>
    </Card>
  );
}

export default function EgaBritroc() {
  const configured = apiConfigured();
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<Row[]>([]);
  const [subjects, setSubjects] = useState<Row | null>(null);
  const [files, setFiles] = useState<Row | null>(null);
  const [samples, setSamples] = useState<Row[]>([]);
  const [profiled, setProfiled] = useState<Row[]>([]);
  const [nAssays, setNAssays] = useState<Row[]>([]);

  const [sid, setSid] = useState("PATIENT_INT:79");
  const [subjInput, setSubjInput] = useState("PATIENT_INT:79");
  const [linLoading, setLinLoading] = useState(false);
  const [linErr, setLinErr] = useState<string | null>(null);
  const [linSamples, setLinSamples] = useState<Row[]>([]);
  const [linAssays, setLinAssays] = useState<Row[]>([]);

  const loadAll = useCallback(async () => {
    if (!configured) return;
    setLoading(true);
    setErr(null);
    try {
      const [cat, subj, fil, smp, prof, na] = await Promise.all([
        runCypher(Q_CATALOG),
        runCypher(Q_SUBJECTS),
        runCypher(Q_FILES),
        runCypher(Q_SAMPLES),
        runCypher(Q_PROFILED),
        runCypher(Q_NASSAYS),
      ]);
      setCatalog(cat.rows);
      setSubjects(subj.rows[0] ?? null);
      setFiles(fil.rows[0] ?? null);
      setSamples(smp.rows);
      setProfiled(prof.rows);
      setNAssays(na.rows);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [configured]);

  const loadLineage = useCallback(async (subjectId: string) => {
    setLinLoading(true);
    setLinErr(null);
    try {
      const [sm, asy] = await Promise.all([
        runCypher(Q_LINEAGE_SAMPLES, { sid: subjectId }, 500),
        runCypher(Q_LINEAGE_ASSAYS, { sid: subjectId }),
      ]);
      setLinSamples(sm.rows);
      setLinAssays(asy.rows);
    } catch (e) {
      setLinErr(e instanceof Error ? e.message : String(e));
    } finally {
      setLinLoading(false);
    }
  }, []);

  useEffect(() => {
    loadAll();
    loadLineage("PATIENT_INT:79");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // derived summary
  const nDatasets = catalog.length;
  const verified = catalog.filter((d) => d.byte_verified === true);
  const totalGb = catalog.reduce((a, d) => a + num(d.total_gb), 0);
  const nonHugeGb = catalog
    .filter((d) => d.size_tier !== "huge")
    .reduce((a, d) => a + num(d.total_gb), 0);

  function submitSubject(e: React.FormEvent) {
    e.preventDefault();
    const v = subjInput.trim();
    if (!v) return;
    setSid(v);
    loadLineage(v);
  }

  return (
    <div className="p-6 max-w-7xl mx-auto space-y-6" data-testid="page-ega">
      {/* header */}
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold flex items-center gap-2">
            <Dna className="w-6 h-6" style={{ color: EGA }} /> EGA / BriTROC HGSOC
          </h1>
          <p className="text-sm text-muted-foreground mt-1 max-w-2xl">
            The C_EGA slice loaded into the production graph: the BriTROC-1 high-grade serous
            ovarian cancer copy-number cohort (Macintyre et al., Nat Genet 2018). Eight controlled-
            access datasets, the subject x assay multimodal matrix, the pullable inventory and its
            byte-verification status — read live from the graph. Controlled-access patient sequence
            bytes are never fetched here; this is catalog + provenance only.
          </p>
        </div>
        <div className="flex items-center gap-2">
          {!loading && !err && configured && (
            <Badge variant="outline" className="gap-1 text-emerald-400 border-emerald-400/40">
              <CheckCircle2 className="w-3 h-3" /> live
            </Badge>
          )}
          <Button onClick={loadAll} disabled={loading || !configured} variant="outline" className="gap-2">
            {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <RefreshCw className="w-4 h-4" />}
            Refresh
          </Button>
        </div>
      </div>

      {!configured && (
        <Card className="border-amber-400/40">
          <CardContent className="py-4 flex items-start gap-3">
            <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
            <div className="text-sm">
              <p className="font-semibold">No backend configured.</p>
              <p className="text-muted-foreground">
                Set <span className="font-mono">VITE_API_BASE</span> (and{" "}
                <span className="font-mono">VITE_ZETA_API_KEY</span>) to point this dashboard at the
                Zeta Bridge API. This page is live-only and does not fall back to a snapshot.
              </p>
            </div>
          </CardContent>
        </Card>
      )}

      {err && (
        <Card className="border-amber-400/40">
          <CardContent className="py-3 text-sm text-amber-300 flex items-center gap-2">
            <AlertTriangle className="w-4 h-4 shrink-0" /> Live graph unavailable — {err}
          </CardContent>
        </Card>
      )}

      {/* summary stat grid */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <StatCard icon={Database} label="Datasets" value={String(nDatasets)} sub="controlled-access" />
        <StatCard
          icon={Users}
          label="Subjects"
          value={subjects ? String(num(subjects.n)) : "—"}
          sub={subjects ? `${num(subjects.multimodal)} multimodal (≥3 assays)` : undefined}
        />
        <StatCard
          icon={FileArchive}
          label="Files"
          value={files ? num(files.n).toLocaleString() : "—"}
          sub={files ? `${num(files.pullable).toLocaleString()} pullable` : undefined}
        />
        <StatCard
          icon={HardDrive}
          label="Catalogued size"
          value={files ? humanBytes(num(files.bytes)) : "—"}
          sub={`${nonHugeGb.toFixed(0)} GB excl. huge`}
        />
        <StatCard
          icon={Layers}
          label="Max assays / subject"
          value={subjects ? String(num(subjects.max_assays)) : "—"}
          sub="deep multimodal"
        />
        <StatCard
          icon={ShieldCheck}
          label="Byte-verified"
          value={`${verified.length}/${nDatasets || 8}`}
          sub="datasets with fetched-byte proof"
        />
      </div>

      {/* 8-dataset catalog */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <Database className="w-4 h-4" style={{ color: EGA }} /> Dataset catalog
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="overflow-auto rounded-md border border-border/50">
            <table className="w-full text-[12px]">
              <thead className="sticky top-0 bg-muted/60 backdrop-blur">
                <tr>
                  {["Accession", "Assay", "Type", "Files", "Size", "Tier", "Byte-verified"].map((c) => (
                    <th key={c} className="text-left px-2.5 py-2 font-semibold whitespace-nowrap">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {catalog.map((d) => (
                  <tr key={String(d.id)} className="border-t border-border/40 hover:bg-muted/30">
                    <td className="px-2.5 py-1.5 font-mono">{String(d.id)}</td>
                    <td className="px-2.5 py-1.5">{String(d.assay)}</td>
                    <td className="px-2.5 py-1.5 font-mono text-muted-foreground">{String(d.file_type)}</td>
                    <td className="px-2.5 py-1.5 tabular-nums">{num(d.n_files).toLocaleString()}</td>
                    <td className="px-2.5 py-1.5 tabular-nums">{humanBytes(num(d.total_bytes))}</td>
                    <td className="px-2.5 py-1.5">
                      <Badge variant="outline" className={TIER_COLOR[String(d.size_tier)] ?? ""}>
                        {String(d.size_tier)}
                      </Badge>
                    </td>
                    <td className="px-2.5 py-1.5">
                      {d.byte_verified === true ? (
                        <span className="inline-flex items-center gap-1 text-emerald-400">
                          <ShieldCheck className="w-3.5 h-3.5" /> proof
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 text-muted-foreground">
                          <ShieldAlert className="w-3.5 h-3.5" /> metadata only
                        </span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-muted-foreground mt-2">
            {nDatasets} datasets · {totalGb.toFixed(1)} GB catalogued ({(totalGb / 1000).toFixed(2)} TB).
            Byte-verified = at least one file confirmed by a live HTTP range probe (BGZF magic), not a
            full download.
          </p>
        </CardContent>
      </Card>

      {/* multimodal matrix + assay coverage + sample class */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Layers className="w-4 h-4" style={{ color: EGA }} /> Subjects by assay count
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {nAssays.map((r) => {
              const n = num(r.n_subjects);
              const max = Math.max(...nAssays.map((x) => num(x.n_subjects)), 1);
              return (
                <div key={String(r.n_assays)} className="flex items-center gap-2 text-xs">
                  <span className="w-16 shrink-0 text-muted-foreground">
                    {num(r.n_assays)} assay{num(r.n_assays) === 1 ? "" : "s"}
                  </span>
                  <div className="flex-1 h-4 bg-muted/40 rounded-sm overflow-hidden">
                    <div className="h-full rounded-sm" style={{ width: `${(n / max) * 100}%`, backgroundColor: EGA }} />
                  </div>
                  <span className="w-10 text-right tabular-nums">{n}</span>
                </div>
              );
            })}
            <p className="text-[11px] text-muted-foreground pt-1">
              Multimodal (≥3 assays) subjects are the cross-assay analysis core.
            </p>
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <GitBranch className="w-4 h-4" style={{ color: EGA }} /> Assay coverage (subjects)
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5">
            {profiled.map((r) => {
              const n = num(r.n_subjects);
              const max = Math.max(...profiled.map((x) => num(x.n_subjects)), 1);
              return (
                <div key={String(r.assay)} className="flex items-center gap-2 text-xs">
                  <span className="w-28 shrink-0 text-muted-foreground truncate" title={String(r.assay)}>
                    {String(r.assay)}
                  </span>
                  <div className="flex-1 h-4 bg-muted/40 rounded-sm overflow-hidden">
                    <div className="h-full rounded-sm" style={{ width: `${(n / max) * 100}%`, backgroundColor: EGA }} />
                  </div>
                  <span className="w-10 text-right tabular-nums">{n}</span>
                </div>
              );
            })}
          </CardContent>
        </Card>

        <Card>
          <CardHeader className="pb-2">
            <CardTitle className="text-sm flex items-center gap-2">
              <Lightbulb className="w-4 h-4" style={{ color: EGA }} /> Coverage &amp; opportunities
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-2 text-xs">
            <div className="flex items-start gap-2">
              <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400 mt-0.5 shrink-0" />
              <span>
                Sample classes: {samples.map((s) => `${num(s.n)} ${String(s.klass)}`).join(" · ") || "—"}.
              </span>
            </div>
            <div className="flex items-start gap-2">
              <ShieldAlert className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" />
              <span>
                Byte-verification: {verified.length}/{nDatasets || 8} datasets carry a fetched-byte
                proof ({verified.map((d) => String(d.id)).join(", ") || "none"}). The remaining
                datasets are catalogued from EGA metadata and can be range-probed next.
              </span>
            </div>
            <div className="flex items-start gap-2">
              <Lightbulb className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" />
              <span>
                Semantic vault currently indexes EGA specimen records only — embedding the full
                8,377-file / 8-dataset catalog into the vault would let RAG answer EGA file/dataset
                questions.
              </span>
            </div>
            <div className="flex items-start gap-2">
              <Lightbulb className="w-3.5 h-3.5 text-amber-400 mt-0.5 shrink-0" />
              <span>
                Deep-WGS tumor/normal pairing is computed in the ETL but not yet materialized as
                graph edges — a pending enhancement for paired somatic analysis.
              </span>
            </div>
          </CardContent>
        </Card>
      </div>

      {/* per-subject lineage drill-down */}
      <Card>
        <CardHeader className="pb-2">
          <CardTitle className="text-sm flex items-center gap-2">
            <GitBranch className="w-4 h-4" style={{ color: EGA }} /> Subject lineage — Subject → Sample → File
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          <form onSubmit={submitSubject} className="flex gap-2 items-center">
            <Input
              value={subjInput}
              onChange={(e) => setSubjInput(e.target.value)}
              placeholder="Subject id, e.g. PATIENT_INT:79 or JBLAB:JBLAB-4004"
              className="h-8 text-xs font-mono max-w-md"
              data-testid="input-subject"
            />
            <Button size="sm" type="submit" disabled={linLoading} className="gap-1 shrink-0">
              {linLoading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <GitBranch className="w-3.5 h-3.5" />}
              Trace
            </Button>
            <span className="text-[11px] text-muted-foreground font-mono">{sid}</span>
          </form>

          {linErr && <p className="text-[11px] text-amber-400">{linErr}</p>}

          {linAssays.length > 0 && (
            <div className="flex flex-wrap gap-1.5">
              {linAssays.map((a) => (
                <Badge key={String(a.dataset)} variant="outline" className="font-mono text-[10px]" title={String(a.dataset)}>
                  {String(a.assay)}
                </Badge>
              ))}
            </div>
          )}

          <div className="overflow-auto max-h-80 rounded-md border border-border/50">
            <table className="w-full text-[11px]">
              <thead className="sticky top-0 bg-muted/60 backdrop-blur">
                <tr>
                  <th className="text-left px-2.5 py-1.5 font-semibold">Sample</th>
                  <th className="text-left px-2.5 py-1.5 font-semibold">Class</th>
                  <th className="text-left px-2.5 py-1.5 font-semibold">Files</th>
                </tr>
              </thead>
              <tbody>
                {linSamples.map((s) => (
                  <tr key={String(s.sample)} className="border-t border-border/40">
                    <td className="px-2.5 py-1 font-mono">{String(s.sample)}</td>
                    <td className="px-2.5 py-1">
                      <Badge
                        variant="outline"
                        className={
                          String(s.klass) === "normal"
                            ? "text-sky-400 border-sky-400/40"
                            : String(s.klass) === "tumor"
                              ? "text-rose-400 border-rose-400/40"
                              : "text-muted-foreground"
                        }
                      >
                        {String(s.klass)}
                      </Badge>
                    </td>
                    <td className="px-2.5 py-1 tabular-nums">{num(s.n_files)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[11px] text-muted-foreground">
            {linSamples.length} sample{linSamples.length === 1 ? "" : "s"} ·{" "}
            {linSamples.reduce((a, s) => a + num(s.n_files), 0)} files · {linAssays.length} assays (live)
          </p>
        </CardContent>
      </Card>
    </div>
  );
}
