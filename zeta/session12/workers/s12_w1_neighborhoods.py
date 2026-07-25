"""
S12 W1 — Seed-node neighborhood extraction (n-hop induced subgraphs).

For high-value seeds carried over from Session 11 (largest treatment arms,
top-ranked AE-burden patient scores, bridge genes, large EGA-file outliers),
extract 2-hop induced subgraphs using apoc.path.subgraphAll and characterize
them: size, boundary-crossing edges (edges leaving the seed's endpoint),
relationship-type mix, endpoints touched. Mint one NodeNeighborhood node per
seed with member + boundary detail.

Schema note (verified live, Session 12): S11 analytic nodes store their
metrics inside an `attributes_json` string, and the seed properties assumed by
an earlier draft (arm.total_burden, score.z_score) do NOT exist as top-level
props. Real seeds:
  - treatment arms rank by top-level `n_patients` (int)
  - PatientAEBurdenScore rank by `rank` (1 = highest z) inside attributes_json
  - EGAFileOutlier `file_id` inside attributes_json points to the ega:file node
"""
import json
import sys

sys.path.insert(0, "/mnt/shared-workspace/shared")
import s12_traverse_util as U
from collections import Counter

drv = U.driver()
nodes, edges = [], []
results = {"worker": "w1_neighborhoods", "neighborhoods": []}


def aj(props):
    try:
        return json.loads(props.get("attributes_json", "") or "{}")
    except Exception:
        return {}


# --- Resolve high-value seeds dynamically against the REAL schema ----------
seed_specs = []

# (a) largest treatment arms by n_patients (most patient/AE connectivity)
r = U.read(drv, "MATCH (n) WHERE n.id STARTS WITH 'arm:sas:' AND n.n_patients IS NOT NULL "
                "RETURN n.id AS id ORDER BY toInteger(n.n_patients) DESC LIMIT 3")
seed_specs += [("largest_arm", x["id"]) for x in r]

# (b) top-ranked AE-burden patient scores (rank 1 = highest z-score)
r = U.read(drv, "MATCH (n) WHERE n.type='PatientAEBurdenScore' "
                "RETURN n.id AS id, n.attributes_json AS aj")
scored = []
for x in r:
    try:
        rk = json.loads(x["aj"] or "{}").get("rank")
        if rk is not None:
            scored.append((int(rk), x["id"]))
    except Exception:
        pass
scored.sort()
seed_specs += [("top_ae_burden_patient", sid) for _, sid in scored[:3]]

# (c) genomic driver genes (bridge nodes to adverse events)
r = U.read(drv, "MATCH (n) WHERE n.id STARTS WITH 'genomicfeature:msk:mut:' "
                "RETURN n.id AS id LIMIT 5")
seed_specs += [("genomic_driver", x["id"]) for x in r]

# (d) large EGA-file outliers -> resolve to the real ega:file node
r = U.read(drv, "MATCH (n) WHERE n.type='EGAFileOutlier' RETURN n.attributes_json AS aj LIMIT 3")
for x in r:
    fid = aj(x).get("file_id")
    if fid:
        seed_specs += [("ega_file_outlier", fid)]

print(f"[w1] {len(seed_specs)} seeds resolved: {[k for k,_ in seed_specs]}")

for kind, sid in seed_specs:
    q = ("MATCH (s {id:$id}) "
         "CALL apoc.path.subgraphAll(s, {maxLevel:2, limit:400}) "
         "YIELD nodes AS ns, relationships AS rs "
         "RETURN [n IN ns | n.id] AS node_ids, "
         "[r IN rs | {s:startNode(r).id, t:endNode(r).id, type:type(r)}] AS rels")
    rows = U.read(drv, q, {"id": sid}, cap=1)
    if not rows:
        print(f"[w1] seed not found / no subgraph: {sid}")
        continue
    node_ids = rows[0]["node_ids"] or []
    rels = rows[0]["rels"] or []
    seed_ep = U.endpoint_of(sid)
    endpoints_touched = Counter(U.endpoint_of(x) for x in node_ids if U.endpoint_of(x))
    boundary_edges = [e for e in rels
                      if U.endpoint_of(e["s"]) and U.endpoint_of(e["t"])
                      and U.endpoint_of(e["s"]) != U.endpoint_of(e["t"])]
    rel_mix = Counter(e["type"] for e in rels)
    nb = {"seed": sid, "kind": kind, "seed_endpoint": seed_ep,
          "n_nodes": len(node_ids), "n_edges": len(rels),
          "endpoints_touched": dict(endpoints_touched),
          "n_boundary_crossing_edges": len(boundary_edges),
          "rel_mix_top": rel_mix.most_common(5)}
    results["neighborhoods"].append(nb)
    print(f"[w1] {kind} {sid}: {len(node_ids)} nodes, {len(boundary_edges)} boundary edges, "
          f"endpoints={dict(endpoints_touched)}")

    nid = f"neighborhood:s12:{sid.replace(':', '_')}"
    nodes.append(U.mk_node(
        nid, "NodeNeighborhood", f"2-hop neighborhood of {sid}",
        {"seed": sid, "seed_kind": kind, "seed_endpoint": seed_ep,
         "n_nodes": len(node_ids), "n_edges": len(rels),
         "endpoints_touched": dict(endpoints_touched),
         "n_boundary_crossing_edges": len(boundary_edges),
         "member_ids": node_ids[:200], "rel_mix": rel_mix.most_common(8)},
        label="NodeNeighborhood"))
    edges.append(U.mk_edge(nid, "neighborhood_of", sid))

results["n_nodes"] = len(nodes)
results["n_edges"] = len(edges)
U.dump_results("/mnt/shared-workspace/shared/s12_w1_results.json",
               {"results": results, "nodes": nodes, "edges": edges})
print(f"[w1] DONE nodes={len(nodes)} edges={len(edges)}")
drv.close()
