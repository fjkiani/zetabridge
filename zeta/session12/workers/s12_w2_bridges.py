"""
S12 W2 — Bridge / bottleneck analysis on the cross-endpoint subgraph.

Pull the induced subgraph over the "connective tissue" node types that link
endpoints (vaults, cohorts, disease, dataset, community, biospecimen, specimen,
ega files, trials, genomic features, adverse events + the S11 bridge/path
nodes), then run networkx: articulation points, betweenness, and identify the
edges whose removal disconnects endpoint pairs (bridges). Mint StructuralBridge
nodes for the true connectors. Deepens S11's "MSK vault = top betweenness".
"""
import sys
sys.path.insert(0, "/mnt/shared-workspace/shared")
import s12_traverse_util as U
import networkx as nx
from collections import Counter

drv = U.driver()
nodes, edges = [], []
results = {"worker": "w2_bridges"}

# Build the connective subgraph: everything EXCEPT the massive patient/AE/signal
# leaf clouds, which don't participate in cross-endpoint connectivity.
# Keep nodes whose id-prefix is structural.
KEEP_PREFIXES = ["vault:", "cohort:", "disease:", "ega:dataset", "ega:file:", "ega:sample:",
                 "specimen:britroc1:", "biospecimen:msk:", "genomicfeature:msk:",
                 "trial:sas:", "trial_design:sas:", "clinical_table:sas:", "arm:sas:",
                 "community:louvain:", "ae:pt:", "bridge:", "xpath:", "fedpath:",
                 "longitudinal_pair:"]
pref_filter = " OR ".join(f"a.id STARTS WITH '{p}'" for p in KEEP_PREFIXES)
tgt_filter = pref_filter.replace("a.id", "b.id")
q = (f"MATCH (a)-[r]-(b) WHERE ({pref_filter}) AND ({tgt_filter}) "
     f"RETURN DISTINCT a.id AS s, b.id AS t, type(r) AS rt")
rows = U.read(drv, q, cap=200000)
G = nx.Graph()
for r in rows:
    if r["s"] != r["t"]:
        G.add_edge(r["s"], r["t"], type=r["rt"])
print(f"[w2] connective subgraph: {G.number_of_nodes()} nodes, {G.number_of_edges()} edges")

# endpoint labels on nodes
for n in G.nodes():
    G.nodes[n]["ep"] = U.endpoint_of(n)

# Articulation points (cut vertices) — removal increases components
artic = list(nx.articulation_points(G))
# rank articulation points by how many endpoints they touch in their neighborhood
def ep_span(node):
    eps = {G.nodes[nb]["ep"] for nb in G.neighbors(node)}
    eps.discard(None)
    return eps
artic_ranked = sorted(artic, key=lambda n: (-len(ep_span(n)), -G.degree(n)))
results["n_articulation_points"] = len(artic)
print(f"[w2] {len(artic)} articulation points")

# Betweenness on the connective subgraph (small -> exact)
bc = nx.betweenness_centrality(G, normalized=True)
top_bc = sorted(bc.items(), key=lambda kv: -kv[1])[:20]
results["top_betweenness"] = [{"id": k, "betweenness": round(v, 5),
                               "endpoint": U.endpoint_of(k)} for k, v in top_bc]
print("[w2] top betweenness:", top_bc[0])

# Bridges (edges whose removal disconnects the graph)
bridge_edges = list(nx.bridges(G))
# keep bridges that connect different endpoints (true federation cut edges)
cross_bridges = [(u, v) for u, v in bridge_edges
                 if G.nodes[u]["ep"] and G.nodes[v]["ep"]
                 and G.nodes[u]["ep"] != G.nodes[v]["ep"]]
results["n_bridge_edges"] = len(bridge_edges)
results["n_cross_endpoint_bridge_edges"] = len(cross_bridges)
print(f"[w2] {len(bridge_edges)} bridge edges, {len(cross_bridges)} cross-endpoint")

# Mint StructuralBridge nodes for top articulation points that span >=2 endpoints
minted = 0
for n in artic_ranked[:15]:
    span = ep_span(n)
    if len(span) < 2:
        continue
    nid = f"bridge:s12:artic:{n.replace(':','_')}"
    nodes.append(U.mk_node(
        nid, "StructuralBridge", f"Articulation point {n} spanning {sorted(span)}",
        {"node": n, "node_endpoint": U.endpoint_of(n), "degree": G.degree(n),
         "endpoint_span": sorted(span), "betweenness": round(bc.get(n, 0), 5),
         "kind": "articulation_point"},
        label="StructuralBridge"))
    edges.append(U.mk_edge(nid, "bridges_node", n))
    minted += 1

# Mint Bottleneck nodes for cross-endpoint bridge edges (the literal cut edges)
for i, (u, v) in enumerate(cross_bridges[:15]):
    nid = f"bottleneck:s12:{i}"
    nodes.append(U.mk_node(
        nid, "Bottleneck", f"Cut edge {u} <-> {v}",
        {"node_u": u, "node_v": v, "endpoint_u": G.nodes[u]["ep"],
         "endpoint_v": G.nodes[v]["ep"], "rel_type": G[u][v].get("type"),
         "kind": "cross_endpoint_bridge_edge"},
        label="Bottleneck"))
    edges.append(U.mk_edge(nid, "cuts_between", u))
    edges.append(U.mk_edge(nid, "cuts_between", v))

results["subgraph_nodes"] = G.number_of_nodes()
results["subgraph_edges"] = G.number_of_edges()
results["n_nodes"] = len(nodes)
results["n_edges"] = len(edges)
U.dump_results("/mnt/shared-workspace/shared/s12_w2_results.json",
               {"results": results, "nodes": nodes, "edges": edges})
print(f"[w2] DONE nodes={len(nodes)} edges={len(edges)} (artic minted + bottlenecks)")
drv.close()
