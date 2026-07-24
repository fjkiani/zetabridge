#!/usr/bin/env python3
"""Session 13: export a bounded, REAL graph snapshot for the front-end.

Reads the live federated Neo4j graph and writes a browser-sized JSON snapshot
that the front-end uses as a fallback when the backend/Neo4j is unreachable.
NO fabrication: every node, edge, count and path is read from the live graph.

Output: <out_dir>/graph-snapshot.json  (single file, bounded size)
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/mnt/shared-workspace/shared")
from neo4j import GraphDatabase, READ_ACCESS  # noqa: E402
import s12_traverse_util as U  # noqa: E402

# ---- config ----
SEED_PER_ENDPOINT = 60      # capped representative nodes per endpoint
NEIGHBOR_SAMPLE = 8         # neighbors to attach per seed (keeps size bounded)
EXAMPLE_PATHS = 12          # precomputed cross-endpoint example paths


def endpoint_of(node_id: str):
    return U.endpoint_of(node_id)


def read(drv, cypher, params=None, cap=200000):
    return U.read(drv, cypher, params, cap)


def ep_pred(var, prefixes):
    return " OR ".join(f'{var}.id STARTS WITH "{p}"' for p in prefixes)


def node_payload(n, labels):
    """Compact node record for the FE."""
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


def main(out_dir: str):
    drv = GraphDatabase.driver(U.NEO4J_URI, auth=U.NEO4J_AUTH)
    os.makedirs(out_dir, exist_ok=True)

    total_nodes = read(drv, "MATCH (n) RETURN count(n) AS c")[0]["c"]
    total_edges = read(drv, "MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]

    # ---- schema: labels + rel-types ----
    labels = read(
        drv,
        "MATCH (n) UNWIND labels(n) AS l RETURN l AS l, count(*) AS c ORDER BY c DESC LIMIT 60",
    )
    reltypes = read(
        drv,
        "MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS c ORDER BY c DESC LIMIT 60",
    )

    # ---- endpoint summary ----
    endpoints = {}
    for ep, pfx in U.ENDPOINT_PREFIXES.items():
        c = read(drv, f"MATCH (n) WHERE {ep_pred('n', pfx)} RETURN count(n) AS c")[0]["c"]
        endpoints[ep] = {"prefixes": pfx, "node_count": c}

    # ---- seed nodes per endpoint (highest-degree first, bounded) + sample neighbors ----
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

    # attach a few neighbors per seed (real edges), bounded
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
                # add neighbor node (lightweight)
                ext = node_payload(r["m"], r["labels"])
                seed_nodes.setdefault("_neighbors", []).append(ext)

    # ---- S12 minted nodes (paths/bridges/reachability/chains) + their edges ----
    s12_labels = [
        "CrossEndpointPath", "CrossEndpointPathSummary", "NodeNeighborhood",
        "StructuralBridge", "ReachabilityProfile", "Metapath", "DeepChain",
    ]
    s12_nodes = []
    for lbl in s12_labels:
        rows = read(drv, f"MATCH (n:{lbl}) RETURN n AS n, labels(n) AS labels")
        for r in rows:
            p = node_payload(r["n"], r["labels"])
            # keep attributes_json (already a string) so the FE can show detail
            aj = r["n"].get("attributes_json")
            if aj:
                p["attributes_json"] = aj
            s12_nodes.append(p)
    s12_ids = {p["id"] for p in s12_nodes}

    s12_edges = read(
        drv,
        "MATCH (s)-[r]->(t) WHERE r.session=12 "
        "RETURN s.id AS source, t.id AS target, type(r) AS rel LIMIT 5000",
    )

    # ---- example cross-endpoint paths (real shortestPath, bounded) ----
    # Exclude S12 provenance rel-types so paths show ORGANIC structural bridges
    # (SAME_FILE_AS / MEMBER_OF_COMMUNITY / BRIDGE_EDGE / SPECIMEN_OF / RELATES ...)
    # rather than routing through the minted CrossEndpointPath nodes.
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

    snapshot = {
        "generated": datetime.now(timezone.utc).isoformat(),
        "source": "live Neo4j export (Session 13)",
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
        "counts_in_snapshot": {
            "seed_nodes": sum(len(v) for v in seed_nodes.values()),
            "seed_edges": len(seed_edges),
            "s12_nodes": len(s12_nodes),
            "s12_edges": len(s12_edges),
            "example_paths": len(example_paths),
        },
    }

    out_path = os.path.join(out_dir, "graph-snapshot.json")
    with open(out_path, "w") as f:
        json.dump(snapshot, f, default=U.np_default, separators=(",", ":"))
    size = os.path.getsize(out_path)
    print(f"WROTE {out_path}  ({size/1024:.1f} KB)")
    print("  totals:", snapshot["totals"])
    print("  endpoints:", {k: v["node_count"] for k, v in endpoints.items()})
    print("  counts_in_snapshot:", snapshot["counts_in_snapshot"])
    drv.close()
    return out_path


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "/workspace/s13_snapshot"
    main(out)
