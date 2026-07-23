"""
Session 11 Worker 0 — Graph Topology + Centrality + Community Detection
PageRank, Betweenness, Louvain, WCC, Rich-club
"""
import json, os, math, time
from datetime import datetime
from collections import Counter, defaultdict
import networkx as nx
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
matplotlib.rcParams['font.family'] = ['Liberation Sans', 'Arimo', 'DejaVu Sans']
matplotlib.rcParams['svg.fonttype'] = 'none'

OUT_DIR = "/mnt/results/session11"
os.makedirs(OUT_DIR, exist_ok=True)

TS = datetime.utcnow().isoformat() + "Z"
SESSION = 11
MINT_PLANNER = "zeta_custodian_session11"

print(f"[W0] Loading KG... {TS}")
with open("/mnt/results/zeta_vault/kg/zeta_entities.json") as f:
    ents = json.load(f)
with open("/mnt/results/zeta_vault/kg/zeta_edges.json") as f:
    edges = json.load(f)

id_to_type = {e["id"]: e.get("type", "?") for e in ents}
id_to_name = {e["id"]: e.get("name", "") for e in ents}
print(f"[W0] Loaded: {len(ents)} entities, {len(edges)} edges")

# ── Build full DiGraph ──────────────────────────────────────────────────────
print("[W0] Building DiGraph...")
G = nx.DiGraph()
for e in ents:
    G.add_node(e["id"], type=e.get("type","?"), name=e.get("name",""))
for e in edges:
    G.add_edge(e["source"], e["target"], relation=e.get("relation","?"))
