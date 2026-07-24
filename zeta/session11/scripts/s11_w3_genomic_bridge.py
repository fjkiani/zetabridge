"""
Session 11 Worker 3 — Cross-Endpoint Genomic-AE Bridge + EGA Structural Analysis
Bridge scoring, shortest paths, EGA file outliers, longitudinal pairs, gene-drug target matches
"""
import json, os, time
from datetime import datetime
from collections import Counter, defaultdict
import numpy as np
import networkx as nx
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']

OUT_DIR = "/mnt/results/session11"
os.makedirs(OUT_DIR, exist_ok=True)
TS = datetime.utcnow().isoformat() + "Z"
SESSION = 11
MINT_PLANNER = "zeta_custodian_session11"

print(f"[W3] Loading KG... {TS}")
with open("/mnt/results/zeta_vault/kg/zeta_entities.json") as f:
    ents = json.load(f)
with open("/mnt/results/zeta_vault/kg/zeta_edges.json") as f:
    edges = json.load(f)

id_to_type = {e["id"]: e.get("type","?") for e in ents}
id_to_name = {e["id"]: e.get("name","") for e in ents}
print(f"[W3] Loaded: {len(ents)} entities, {len(edges)} edges")

def make_node(nid, ntype, name, **kwargs):
    return {"id": nid, "type": ntype, "name": name,
            "source_receipts": ["Session 11 W3 cross-endpoint analysis"],
            "verbatim_evidence": [], "cross_refs": {},
            "attributes": {k: v for k, v in kwargs.items()},
            "_session": SESSION, "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

def make_edge(src, tgt, rel, **attrs):
    return {"source": src, "target": tgt, "relation": rel,
            "attributes": attrs, "_session": SESSION,
            "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

new_nodes = []
new_edges = []

# ── 1. Genomic-AE bridge scoring ─────────────────────────────────────────────
print("\n[W3] Computing genomic-AE bridge scores...")
genomic_features = {e["id"]: e for e in ents if e.get("type") == "GenomicFeature"}
drug_ae_signals = {e["id"]: e for e in ents if e.get("type") == "DrugAESignal"}
cross_patterns = {e["id"]: e for e in ents if e.get("type") == "CrossTrialEscalationPattern"}
ae_terms = {e["id"]: e for e in ents if e.get("type") == "AdverseEventTerm"}

# Build AE term -> DrugAESignal RR map
ae_to_max_rr = defaultdict(float)
for sig in drug_ae_signals.values():
    ae_name = sig.get("ae_term","").upper()
    rr = sig.get("rate_ratio", 0)
    if rr < 999:
        ae_to_max_rr[ae_name] = max(ae_to_max_rr[ae_name], rr)

# Build AE term -> CrossTrialPattern consistency score map
ae_to_consistency = defaultdict(float)
for pat in cross_patterns.values():
    ae_name = pat.get("ae_term","").upper()
    ae_to_consistency[ae_name] = max(ae_to_consistency[ae_name], pat.get("consistency_score",0))

# Traverse genomic_ae_hypothesis edges
genomic_ae_edges = [e for e in edges if e["relation"] == "genomic_ae_hypothesis"]
print(f"[W3] genomic_ae_hypothesis edges: {len(genomic_ae_edges)}")

bridge_scores = []
for e in genomic_ae_edges:
    gf_id = e["source"]
    ae_id = e["target"]
    gf = genomic_features.get(gf_id, {})
    ae = ae_terms.get(ae_id, {})

    recurrence_pct = gf.get("attributes", {}).get("recurrence_pct", 0)
    gene = gf.get("attributes", {}).get("gene", "?")
    ae_name = ae.get("name","").upper()

    max_rr = ae_to_max_rr.get(ae_name, 0)
    consistency = ae_to_consistency.get(ae_name, 0)

    # Bridge strength: recurrence_pct * max(RR, consistency_score)
    signal_strength = max(max_rr, consistency)
    bridge_score = recurrence_pct * signal_strength / 100.0  # normalize recurrence to 0-1

    bridge_scores.append({
        "gf_id": gf_id, "ae_id": ae_id,
        "gene": gene, "ae_name": ae_name,
        "recurrence_pct": recurrence_pct,
        "max_rr": max_rr,
        "consistency_score": consistency,
        "signal_strength": signal_strength,
        "bridge_score": round(bridge_score, 4),
        "feature_type": gf.get("attributes",{}).get("feature_type","?"),
    })

bridge_scores.sort(key=lambda x: -x["bridge_score"])
print("[W3] Top 20 genomic-AE bridges:")
for b in bridge_scores[:20]:
    print(f"  {b['gene']} ({b['feature_type']}) -> {b['ae_name']}: "
          f"score={b['bridge_score']:.3f} (recur={b['recurrence_pct']}%, "
          f"RR={b['max_rr']:.1f}, consistency={b['consistency_score']:.1f})")

# Mint GenomicAEBridge nodes
for rank, b in enumerate(bridge_scores[:20]):
    gb_id = f"genomic_ae_bridge:s11:{b['gene']}:{b['ae_name'][:20].replace(' ','_')}"
    new_nodes.append(make_node(gb_id, "GenomicAEBridge",
        f"Bridge: {b['gene']} -> {b['ae_name'][:30]} (score={b['bridge_score']:.3f})",
        gene=b["gene"],
        ae_term=b["ae_name"],
        bridge_score=b["bridge_score"],
        recurrence_pct=b["recurrence_pct"],
        max_rr=b["max_rr"],
        consistency_score=b["consistency_score"],
        feature_type=b["feature_type"],
        rank=rank+1,
        interpretation=f"{b['gene']} mutated in {b['recurrence_pct']}% HGSOC samples; "
                       f"associated AE {b['ae_name']} has RR={b['max_rr']:.1f} in clinical trials"))
    new_edges.append(make_edge(b["gf_id"], gb_id, "has_bridge_score",
        bridge_score=b["bridge_score"], rank=rank+1))
    new_edges.append(make_edge(gb_id, b["ae_id"], "bridges_to_ae",
        bridge_score=b["bridge_score"]))

# ── 2. Shortest path analysis between endpoint anchors ───────────────────────
print("\n[W3] Shortest path analysis between endpoint anchors...")
# Build undirected graph for path analysis (exclude TrialPatient — too many)
EXCLUDE_TYPES = {"TrialPatient", "EGASample", "EGAFile", "Biospecimen"}
signal_nodes = {e["id"] for e in ents if e.get("type","?") not in EXCLUDE_TYPES}
G_path = nx.Graph()
for e in ents:
    if e["id"] in signal_nodes:
        G_path.add_node(e["id"], type=e.get("type","?"))
for e in edges:
    if e["source"] in signal_nodes and e["target"] in signal_nodes:
        G_path.add_edge(e["source"], e["target"], relation=e.get("relation","?"))

print(f"[W3] Path graph: {G_path.number_of_nodes()} nodes, {G_path.number_of_edges()} edges")

# Endpoint anchors
anchors = {
    "EGA_BriTROC": "vault:ega_britroc1",
    "SPECTRUM": "vault:synapse_msk_spectrum",
}
# Top 5 trials by degree
trial_nodes_list = [(e["id"], G_path.degree(e["id"])) for e in ents
                    if e.get("type")=="Trial" and e["id"] in G_path]
top_trials = sorted(trial_nodes_list, key=lambda x: -x[1])[:5]

path_node_counts = Counter()
path_results = []
for anchor_name, anchor_id in anchors.items():
    if anchor_id not in G_path:
        print(f"[W3] Anchor {anchor_id} not in path graph")
        continue
    for trial_id, deg in top_trials:
        try:
            path = nx.shortest_path(G_path, anchor_id, trial_id)
            path_results.append({"from": anchor_name, "to": trial_id,
                                  "length": len(path), "path": path})
            for n in path[1:-1]:  # intermediate nodes
                path_node_counts[n] += 1
        except nx.NetworkXNoPath:
            pass

print(f"[W3] Paths computed: {len(path_results)}")
print("[W3] Top intermediate nodes on shortest paths:")
for nid, cnt in path_node_counts.most_common(15):
    print(f"  {nid} | type={id_to_type.get(nid,'?')} | appears_on={cnt} paths")

# Mint FederationPathNode nodes
for rank, (nid, cnt) in enumerate(path_node_counts.most_common(15)):
    if cnt < 2:
        continue
    fp_id = f"federation_path:s11:{nid.replace(':','_')[:50]}"
    new_nodes.append(make_node(fp_id, "FederationPathNode",
        f"Federation bridge node: {id_to_name.get(nid,nid)[:40]}",
        target_node=nid,
        target_type=id_to_type.get(nid,"?"),
        n_paths=cnt,
        rank=rank+1,
        note="Appears on shortest paths between EGA/SPECTRUM endpoints and top trials"))
    new_edges.append(make_edge(nid, fp_id, "is_federation_bridge",
        n_paths=cnt, rank=rank+1))

# ── 3. EGA file size outlier analysis ────────────────────────────────────────
print("\n[W3] EGA file size outlier analysis...")
ega_files = [e for e in ents if e.get("type") == "EGAFile"]
file_sizes = np.array([e.get("filesize_bytes", 0) for e in ega_files])
size_mean, size_std = np.mean(file_sizes), np.std(file_sizes)
size_upper = size_mean + 2 * size_std
size_lower = max(0, size_mean - 2 * size_std)

print(f"[W3] EGA file sizes: mean={size_mean/1e9:.2f} GB, std={size_std/1e9:.2f} GB")
print(f"     Outlier thresholds: >{size_upper/1e9:.2f} GB or <{size_lower/1e9:.2f} GB")

large_files = [e for e in ega_files if e.get("filesize_bytes",0) > size_upper]
small_files = [e for e in ega_files if 0 < e.get("filesize_bytes",0) < size_lower]
print(f"[W3] Large file outliers: {len(large_files)}")
print(f"[W3] Small file outliers: {len(small_files)}")

for f in sorted(large_files, key=lambda x: -x.get("filesize_bytes",0))[:5]:
    print(f"  LARGE: {f['accession_id']} | {f.get('filesize_bytes',0)/1e9:.2f} GB | ext={f.get('extension','?')}")
for f in sorted(small_files, key=lambda x: x.get("filesize_bytes",0))[:5]:
    print(f"  SMALL: {f['accession_id']} | {f.get('filesize_bytes',0)/1e6:.1f} MB | ext={f.get('extension','?')}")

# Mint EGAFileOutlier nodes
for f in large_files + small_files:
    size_bytes = f.get("filesize_bytes",0)
    z = (size_bytes - size_mean) / size_std if size_std > 0 else 0
    fo_id = f"ega_file_outlier:s11:{f['accession_id']}"
    new_nodes.append(make_node(fo_id, "EGAFileOutlier",
        f"File size outlier: {f['accession_id']} ({size_bytes/1e9:.2f} GB)",
        file_id=f["id"],
        accession_id=f["accession_id"],
        filesize_bytes=size_bytes,
        filesize_gb=round(size_bytes/1e9,3),
        z_score=round(z,3),
        outlier_type="large" if z > 0 else "small",
        extension=f.get("extension","?"),
        checksum=f.get("checksum","")))
    new_edges.append(make_edge(f["id"], fo_id, "is_file_outlier",
        z_score=round(z,3), outlier_type="large" if z>0 else "small"))

# ── 4. Longitudinal pairs (EGA subjects with both diagnosis + relapse) ────────
print("\n[W3] Finding longitudinal pairs (diagnosis + relapse)...")
ega_samples = [e for e in ents if e.get("type") == "EGASample"]
subject_timepoints = defaultdict(set)
subject_sample_ids = defaultdict(list)
for s in ega_samples:
    subj = s.get("subject_id","")
    tp = s.get("timepoint","")
    if subj:
        subject_timepoints[subj].add(tp)
        subject_sample_ids[subj].append(s["id"])

longitudinal = {subj: tps for subj, tps in subject_timepoints.items()
                if "diagnosis" in tps and "post-relapse" in tps}
print(f"[W3] Subjects with both diagnosis + relapse samples: {len(longitudinal)}")
print(f"[W3] Diagnosis-only: {sum(1 for tps in subject_timepoints.values() if tps=={'diagnosis'})}")
print(f"[W3] Relapse-only: {sum(1 for tps in subject_timepoints.values() if tps=={'post-relapse'})}")

# Mint LongitudinalPair nodes
for subj, tps in list(longitudinal.items())[:294]:  # cap at 294 (all relapse subjects)
    lp_id = f"longitudinal_pair:ega:s11:subj_{subj}"
    sample_ids = subject_sample_ids[subj]
    new_nodes.append(make_node(lp_id, "LongitudinalPair",
        f"BriTROC longitudinal pair: subject {subj}",
        subject_id=subj,
        timepoints=json.dumps(list(tps)),
        n_samples=len(sample_ids),
        sample_ids=json.dumps(sample_ids[:4]),
        cohort="BriTROC-1",
        disease="HGSOC",
        note="Same subject with tumor samples at diagnosis and post-relapse"))
    for sid in sample_ids:
        new_edges.append(make_edge(sid, lp_id, "part_of_longitudinal_pair",
            subject_id=subj))

print(f"[W3] Minted {len(longitudinal)} LongitudinalPair nodes")

# ── 5. Gene-drug target match ─────────────────────────────────────────────────
print("\n[W3] Gene-drug target matching...")
drug_nodes_list = [e for e in ents if e.get("type") == "DrugIntervention"]
gf_nodes_list = [e for e in ents if e.get("type") == "GenomicFeature"]

matches = []
for gf in gf_nodes_list:
    gene = gf.get("attributes",{}).get("gene","")
    for drug in drug_nodes_list:
        target = drug.get("target","")
        # Check if gene appears in drug target string
        if gene and target and gene.upper() in target.upper():
            matches.append({
                "gene": gene, "drug": drug.get("name",""),
                "drug_id": drug["id"], "gf_id": gf["id"],
                "target": target,
                "recurrence_pct": gf.get("attributes",{}).get("recurrence_pct",0),
                "drug_class": drug.get("drug_class",""),
                "mechanism": drug.get("mechanism","")
            })

print(f"[W3] Gene-drug target matches: {len(matches)}")
for m in matches:
    print(f"  {m['gene']} (recur={m['recurrence_pct']}%) <-> {m['drug']} (target={m['target']})")

# Mint gene_drug_target_match edges
for m in matches:
    new_edges.append(make_edge(m["gf_id"], m["drug_id"], "gene_drug_target_match",
        gene=m["gene"],
        drug=m["drug"],
        target=m["target"],
        recurrence_pct=m["recurrence_pct"],
        drug_class=m["drug_class"],
        mechanism=m["mechanism"],
        note=f"{m['gene']} is a genomic target of {m['drug']} ({m['mechanism']})"))

# ── 5b. Biospecimen-EGASample reconciliation ─────────────────────────────────
print("\n[W3] Biospecimen-EGASample reconciliation...")
biospecimens = [e for e in ents if e.get("type") == "Biospecimen"]
# Biospecimen IDs are JBLAB-*, EGASample subject_ids are integers
# Both are BriTROC — reconcile by EGAF accession in Biospecimen cross_refs
def _extract_egaf(b):
    # attributes.egaf_id is the reliable source; fall back to cross_refs (dict or list)
    attrs = b.get("attributes", {})
    if isinstance(attrs, dict) and attrs.get("egaf_id"):
        return attrs["egaf_id"]
    xr = b.get("cross_refs", {})
    if isinstance(xr, dict):
        return xr.get("egaf_id", "")
    if isinstance(xr, list):
        for item in xr:
            if isinstance(item, dict) and item.get("egaf_id"):
                return item["egaf_id"]
            if isinstance(item, str) and item.startswith("EGAF"):
                return item
    return ""

bio_egaf = {}
for b in biospecimens:
    egaf = _extract_egaf(b)
    if egaf:
        bio_egaf[egaf] = b["id"]

# EGAFile accession_ids are EGAF*
ega_files_map = {e.get("accession_id",""): e["id"] for e in ega_files}

reconciled = 0
for egaf, bio_id in bio_egaf.items():
    if egaf in ega_files_map:
        file_id = ega_files_map[egaf]
        new_edges.append(make_edge(bio_id, file_id, "same_file_as",
            note="Biospecimen (Session 7/8 ingestion) and EGAFile (Session 10 ingestion) "
                 "represent the same BriTROC file — within-endpoint reconciliation only",
            egaf_id=egaf))
        reconciled += 1

print(f"[W3] Reconciled Biospecimen<->EGAFile pairs: {reconciled}")

# ── Figures ──────────────────────────────────────────────────────────────────
# 1. Genomic-AE bridge score ranking
fig, ax = plt.subplots(figsize=(12, 7))
top_bridges = bridge_scores[:20]
labels = [f"{b['gene']} → {b['ae_name'][:18]}" for b in top_bridges]
vals = [b["bridge_score"] for b in top_bridges]
colors = ['#FF9400' if b["recurrence_pct"] >= 50 else '#0279EE' for b in top_bridges]
bars = ax.barh(range(len(labels)), vals, color=colors)
ax.set_yticks(range(len(labels)))
ax.set_yticklabels(labels, fontsize=8)
ax.set_xlabel("Bridge Score (recurrence_pct × signal_strength / 100)")
ax.set_title("Top 20 Cross-Endpoint Genomic-AE Bridge Scores\n"
             "(Orange = gene recurrence ≥50% in HGSOC)")
ax.invert_yaxis()
ax.grid(True, alpha=0.3, axis='x')
# Add recurrence annotations
for i, b in enumerate(top_bridges):
    ax.text(vals[i]+0.001, i, f"  recur={b['recurrence_pct']}%", va='center', fontsize=7)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/w3_genomic_ae_bridge_scores.png", dpi=150, bbox_inches='tight')
plt.close()
print("[W3] Saved genomic_ae_bridge_scores.png")

# 2. EGA file size distribution
fig, ax = plt.subplots(figsize=(10, 5))
sizes_gb = file_sizes / 1e9
ax.hist(sizes_gb, bins=40, color='#0279EE', edgecolor='white', alpha=0.8)
ax.axvline(x=size_upper/1e9, color='#FF9400', linestyle='--', linewidth=2,
           label=f'Upper outlier threshold ({size_upper/1e9:.2f} GB)')
ax.axvline(x=size_lower/1e9, color='#75A025', linestyle='--', linewidth=2,
           label=f'Lower outlier threshold ({size_lower/1e9:.2f} GB)')
ax.axvline(x=size_mean/1e9, color='red', linestyle='-', linewidth=1.5,
           label=f'Mean ({size_mean/1e9:.2f} GB)')
ax.set_xlabel("File size (GB)")
ax.set_ylabel("Count")
ax.set_title(f"EGA BriTROC File Size Distribution (n={len(ega_files)} files)\n"
             f"Large outliers: {len(large_files)}, Small outliers: {len(small_files)}")
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/w3_ega_filesize_distribution.png", dpi=150, bbox_inches='tight')
plt.close()
print("[W3] Saved ega_filesize_distribution.png")

# ── Save ─────────────────────────────────────────────────────────────────────
results = {
    "worker": 3, "session": SESSION, "timestamp": TS,
    "top_bridges": bridge_scores[:20],
    "n_federation_path_nodes": len([n for n in new_nodes if n["type"]=="FederationPathNode"]),
    "top_path_intermediates": [(nid, cnt) for nid,cnt in path_node_counts.most_common(15)],
    "ega_file_outliers": {"n_large": len(large_files), "n_small": len(small_files),
                           "mean_gb": round(float(size_mean/1e9),3),
                           "std_gb": round(float(size_std/1e9),3)},
    "longitudinal_pairs": len(longitudinal),
    "gene_drug_matches": matches,
    "biospecimen_reconciled": reconciled,
    "new_nodes": new_nodes,
    "new_edges": new_edges,
}
def _np_default(o):
    import numpy as _np
    if isinstance(o, _np.bool_): return bool(o)
    if isinstance(o, _np.integer): return int(o)
    if isinstance(o, _np.floating): return float(o)
    if isinstance(o, _np.ndarray): return o.tolist()
    if isinstance(o, (set, frozenset)): return list(o)
    raise TypeError(f"Object of type {o.__class__.__name__} is not JSON serializable")
with open("/mnt/shared-workspace/shared/s11_w3_results.json", "w") as f:
    json.dump(results, f, indent=2, default=_np_default)
print(f"[W3] DONE. New nodes: {len(new_nodes)}, new edges: {len(new_edges)}")
