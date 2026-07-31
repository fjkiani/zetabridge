import { useEffect, useState } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Layers as LayersIcon, ChevronRight } from "lucide-react";

const API_BASE = import.meta.env.VITE_API_BASE || "";
const API_KEY = import.meta.env.VITE_ZETA_API_KEY || "";

interface LayerInfo { layer: string; label: string; signals: number; detail: string; }
interface DatasetLayers { id: string; name: string; source: string; maxLayer: string; layers: LayerInfo[]; }

const LAYER_DEFS: Record<string, DatasetLayers> = {
  A_MSK: { id: "A_MSK", name: "SPECTRUM (MSK/Synapse)", source: "vault:synapse_msk_spectrum", maxLayer: "L4",
    layers: [
      { layer: "L1", label: "Metadata", signals: 1, detail: "39-patient HGSOC cohort, subtype labels" },
      { layer: "L2", label: "Summary", signals: 50, detail: "ssGSEA pathway scores (50 Hallmark), mutational signatures" },
      { layer: "L3", label: "Derived signals", signals: 11, detail: "Prognostic pathways (FATTY_ACID_METABOLISM HR 8.5), is_fbi, CN bins" },
      { layer: "L4", label: "Raw regen", signals: 200, detail: "Pseudobulk 32,175 genes x 39 patients, per-patient gene Cox" },
    ]},
  B_SAS: { id: "B_SAS", name: "PDS (SAS)", source: "vault:sas_pds", maxLayer: "L3",
    layers: [
      { layer: "L1", label: "Metadata", signals: 1, detail: "94 trials, 402 tables, 12,069 patients" },
      { layer: "L2", label: "Summary", signals: 6, detail: "Per-trial survival endpoints (units-normalized)" },
      { layer: "L3", label: "Derived signals", signals: 3, detail: "Age/ECOG/KRAS covariates; ECOG HR 1.632 (C-index 0.604)" },
    ]},
  C_EGA: { id: "C_EGA", name: "BriTROC (EGA)", source: "vault:ega_britroc1", maxLayer: "L3",
    layers: [
      { layer: "L1", label: "Metadata", signals: 1, detail: "273-patient HGSOC relapse cohort" },
      { layer: "L2", label: "Summary", signals: 87, detail: "CN-stamped specimens (LST/FGA/ploidy/purity)" },
      { layer: "L3", label: "Derived signals", signals: 4, detail: "Treatment lines, platinum resistance (HR 1.751), CN signatures" },
    ]},
  D_ARGO: { id: "D_ARGO", name: "POG570 (ICGC ARGO)", source: "vault:argo_pogca", maxLayer: "L3",
    layers: [
      { layer: "L1", label: "Metadata", signals: 1, detail: "570-patient pan-cancer cohort" },
      { layer: "L2", label: "Summary", signals: 7, detail: "CIBERSORT immune fractions" },
      { layer: "L3", label: "Derived signals", signals: 2, detail: "ICI response + CD8 protective (HR 0.756) in 76 ICI patients" },
    ]},
  CRC: { id: "CRC", name: "Colorectal (cBioPortal + SAS PDS)", source: "vault:cbioportal_crc", maxLayer: "L3",
    layers: [
      { layer: "L1", label: "Metadata", signals: 2, detail: "4,025 cBioPortal + 4,028 SAS PDS mCRC patients" },
      { layer: "L2", label: "Summary", signals: 5, detail: "TMB / MSI / RAS / BRAF / ECOG across 5+5 cohorts" },
      { layer: "L3", label: "Derived signals", signals: 8, detail: "BRAF + RAS externally validated; TMB≥28 protective (HR 0.726); treat×RAS predictive (HR 1.258, discovery); TCGA immune proxy validated" },
      { layer: "L3", label: "Immune→response frame", signals: 5, detail: "Predictive frame literature-grounded: MAYA spatial cytotoxic T cells + post-TMZ TMB→PFS (Nat Commun 2026); BRAF-trial immune-program induction→outcome (Nat Med 2023); pre-treatment proxy honest null. treat×RAS stays discovery-level (no randomized external cohort)." },
    ]},
};

export default function Layers() {
  const [signalCount, setSignalCount] = useState<number | null>(null);
  useEffect(() => {
    fetch(`${API_BASE}/api/graph/cypher`, { method: "POST", headers: { "Content-Type": "application/json", "X-Zeta-Api-Key": API_KEY },
      body: JSON.stringify({ cypher: "MATCH (n:Signal) RETURN count(n) AS c" }) })
      .then(r => r.json()).then(d => setSignalCount(d?.results?.[0]?.c ?? null)).catch(() => {});
  }, []);
  return (
    <div className="p-6 space-y-6">
      <div className="flex items-center justify-between">
        <div><h1 className="text-2xl font-bold flex items-center gap-2"><LayersIcon className="h-6 w-6" /> Recursive Extraction Layers</h1>
        <p className="text-muted-foreground">Depth of signal extraction per dataset — L1 metadata to L4 raw regeneration</p></div>
        {signalCount !== null && <Badge variant="secondary" className="text-sm">{signalCount} signal nodes in graph</Badge>}
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        {Object.values(LAYER_DEFS).map(ds => (
          <Card key={ds.id}>
            <CardHeader className="pb-2">
              <div className="flex items-center justify-between">
                <CardTitle className="text-lg">{ds.name}</CardTitle>
                <Badge>{ds.maxLayer}</Badge>
              </div>
            </CardHeader>
            <CardContent className="space-y-2">
              {ds.layers.map(l => (
                <div key={l.layer} className="flex items-start gap-2 rounded border p-2">
                  <Badge variant="outline" className="mt-0.5 shrink-0">{l.layer}</Badge>
                  <div className="min-w-0">
                    <div className="flex items-center gap-1 text-sm font-medium">{l.label}<ChevronRight className="h-3 w-3" /><span className="text-muted-foreground">{l.signals} signals</span></div>
                    <p className="text-xs text-muted-foreground">{l.detail}</p>
                  </div>
                </div>
              ))}
            </CardContent>
          </Card>
        ))}
      </div>
    </div>
  );
}