print(f"[W0] DiGraph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# ── 1. PageRank ─────────────────────────────────────────────────────────────
print("[W0] Computing PageRank...")
t0 = time.time()
pr = nx.pagerank(G, alpha=0.85, max_iter=100, tol=1e-6)
print(f"[W0] PageRank done in {time.time()-t0:.1f}s")

pr_sorted = sorted(pr.items(), key=lambda x: -x[1])
print("[W0] Top 30 PageRank nodes:")
for nid, score in pr_sorted[:30]:
    print(f"  {nid} | type={id_to_type.get(nid,'?')} | PR={score:.6f}")

# PageRank on signal subgraph (exclude TrialPatient — too many, dominate)
SIGNAL_TYPES = {"Trial","TreatmentArm","AdverseEventTerm","DrugAESignal",
                "DrugIntervention","CrossTrialEscalationPattern","SerialEscalationSignal",
                "ArmAEProfile","PharmacovigSignal","GenomicFeature","DataVault","PatientCohort"}
signal_nodes = {e["id"] for e in ents if e.get("type","?") in SIGNAL_TYPES}
G_sig = G.subgraph(signal_nodes).copy()
print(f"[W0] Signal subgraph: {G_sig.number_of_nodes()} nodes, {G_sig.number_of_edges()} edges")
pr_sig = nx.pagerank(G_sig, alpha=0.85, max_iter=200, tol=1e-6)
pr_sig_sorted = sorted(pr_sig.items(), key=lambda x: -x[1])
print("[W0] Top 20 Signal-subgraph PageRank:")
for nid, score in pr_sig_sorted[:20]:
    print(f"  {nid} | type={id_to_type.get(nid,'?')} | PR={score:.6f}")

# ── 2. Betweenness centrality (signal subgraph only) ────────────────────────
print("[W0] Computing betweenness centrality on signal subgraph...")
t0 = time.time()
bc = nx.betweenness_centrality(G_sig, normalized=True, k=min(500, G_sig.number_of_nodes()))
bc_sorted = sorted(bc.items(), key=lambda x: -x[1])
print(f"[W0] Betweenness done in {time.time()-t0:.1f}s")
print("[W0] Top 20 Betweenness:")
for nid, score in bc_sorted[:20]:
    print(f"  {nid} | type={id_to_type.get(nid,'?')} | BC={score:.6f}")

# ── 3. Louvain community detection ──────────────────────────────────────────
print("[W0] Running Louvain community detection...")
G_und = G.to_undirected()
try:
    # Use networkx native Louvain (avoids python-louvain package-name collision)
    communities = nx.community.louvain_communities(G_und, seed=42)
    partition = {}
    for cid, members in enumerate(communities):
        for nid in members:
            partition[nid] = cid
    n_communities = len(communities)
    print(f"[W0] Louvain: {n_communities} communities")
    comm_members = defaultdict(list)
    for nid, comm_id in partition.items():
        comm_members[comm_id].append(nid)
    comm_sizes = sorted([(k, len(v)) for k, v in comm_members.items()], key=lambda x: -x[1])
    print("[W0] Top 15 communities by size:")
    for cid, sz in comm_sizes[:15]:
        members = comm_members[cid]
        type_dist = Counter(id_to_type.get(n,"?") for n in members)
        dominant = type_dist.most_common(1)[0][0]
        print(f"  Community {cid}: size={sz}, dominant_type={dominant}, types={dict(type_dist.most_common(3))}")
except Exception as e:
    print(f"[W0] Louvain failed: {e}")
    partition = {}
    comm_members = {}
    comm_sizes = []

# ── 4. Weakly Connected Components ──────────────────────────────────────────
print("[W0] Computing WCC...")
wccs = list(nx.weakly_connected_components(G))
wcc_sizes = sorted([len(c) for c in wccs], reverse=True)
print(f"[W0] WCC: {len(wccs)} components")
print(f"  Largest: {wcc_sizes[0]}, 2nd: {wcc_sizes[1] if len(wcc_sizes)>1 else 0}")
print(f"  Singletons (size=1): {sum(1 for s in wcc_sizes if s==1)}")
print(f"  Size 2-5: {sum(1 for s in wcc_sizes if 2<=s<=5)}")

# Characterize isolated nodes by type
isolated = [n for n in G.nodes() if G.degree(n) == 0]
singleton_wcc = [list(c)[0] for c in wccs if len(c)==1]
iso_types = Counter(id_to_type.get(n,"?") for n in isolated)
sing_types = Counter(id_to_type.get(n,"?") for n in singleton_wcc)
print(f"[W0] Isolated (degree=0) by type: {dict(iso_types)}")
print(f"[W0] Singleton WCC by type: {dict(sing_types)}")

# ── 5. Rich-club coefficient ─────────────────────────────────────────────────
print("[W0] Computing rich-club coefficient...")
G_und_simple = nx.Graph(G_und)  # remove multi-edges
rc = nx.rich_club_coefficient(G_und_simple, normalized=False)
thresholds = [10, 25, 50, 100]
print("[W0] Rich-club coefficients:")
for k in thresholds:
    if k in rc:
        print(f"  k={k}: phi={rc[k]:.4f}")

# ── Degree distribution stats ────────────────────────────────────────────────
degrees = dict(G.degree())
deg_vals = list(degrees.values())
print(f"\n[W0] Degree stats: min={min(deg_vals)}, max={max(deg_vals)}, "
      f"mean={np.mean(deg_vals):.2f}, median={np.median(deg_vals):.1f}, "
      f"std={np.std(deg_vals):.2f}")
print(f"  Nodes deg>=100: {sum(1 for d in deg_vals if d>=100)}")
print(f"  Nodes deg>=500: {sum(1 for d in deg_vals if d>=500)}")
print(f"  Nodes deg==0: {sum(1 for d in deg_vals if d==0)}")
print(f"  Nodes deg==1: {sum(1 for d in deg_vals if d==1)}")

# ── Mint new KG nodes ────────────────────────────────────────────────────────
new_nodes = []
new_edges = []

def make_node(nid, ntype, name, **kwargs):
    return {"id": nid, "type": ntype, "name": name,
            "source_receipts": ["Session 11 W0 graph analysis"],
            "verbatim_evidence": [], "cross_refs": {},
            "attributes": {k: v for k, v in kwargs.items()},
            "_session": SESSION, "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

def make_edge(src, tgt, rel, **attrs):
    return {"source": src, "target": tgt, "relation": rel,
            "attributes": attrs, "_session": SESSION,
            "_mint_planner": MINT_PLANNER, "_mint_timestamp": TS}

# GraphCentralityScore nodes — top 50 PageRank (full graph)
for rank, (nid, score) in enumerate(pr_sorted[:50]):
    cn_id = f"centrality:pagerank:s11:{rank+1}"
    new_nodes.append(make_node(cn_id, "GraphCentralityScore",
        f"PageRank rank {rank+1}: {id_to_name.get(nid,'')[:40]}",
        algorithm="pagerank", rank=rank+1, score=round(score,8),
        target_node=nid, target_type=id_to_type.get(nid,"?"),
        degree=degrees.get(nid,0)))
    new_edges.append(make_edge(nid, cn_id, "has_centrality_score",
        algorithm="pagerank", rank=rank+1, score=round(score,8)))

# Top 20 signal-subgraph PageRank
for rank, (nid, score) in enumerate(pr_sig_sorted[:20]):
    cn_id = f"centrality:pagerank_signal:s11:{rank+1}"
    new_nodes.append(make_node(cn_id, "GraphCentralityScore",
        f"Signal-PR rank {rank+1}: {id_to_name.get(nid,'')[:40]}",
        algorithm="pagerank_signal_subgraph", rank=rank+1, score=round(score,8),
        target_node=nid, target_type=id_to_type.get(nid,"?")))
    new_edges.append(make_edge(nid, cn_id, "has_centrality_score",
        algorithm="pagerank_signal_subgraph", rank=rank+1))

# Top 20 betweenness
for rank, (nid, score) in enumerate(bc_sorted[:20]):
    cn_id = f"centrality:betweenness:s11:{rank+1}"
    new_nodes.append(make_node(cn_id, "GraphCentralityScore",
        f"Betweenness rank {rank+1}: {id_to_name.get(nid,'')[:40]}",
        algorithm="betweenness", rank=rank+1, score=round(score,8),
        target_node=nid, target_type=id_to_type.get(nid,"?")))
    new_edges.append(make_edge(nid, cn_id, "has_centrality_score",
        algorithm="betweenness", rank=rank+1))

# GraphCommunity nodes (communities with >=5 members)
for cid, sz in comm_sizes:
    if sz < 5:
        continue
    members = comm_members[cid]
    type_dist = Counter(id_to_type.get(n,"?") for n in members)
    dominant = type_dist.most_common(1)[0][0]
    top_pr = sorted([(n, pr.get(n,0)) for n in members], key=lambda x:-x[1])[:5]
    gc_id = f"community:louvain:s11:{cid}"
    new_nodes.append(make_node(gc_id, "GraphCommunity",
        f"Louvain community {cid} (n={sz}, dominant={dominant})",
        algorithm="louvain", community_id=cid, size=sz,
        dominant_type=dominant,
        type_distribution=json.dumps(dict(type_dist.most_common(5))),
        top_pagerank_members=json.dumps([n for n,_ in top_pr[:3]])))
    for n in members[:20]:  # edge to top 20 members only
        new_edges.append(make_edge(n, gc_id, "member_of_community",
            community_id=cid, dominant_type=dominant))

# StructuralGap nodes for isolated/singleton clusters
for ntype, count in iso_types.items():
    sg_id = f"structural_gap:isolated:{ntype.lower()}:s11"
    new_nodes.append(make_node(sg_id, "StructuralGap",
        f"Isolated nodes (degree=0): {ntype} ({count} nodes)",
        gap_type="isolated_degree_zero", node_type=ntype, count=count,
        note="These nodes have no edges in the KG — potential ingestion gaps or intentional stubs"))

print(f"\n[W0] Minted: {len(new_nodes)} nodes, {len(new_edges)} edges")

# ── Figures ──────────────────────────────────────────────────────────────────
# 1. PageRank distribution (log-scale)
fig, ax = plt.subplots(figsize=(10, 5))
pr_vals = np.array(sorted(pr.values(), reverse=True))
ax.semilogy(range(1, len(pr_vals)+1), pr_vals, color='#0279EE', linewidth=1.5)
ax.axvline(x=50, color='#FF9400', linestyle='--', label='Top 50 threshold')
ax.set_xlabel("Node rank (by PageRank)")
ax.set_ylabel("PageRank score (log scale)")
ax.set_title("PageRank Distribution — Zeta KG (26,312 nodes)")
ax.legend()
ax.grid(True, alpha=0.3)
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/w0_pagerank_distribution.png", dpi=150, bbox_inches='tight')
plt.close()
print(f"[W0] Saved pagerank_distribution.png")

# 2. Community size distribution
if comm_sizes:
    fig, ax = plt.subplots(figsize=(10, 5))
    sizes = [s for _, s in comm_sizes]
    ax.hist(sizes, bins=30, color='#75A025', edgecolor='white', linewidth=0.5)
    ax.set_xlabel("Community size (nodes)")
    ax.set_ylabel("Count")
    ax.set_title(f"Louvain Community Size Distribution ({len(comm_sizes)} communities)")
    ax.axvline(x=5, color='#FF9400', linestyle='--', label='Min size threshold (5)')
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.savefig(f"{OUT_DIR}/w0_community_sizes.png", dpi=150, bbox_inches='tight')
    plt.close()
    print(f"[W0] Saved community_sizes.png")

# 3. Betweenness top-20 bar chart
fig, ax = plt.subplots(figsize=(12, 6))
top_bc = bc_sorted[:20]
labels = [f"{id_to_type.get(n,'?')[:8]}:{id_to_name.get(n,'')[:20]}" for n,_ in top_bc]
vals = [s for _,s in top_bc]
colors = ['#0279EE' if id_to_type.get(n,'?') in ('AdverseEventTerm','DrugAESignal') else
          '#FF9400' if id_to_type.get(n,'?') in ('Trial','TreatmentArm') else
          '#75A025' for n,_ in top_bc]
bars = ax.barh(range(len(labels)), vals, color=colors)
ax.set_yticks(range(len(labels)))
ax.set_yticklabels(labels, fontsize=8)
ax.set_xlabel("Betweenness Centrality (normalized)")
ax.set_title("Top 20 Betweenness Centrality — Signal Subgraph")
ax.invert_yaxis()
ax.grid(True, alpha=0.3, axis='x')
plt.tight_layout()
plt.savefig(f"{OUT_DIR}/w0_betweenness_top20.png", dpi=150, bbox_inches='tight')
plt.close()
print(f"[W0] Saved betweenness_top20.png")

# ── Save outputs ─────────────────────────────────────────────────────────────
results = {
    "worker": 0, "session": SESSION, "timestamp": TS,
    "pagerank_top50": [(nid, round(s,8)) for nid,s in pr_sorted[:50]],
    "pagerank_signal_top20": [(nid, round(s,8)) for nid,s in pr_sig_sorted[:20]],
    "betweenness_top20": [(nid, round(s,8)) for nid,s in bc_sorted[:20]],
    "n_communities": len(comm_sizes),
    "community_sizes": [(cid, sz) for cid,sz in comm_sizes[:20]],
    "wcc_count": len(wccs),
    "wcc_largest": wcc_sizes[0] if wcc_sizes else 0,
    "isolated_by_type": dict(iso_types),
    "singleton_by_type": dict(sing_types),
    "rich_club": {str(k): round(rc.get(k,0),4) for k in thresholds},
    "degree_stats": {
        "min": int(min(deg_vals)), "max": int(max(deg_vals)),
        "mean": round(float(np.mean(deg_vals)),2),
        "median": float(np.median(deg_vals)),
        "std": round(float(np.std(deg_vals)),2),
        "n_deg_ge_100": int(sum(1 for d in deg_vals if d>=100)),
        "n_isolated": int(sum(1 for d in deg_vals if d==0)),
        "n_singleton": int(sum(1 for d in deg_vals if d==1)),
    },
    "new_nodes": new_nodes,
    "new_edges": new_edges,
}

with open("/mnt/shared-workspace/shared/s11_w0_results.json", "w") as f:
    json.dump(results, f, indent=2)
print(f"[W0] Saved results to shared workspace")
print(f"[W0] DONE. New nodes: {len(new_nodes)}, new edges: {len(new_edges)}")
