"""GraphRAG engine — multi-hop traversal over the LIVE Neo4j KG (no caching).

Replaces the retired flat-file engine (zeta_vault/kg/*.json). Traversal is BFS
over relationships with optional relation filtering; results cite the actual
nodes/edges traversed so every answer is grounded in the graph.

Query flow:
  1. resolve   — find seed nodes matching the question (name/id contains)
  2. traverse  — BFS out to max_hops, collecting nodes + edges
  3. synthesize— rank paths, group by endpoint, produce a cited answer
"""

from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


class Neo4jGraphRAG:
    def __init__(self, service):
        """service: a GraphService (has ._read(cypher, params))."""
        self.svc = service

    # ── 1. resolve seed nodes ────────────────────────────────────────────────
    def resolve(self, query: str, limit: int = 8) -> list[dict]:
        # Prefer well-connected nodes: order by degree so traversal seeds are
        # the entities that actually participate in relationships, not leaves.
        rows = self.svc._read(
            "MATCH (n) WHERE toLower(coalesce(n.name,'')) CONTAINS toLower($q) "
            "OR toLower(coalesce(n.id,'')) CONTAINS toLower($q) "
            "OPTIONAL MATCH (n)-[r]-() "
            "WITH n, count(r) AS deg "
            "RETURN n.id AS id, n.name AS name, labels(n) AS labels, "
            "n.gps_endpoint AS endpoint, deg ORDER BY deg DESC LIMIT $lim",
            {"q": query, "lim": limit},
        )
        return rows

    # ── 2. BFS traversal ─────────────────────────────────────────────────────
    def traverse(self, start_id: str, max_hops: int = 3, rel_types: list[str] | None = None,
                 cap: int = 200) -> dict:
        rel_filter = ""
        params: dict[str, Any] = {"sid": start_id, "hops": max_hops, "cap": cap}
        if rel_types:
            rel_filter = "AND type(r) IN $rels"
            params["rels"] = rel_types
        # variable-length path expansion
        rows = self.svc._read(
            f"MATCH path = (a {{id: $sid}})-[r*1..{max_hops}]-(b) "
            f"WHERE ALL(rel IN relationships(path) WHERE true {rel_filter}) "
            f"WITH path LIMIT $cap "
            f"RETURN [n IN nodes(path) | {{id: n.id, name: n.name, labels: labels(n), "
            f"endpoint: n.gps_endpoint}}] AS nodes, "
            f"[rel IN relationships(path) | type(rel)] AS rels, length(path) AS hops",
            params,
        )
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        paths: list[dict] = []
        for r in rows:
            ns, rels = r["nodes"], r["rels"]
            for n in ns:
                nodes[n["id"]] = n
            for i in range(len(ns) - 1):
                edges.append({"source": ns[i]["id"], "target": ns[i+1]["id"],
                              "relation": rels[i] if i < len(rels) else "?"})
            paths.append({"hops": r["hops"], "nodes": [n["id"] for n in ns], "rels": rels})
        return {"start": start_id, "nodes": list(nodes.values()), "edges": edges,
                "paths": paths, "n_nodes": len(nodes), "n_edges": len(edges)}

    # ── 3. synthesize a cited answer ─────────────────────────────────────────
    def answer(self, query: str, max_hops: int = 3) -> dict:
        seeds = self.resolve(query)
        if not seeds:
            return {"query": query, "found": False,
                    "summary": f"No graph nodes match '{query}'.",
                    "seeds": [], "paths": []}
        all_paths, cited_nodes, endpoints = [], {}, defaultdict(int)
        for s in seeds[:3]:  # traverse from top seeds
            t = self.traverse(s["id"], max_hops=max_hops)
            for n in t["nodes"]:
                cited_nodes[n["id"]] = n
                endpoints[n.get("endpoint") or "GRAPH"] += 1
            all_paths.extend(t["paths"])
        # rank: prefer short paths that cross endpoints
        def crosses(p):
            eps = {cited_nodes[nid].get("endpoint") for nid in p["nodes"] if nid in cited_nodes}
            return len(eps) > 1
        all_paths.sort(key=lambda p: (not crosses(p), p["hops"]))
        top = all_paths[:5]
        seed_names = ", ".join(s.get("name") or s["id"] for s in seeds[:3])
        summary = (f"GraphRAG: '{query}' resolves to {len(seeds)} seed node(s) "
                   f"({seed_names}). Traversed {len(cited_nodes)} nodes across "
                   f"{dict(endpoints)} endpoints within {max_hops} hops. "
                   f"{len(top)} cited path(s) below.")
        return {"query": query, "found": True, "summary": summary,
                "seeds": seeds, "endpoints": dict(endpoints),
                "paths": [{"hops": p["hops"],
                           "chain": " → ".join((cited_nodes[nid].get("name") or nid) for nid in p["nodes"]),
                           "rels": p["rels"]} for p in top],
                "cited_node_count": len(cited_nodes)}
