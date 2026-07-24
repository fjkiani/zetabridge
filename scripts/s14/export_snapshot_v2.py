#!/usr/bin/env python3
"""Session 14: export a bounded, REAL graph snapshot v2 for the front-end.

This is a SUPERSET of the Session-13 snapshot. It keeps every key the FE already
reads (totals / endpoints / schema / seed_nodes / seed_edges / s12_nodes /
s12_edges / example_paths / counts_in_snapshot) and ADDS the signal-intelligence
layer the Session-14 value surfaces need:

  - signals: per-family ranked signal lists (native metric + derived strength +
    slug + endpoints), each capped with "showing top-N of M" metadata.
  - bridges: GenomicAEBridge gene->AE edge paths resolved end-to-end.
  - gaps:    KBGap / ZetaKBGap blind-spot feed (normalized shape).
  - overview: the headline value metrics block.

CRITICAL: the signal / bridge / gap / overview blocks are produced by calling the
SAME ``SignalService`` methods the live ``/api/signals/*`` API uses, so the
offline snapshot is guaranteed to match live values 1:1. NO fabrication: every
node, edge, count, path, and metric is read from the live Neo4j graph.

The base graph-structure blocks (seed nodes/edges, S12 nodes/edges, example
paths) are read directly from Neo4j exactly as Session 13 did, so the Graph
Explorer / Path Finder pages keep working unchanged.

Output: <out_dir>/graph-snapshot.json  (single file, bounded, target < ~2 MB)
The previous file is preserved as <out_dir>/graph-snapshot-v1.json (once).

License: Apache-2.0
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/mnt/shared-workspace/shared")
sys.path.insert(0, "/workspace/zetabridge/backend")

from neo4j import GraphDatabase  # noqa: E402
import s12_traverse_util as U  # noqa: E402
from federation.signal_service import SignalService  # noqa: E402

# ---- config (mirror S13 for the base graph blocks) ----
SEED_PER_ENDPOINT = 60      # capped representative nodes per endpoint
NEIGHBOR_SAMPLE = 8         # neighbors to attach per seed (keeps size bounded)
EXAMPLE_PATHS = 12          # precomputed cross-endpoint example paths

# Per-family cap for the bundled snapshot (top-N by native metric). The full
# count M is recorded alongside so the FE can say "showing top-N of M".
SIGNAL_CAP_PER_FAMILY = 40
BRIDGE_CAP = 40             # GenomicAEBridge is small (20) but keep a guard


def endpoint_of(node_id: str):
    return U.endpoint_of(node_id)


def read(drv, cypher, params=None, cap=200000):
    return U.read(drv, cypher, params, cap)


def ep_pred(var, prefixes):
    return " OR ".join(f'{var}.id STARTS WITH "{p}"' for p in prefixes)


def node_payload(n, labels):
    nid = n.get("id")
    return {
        "id": nid,
        "labels": labels,
        "label": labels[0] if labels else (n.get("type") or "Node"),
        "name": n.get("name") or n.get("id"),
        "type": n.get("type"),
        "endpoint": endpoint_of(nid),
        "session": n.get("session") or n.get("_session"),
    }


# ── base graph blocks (identical semantics to S13) ─────────────────────────────

def build_base(drv) -> dict:
    total_nodes = read(drv, "MATCH (n) RETURN count(n) AS c")[0]["c"]
    total_edges = read(drv, "MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]

    labels = read(
        drv,
        "MATCH (n) UNWIND labels(n) AS l RETURN l AS l, count(*) AS c ORDER BY c DESC LIMIT 60",
    )
    reltypes = read(
        drv,
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC LIMIT 60",
    )

    endpoints = {}
    for ep, pfx in U.ENDPOINT_PREFIXES.items():
        c = read(drv, f"MATCH (n) WHERE {ep_pred('n', pfx)} RETURN count(n) AS c")[0]["c"]
        endpoints[ep] = {"prefixes": pfx, "node_count": c}

    seed_nodes = {}
    seed_edges = []
    seen_ids = set()
    for ep, pfx in U.ENDPOINT_PREFIXES.items():
        rows = read(
            drv,
            f"""
            MATCH (n) WHERE {ep_pred('n', pfx)}
            RETURN n AS n, labels(n) AS labels, count{{ (n)--() }} AS deg
            ORDER BY deg DESC LIMIT {SEED_PER_ENDPOINT}
            """,
        )
        ep_nodes = []
        for r in rows:
            p = node_payload(r["n"], r["labels"])
            p["degree"] = r["deg"]
            ep_nodes.append(p)
            seen_ids.add(p["id"])
        seed_nodes[ep] = ep_nodes

    all_seed_ids = [p["id"] for lst in seed_nodes.values() for p in lst]
    for sid in all_seed_ids:
        rows = read(
            drv,
            f"""
            MATCH (s {{id:$id}})-[r]-(m)
            RETURN type(r) AS rel, startNode(r).id AS src, endNode(r).id AS tgt,
                   m AS m, labels(m) AS labels LIMIT {NEIGHBOR_SAMPLE}
            """,
            {"id": sid},
        )
        for r in rows:
            seed_edges.append({"source": r["src"], "target": r["tgt"], "rel": r["rel"]})
            mid = r["m"].get("id")
            if mid not in seen_ids:
                seen_ids.add(mid)
                ext = node_payload(r["m"], r["labels"])
                seed_nodes.setdefault("_neighbors", []).append(ext)

    s12_labels = [
        "CrossEndpointPath", "CrossEndpointPathSummary", "NodeNeighborhood",
        "StructuralBridge", "ReachabilityProfile", "Metapath", "DeepChain",
    ]
    s12_nodes = []
    for lbl in s12_labels:
        rows = read(drv, f"MATCH (n:{lbl}) RETURN n AS n, labels(n) AS labels")
        for r in rows:
            p = node_payload(r["n"], r["labels"])
            aj = r["n"].get("attributes_json")
            if aj:
                p["attributes_json"] = aj
            s12_nodes.append(p)

    s12_edges = read(
        drv,
        "MATCH (s)-[r]->(t) WHERE r.session=12 "
        "RETURN s.id AS source, t.id AS target, type(r) AS rel LIMIT 5000",
    )

    S12_RELTYPES = [
        "PATH_SOURCE", "PATH_TARGET", "TRAVERSES", "NEIGHBORHOOD_OF",
        "BRIDGES_NODE", "INSTANCE_OF_REACHABILITY", "DEEP_CHAIN_OF",
        "VIA_SAMPLE", "REACHES_DATASET", "CHAIN_GENE", "CHAIN_AE", "VIA_SPECIMEN",
    ]
    excl = ", ".join(f'"{t}"' for t in S12_RELTYPES)
    example_paths = []
    pair_specs = [
        ("C_EGA", "B_SAS", "ega:file:", "trial:sas:"),
        ("A_MSK", "B_SAS", "genomicfeature:msk:", "trial:sas:"),
        ("C_EGA", "A_MSK", "specimen:britroc1:", "genomicfeature:msk:"),
    ]
    for src_ep, tgt_ep, src_pfx, tgt_pfx in pair_specs:
        rows = read(
            drv,
            f"""
            MATCH (s) WHERE s.id STARTS WITH "{src_pfx}"
            WITH s LIMIT 40
            MATCH (t) WHERE t.id STARTS WITH "{tgt_pfx}"
            WITH s, t LIMIT 400
            MATCH p = shortestPath((s)-[*1..6]-(t))
            WHERE none(rel IN relationships(p) WHERE type(rel) IN [{excl}])
            WITH s, t, p, length(p) AS L ORDER BY L ASC LIMIT {EXAMPLE_PATHS}
            RETURN [x IN nodes(p) | x.id] AS node_ids,
                   [rel IN relationships(p) | type(rel)] AS rel_types, L AS hops
            LIMIT {EXAMPLE_PATHS}
            """,
        )
        for r in rows:
            example_paths.append({
                "source_endpoint": src_ep,
                "target_endpoint": tgt_ep,
                "node_ids": r["node_ids"],
                "rel_types": r["rel_types"],
                "hops": r["hops"],
            })

    return {
        "totals": {"nodes": total_nodes, "edges": total_edges},
        "endpoints": endpoints,
        "schema": {
            "labels": [{"label": r["l"], "count": r["c"]} for r in labels],
            "relationship_types": [{"type": r["t"], "count": r["c"]} for r in reltypes],
        },
        "seed_nodes": seed_nodes,
        "seed_edges": seed_edges,
        "s12_nodes": s12_nodes,
        "s12_edges": [dict(x) for x in s12_edges],
        "example_paths": example_paths,
    }


# ── signal blocks (reuse SignalService => guaranteed live-consistent) ──────────

def build_signals(svc: SignalService) -> dict:
    """Per-family ranked signals via SignalService.top_signals (native + derived)."""
    families = ["drug_ae", "pharmacovig", "genomic_bridge", "cross_trial", "outlier"]
    per_family = {}
    metric_by_family = {}
    for fam in families:
        full = svc.top_signals(fam, limit=SIGNAL_CAP_PER_FAMILY)
        total_m = full["count"]
        shown = full["signals"]
        # strip the bulky raw attrs dict for the bundled snapshot but keep the
        # fields the hub/detail cards actually render; the live API still
        # returns full attrs, and the FE prefers live when the key is set.
        slim = []
        for s in shown:
            a = s.get("attrs") or {}
            slim.append({
                "slug": s["slug"],
                "family": s["family"],
                "label": s["label"],
                "name": s["name"],
                "native_metric": s["native_metric"],
                "native_value": s["native_value"],
                "strength_derived": s.get("strength_derived"),
                "endpoint": s.get("endpoint"),
                "session": s.get("session"),
                # a compact, human-relevant subset of attrs for card display
                "detail": {
                    k: a.get(k) for k in (
                        "exp_drug", "ctrl_drug", "ae_term", "gene", "trial", "trial_id",
                        "exp_rate", "ctrl_rate", "rate_ratio", "ror", "bridge_score",
                        "consistency_score", "n_trials", "n_patients", "recurrence_pct",
                        "interpretation", "severity", "arm", "signal_type", "feature_type",
                    ) if a.get(k) is not None
                },
            })
        per_family[fam] = {
            "metric": full["signals"][0]["native_metric"] if shown else None,
            "shown": len(slim),
            "total": total_m,
            "signals": slim,
        }
        metric_by_family[fam] = per_family[fam]["metric"]

    return {"families": per_family, "metric_by_family": metric_by_family}


def build_bridges(svc: SignalService) -> dict:
    b = svc.bridges()
    bridges = b["bridges"][:BRIDGE_CAP]
    return {"shown": len(bridges), "total": b["count"], "bridges": bridges}


def build_gaps(svc: SignalService) -> dict:
    g = svc.gaps()
    # keep the normalized shape but drop the bulky raw attrs to bound size
    slim = []
    for x in g["gaps"]:
        slim.append({k: v for k, v in x.items() if k != "attrs"})
    return {"count": g["count"], "gaps": slim}


def main(out_dir: str):
    os.makedirs(out_dir, exist_ok=True)

    # preserve the existing snapshot as v1 (only once — never clobber a real v1)
    cur = os.path.join(out_dir, "graph-snapshot.json")
    v1 = os.path.join(out_dir, "graph-snapshot-v1.json")
    if os.path.exists(cur) and not os.path.exists(v1):
        shutil.copy2(cur, v1)
        print(f"PRESERVED existing snapshot -> {v1} ({os.path.getsize(v1)/1024:.1f} KB)")

    drv = GraphDatabase.driver(U.NEO4J_URI, auth=U.NEO4J_AUTH)
    svc = SignalService.from_env()
    try:
        base = build_base(drv)
        overview = svc.overview()
        signals = build_signals(svc)
        bridges = build_bridges(svc)
        gaps = build_gaps(svc)
    finally:
        svc.close()
        drv.close()

    snapshot = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": "live Neo4j export (Session 14, v2 — signal-intelligence superset)",
        "schema_version": 2,
        **base,
        # NEW signal-intelligence layer (all via SignalService => live-consistent)
        "overview": overview,
        "signals": signals,
        "bridges": bridges,
        "gaps": gaps,
        "counts_in_snapshot": {
            "seed_nodes": sum(len(v) for v in base["seed_nodes"].values()),
            "seed_edges": len(base["seed_edges"]),
            "s12_nodes": len(base["s12_nodes"]),
            "s12_edges": len(base["s12_edges"]),
            "example_paths": len(base["example_paths"]),
            "signal_families": {fam: v["total"] for fam, v in signals["families"].items()},
            "signals_shown": sum(v["shown"] for v in signals["families"].values()),
            "signals_total": sum(v["total"] for v in signals["families"].values()),
            "bridges_shown": bridges["shown"],
            "bridges_total": bridges["total"],
            "gaps": gaps["count"],
        },
    }

    with open(cur, "w") as f:
        json.dump(snapshot, f, default=U.np_default, separators=(",", ":"))
    size = os.path.getsize(cur)
    print(f"WROTE {cur}  ({size/1024:.1f} KB / {size/1024/1024:.2f} MB)")
    print("  totals:", snapshot["totals"])
    print("  endpoints:", {k: v["node_count"] for k, v in base["endpoints"].items()})
    print("  overview.n_signals:", overview["n_signals"], "n_blind_spots:", overview["n_blind_spots"])
    print("  counts_in_snapshot:", json.dumps(snapshot["counts_in_snapshot"]))
    return cur


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else \
        "/workspace/zetabridge/artifacts/zetabridge/public/graph-snapshot"
    main(out)
