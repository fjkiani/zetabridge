"""Session 11 integration: merge 5 worker outputs into KG, validate, save."""
import json, os, shutil
from collections import Counter
from datetime import datetime

KG_DIR = "/mnt/results/zeta_vault/kg"
SHARED = "/mnt/shared-workspace/shared"
WORK = "/workspace"

print("[INT] Loading authoritative KG...")
ents = json.load(open(f"{KG_DIR}/zeta_entities.json"))
edges = json.load(open(f"{KG_DIR}/zeta_edges.json"))
print(f"[INT] KG: {len(ents)} entities, {len(edges)} edges")

existing_node_ids = {e["id"] for e in ents}
existing_edge_keys = {(e["source"], e["relation"], e["target"]) for e in edges}

# ── Merge worker outputs ──────────────────────────────────────────────────
new_nodes_all, new_edges_all = [], []
per_worker = {}
for w in range(5):
    d = json.load(open(f"{SHARED}/s11_w{w}_results.json"))
    per_worker[w] = (len(d["new_nodes"]), len(d["new_edges"]))
    new_nodes_all.extend(d["new_nodes"])
    new_edges_all.extend(d["new_edges"])

print(f"[INT] Collected {len(new_nodes_all)} new nodes, {len(new_edges_all)} new edges from 5 workers")

# ── Dedup nodes by id (within new set + against KG) ────────────────────────
seen_node_ids = set()
merged_new_nodes = []
skipped_existing = 0
skipped_dup = 0
for n in new_nodes_all:
    nid = n["id"]
    if nid in existing_node_ids:
        skipped_existing += 1
        continue
    if nid in seen_node_ids:
        skipped_dup += 1
        continue
    seen_node_ids.add(nid)
    # normalize: ensure _stream present
    n.setdefault("_stream", "session11_deep_graph")
    n.setdefault("cross_refs", {})
    n.setdefault("verbatim_evidence", [])
    n.setdefault("source_receipts", ["Session 11 deep graph analysis"])
    merged_new_nodes.append(n)

print(f"[INT] Nodes: {len(merged_new_nodes)} to add "
      f"({skipped_existing} already in KG, {skipped_dup} intra-batch dups)")

# valid node id universe = existing KG + newly added
valid_ids = existing_node_ids | seen_node_ids

# ── Dedup + validate edges ─────────────────────────────────────────────────
seen_edge_keys = set()
merged_new_edges = []
skipped_edge_existing = 0
skipped_edge_dup = 0
dangling = []
for e in new_edges_all:
    key = (e["source"], e["relation"], e["target"])
    if key in existing_edge_keys:
        skipped_edge_existing += 1
        continue
    if key in seen_edge_keys:
        skipped_edge_dup += 1
        continue
    # validate endpoints exist
    if e["source"] not in valid_ids or e["target"] not in valid_ids:
        dangling.append(key)
        continue
    seen_edge_keys.add(key)
    e.setdefault("source_receipts", ["Session 11 deep graph analysis"])
    merged_new_edges.append(e)

print(f"[INT] Edges: {len(merged_new_edges)} to add "
      f"({skipped_edge_existing} already in KG, {skipped_edge_dup} intra-batch dups, "
      f"{len(dangling)} dangling-endpoint DROPPED)")
if dangling:
    print("[INT] WARNING sample dangling edges:")
    for k in dangling[:10]:
        print(f"      {k}")

# ── Assemble updated KG ─────────────────────────────────────────────────────
ents_out = ents + merged_new_nodes
edges_out = edges + merged_new_edges
print(f"[INT] Updated KG: {len(ents_out)} entities (+{len(merged_new_nodes)}), "
      f"{len(edges_out)} edges (+{len(merged_new_edges)})")

# node-type breakdown of additions
print("[INT] New node types:", dict(Counter(n["type"] for n in merged_new_nodes)))
print("[INT] New edge relations:", dict(Counter(e["relation"] for e in merged_new_edges)))

# ── Write to /workspace first (large JSON; then copy to results) ────────────
with open(f"{WORK}/zeta_entities.json", "w") as f:
    json.dump(ents_out, f)
with open(f"{WORK}/zeta_edges.json", "w") as f:
    json.dump(edges_out, f)
print("[INT] Wrote updated KG to /workspace")

# summary sidecar
summary = {
    "session": 11,
    "timestamp": datetime.utcnow().isoformat() + "Z",
    "kg_before": {"entities": len(ents), "edges": len(edges)},
    "kg_after": {"entities": len(ents_out), "edges": len(edges_out)},
    "added": {"nodes": len(merged_new_nodes), "edges": len(merged_new_edges)},
    "per_worker": {f"w{w}": {"nodes": per_worker[w][0], "edges": per_worker[w][1]} for w in per_worker},
    "new_node_types": dict(Counter(n["type"] for n in merged_new_nodes)),
    "new_edge_relations": dict(Counter(e["relation"] for e in merged_new_edges)),
    "dropped_dangling_edges": len(dangling),
    "skipped_existing_nodes": skipped_existing,
    "skipped_existing_edges": skipped_edge_existing,
}
with open(f"{SHARED}/s11_integration_summary.json", "w") as f:
    json.dump(summary, f, indent=2)
print("[INT] Wrote integration summary")
print("[INT] DONE.")
