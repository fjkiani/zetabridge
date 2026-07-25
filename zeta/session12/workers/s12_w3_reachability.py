"""
S12 W3 — Reachability + metapath mining.

For each endpoint pair, compute what fraction of endpoint-X nodes can reach
endpoint-Y within N hops, and mine the dominant relationship-type sequences
(metapaths) that carry those connections. Uses the connective subgraph (same
node set as W2) so BFS is cheap and meaningful. Mint Metapath nodes ranked by
frequency and a ReachabilityProfile node per pair.
"""
import sys
sys.path.insert(0, "/mnt/shared-workspace/shared")
import s12_traverse_util as U
import networkx as nx
from collections import Counter

drv = U.driver()
nodes, edges = [], []
results = {"worker": "w3_reachability", "pairs": {}}

KEEP_PREFIXES = ["vault:", "cohort:", "disease:", "ega:dataset", "ega:file:", "ega:sample:",
                 "specimen:britroc1:", "biospecimen:msk:", "genomicfeature:msk:",
                 "trial:sas:", "trial_design:sas:", "clinical_table:sas:", "arm:sas:",
                 "community:louvain:", "ae:pt:", "bridge:", "longitudinal_pair:"]
pref_filter = " OR ".join(f"a.id STARTS WITH '{p}'" for p in KEEP_PREFIXES)
tgt_filter = pref_filter.replace("a.id", "b.id")
q = (f"MATCH (a)-[r]-(b) WHERE ({pref_filter}) AND ({tgt_filter}) "
     f"RETURN DISTINCT a.id AS s, b.id AS t, type(r) AS rt")
rows = U.read(drv, q, cap=200000)
G = nx.Graph()
for r in rows:
    if r["s"] != r["t"]:
        G.add_edge(r["s"], r["t"], type=r["rt"])
for n in G.nodes():
    G.nodes[n]["ep"] = U.endpoint_of(n)
print(f"[w3] connective subgraph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

by_ep = {}
for n in G.nodes():
    by_ep.setdefault(G.nodes[n]["ep"], []).append(n)
for ep in ("A_MSK", "B_SAS", "C_EGA"):
    print(f"[w3] {ep}: {len(by_ep.get(ep, []))} nodes in connective subgraph")

MAX_HOPS = 6
PAIRS = [("A_MSK", "B_SAS"), ("A_MSK", "C_EGA"), ("B_SAS", "C_EGA"),
         ("B_SAS", "A_MSK"), ("C_EGA", "A_MSK"), ("C_EGA", "B_SAS")]

def metapath_for(path):
    seq = []
    for i in range(len(path) - 1):
        seq.append(G[path[i]][path[i + 1]].get("type"))
    return "->".join(seq)

for a, b in PAIRS:
    src_nodes = by_ep.get(a, [])
    tgt_set = set(by_ep.get(b, []))
    if not src_nodes or not tgt_set:
        continue
    reached = 0
    metapaths = Counter()
    sample = src_nodes[:60]  # bound BFS to a sample for large endpoints
    for s in sample:
        # single-source shortest paths up to MAX_HOPS
        lengths = nx.single_source_shortest_path_length(G, s, cutoff=MAX_HOPS)
        tgts = [t for t in lengths if t in tgt_set and t != s]
        if tgts:
            reached += 1
            # nearest target -> its metapath
            nearest = min(tgts, key=lambda t: lengths[t])
            try:
                p = nx.shortest_path(G, s, nearest)
                metapaths[metapath_for(p)] += 1
            except nx.NetworkXNoPath:
                pass
    frac = reached / len(sample) if sample else 0
    results["pairs"][f"{a}->{b}"] = {
        "sampled": len(sample), "reached": reached,
        "reach_fraction": round(frac, 3),
        "top_metapaths": metapaths.most_common(5)}
    print(f"[w3] {a}->{b}: reach {reached}/{len(sample)} ({frac:.0%}); "
          f"top metapath {metapaths.most_common(1)}")

    # ReachabilityProfile node
    rid = f"reach:s12:{a}:{b}"
    nodes.append(U.mk_node(
        rid, "ReachabilityProfile", f"{a}->{b} reachability (<= {MAX_HOPS} hops)",
        {"endpoint_from": a, "endpoint_to": b, "sampled": len(sample),
         "reached": reached, "reach_fraction": round(frac, 3),
         "top_metapaths": metapaths.most_common(5), "max_hops": MAX_HOPS},
        label="ReachabilityProfile"))
    # Metapath nodes for the dominant sequences
    for j, (mp, cnt) in enumerate(metapaths.most_common(3)):
        mid = f"metapath:s12:{a}:{b}:{j}"
        nodes.append(U.mk_node(
            mid, "Metapath", f"{a}->{b}: {mp}",
            {"endpoint_from": a, "endpoint_to": b, "rel_sequence": mp.split("->"),
             "frequency": cnt, "n_hops": mp.count("->") + 1},
            label="Metapath"))
        edges.append(U.mk_edge(mid, "instance_of_reachability", rid))

results["n_nodes"] = len(nodes)
results["n_edges"] = len(edges)
U.dump_results("/mnt/shared-workspace/shared/s12_w3_results.json",
               {"results": results, "nodes": nodes, "edges": edges})
print(f"[w3] DONE nodes={len(nodes)} edges={len(edges)}")
drv.close()
