"""
Session 11 Worker 1 — AE Signal Outlier Detection (Statistical + Graph)
Z-score/IQR outliers, AE co-occurrence network, drug toxicity similarity
"""
import json, os, time
from datetime import datetime
from collections import Counter, defaultdict
import numpy as np
import scipy.stats as stats
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

print(f"[W1] Loading KG... {TS}")
with open("/mnt/results/zeta_vault/kg/zeta_entities.json") as f:
    ents = json.load(f)
with open("/mnt/results/zeta_vault/kg/zeta_edges.json") as f:
    edges = json.load(f)

id_to_type = {e["id"]: e.get("type","?") for e in ents}
id_to_name = {e["id"]: e.get("name","") for e in ents}
print(f"[W1] Loaded: {len(ents)} entities, {len(edges)} edges")

def make_node(nid, ntype, name, **kwargs):
    return {"id": nid, "type": ntype, "name": name,
            "source_receipts": ["Session 11 W1 AE outlier analysis"],
            "verbatim_evidence": [], "cross_refs": {},
            "attributes": {k: v for k, v in kwargs.items()},
            "_session": SESSION, "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

def make_edge(src, tgt, rel, **attrs):
    return {"source": src, "target": tgt, "relation": rel,
            "attributes": attrs, "_session": SESSION,
            "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

new_nodes = []
new_edges = []

# ── 1. DrugAESignal outlier detection ───────────────────────────────────────
print("[W1] Analyzing DrugAESignal outliers...")
drug_ae = [e for e in ents if e.get("type") == "DrugAESignal"]
real_signals = [e for e in drug_ae if e.get("rate_ratio", 0) < 999]
capped_signals = [e for e in drug_ae if e.get("rate_ratio", 0) >= 999]

rrs = np.array([e["rate_ratio"] for e in real_signals])
rr_mean, rr_std = npr_mean, rr_std = np.mean(rrs), np.std(rrs)
rr_q1, rr_q3 = np.percentile(rrs, 25), np.percentile(rrs, 75)
rr_iqr = rr_q3 - rr_q1
rr_upper_iqr = rr_q3 + 1.5 * rr_iqr

print(f"[W1] DrugAESignal real RR: n={len(real_signals)}, mean={rr_mean:.1f}, std={rr_std:.1f}")
print(f"     IQR upper fence: {rr_upper_iqr:.1f}, Z>2.5 threshold: {rr_mean+2.5*rr_std:.1f}")

outlier_signals = []
for sig in real_signals:
    rr = sig["rate_ratio"]
    z = (rr - rr_mean) / rr_std if rr_std > 0 else 0
    iqr_flag = rr > rr_upper_iqr
    outlier_signals.append({**sig, "z_score": round(z, 3), "iqr_flag": iqr_flag})

outlier_signals.sort(key=lambda x: -x["z_score"])
print(f"[W1] Z>2.5 outliers: {sum(1 for s in outlier_signals if s['z_score']>2.5)}")
print(f"[W1] IQR outliers: {sum(1 for s in outlier_signals if s['iqr_flag'])}")
print("[W1] Top 15 outlier signals:")
for s in outlier_signals[:15]:
    print(f"  {s['ae_term']} | drug={s['exp_drug']} | RR={s['rate_ratio']:.1f} | z={s['z_score']:.2f}")

# Cross-trial replication score for each AE term
ae_term_trials = defaultdict(set)
ae_term_drugs = defaultdict(set)
for sig in drug_ae:
    ae_term_trials[sig["ae_term"]].add(sig["trial_id"])
    ae_term_drugs[sig["ae_term"]].add(sig.get("exp_drug","?"))

print("\n[W1] AE terms replicated in >=2 trials:")
replicated = sorted([(t, len(trials), list(trials)) for t, trials in ae_term_trials.items() if len(trials)>=2],
                    key=lambda x: -x[1])
for term, n_trials, trials in replicated[:15]:
    print(f"  {term}: {n_trials} trials, drugs={list(ae_term_drugs[term])}")

# Mint AEOutlierSignal nodes
for rank, sig in enumerate(outlier_signals[:30]):
    if sig["z_score"] < 1.5 and not sig["iqr_flag"]:
        continue
    oid = f"ae_outlier:s11:{sig['id'].replace(':','_')[:60]}"
    replication = len(ae_term_trials.get(sig["ae_term"], set()))
    new_nodes.append(make_node(oid, "AEOutlierSignal",
        f"Outlier: {sig['ae_term']} ({sig['exp_drug']}) RR={sig['rate_ratio']:.1f}",
        source_signal_id=sig["id"],
        ae_term=sig["ae_term"],
        drug=sig["exp_drug"],
        rate_ratio=sig["rate_ratio"],
        z_score=sig["z_score"],
        iqr_flag=sig["iqr_flag"],
        exp_rate=sig["exp_rate"],
        ctrl_rate=sig["ctrl_rate"],
        replication_count=replication,
        confirmed=(replication >= 2),
        trial_id=sig["trial_id"]))
    new_edges.append(make_edge(sig["id"], oid, "is_outlier_signal",
        z_score=sig["z_score"], rank=rank+1))

# Capped signals (ctrl_rate=0, absolute drug-exclusive AEs)
print(f"\n[W1] Capped signals (RR=999, ctrl_rate=0): {len(capped_signals)}")
for sig in sorted(capped_signals, key=lambda x: -x.get("exp_rate",0))[:10]:
    print(f"  {sig['ae_term']} | drug={sig['exp_drug']} | exp_rate={sig['exp_rate']:.3f}")

for sig in capped_signals:
    oid = f"ae_outlier:capped:s11:{sig['id'].replace(':','_')[:60]}"
    new_nodes.append(make_node(oid, "AEOutlierSignal",
        f"Absolute AE (ctrl=0): {sig['ae_term']} ({sig['exp_drug']}) rate={sig['exp_rate']:.3f}",
        source_signal_id=sig["id"],
        ae_term=sig["ae_term"],
        drug=sig["exp_drug"],
        rate_ratio=999,
        z_score=999.0,
        iqr_flag=True,
        exp_rate=sig["exp_rate"],
        ctrl_rate=0.0,
        signal_class="absolute_drug_exclusive",
        replication_count=len(ae_term_trials.get(sig["ae_term"],set())),
        trial_id=sig["trial_id"]))
    new_edges.append(make_edge(sig["id"], oid, "is_outlier_signal",
        signal_class="absolute_drug_exclusive"))

# ── 2. SerialEscalationSignal IQR outliers ──────────────────────────────────
print("\n[W1] Analyzing SerialEscalationSignal outliers...")
esc_sigs = [e for e in ents if e.get("type") == "SerialEscalationSignal"]
grade_deltas = np.array([e.get("grade_delta", 0) for e in esc_sigs])
patient_counts = np.array([e.get("patient_count", 0) for e in esc_sigs])

gd_q3 = np.percentile(grade_deltas, 75)
gd_iqr = gd_q3 - np.percentile(grade_deltas, 25)
pc_mean, pc_std = np.mean(patient_counts), np.std(patient_counts)

print(f"[W1] grade_delta: max={grade_deltas.max()}, IQR upper={gd_q3+1.5*gd_iqr:.1f}")
print(f"[W1] patient_count: mean={pc_mean:.1f}, std={pc_std:.1f}, max={patient_counts.max()}")

# High-impact escalation: grade_delta=2 AND patient_count > mean+2std
high_impact = [e for e in esc_sigs
               if e.get("grade_delta",0) >= 2 and e.get("patient_count",0) > pc_mean + 2*pc_std]
print(f"[W1] High-impact escalation signals (delta>=2, count>mean+2std): {len(high_impact)}")
for s in sorted(high_impact, key=lambda x: -x.get("patient_count",0))[:10]:
    print(f"  {s['ae_term']} | trial={s['trial_id']} | arm={s['arm_code']} | "
          f"delta={s['grade_delta']} | n={s['patient_count']}")

# ── 3. AE co-occurrence network (severe AEs only, max_grade >= 3) ────────────
print("\n[W1] Building AE co-occurrence network (severe AEs, grade>=3)...")
t0 = time.time()

# Build patient -> severe AE terms mapping
patient_severe_aes = defaultdict(set)
ae_edges = [e for e in edges if e["relation"] == "experienced_ae"]
for e in ae_edges:
    attrs = e.get("attributes", {})
    if attrs.get("max_grade", 0) >= 3:
        patient_severe_aes[e["source"]].add(e["target"])

print(f"[W1] Patients with >=1 severe AE: {len(patient_severe_aes)}")

# Count co-occurrences
cooc = defaultdict(int)
cooc_patients = defaultdict(set)
for pid, ae_set in patient_severe_aes.items():
    ae_list = sorted(ae_set)
    for i in range(len(ae_list)):
        for j in range(i+1, len(ae_list)):
            pair = (ae_list[i], ae_list[j])
            cooc[pair] += 1
            cooc_patients[pair].add(pid)

print(f"[W1] Co-occurrence pairs: {len(cooc)}")
top_cooc = sorted(cooc.items(), key=lambda x: -x[1])[:30]
print("[W1] Top 20 severe AE co-occurrences:")
for (a, b), cnt in top_cooc[:20]:
    print(f"  {id_to_name.get(a,a)[:25]} + {id_to_name.get(b,b)[:25]}: {cnt} patients")

print(f"[W1] Co-occurrence analysis done in {time.time()-t0:.1f}s")

# Build co-occurrence graph for community detection
G_cooc = nx.Graph()
for (a, b), cnt in cooc.items():
    if cnt >= 5:  # min 5 patients sharing both severe AEs
        G_cooc.add_edge(a, b, weight=cnt)

print(f"[W1] Co-occurrence graph (min 5 patients): {G_cooc.number_of_nodes()} nodes, {G_cooc.number_of_edges()} edges")

# Find cliques (toxicity syndromes)
cliques = list(nx.find_cliques(G_cooc))
cliques_sorted = sorted(cliques, key=lambda x: -len(x))
print(f"[W1] Cliques (toxicity syndromes): {len(cliques)}")
print("[W1] Top 10 cliques:")
for cl in cliques_sorted[:10]:
    names = [id_to_name.get(n,n)[:20] for n in cl]
    # Total patients in this syndrome
    total_pts = len(set.union(*[cooc_patients.get(tuple(sorted([cl[i],cl[j]])),set())
                                for i in range(len(cl)) for j in range(i+1,len(cl))]) if len(cl)>1 else set())
    print(f"  Size {len(cl)}: {names}")

# Mint ToxicitySyndrome nodes
for idx, cl in enumerate(cliques_sorted[:20]):
    if len(cl) < 3:
        continue
    # Compute total patients in syndrome (union of all pairs)
    if len(cl) > 1:
        patient_sets = [cooc_patients.get(tuple(sorted([cl[i],cl[j]])),set())
                       for i in range(len(cl)) for j in range(i+1,len(cl))]
        total_pts = len(set.union(*patient_sets)) if patient_sets else 0
    else:
        total_pts = 0
    ts_id = f"toxicity_syndrome:s11:{idx}"
    new_nodes.append(make_node(ts_id, "ToxicitySyndrome",
        f"Toxicity syndrome {idx}: {len(cl)} co-occurring severe AEs",
        ae_terms=json.dumps([id_to_name.get(n,n) for n in cl]),
        ae_term_ids=json.dumps(cl),
        n_terms=len(cl),
        total_patients=total_pts,
        min_cooccurrence=min(cooc.get(tuple(sorted([cl[i],cl[j]])),0)
                            for i in range(len(cl)) for j in range(i+1,len(cl))) if len(cl)>1 else 0))
    for ae_id in cl:
        new_edges.append(make_edge(ae_id, ts_id, "co_occurs_with",
            syndrome_id=idx, n_terms=len(cl)))

# ── 4. Drug toxicity similarity ──────────────────────────────────────────────
print("\n[W1] Computing drug toxicity similarity...")
drug_nodes = [e for e in ents if e.get("type") == "DrugIntervention"]
drug_ae_map = defaultdict(set)  # drug_id -> set of AE terms
for sig in drug_ae:
    drug_ae_map[sig.get("drug_node_id","")].add(sig["ae_term"])

print(f"[W1] Drugs with AE signals: {len(drug_ae_map)}")
drug_ids = list(drug_ae_map.keys())

# Jaccard similarity between drug AE profiles
drug_sim = {}
for i in range(len(drug_ids)):
    for j in range(i+1, len(drug_ids)):
        a, b = drug_ids[i], drug_ids[j]
        inter = len(drug_ae_map[a] & drug_ae_map[b])
        union = len(drug_ae_map[a] | drug_ae_map[b])
        if union > 0:
            drug_sim[(a,b)] = inter / union

top_sim = sorted(drug_sim.items(), key=lambda x: -x[1])
print("[W1] Drug toxicity similarity (Jaccard):")
for (a,b), sim in top_sim[:10]:
    print(f"  {id_to_name.get(a,a)} <-> {id_to_name.get(b,b)}: {sim:.3f}")

# Mint DrugToxicityProfile nodes
for drug_id in drug_ids:
    if not drug_id:
        continue
    ae_terms = list(drug_ae_map[drug_id])
    dtp_id = f"drug_tox_profile:s11:{drug_id.replace(':','_')}"
    new_nodes.append(make_node(dtp_id, "DrugToxicityProfile",
        f"Toxicity profile: {id_to_name.get(drug_id, drug_id)}",
        drug_id=drug_id,
        n_ae_signals=len(ae_terms),
        ae_terms=json.dumps(ae_terms[:20]),
        top_rr_signal=max((s["rate_ratio"] for s in drug_ae if s.get("drug_node_id")==drug_id), default=0)))
    new_edges.append(make_edge(drug_id, dtp_id, "has_toxicity_profile"))

# Drug similarity edges
for (a,b), sim in top_sim:
    if sim > 0.1:  # only meaningful similarity
        new_edges.append(make_edge(a, b, "toxicity_similar_to",
            jaccard_similarity=round(sim,4),
            shared_ae_count=len(drug_ae_map[a] & drug_ae_map[b])))

# ── Figures ──────────────────────────────────────────────────────────────────
# 1. RR distribution with outlier annotations
fig, ax = plt.subplots(figsize=(12, 5))
rr_plot = [s["rate_ratio"] for s in real_signals]
ax.hist(rr_plot, bins=30, color='#0279EE', edgecolor='white', alpha=0.8, label='Real RR signals')
threshold_z = rr_mean + 2.5 * rr_std
ax.axvline(x=threshold_z, color='#FF9400', linestyle='--', linewidth=2,
           label=f'Z>2.5 threshold (RR={threshold_z:.1f})')
ax.axvline(x=rr_upper_iqr, color='#75A025', linestyle=':', linewidth=2,
           label=f'IQR upper fence (RR={rr_upper_iqr:.1f})')
# Annotate top outliers
for s in outlier_signals[:5]:
    ax.annotate(f"{s['ae_term'][:15]}\n({s['exp_drug'][:12]})",
                xy=(s["rate_ratio"], 0.5), xytext=(s["rate_ratio"]-5, 3),
                fontsize=7, color='red',
                arrowprops=dict(arrowstyle='->', color='red', lw=0.8))
ax.set_xlabel("Rate Ratio (RR)")
ax.set_ylabel("Count")
ax.set_title(f"DrugAESignal Rate Ratio Distribution — Outlier Detection\n"
             f"(n={len(real_signals)} real signals + {len(capped_signals)} capped at RR=999)")
ax.legend(fontsize=9)
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/w1_rr_outliers.png", dpi=150, bbox_inches='tight')
plt.close()
print("[W1] Saved rr_outliers.png")

# 2. AE co-occurrence heatmap (top 20 terms)
top_ae_terms = [t for t,_ in Counter({n: G_cooc.degree(n, weight='weight')
                                       for n in G_cooc.nodes()}).most_common(20)]
if len(top_ae_terms) >= 4:
    mat = np.zeros((len(top_ae_terms), len(top_ae_terms)))
    for i, a in enumerate(top_ae_terms):
        for j, b in enumerate(top_ae_terms):
            if i != j:
                mat[i,j] = cooc.get(tuple(sorted([a,b])), 0)
    fig, ax = plt.subplots(figsize=(12, 10))
    labels = [id_to_name.get(t,t)[:20] for t in top_ae_terms]
    im = ax.imshow(mat, cmap='Blues', aspect='auto')
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(labels, fontsize=8)
    plt.colorbar(im, ax=ax, label='Co-occurrence count (severe AEs, grade>=3)')
    ax.set_title("Severe AE Co-occurrence Heatmap (Top 20 Terms)")
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/w1_ae_cooccurrence_heatmap.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("[W1] Saved ae_cooccurrence_heatmap.png")

# 3. Drug toxicity similarity matrix
if len(drug_ids) >= 2:
    n = len(drug_ids)
    sim_mat = np.zeros((n, n))
    for i in range(n):
        for j in range(n):
            if i != j:
                key = tuple(sorted([drug_ids[i], drug_ids[j]]))
                sim_mat[i,j] = drug_sim.get(key, 0)
    fig, ax = plt.subplots(figsize=(10, 8))
    dlabels = [id_to_name.get(d,d)[:20] for d in drug_ids]
    im = ax.imshow(sim_mat, cmap='YlOrRd', vmin=0, vmax=1, aspect='auto')
    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(dlabels, rotation=45, ha='right', fontsize=8)
    ax.set_yticklabels(dlabels, fontsize=8)
    plt.colorbar(im, ax=ax, label='Jaccard similarity (shared AE profile)')
    ax.set_title("Drug Toxicity Profile Similarity (Jaccard)")
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/w1_drug_toxicity_similarity.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("[W1] Saved drug_toxicity_similarity.png")

# ── Save ─────────────────────────────────────────────────────────────────────
results = {
    "worker": 1, "session": SESSION, "timestamp": TS,
    "rr_stats": {"mean": round(float(rr_mean),3), "std": round(float(rr_std),3),
                 "z25_threshold": round(float(rr_mean+2.5*rr_std),3),
                 "iqr_upper": round(float(rr_upper_iqr),3)},
    "n_real_signals": len(real_signals),
    "n_capped_signals": len(capped_signals),
    "n_z_outliers": int(sum(1 for s in outlier_signals if s["z_score"]>2.5)),
    "top_outlier_signals": [{"ae_term": s["ae_term"], "drug": s["exp_drug"],
                              "rr": s["rate_ratio"], "z": s["z_score"]}
                             for s in outlier_signals[:20]],
    "replicated_ae_terms": [(t, n, list(trials)) for t,n,trials in replicated[:20]],
    "top_cooccurrences": [{"ae_a": id_to_name.get(a,a), "ae_b": id_to_name.get(b,b), "count": cnt}
                          for (a,b),cnt in top_cooc[:20]],
    "n_toxicity_syndromes": sum(1 for cl in cliques_sorted if len(cl)>=3),
    "drug_similarity": [{"drug_a": id_to_name.get(a,a), "drug_b": id_to_name.get(b,b),
                          "jaccard": round(s,4)} for (a,b),s in top_sim[:10]],
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
with open("/mnt/shared-workspace/shared/s11_w1_results.json", "w") as f:
    json.dump(results, f, indent=2, default=_np_default)
print(f"[W1] DONE. New nodes: {len(new_nodes)}, new edges: {len(new_edges)}")
