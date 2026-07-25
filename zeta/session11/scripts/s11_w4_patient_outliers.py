"""
Session 11 Worker 4 — Patient-Level Outlier Detection + AE Burden Profiling
Per-patient severe AE burden, rare AE combinations, arm KL divergence, OS descriptive stats
"""
import json, os, time, math
from datetime import datetime
from collections import Counter, defaultdict
import numpy as np
import scipy.stats as stats
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']

OUT_DIR = "/mnt/results/session11"
os.makedirs(OUT_DIR, exist_ok=True)
TS = datetime.utcnow().isoformat() + "Z"
SESSION = 11
MINT_PLANNER = "zeta_custodian_session11"

print(f"[W4] Loading KG... {TS}")
with open("/mnt/results/zeta_vault/kg/zeta_entities.json") as f:
    ents = json.load(f)
with open("/mnt/results/zeta_vault/kg/zeta_edges.json") as f:
    edges = json.load(f)

id_to_type = {e["id"]: e.get("type","?") for e in ents}
id_to_name = {e["id"]: e.get("name","") for e in ents}
print(f"[W4] Loaded: {len(ents)} entities, {len(edges)} edges")

def make_node(nid, ntype, name, **kwargs):
    return {"id": nid, "type": ntype, "name": name,
            "source_receipts": ["Session 11 W4 patient outlier analysis"],
            "verbatim_evidence": [], "cross_refs": {},
            "attributes": {k: v for k, v in kwargs.items()},
            "_session": SESSION, "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

def make_edge(src, tgt, rel, **attrs):
    return {"source": src, "target": tgt, "relation": rel,
            "attributes": attrs, "_session": SESSION,
            "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

new_nodes = []
new_edges = []

# ── Build patient AE profile ──────────────────────────────────────────────────
print("[W4] Building patient AE profiles from experienced_ae edges...")
t0 = time.time()

ae_edges = [e for e in edges if e["relation"] == "experienced_ae"]
print(f"[W4] AE edges: {len(ae_edges)}")

# patient -> {ae_term: max_grade, ...}
patient_ae_profile = defaultdict(dict)
patient_severe_count = defaultdict(int)
patient_drug_severe_count = defaultdict(int)
patient_ae_diversity = defaultdict(set)

for e in ae_edges:
    pid = e["source"]
    ae_id = e["target"]
    attrs = e.get("attributes", {})
    max_grade = attrs.get("max_grade", 0)
    causality = str(attrs.get("causality","")).lower()
    is_drug_related = any(kw in causality for kw in ["drug","related","yes","1","2","3"])

    patient_ae_profile[pid][ae_id] = max(patient_ae_profile[pid].get(ae_id,0), max_grade)
    patient_ae_diversity[pid].add(ae_id)
    if max_grade >= 3:
        patient_severe_count[pid] += 1
        if is_drug_related:
            patient_drug_severe_count[pid] += 1

print(f"[W4] Patients with AE data: {len(patient_ae_profile)}")
print(f"[W4] Done in {time.time()-t0:.1f}s")

# ── 1. Per-patient AE burden outliers ────────────────────────────────────────
print("\n[W4] Computing per-patient AE burden outliers...")
severe_counts = np.array([patient_severe_count.get(pid,0) for pid in patient_ae_profile])
diversity_counts = np.array([len(patient_ae_diversity.get(pid,set())) for pid in patient_ae_profile])

sc_mean, sc_std = np.mean(severe_counts), np.std(severe_counts)
sc_threshold_3sig = sc_mean + 3 * sc_std
sc_threshold_2sig = sc_mean + 2 * sc_std

print(f"[W4] Severe AE count: mean={sc_mean:.2f}, std={sc_std:.2f}")
print(f"     3σ threshold: {sc_threshold_3sig:.1f}, 2σ threshold: {sc_threshold_2sig:.1f}")
print(f"     Patients with >=1 severe AE: {sum(1 for c in severe_counts if c>=1)}")
print(f"     Patients with >3σ severe AEs: {sum(1 for c in severe_counts if c>sc_threshold_3sig)}")

# Get patient node attributes
patient_nodes = {e["id"]: e for e in ents if e.get("type") == "TrialPatient"}

# Top outlier patients
patient_ids = list(patient_ae_profile.keys())
patient_burden_data = []
for pid in patient_ids:
    sc = patient_severe_count.get(pid,0)
    dsc = patient_drug_severe_count.get(pid,0)
    div = len(patient_ae_diversity.get(pid,set()))
    z = (sc - sc_mean) / sc_std if sc_std > 0 else 0
    pnode = patient_nodes.get(pid, {})
    attrs = pnode.get("attributes",{})
    patient_burden_data.append({
        "pid": pid,
        "severe_count": sc,
        "drug_severe_count": dsc,
        "ae_diversity": div,
        "z_score": round(z,3),
        "trial": attrs.get("trial","?"),
        "arm": attrs.get("arm","?"),
        "cancer_type": attrs.get("cancer_type","?"),
        "os_event": attrs.get("os_event","?"),
        "n_ae_recorded": attrs.get("n_ae_recorded",0),
    })

patient_burden_data.sort(key=lambda x: -x["severe_count"])
print("\n[W4] Top 20 patients by severe AE count:")
for p in patient_burden_data[:20]:
    print(f"  {p['pid']}: severe={p['severe_count']}, z={p['z_score']:.2f}, "
          f"diversity={p['ae_diversity']}, trial={p['trial']}, arm={p['arm']}")

# Mint PatientAEBurdenScore nodes (top 100 outliers)
outlier_patients = [p for p in patient_burden_data if p["z_score"] > 2.0][:100]
print(f"\n[W4] Minting {len(outlier_patients)} PatientAEBurdenScore nodes...")
for rank, p in enumerate(outlier_patients):
    pb_id = f"patient_ae_burden:s11:{p['pid'].replace(':','_')[:60]}"
    new_nodes.append(make_node(pb_id, "PatientAEBurdenScore",
        f"AE burden outlier: {p['pid'].split(':')[-1]} (severe={p['severe_count']}, z={p['z_score']:.2f})",
        patient_id=p["pid"],
        severe_ae_count=p["severe_count"],
        drug_severe_count=p["drug_severe_count"],
        ae_diversity=p["ae_diversity"],
        z_score=p["z_score"],
        rank=rank+1,
        trial=p["trial"],
        arm=p["arm"],
        cancer_type=p["cancer_type"],
        os_event=str(p["os_event"])))
    if p["pid"] in patient_nodes:
        new_edges.append(make_edge(p["pid"], pb_id, "has_burden_score",
            severe_count=p["severe_count"], z_score=p["z_score"], rank=rank+1))

# ── 2. Rare AE combination detection ─────────────────────────────────────────
print("\n[W4] Detecting rare AE combinations...")
# AE combination = frozenset of AE term IDs for each patient
ae_combo_counts = Counter()
patient_combos = {}
for pid, ae_dict in patient_ae_profile.items():
    combo = frozenset(ae_dict.keys())
    ae_combo_counts[combo] += 1
    patient_combos[pid] = combo

# Rare = combo shared by <=2 patients AND patient has >=3 distinct AEs
rare_profiles = []
for pid, combo in patient_combos.items():
    if len(combo) >= 3 and ae_combo_counts[combo] <= 2:
        rare_profiles.append({
            "pid": pid,
            "n_ae_terms": len(combo),
            "n_patients_sharing": ae_combo_counts[combo],
            "severe_count": patient_severe_count.get(pid,0),
            "ae_terms": list(combo)[:10],
        })

rare_profiles.sort(key=lambda x: (-x["n_ae_terms"], -x["severe_count"]))
print(f"[W4] Rare AE profiles (<=2 patients sharing, >=3 terms): {len(rare_profiles)}")
print("[W4] Top 10 rare profiles:")
for rp in rare_profiles[:10]:
    print(f"  {rp['pid']}: {rp['n_ae_terms']} terms, shared_by={rp['n_patients_sharing']}, severe={rp['severe_count']}")

# Mint RareAEProfile nodes (top 50)
for rank, rp in enumerate(rare_profiles[:50]):
    rp_id = f"rare_ae_profile:s11:{rp['pid'].replace(':','_')[:60]}"
    new_nodes.append(make_node(rp_id, "RareAEProfile",
        f"Rare AE profile: {rp['pid'].split(':')[-1]} ({rp['n_ae_terms']} terms)",
        patient_id=rp["pid"],
        n_ae_terms=rp["n_ae_terms"],
        n_patients_sharing=rp["n_patients_sharing"],
        severe_count=rp["severe_count"],
        ae_term_ids=json.dumps(rp["ae_terms"][:10]),
        rank=rank+1))
    if rp["pid"] in patient_nodes:
        new_edges.append(make_edge(rp["pid"], rp_id, "has_rare_ae_profile",
            n_terms=rp["n_ae_terms"], rank=rank+1))

# ── 3. Per-trial patient outlier ranking ─────────────────────────────────────
print("\n[W4] Per-trial patient outlier ranking...")
trial_patient_burdens = defaultdict(list)
for p in patient_burden_data:
    trial_patient_burdens[p["trial"]].append(p)

trial_outlier_summary = {}
for trial, patients in trial_patient_burdens.items():
    if len(patients) < 5:
        continue
    sc_vals = [p["severe_count"] for p in patients]
    threshold_1pct = np.percentile(sc_vals, 99)
    top_1pct = [p for p in patients if p["severe_count"] >= threshold_1pct and p["severe_count"] > 0]
    trial_outlier_summary[trial] = {
        "n_patients": len(patients),
        "threshold_99pct": round(threshold_1pct,1),
        "n_top1pct": len(top_1pct),
        "top_patient": top_1pct[0] if top_1pct else None,
    }

print("[W4] Top 10 trials by 99th percentile severe AE count:")
for trial, data in sorted(trial_outlier_summary.items(),
                           key=lambda x: -x[1]["threshold_99pct"])[:10]:
    print(f"  {trial}: n={data['n_patients']}, 99pct={data['threshold_99pct']:.1f}, "
          f"n_top1pct={data['n_top1pct']}")

# ── 4. Arm grade distribution + KL divergence ────────────────────────────────
print("\n[W4] Computing arm grade distributions and KL divergence...")
arm_grade_dist = defaultdict(lambda: Counter())
for e in ae_edges:
    attrs = e.get("attributes",{})
    grade = attrs.get("max_grade",0)
    # Find arm for this patient
    pid = e["source"]
    pnode = patient_nodes.get(pid,{})
    trial = pnode.get("attributes",{}).get("trial","?")
    arm = pnode.get("attributes",{}).get("arm","?")
    arm_key = f"{trial}:{arm}"
    arm_grade_dist[arm_key][grade] += 1

def kl_divergence(p_dist, q_dist, grades=[1,2,3,4,5]):
    """KL divergence D(P||Q) with Laplace smoothing."""
    p = np.array([p_dist.get(g,0)+1 for g in grades], dtype=float)
    q = np.array([q_dist.get(g,0)+1 for g in grades], dtype=float)
    p /= p.sum()
    q /= q.sum()
    return float(np.sum(p * np.log(p/q)))

# Compute KL divergence between arms within same trial
trial_arm_kl = {}
trial_arms = defaultdict(list)
for arm_key in arm_grade_dist:
    trial = arm_key.split(":")[0]
    trial_arms[trial].append(arm_key)

kl_results = []
for trial, arms in trial_arms.items():
    if len(arms) < 2:
        continue
    for i in range(len(arms)):
        for j in range(i+1, len(arms)):
            kl = kl_divergence(arm_grade_dist[arms[i]], arm_grade_dist[arms[j]])
            kl_results.append({"trial": trial, "arm_a": arms[i], "arm_b": arms[j], "kl": round(kl,4)})

kl_results.sort(key=lambda x: -x["kl"])
print(f"[W4] KL divergence pairs computed: {len(kl_results)}")
print("[W4] Top 15 arm pairs by KL divergence (most different grade profiles):")
for kr in kl_results[:15]:
    print(f"  {kr['arm_a']} vs {kr['arm_b']}: KL={kr['kl']:.4f}")

# Mint ArmGradeProfile nodes
arm_grade_nodes = {}
for arm_key, grade_dist in arm_grade_dist.items():
    total = sum(grade_dist.values())
    if total < 5:
        continue
    agp_id = f"arm_grade_profile:s11:{arm_key.replace(':','_')[:60]}"
    trial, arm = arm_key.split(":",1)
    new_nodes.append(make_node(agp_id, "ArmGradeProfile",
        f"Grade profile: {arm_key}",
        trial=trial, arm=arm,
        total_ae_events=total,
        grade_1=grade_dist.get(1,0), grade_2=grade_dist.get(2,0),
        grade_3=grade_dist.get(3,0), grade_4=grade_dist.get(4,0),
        grade_5=grade_dist.get(5,0),
        severe_fraction=round((grade_dist.get(3,0)+grade_dist.get(4,0)+grade_dist.get(5,0))/total,3),
        grade_distribution=json.dumps(dict(grade_dist))))
    arm_grade_nodes[arm_key] = agp_id

# Add KL divergence edges for top pairs
for kr in kl_results[:30]:
    if kr["arm_a"] in arm_grade_nodes and kr["arm_b"] in arm_grade_nodes:
        new_edges.append(make_edge(arm_grade_nodes[kr["arm_a"]],
                                   arm_grade_nodes[kr["arm_b"]],
                                   "grade_profile_divergence",
                                   kl_divergence=kr["kl"],
                                   trial=kr["trial"]))

# ── 5. OS event descriptive stats ────────────────────────────────────────────
print("\n[W4] OS event descriptive statistics (no survival inference)...")
os1_severe = [p["severe_count"] for p in patient_burden_data if str(p["os_event"])=="1"]
os0_severe = [p["severe_count"] for p in patient_burden_data if str(p["os_event"])=="0"]
os1_div = [p["ae_diversity"] for p in patient_burden_data if str(p["os_event"])=="1"]
os0_div = [p["ae_diversity"] for p in patient_burden_data if str(p["os_event"])=="0"]

print(f"[W4] OS event=1 (n={len(os1_severe)}): mean severe AEs={np.mean(os1_severe):.2f}, "
      f"mean diversity={np.mean(os1_div):.2f}")
print(f"[W4] OS event=0 (n={len(os0_severe)}): mean severe AEs={np.mean(os0_severe):.2f}, "
      f"mean diversity={np.mean(os0_div):.2f}")
print("[W4] NOTE: Descriptive only — no time-to-event data available, no survival inference.")

# ── Figures ──────────────────────────────────────────────────────────────────
# 1. Per-patient severe AE count distribution
fig, ax = plt.subplots(figsize=(12, 5))
ax.hist(severe_counts, bins=50, color='#0279EE', edgecolor='white', alpha=0.8)
ax.axvline(x=sc_threshold_3sig, color='red', linestyle='--', linewidth=2,
           label=f'3σ threshold ({sc_threshold_3sig:.1f})')
ax.axvline(x=sc_threshold_2sig, color='#FF9400', linestyle='--', linewidth=1.5,
           label=f'2σ threshold ({sc_threshold_2sig:.1f})')
ax.axvline(x=sc_mean, color='green', linestyle='-', linewidth=1.5,
           label=f'Mean ({sc_mean:.2f})')
ax.set_xlabel("Severe AE count per patient (max_grade ≥ 3)")
ax.set_ylabel("Number of patients")
ax.set_title(f"Per-Patient Severe AE Count Distribution (n={len(severe_counts):,} patients)\n"
             f"Outliers (>3σ): {sum(1 for c in severe_counts if c>sc_threshold_3sig)}")
ax.legend()
ax.grid(True, alpha=0.3)
ax.set_yscale('log')
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/w4_patient_ae_burden_distribution.png", dpi=150, bbox_inches='tight')
plt.close()
print("[W4] Saved patient_ae_burden_distribution.png")

# 2. Top 20 outlier patients bar chart
fig, ax = plt.subplots(figsize=(12, 7))
top20 = patient_burden_data[:20]
labels = [f"{p['pid'].split(':')[-1]}\n({p['trial'][:12]})" for p in top20]
vals = [p["severe_count"] for p in top20]
colors = ['#FF9400' if p["z_score"] > 3 else '#0279EE' for p in top20]
ax.bar(range(len(labels)), vals, color=colors)
ax.set_xticks(range(len(labels)))
ax.set_xticklabels(labels, rotation=45, ha='right', fontsize=7)
ax.axhline(y=sc_threshold_3sig, color='red', linestyle='--', linewidth=1.5,
           label=f'3σ threshold ({sc_threshold_3sig:.1f})')
ax.set_ylabel("Severe AE count (max_grade ≥ 3)")
ax.set_title("Top 20 Patients by Severe AE Count\n(Orange = z > 3σ)")
ax.legend()
ax.grid(True, alpha=0.3, axis='y')
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/w4_outlier_patients_top20.png", dpi=150, bbox_inches='tight')
plt.close()
print("[W4] Saved outlier_patients_top20.png")

# 3. Arm KL divergence heatmap (top trials)
top_kl_trials = list({kr["trial"] for kr in kl_results[:20]})[:8]
if top_kl_trials:
    fig, axes = plt.subplots(1, min(len(top_kl_trials),4), figsize=(16, 5))
    if len(top_kl_trials) == 1:
        axes = [axes]
    for ax_idx, trial in enumerate(top_kl_trials[:4]):
        ax = axes[ax_idx]
        trial_arms_list = trial_arms.get(trial, [])
        if len(trial_arms_list) < 2:
            ax.set_visible(False)
            continue
        n = len(trial_arms_list)
        mat = np.zeros((n,n))
        for i in range(n):
            for j in range(n):
                if i != j:
                    kl = kl_divergence(arm_grade_dist[trial_arms_list[i]],
                                       arm_grade_dist[trial_arms_list[j]])
                    mat[i,j] = kl
        im = ax.imshow(mat, cmap='Reds', aspect='auto')
        arm_labels = [a.split(":")[-1][:8] for a in trial_arms_list]
        ax.set_xticks(range(n))
        ax.set_yticks(range(n))
        ax.set_xticklabels(arm_labels, rotation=45, ha='right', fontsize=7)
        ax.set_yticklabels(arm_labels, fontsize=7)
        ax.set_title(f"{trial[:20]}", fontsize=8)
        plt.colorbar(im, ax=ax, label='KL div')
    plt.suptitle("Arm Grade Profile KL Divergence (within-trial)", fontsize=11)
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/w4_arm_kl_divergence.png", dpi=150, bbox_inches='tight')
    plt.close()
    print("[W4] Saved arm_kl_divergence.png")

# ── Save ─────────────────────────────────────────────────────────────────────
results = {
    "worker": 4, "session": SESSION, "timestamp": TS,
    "burden_stats": {
        "n_patients": len(patient_burden_data),
        "mean_severe": round(float(sc_mean),3),
        "std_severe": round(float(sc_std),3),
        "threshold_3sig": round(float(sc_threshold_3sig),2),
        "n_outliers_3sig": int(sum(1 for c in severe_counts if c>sc_threshold_3sig)),
        "n_outliers_2sig": int(sum(1 for c in severe_counts if c>sc_threshold_2sig)),
    },
    "top_outlier_patients": patient_burden_data[:20],
    "n_rare_profiles": len(rare_profiles),
    "n_arm_grade_profiles": len(arm_grade_nodes),
    "top_kl_pairs": kl_results[:20],
    "os_descriptive": {
        "os1_n": len(os1_severe), "os1_mean_severe": round(float(np.mean(os1_severe)),3) if os1_severe else 0,
        "os0_n": len(os0_severe), "os0_mean_severe": round(float(np.mean(os0_severe)),3) if os0_severe else 0,
        "note": "Descriptive only — no time-to-event data, no survival inference"
    },
    "new_nodes": new_nodes,
    "new_edges": new_edges,
}
with open("/mnt/shared-workspace/shared/s11_w4_results.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"[W4] DONE. New nodes: {len(new_nodes)}, new edges: {len(new_edges)}")
