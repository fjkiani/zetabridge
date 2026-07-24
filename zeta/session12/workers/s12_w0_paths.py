"""
S12 W0 — Cross-endpoint path enumeration (node-by-node graph travel).

For each ordered endpoint pair (A->B, A->C, B->C and reverses), pick seed sets
of representative nodes and enumerate shortest paths that cross the boundary.
Rank by hop count; record the full node-id chain + relationship-type sequence.
Mint one CrossEndpointPath node per distinct (src, tgt) shortest path, plus a
CrossEndpointPathSummary per endpoint-pair with the dominant relationship
sequences. Deep, not shallow: we keep every intermediate node.
"""
import sys, os, json
from collections import Counter
sys.path.insert(0, "/mnt/shared-workspace/shared")
import s12_traverse_util as U

drv = U.driver()
nodes, edges = [], []
results = {"worker": "w0_cross_endpoint_paths", "pairs": {}, "n_paths_minted": 0}

# Seed sets: high-value, low-cardinality anchors per endpoint (verified prefixes).
SEEDS = {
    "A_MSK": "MATCH (n) WHERE n.id STARTS WITH 'genomicfeature:msk:' RETURN n.id AS id LIMIT 25",
    "B_SAS": "MATCH (n) WHERE n.id STARTS WITH 'trial:sas:' RETURN n.id AS id LIMIT 25",
    "C_EGA": "MATCH (n) WHERE n.id STARTS WITH 'ega:file:' RETURN n.id AS id LIMIT 25",
}
seed_ids = {ep: [r["id"] for r in U.read(drv, q)] for ep, q in SEEDS.items()}
for ep, ids in seed_ids.items():
    print(f"[w0] seeds {ep}: {len(ids)}")

PAIRS = [("A_MSK", "B_SAS"), ("A_MSK", "C_EGA"), ("B_SAS", "C_EGA")]
MAX_HOPS = 6
TARGET_PREFIX = {
    "A_MSK": "genomicfeature:msk:", "B_SAS": "trial:sas:", "C_EGA": "ega:file:",
}

minted_pair_keys = set()
for a, b in PAIRS:
    pair_paths = []
    rel_seqs = Counter()
    # sample up to 8 source seeds per direction to bound cost
    for src in seed_ids[a][:8]:
        q = (
            f"MATCH (s {{id:$src}}), (t) WHERE t.id STARTS WITH $tpfx AND t <> s "
            f"MATCH p = shortestPath((s)-[*1..{MAX_HOPS}]-(t)) "
            f"WITH p, length(p) AS hops ORDER BY hops ASC LIMIT 5 "
            f"RETURN [n IN nodes(p) | n.id] AS ids, [r IN relationships(p) | type(r)] AS rels, hops"
        )
        for r in U.read(drv, q, {"src": src, "tpfx": TARGET_PREFIX[b]}):
            ids, rels, hops = r["ids"], r["rels"], r["hops"]
            crosses = len({U.endpoint_of(x) for x in ids if U.endpoint_of(x)}) > 1
            if not crosses:
                continue
            rel_seqs["->".join(rels)] += 1
            pair_paths.append({"src": ids[0], "tgt": ids[-1], "hops": hops,
                               "node_ids": ids, "rel_types": rels})
    # dedup by (src,tgt)
    seen = {}
    for pp in pair_paths:
        key = (pp["src"], pp["tgt"])
        if key not in seen or pp["hops"] < seen[key]["hops"]:
            seen[key] = pp
    pair_paths = sorted(seen.values(), key=lambda x: x["hops"])
    results["pairs"][f"{a}->{b}"] = {
        "n_paths": len(pair_paths),
        "min_hops": pair_paths[0]["hops"] if pair_paths else None,
        "max_hops": pair_paths[-1]["hops"] if pair_paths else None,
        "top_rel_sequences": rel_seqs.most_common(5),
        "example": pair_paths[0]["node_ids"] if pair_paths else None,
    }
    print(f"[w0] {a}->{b}: {len(pair_paths)} distinct cross-endpoint shortest paths")

    # mint nodes (cap 12 per pair to keep the batch focused on the strongest/shortest)
    for i, pp in enumerate(pair_paths[:12]):
        nid = f"xpath:s12:{a}:{b}:{i}"
        n = U.mk_node(
            nid, "CrossEndpointPath",
            f"{a}->{b} path {pp['src']} -> {pp['tgt']} ({pp['hops']} hops)",
            {"endpoint_a": a, "endpoint_b": b, "source_node": pp["src"],
             "target_node": pp["tgt"], "hops": pp["hops"],
             "node_chain": pp["node_ids"], "rel_sequence": pp["rel_types"],
             "n_intermediate": len(pp["node_ids"]) - 2},
            label="CrossEndpointPath")
        nodes.append(n)
        # edge linking the path node to its two anchor endpoints
        edges.append(U.mk_edge(nid, "path_source", pp["src"]))
        edges.append(U.mk_edge(nid, "path_target", pp["tgt"]))
        # traversal edges: connect path node to each intermediate node it passes through
        for mid in pp["node_ids"][1:-1]:
            edges.append(U.mk_edge(nid, "traverses", mid))

    # pair summary node
    if pair_paths:
        sid = f"xpathsummary:s12:{a}:{b}"
        nodes.append(U.mk_node(
            sid, "CrossEndpointPathSummary",
            f"{a}<->{b} traversal summary",
            {"endpoint_a": a, "endpoint_b": b, "n_distinct_paths": len(pair_paths),
             "min_hops": pair_paths[0]["hops"], "max_hops": pair_paths[-1]["hops"],
             "dominant_rel_sequences": rel_seqs.most_common(5)},
            label="CrossEndpointPathSummary"))

results["n_paths_minted"] = sum(1 for n in nodes if n["type"] == "CrossEndpointPath")
results["n_nodes"] = len(nodes)
results["n_edges"] = len(edges)
out = {"results": results, "nodes": nodes, "edges": edges}
U.dump_results("/mnt/shared-workspace/shared/s12_w0_results.json", out)
print(f"[w0] DONE nodes={len(nodes)} edges={len(edges)} paths={results['n_paths_minted']}")
drv.close()
