"""
Session 11 Worker 2 — Escalation Pattern Deep Mining
Co-escalation network, arm burden scores, drug class enrichment, grade trajectories
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

print(f"[W2] Loading KG... {TS}")
with open("/mnt/results/zeta_vault/kg/zeta_entities.json") as f:
    ents = json.load(f)
with open("/mnt/results/zeta_vault/kg/zeta_edges.json") as f:
    edges = json.load(f)

id_to_type = {e["id"]: e.get("type","?") for e in ents}
id_to_name = {e["id"]: e.get("name","") for e in ents}
print(f"[W2] Loaded: {len(ents)} entities, {len(edges)} edges")

def make_node(nid, ntype, name, **kwargs):
    return {"id": nid, "type": ntype, "name": name,
            "source_receipts": ["Session 11 W2 escalation analysis"],
            "verbatim_evidence": [], "cross_refs": {},
            "attributes": {k: v for k, v in kwargs.items()},
            "_session": SESSION, "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

def make_edge(src, tgt, rel, **attrs):
    return {"source": src, "target": tgt, "relation": rel,
            "attributes": attrs, "_session": SESSION,
            "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

new_nodes = []
new_edges = []

# Load escalation signals
esc_sigs = [e for e in ents if e.get("type") == "SerialEscalationSignal"]
cross_patterns = [e for e in ents if e.get("type") == "CrossTrialEscalationPattern"]
arm_nodes = {e["id"]: e for e in ents if e.get("type") == "TreatmentArm"}
trial_nodes = {e["id"]: e for e in ents if e.get("type") == "Trial"}
drug_nodes = {e["id"]: e for e in ents if e.get("type") == "DrugIntervention"}

print(f"[W2] SerialEscalationSignal: {len(esc_sigs)}")
print(f"[W2] CrossTrialEscalationPattern: {len(cross_patterns)}")

# ── 1. Arm-level escalation burden score ────────────────────────────────────
print("\n[W2] Computing arm-level escalation burden scores...")
arm_burden = defaultdict(lambda: {"total_burden": 0, "n_signals": 0, "severe_count": 0,
                                   "drug_related_count": 0, "max_delta": 0, "ae_terms": []})
for sig in esc_sigs:
    trial_id = sig.get("trial_id","")
    arm_code = sig.get("arm_code","")
    # Normalize arm_code to match arm node IDs
    try:
        arm_code_norm = str(int(float(arm_code)))
    except:
        arm_code_norm = str(arm_code).replace(".0","")
    arm_id = f"arm:sas:{trial_id}:{arm_code_norm}"
    # Also try original
    if arm_id not in arm_nodes:
        arm_id = f"arm:sas:{trial_id}:{arm_code}"

    delta = sig.get("grade_delta", 0)
    n_patients = sig.get("patient_count", 0)
    burden = delta * n_patients
    arm_burden[arm_id]["total_burden"] += burden
    arm_burden[arm_id]["n_signals"] += 1
    arm_burden[arm_id]["ae_terms"].append(sig.get("ae_term",""))
    if sig.get("max_grade", 0) >= 3:
        arm_burden[arm_id]["severe_count"] += n_patients
    if sig.get("drug_related", False):
        arm_burden[arm_id]["drug_related_count"] += n_patients
    arm_burden[arm_id]["max_delta"] = max(arm_burden[arm_id]["max_delta"], delta)

burden_vals = np.array([v["total_burden"] for v in arm_burden.values()])
burden_mean, burden_std = np.mean(burden_vals), np.std(burden_vals)
burden_threshold = burden_mean + 2 * burden_std

print(f"[W2] Arm burden: mean={burden_mean:.1f}, std={burden_std:.1f}, threshold={burden_threshold:.1f}")
arm_burden_sorted = sorted(arm_burden.items(), key=lambda x: -x[1]["total_burden"])
print("[W2] Top 15 arms by escalation burden:")
for arm_id, data in arm_burden_sorted[:15]:
    flag = "OUTLIER" if data["total_burden"] > burden_threshold else ""
    print(f"  {arm_id}: burden={data['total_burden']:.0f}, n_signals={data['n_signals']}, "
          f"severe={data['severe_count']}, drug_rel={data['drug_related_count']} {flag}")

# Mint ArmEscalationBurden nodes
for arm_id, data in arm_burden_sorted:
    burden = data["total_burden"]
    is_outlier = burden > burden_threshold
    aeb_id = f"arm_esc_burden:s11:{arm_id.replace(':','_')[:60]}"
    new_nodes.append(make_node(aeb_id, "ArmEscalationBurden",
        f"Escalation burden: {arm_id.split(':')[-1]} ({burden:.0f})",
        arm_id=arm_id,
        total_burden=round(burden, 2),
        n_signals=data["n_signals"],
        severe_count=data["severe_count"],
        drug_related_count=data["drug_related_count"],
        max_grade_delta=data["max_delta"],
        is_outlier=is_outlier,
        burden_z_score=round((burden - burden_mean) / burden_std, 3) if burden_std > 0 else 0,
        top_ae_terms=json.dumps(list(Counter(data["ae_terms"]).most_common(5)))))
    if arm_id in arm_nodes:
        new_edges.append(make_edge(arm_id, aeb_id, "has_escalation_burden",
            total_burden=round(burden,2), is_outlier=is_outlier))

# ── 2. Drug class escalation enrichment ─────────────────────────────────────
print("\n[W2] Drug class escalation enrichment...")
# Map arm -> drug class via uses_drug edges
arm_to_drug = {}
for e in edges:
    if e["relation"] == "uses_drug":
        arm_to_drug[e["source"]] = e["target"]

drug_class_burdens = defaultdict(list)
for arm_id, data in arm_burden.items():
    drug_id = arm_to_drug.get(arm_id)
    if drug_id and drug_id in drug_nodes:
        drug_class = drug_nodes[drug_id].get("drug_class", "Unknown")
        drug_class_burdens[drug_class].append(data["total_burden"])

print("[W2] Drug class escalation burden:")
for dc, burdens in sorted(drug_class_burdens.items(), key=lambda x: -np.mean(x[1])):
    print(f"  {dc}: n={len(burdens)}, mean={np.mean(burdens):.1f}, max={max(burdens):.1f}")

# Mann-Whitney U: EGFR inhibitor arms vs. chemotherapy-only arms
egfr_burdens = []
chemo_burdens = []
for arm_id, data in arm_burden.items():
    drug_id = arm_to_drug.get(arm_id)
    if drug_id and drug_id in drug_nodes:
        dc = drug_nodes[drug_id].get("drug_class","")
        if "EGFR" in dc or "panitumumab" in drug_id.lower():
            egfr_burdens.append(data["total_burden"])
        elif "Chemotherapy" in dc or "Platinum" in dc or "Nucleoside" in dc:
            chemo_burdens.append(data["total_burden"])

if egfr_burdens and chemo_burdens:
    u_stat, p_val = stats.mannwhitneyu(egfr_burdens, chemo_burdens, alternative='two-sided')
    print(f"\n[W2] Mann-Whitney U: EGFR ({len(egfr_burdens)} arms, mean={np.mean(egfr_burdens):.1f}) "
          f"vs Chemo ({len(chemo_burdens)} arms, mean={np.mean(chemo_burdens):.1f})")
    print(f"     U={u_stat:.1f}, p={p_val:.4f}")
else:
    u_stat, p_val = 0, 1.0
    print("[W2] Insufficient data for Mann-Whitney U test")

# Mint DrugClassEscalationProfile nodes
for dc, burdens in drug_class_burdens.items():
    dce_id = f"drug_class_esc:s11:{dc.replace(' ','_').replace('/','_')[:40]}"
    new_nodes.append(make_node(dce_id, "DrugClassEscalationProfile",
        f"Escalation profile: {dc}",
        drug_class=dc,
        n_arms=len(burdens),
        mean_burden=round(float(np.mean(burdens)),2),
        max_burden=round(float(max(burdens)),2),
        std_burden=round(float(np.std(burdens)),2),
        mw_u_vs_chemo=round(u_stat,2) if "EGFR" in dc else None,
        mw_p_vs_chemo=round(p_val,4) if "EGFR" in dc else None))

# ── 3. Co-escalation network ─────────────────────────────────────────────────
print("\n[W2] Building co-escalation network...")
# Group escalation signals by (trial, arm) -> set of AE terms
arm_ae_escalations = defaultdict(set)
for sig in esc_sigs:
    key = (sig.get("trial_id",""), sig.get("arm_code",""))
    arm_ae_escalations[key].add(sig.get("ae_term",""))

# Count co-escalation pairs
coesc = defaultdict(int)
coesc_arms = defaultdict(set)
for key, ae_set in arm_ae_escalations.items():
    ae_list = sorted(ae_set)
    for i in range(len(ae_list)):
        for j in range(i+1, len(ae_list)):
            pair = (ae_list[i], ae_list[j])
            coesc[pair] += 1
            coesc_arms[pair].add(key)

top_coesc = sorted(coesc.items(), key=lambda x: -x[1])[:30]
print(f"[W2] Co-escalation pairs: {len(coesc)}")
print("[W2] Top 15 co-escalating AE pairs:")
for (a,b), cnt in top_coesc[:15]:
    print(f"  {a} + {b}: {cnt} arms")

# Build co-escalation graph
G_coesc = nx.Graph()
for (a,b), cnt in coesc.items():
    if cnt >= 3:
        G_coesc.add_edge(a, b, weight=cnt)

print(f"[W2] Co-escalation graph (min 3 arms): {G_coesc.number_of_nodes()} nodes, {G_coesc.number_of_edges()} edges")

# Find cliques (escalation syndromes)
esc_cliques = sorted(list(nx.find_cliques(G_coesc)), key=lambda x: -len(x))
print(f"[W2] Escalation cliques: {len(esc_cliques)}")
for cl in esc_cliques[:10]:
    print(f"  Size {len(cl)}: {cl[:5]}")

# Mint EscalationSyndrome nodes
for idx, cl in enumerate(esc_cliques[:20]):
    if len(cl) < 3:
        continue
    es_id = f"escalation_syndrome:s11:{idx}"
    n_arms = len(set.union(*[coesc_arms.get(tuple(sorted([cl[i],cl[j]])),set())
                              for i in range(len(cl)) for j in range(i+1,len(cl))]) if len(cl)>1 else set())
    new_nodes.append(make_node(es_id, "EscalationSyndrome",
        f"Escalation syndrome {idx}: {len(cl)} co-escalating AEs",
        ae_terms=json.dumps(cl),
        n_terms=len(cl),
        n_arms=n_arms,
        min_coescalation=min(coesc.get(tuple(sorted([cl[i],cl[j]])),0)
                            for i in range(len(cl)) for j in range(i+1,len(cl))) if len(cl)>1 else 0))
    for ae_term in cl:
        # Find matching AdverseEventTerm nodes
        ae_node_ids = [e["id"] for e in ents
                       if e.get("type")=="AdverseEventTerm" and
                       (e.get("name","").upper()==ae_term.upper() or
                        ae_term.upper() in e.get("name","").upper())]
        for ae_id in ae_node_ids[:1]:
            new_edges.append(make_edge(ae_id, es_id, "co_escalates_with",
                syndrome_id=idx))

# ── 4. Grade trajectory analysis ─────────────────────────────────────────────
print("\n[W2] Grade trajectory analysis...")
# For each AE term, find min_grade and max_grade across all signals
ae_trajectories = defaultdict(lambda: {"min_grades": [], "max_grades": [], "deltas": [], "trials": set()})
for sig in esc_sigs:
    term = sig.get("ae_term","")
    ae_trajectories[term]["min_grades"].append(sig.get("min_grade",0))
    ae_trajectories[term]["max_grades"].append(sig.get("max_grade",0))
    ae_trajectories[term]["deltas"].append(sig.get("grade_delta",0))
    ae_trajectories[term]["trials"].add(sig.get("trial_id",""))

# Find terms with monotonically increasing max_grade across trials (dose-response-like)
monotone_terms = []
for term, data in ae_trajectories.items():
    if len(data["trials"]) >= 3 and max(data["max_grades"]) >= 3:
        mean_delta = np.mean(data["deltas"])
        max_delta = max(data["deltas"])
        monotone_terms.append({
            "term": term,
            "n_trials": len(data["trials"]),
            "mean_delta": round(mean_delta, 2),
            "max_delta": max_delta,
            "max_grade": max(data["max_grades"]),
            "n_signals": len(data["deltas"])
        })

monotone_terms.sort(key=lambda x: (-x["n_trials"], -x["mean_delta"]))
print(f"[W2] AE terms with grade escalation in >=3 trials: {len(monotone_terms)}")
for t in monotone_terms[:15]:
    print(f"  {t['term']}: {t['n_trials']} trials, mean_delta={t['mean_delta']:.2f}, max_grade={t['max_grade']}")

# ── 5. Severe conversion rate ─────────────────────────────────────────────────
print("\n[W2] Severe conversion rate analysis...")
arm_drug_related_severe = defaultdict(lambda: {"drug_related": 0, "total": 0, "severe": 0})
for sig in esc_sigs:
    trial_id = sig.get("trial_id","")
    arm_code = sig.get("arm_code","")
    key = f"{trial_id}:{arm_code}"
    arm_drug_related_severe[key]["total"] += 1
    if sig.get("drug_related", False):
        arm_drug_related_severe[key]["drug_related"] += 1
    if sig.get("max_grade",0) >= 3:
        arm_drug_related_severe[key]["severe"] += 1

high_conversion = []
for key, data in arm_drug_related_severe.items():
    if data["total"] >= 5 and data["severe"] >= 2:
        dr_rate = data["drug_related"] / data["total"]
        sev_rate = data["severe"] / data["total"]
        if dr_rate > 0.5 and sev_rate > 0.3:
            high_conversion.append({"arm": key, **data,
                                    "dr_rate": round(dr_rate,3), "sev_rate": round(sev_rate,3)})

high_conversion.sort(key=lambda x: -x["sev_rate"])
print(f"[W2] Arms with high drug-related severe conversion: {len(high_conversion)}")
for hc in high_conversion[:10]:
    print(f"  {hc['arm']}: dr_rate={hc['dr_rate']:.2f}, sev_rate={hc['sev_rate']:.2f}, n={hc['total']}")

# ── Figures ──────────────────────────────────────────────────────────────────
# 1. Arm escalation burden ranking
fig, ax = plt.subplots(figsize=(14, 7))
top_arms = arm_burden_sorted[:20]
labels = [aid.split(":")[-2] + ":" + aid.split(":")[-1] for aid,_ in top_arms]
vals = [d["total_burden"] for _,d in top_arms]
colors = ['#FF9400' if d["total_burden"] > burden_threshold else '#0279EE' for _,d in top_arms]
bars = ax.barh(range(len(labels)), vals, color=colors)
ax.set_yticks(range(len(labels)))
ax.set_yticklabels(labels, fontsize=8)
ax.axvline(x=burden_threshold, color='red', linestyle='--', linewidth=1.5,
           label=f'Outlier threshold (mean+2σ={burden_threshold:.0f})')
ax.set_xlabel("Escalation Burden Score (grade_delta × patient_count)")
ax.set_title("Top 20 Arms by Escalation Burden Score")
ax.legend()
ax.invert_yaxis()
ax.grid(True, alpha=0.3, axis='x')
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/w2_arm_escalation_burden.png", dpi=150, bbox_inches='tight')
plt.close()
print("[W2] Saved arm_escalation_burden.png")

# 2. Drug class comparison (box plot)
if drug_class_burdens:
    fig, ax = plt.subplots(figsize=(12, 6))
    dc_data = [(dc, burdens) for dc, burdens in drug_class_burdens.items() if len(burdens)>=1]
    dc_data.sort(key=lambda x: -np.mean(x[1]))
    bp = ax.boxplot([d[1] for d in dc_data], labels=[d[0][:20] for d in dc_data],
                    patch_artist=True, notch=False)
    colors_bp = ['#FF9400' if 'EGFR' in d[0] else '#0279EE' for d in dc_data]
    for patch, color in zip(bp['boxes'], colors_bp):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    ax.set_xlabel("Drug Class")
    ax.set_ylabel("Escalation Burden Score")
    ax.set_title("Escalation Burden by Drug Class")
    plt.xticks(rotation=30, ha='right', fontsize=8)
    ax.grid(True, alpha=0.3, axis='y')
    if egfr_burdens and chemo_burdens:
        ax.set_title(f"Escalation Burden by Drug Class\n(EGFR vs Chemo Mann-Whitney p={p_val:.4f})")
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/w2_drug_class_comparison.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("[W2] Saved drug_class_comparison.png")

# 3. Co-escalation network (top 30 pairs)
if G_coesc.number_of_nodes() > 0:
    fig, ax = plt.subplots(figsize=(14, 12))
    # Use top 30 nodes by degree
    top_coesc_nodes = sorted(G_coesc.degree(), key=lambda x: -x[1])[:30]
    G_sub = G_coesc.subgraph([n for n,_ in top_coesc_nodes])
    pos = nx.spring_layout(G_sub, seed=42, k=2)
    weights = [G_sub[u][v]['weight'] for u,v in G_sub.edges()]
    max_w = max(weights) if weights else 1
    nx.draw_networkx_nodes(G_sub, pos, ax=ax, node_size=300, node_color='#0279EE', alpha=0.8)
    nx.draw_networkx_edges(G_sub, pos, ax=ax,
                           width=[2*w/max_w for w in weights],
                           edge_color='#FF9400', alpha=0.6)
    nx.draw_networkx_labels(G_sub, pos, ax=ax,
                            labels={n: n[:15] for n in G_sub.nodes()}, font_size=6)
    ax.set_title(f"Co-escalation Network (top 30 nodes, min 3 arms)\n"
                 f"{G_coesc.number_of_nodes()} total nodes, {G_coesc.number_of_edges()} edges")
    ax.axis('off')
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/w2_coescalation_network.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("[W2] Saved coescalation_network.png")

# ── Save ─────────────────────────────────────────────────────────────────────
results = {
    "worker": 2, "session": SESSION, "timestamp": TS,
    "arm_burden_stats": {"mean": round(float(burden_mean),2), "std": round(float(burden_std),2),
                          "threshold": round(float(burden_threshold),2),
                          "n_outlier_arms": int(sum(1 for _,d in arm_burden.items()
                                                    if d["total_burden"]>burden_threshold))},
    "top_arms": [{"arm_id": aid, "burden": round(d["total_burden"],2),
                  "n_signals": d["n_signals"], "severe": d["severe_count"]}
                 for aid,d in arm_burden_sorted[:20]],
    "drug_class_stats": {dc: {"n": len(b), "mean": round(float(np.mean(b)),2)}
                         for dc,b in drug_class_burdens.items()},
    "mw_egfr_vs_chemo": {"u": round(u_stat,2), "p": round(p_val,4)},
    "n_escalation_syndromes": sum(1 for cl in esc_cliques if len(cl)>=3),
    "monotone_terms": monotone_terms[:20],
    "high_conversion_arms": high_conversion[:10],
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
with open("/mnt/shared-workspace/shared/s11_w2_results.json", "w") as f:
    json.dump(results, f, indent=2, default=_np_default)
print(f"[W2] DONE. New nodes: {len(new_nodes)}, new edges: {len(new_edges)}")
