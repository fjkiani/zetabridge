import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Database, Dna, FileText, Loader2, ExternalLink } from "lucide-react";

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined)?.replace(/\/$/, "") ?? "";
const API_KEY = (import.meta.env.VITE_ZETA_API_KEY as string | undefined) ?? "";

interface Row {
  id: string;
  name?: string;
  description?: string;
  total_gb?: number;
  n_files?: number;
  assay?: string;
  byte_verified?: boolean;
  download_status?: string;
  dataset_name?: string;
  kind: string;
}

async function cypher(query: string) {
  const res = await fetch(`${API_BASE}/api/graph/cypher`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(API_KEY ? { "X-Zeta-Api-Key": API_KEY } : {}),
    },
    body: JSON.stringify({ query }),
  });
  if (!res.ok) throw new Error(`${res.status}`);
  return res.json();
}

export default function Datasets() {
  const [synapse, setSynapse] = useState<Row[]>([]);
  const [ega, setEga] = useState<Row[]>([]);
  const [pubs, setPubs] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    (async () => {
      try {
        const [s, e, p] = await Promise.all([
          cypher("MATCH (n:ZetaDataset) RETURN n.id AS id, n.dataset_name AS dataset_name, n.name AS name, n.biological_relevance AS description, n.download_status AS download_status"),
          cypher("MATCH (n:Dataset) RETURN n.id AS id, n.description AS description, n.total_gb AS total_gb, n.n_files AS n_files, n.assay AS assay, n.byte_verified AS byte_verified"),
          cypher("MATCH (n:Publication) RETURN n.id AS id, n.name AS name"),
        ]);
        const rows = (r: any) => (r?.rows ?? r?.results ?? r ?? []);
        setSynapse(rows(s).map((x: any) => ({ ...x, kind: "synapse" })));
        setEga(rows(e).map((x: any) => ({ ...x, kind: "ega" })));
        setPubs(rows(p).map((x: any) => ({ ...x, kind: "publication" })));
      } catch (err) {
        setError(String(err));
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  if (loading)
    return (
      <div className="p-6 flex items-center gap-2 text-muted-foreground">
        <Loader2 className="h-5 w-5 animate-spin" /> Loading public datasets…
      </div>
    );

  return (
    <div className="p-6 space-y-6 max-w-6xl mx-auto">
      <div>
        <h1 className="text-2xl font-semibold flex items-center gap-2">
          <Database className="h-6 w-6 text-primary" /> Public Datasets & Repos
        </h1>
        <p className="text-sm text-muted-foreground mt-1">
          External datasets and publications backing the federated knowledge graph — Synapse
          SPECTRUM, EGA BriTROC, and supporting literature.
        </p>
      </div>

      {error && (
        <Card className="border-destructive">
          <CardContent className="pt-4 text-sm text-destructive">{error}</CardContent>
        </Card>
      )}

      <Section
        title="Synapse — SPECTRUM (ovarian HGSOC)"
        icon={<Dna className="h-5 w-5" />}
        count={synapse.length}
      >
        {synapse.map((r) => (
          <DatasetCard key={r.id} row={r} link={`https://www.synapse.org/#!Synapse:${r.id.replace("dataset:synapse:", "")}`} />
        ))}
      </Section>

      <Section title="EGA — BriTROC (HGSOC relapse)" icon={<Dna className="h-5 w-5" />} count={ega.length}>
        {ega.map((r) => (
          <DatasetCard key={r.id} row={r} link={`https://ega-archive.org/datasets/${r.id}`} />
        ))}
      </Section>

      <Section title="Publications" icon={<FileText className="h-5 w-5" />} count={pubs.length}>
        {pubs.map((r) => (
          <Card key={r.id}>
            <CardContent className="pt-4">
              <div className="font-medium text-sm">{r.name || r.id}</div>
              <div className="text-xs text-muted-foreground font-mono mt-1">{r.id}</div>
            </CardContent>
          </Card>
        ))}
      </Section>
    </div>
  );
}

function Section({
  title,
  icon,
  count,
  children,
}: {
  title: string;
  icon: React.ReactNode;
  count: number;
  children: React.ReactNode;
}) {
  return (
    <div className="space-y-3">
      <h2 className="text-lg font-semibold flex items-center gap-2">
        {icon} {title} <Badge variant="secondary">{count}</Badge>
      </h2>
      <div className="grid md:grid-cols-2 gap-3">{children}</div>
    </div>
  );
}

function DatasetCard({ row, link }: { row: Row; link: string }) {
  return (
    <Card>
      <CardContent className="pt-4 space-y-2">
        <div className="flex items-start justify-between gap-2">
          <div className="font-medium text-sm break-all">{row.dataset_name || row.name || row.id}</div>
          <a href={link} target="_blank" rel="noreferrer" className="text-primary shrink-0">
            <ExternalLink className="h-4 w-4" />
          </a>
        </div>
        {row.description && (
          <div className="text-xs text-muted-foreground line-clamp-2">{row.description}</div>
        )}
        <div className="flex flex-wrap gap-2 text-xs">
          <Badge variant="outline" className="font-mono">{row.id.replace("dataset:synapse:", "")}</Badge>
          {row.assay && <Badge variant="outline">{row.assay}</Badge>}
          {row.total_gb != null && <Badge variant="outline">{Number(row.total_gb).toFixed(1)} GB</Badge>}
          {row.n_files != null && <Badge variant="outline">{row.n_files} files</Badge>}
          {row.byte_verified && <Badge variant="default">byte-verified</Badge>}
          {row.download_status && <Badge variant="secondary">{row.download_status}</Badge>}
        </div>
      </CardContent>
    </Card>
  );
}
