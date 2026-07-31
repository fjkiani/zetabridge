"""Neo4j-backed graph service for read-only third-party agent access.

Wraps a pooled Neo4j driver and exposes a small set of read-only traversal
primitives consumed by the FastAPI `/api/graph` router and the MCP server.

Schema conventions (federated Zeta KG, verified live):
- A node's *type* may live in a Neo4j ``:Label`` (most nodes) and/or a
  ``type`` / ``entity_type`` property (older + Session-11 nodes).
- Endpoints are distinguished by node-id prefix (see ``ENDPOINT_PREFIXES``).

All reads run inside a managed READ transaction. neo4j-python 6.x does not
expose a per-transaction timeout via the managed API, so we bound work with a
result-row cap and a connection-acquisition timeout instead.
"""

from __future__ import annotations

import os
import time
from typing import Any, Iterable

from neo4j import GraphDatabase

from federation.cypher_guard import enforce_limit, validate_read_only

DEFAULT_ROW_CAP = 1000
DEFAULT_TIMEOUT_S = 20

# node-id prefix -> endpoint code
ENDPOINT_PREFIXES: dict[str, list[str]] = {
    "A_MSK": [
        "genomicfeature:msk:",
        "biospecimen:msk:",
        "cohort:msk",
        "vault:synapse",
    ],
    "B_SAS": [
        "patient:sas:",
        "trial:sas:",
        "arm:sas:",
        "clinical_table:sas:",
        "trial_design:sas:",
        "vault:sas",
    ],
    "C_EGA": [
        "ega:file:",
        "ega:sample:",
        "ega:dataset:",
        "specimen:britroc1:",
        "cohort:britroc",
        "vault:ega",
    ],
}

# Second-segment refinement: ids whose endpoint is encoded in the 2nd colon
# segment (e.g. biomarker:kras:colorectal -> B_SAS, genomicfeature:msk -> A_MSK).
# endpoint_of() checks these first so key biomarker/gene nodes are not
# misclassified as GRAPH hubs.
ENDPOINT_SECOND_SEGMENT: dict[str, str] = {
    "msk": "A_MSK", "synapse": "A_MSK", "spectrum": "A_MSK",
    "sas": "B_SAS", "pds": "B_SAS", "colorectal": "B_SAS", "breast": "B_SAS",
    "kras": "B_SAS",
    "ega": "C_EGA", "britroc": "C_EGA", "britroc1": "C_EGA",
    "argo": "D_ARGO", "icgc": "D_ARGO", "pog": "D_ARGO", "pogca": "D_ARGO",
}

# labels that are structural, not the semantic type
_STRUCTURAL_LABELS = {"Entity", "ZetaVault", "Resource", "_Bloom_Perspective_"}


def endpoint_of(node_id: str | None) -> str | None:
    """Return endpoint code (A_MSK/B_SAS/C_EGA/D_ARGO) for a node id, else None.

    Checks the explicit prefix map first, then falls back to a 2nd-segment
    lookup so ids like ``biomarker:kras:colorectal`` or ``genomicfeature:msk:``
    resolve to their true source instead of the GRAPH hub.
    """
    if not node_id:
        return None
    for code, prefixes in ENDPOINT_PREFIXES.items():
        for p in prefixes:
            if node_id.startswith(p):
                return code
    parts = node_id.split(":")
    if len(parts) >= 2:
        seg = parts[1].lower()
        if seg in ENDPOINT_SECOND_SEGMENT:
            return ENDPOINT_SECOND_SEGMENT[seg]
    return None


def node_type_of(labels: Iterable[str] | None, props: dict[str, Any] | None) -> str | None:
    """Resolve a node's semantic type across conventions.

    Priority: first non-structural Neo4j label -> ``type`` prop -> ``entity_type`` prop.
    """
    if labels:
        for lb in labels:
            if lb and lb not in _STRUCTURAL_LABELS:
                return lb
    props = props or {}
    if props.get("type"):
        return props["type"]
    if props.get("entity_type"):
        return props["entity_type"]
    return None


class GraphService:
    def __init__(self, uri: str, user: str, password: str,
                 row_cap: int = DEFAULT_ROW_CAP, timeout_s: int = DEFAULT_TIMEOUT_S):
        self._driver = GraphDatabase.driver(
            uri,
            auth=(user, password),
            max_connection_pool_size=16,
            connection_acquisition_timeout=timeout_s,
            # Fail fast when the Neo4j host is unreachable (e.g. an Aura instance
            # that has been paused/deleted and no longer resolves in DNS) so
            # callers get a prompt error instead of a ~30s retry storm. Clients
            # can then fall back to their baked snapshot quickly.
            connection_timeout=5.0,
            max_transaction_retry_time=4.0,
        )
        self.row_cap = row_cap
        self.timeout_s = timeout_s

    # --- lifecycle ---------------------------------------------------------
    @classmethod
    def from_env(cls) -> "GraphService":
        uri = os.environ.get("NEO4J_URI")
        user = os.environ.get("NEO4J_USER")
        pw = os.environ.get("NEO4J_PASSWORD")
        if not (uri and user and pw):
            raise RuntimeError("NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD not configured")
        return cls(uri, user, pw)

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:
            pass

    # --- low-level read ----------------------------------------------------
    def _read(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
        params = params or {}

        def _work(tx):
            res = tx.run(cypher, **params)
            rows = []
            for i, rec in enumerate(res):
                if i >= self.row_cap:
                    break
                rows.append(rec.data())
            return rows

        with self._driver.session() as s:
            runner = getattr(s, "execute_read", None) or s.read_transaction
            return runner(_work)

    # --- node payload helper ----------------------------------------------
    @staticmethod
    def _node_payload(props: dict[str, Any], labels: list[str]) -> dict[str, Any]:
        nid = props.get("id")
        payload = dict(props)
        payload["_labels"] = labels
        payload["_type"] = node_type_of(labels, props)
        payload["_endpoint"] = endpoint_of(nid)
        return payload

    # --- public operations -------------------------------------------------
    def health(self) -> dict[str, Any]:
        rows = self._read(
            "MATCH (n) WITH count(n) AS nodes "
            "CALL () { MATCH ()-[r]->() RETURN count(r) AS rels } "
            "RETURN nodes, rels"
        )
        r = rows[0] if rows else {"nodes": 0, "rels": 0}
        return {"status": "ok", "nodes": r["nodes"], "relationships": r["rels"]}

    def schema(self) -> dict[str, Any]:
        labels = [r["label"] for r in self._read("CALL db.labels() YIELD label RETURN label ORDER BY label")]
        rels = self._read(
            "CALL db.relationshipTypes() YIELD relationshipType "
            "RETURN relationshipType ORDER BY relationshipType"
        )
        rel_types = [r["relationshipType"] for r in rels]
        # relationship-type counts (bounded — there are few dozen types)
        rel_counts = {}
        for rt in rel_types:
            c = self._read(f"MATCH ()-[r:`{rt}`]->() RETURN count(r) AS c")
            rel_counts[rt] = c[0]["c"] if c else 0
        label_counts = {}
        for lb in labels:
            c = self._read(f"MATCH (n:`{lb}`) RETURN count(n) AS c")
            label_counts[lb] = c[0]["c"] if c else 0
        return {
            "labels": labels,
            "label_counts": label_counts,
            "relationship_types": rel_types,
            "relationship_counts": rel_counts,
            "endpoint_prefixes": ENDPOINT_PREFIXES,
        }

    def catalog_tables(self) -> list[dict[str, Any]]:
        """Expose the live KG as a browsable catalog.

        The legacy catalog router read Gravitino/Snowflake/Databricks connectors
        (none of which hold the federated KG), so the Catalog page rendered empty.
        Here each *node label* becomes a "table": catalog = owning endpoint,
        schema = semantic node type, columns = the union of property keys seen on
        sampled nodes of that label. Read-only; bounded sampling.
        """
        rows = self._read(
            "MATCH (n) UNWIND labels(n) AS lb "
            "WITH DISTINCT lb WHERE NOT lb IN $structural "
            "RETURN lb ORDER BY lb",
            {"structural": list(_STRUCTURAL_LABELS)},
        )
        labels = [r["lb"] for r in rows]
        tables: list[dict[str, Any]] = []
        tid = 0
        for lb in labels:
            # Efficient distinct property-key union over a small sample:
            # UNWIND keys(n) + DISTINCT is far cheaper than reduce+collect of
            # per-node key lists (which 502'd on the live graph).
            sample = self._read(
                f"MATCH (n:`{lb}`) WITH n LIMIT 25 "
                "UNWIND keys(n) AS k "
                "WITH DISTINCT k "
                "RETURN collect(k) AS pkeys",
            )
            cnt = self._read(f"MATCH (n:`{lb}`) RETURN count(n) AS c")
            sid = self._read(f"MATCH (n:`{lb}`) RETURN n.id AS i LIMIT 1")
            n_nodes = cnt[0]["c"] if cnt else 0
            pkeys = sorted(sample[0]["pkeys"]) if sample and sample[0]["pkeys"] else []
            sample_id = sid[0]["i"] if sid else None
            endpoint = endpoint_of(sample_id) or "GRAPH"
            cols = [{"name": k, "type": "property", "nullable": True} for k in pkeys]
            tables.append({
                "id": tid,
                "catalog_name": endpoint,
                "catalog_type": "Neo4j",
                "schema_name": lb,
                "table_name": lb,
                "columns": cols,
                "properties": {"node_count": str(n_nodes), "kind": "node_label"},
                "fqn": f"{endpoint}.{lb}.{lb}",
            })
            tid += 1
        return tables

    def catalog_stats(self) -> dict[str, Any]:
        """Cheap catalog stats — single consolidated Cypher query + 5-min cache.

        Previously this called catalog_tables(), which issues 3 Cypher
        round-trips per label (91 labels = 273 queries, ~102s), then called it
        AGAIN -> ~204s -> Render 30s proxy timeout -> 502. This version gets
        per-label node counts in ONE query and derives endpoint from a single
        sampled id per label, all cached for 5 minutes.
        """
        now = time.time()
        cache = getattr(self, "_catalog_stats_cache", None)
        if cache and (now - cache["ts"]) < 300:
            return cache["data"]
        # One query: single scan, unwind labels, count per label (~1.2s on 47k nodes).
        rows = self._read(
            "MATCH (n) UNWIND labels(n) AS lb "
            "WITH lb, count(*) AS c WHERE NOT lb IN $structural "
            "RETURN lb AS lb, c ORDER BY c DESC",
            {"structural": list(_STRUCTURAL_LABELS)},
        )
        # One query: one sample id per label for endpoint resolution (first label only).
        sid_rows = self._read(
            "MATCH (n) WITH labels(n)[0] AS lb, n.id AS i WHERE NOT lb IN $structural "
            "WITH lb, collect(i)[0] AS i RETURN lb AS lb, i",
            {"structural": list(_STRUCTURAL_LABELS)},
        )
        sid_map = {r["lb"]: r["i"] for r in sid_rows}
        catalogs = sorted({endpoint_of(sid_map.get(r["lb"])) or "GRAPH" for r in rows})
        data = {
            "n_tables": len(rows),
            "n_catalogs": len(catalogs),
            "catalogs": catalogs,
            "total_nodes": sum(int(r["c"]) for r in rows),
        }
        self._catalog_stats_cache = {"ts": now, "data": data}
        return data

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        rows = self._read(
            "MATCH (n {id:$id}) "
            "WITH n, size([(n)--() | 1]) AS degree "
            "RETURN properties(n) AS props, labels(n) AS labels, degree",
            {"id": node_id},
        )
        if not rows:
            return None
        r = rows[0]
        payload = self._node_payload(r["props"], r["labels"])
        payload["_degree"] = r["degree"]
        # immediate relationship-type summary
        rel_summary = self._read(
            "MATCH (n {id:$id})-[r]-() RETURN type(r) AS rel, count(*) AS c ORDER BY c DESC",
            {"id": node_id},
        )
        payload["_rel_summary"] = {row["rel"]: row["c"] for row in rel_summary}
        return payload

    def search(self, prefix: str | None = None, label: str | None = None,
               type_: str | None = None, name_contains: str | None = None,
               limit: int = 50) -> list[dict]:
        limit = max(1, min(int(limit), 200))
        where = []
        params: dict[str, Any] = {}
        label_clause = ""
        if label:
            label_clause = f":`{label}`"
        if prefix:
            where.append("n.id STARTS WITH $prefix")
            params["prefix"] = prefix
        if type_:
            where.append("(n.type = $type OR n.entity_type = $type OR $type IN labels(n))")
            params["type"] = type_
        if name_contains:
            where.append(
                "(toLower(coalesce(n.name,'')) CONTAINS toLower($nc) "
                "OR toLower(coalesce(n.id,'')) CONTAINS toLower($nc))"
            )
            params["nc"] = name_contains
        where_clause = ("WHERE " + " AND ".join(where)) if where else ""
        # Relevance ranking: exact name/id match > name/id prefix > name/id
        # substring > incidental (verbatim/attribute) text match. Without an
        # ORDER BY, Neo4j returns nodes in arbitrary storage order, so a search
        # for "BRCA1" buried the actual BRCA1 gene node under unrelated nodes
        # that merely mention it in free text.
        if name_contains:
            score = (
                "WITH n, CASE "
                "WHEN toLower(coalesce(n.name,'')) = toLower($nc) "
                "  OR toLower(coalesce(n.id,'')) = toLower($nc) THEN 0 "
                "WHEN toLower(coalesce(n.name,'')) STARTS WITH toLower($nc) "
                "  OR toLower(coalesce(n.id,'')) STARTS WITH toLower($nc) THEN 1 "
                "WHEN toLower(coalesce(n.name,'')) CONTAINS toLower($nc) THEN 2 "
                "ELSE 3 END AS _score "
            )
            order = "ORDER BY _score ASC, coalesce(n.name, n.id) ASC "
        else:
            score = "WITH n, 0 AS _score "
            order = "ORDER BY coalesce(n.name, n.id) ASC "
        cypher = (
            f"MATCH (n{label_clause}) {where_clause} "
            f"{score}"
            f"RETURN properties(n) AS props, labels(n) AS labels {order} LIMIT {limit}"
        )
        rows = self._read(cypher, params)
        return [self._node_payload(r["props"], r["labels"]) for r in rows]

    def neighbors(self, node_id: str, hops: int = 1, rel_types: list[str] | None = None,
                  direction: str = "both", cap: int = 500) -> dict[str, Any]:
        hops = max(1, min(int(hops), 3))
        cap = max(1, min(int(cap), 2000))
        # relationship filter for apoc.path.subgraphAll
        # direction: '>' out, '<' in, '' both. rel spec 'TYPE>' / '<TYPE' / 'TYPE'
        if direction == "out":
            arrow = ">"
        elif direction == "in":
            arrow = "<"
        else:
            arrow = ""
        if rel_types:
            relspec = "|".join(f"{('<' if arrow=='<' else '')}{rt}{('>' if arrow=='>' else '')}" for rt in rel_types)
        else:
            relspec = arrow if arrow else None
        params = {"id": node_id, "hops": hops, "cap": cap, "relspec": relspec}
        rows = self._read(
            "MATCH (s {id:$id}) "
            "CALL apoc.path.subgraphAll(s, {maxLevel:$hops, relationshipFilter:$relspec, limit:$cap}) "
            "YIELD nodes, relationships "
            "RETURN [x IN nodes | {props: properties(x), labels: labels(x)}] AS ns, "
            "[r IN relationships | {type: type(r), start: startNode(r).id, end: endNode(r).id, props: properties(r)}] AS rs",
            params,
        )
        if not rows:
            return {"center": node_id, "nodes": [], "edges": []}
        r = rows[0]
        nodes = [self._node_payload(x["props"], x["labels"]) for x in r["ns"]]
        edges = [
            {"source": e["start"], "target": e["end"], "type": e["type"], "props": e.get("props", {})}
            for e in r["rs"]
        ]
        return {
            "center": node_id,
            "hops": hops,
            "n_nodes": len(nodes),
            "n_edges": len(edges),
            "nodes": nodes,
            "edges": edges,
        }

    def find_paths(self, source_id: str, target_id: str | None = None,
                   target_prefix: str | None = None, max_hops: int = 5,
                   k: int = 10) -> dict[str, Any]:
        max_hops = max(1, min(int(max_hops), 5))
        k = max(1, min(int(k), 25))
        if not target_id and not target_prefix:
            raise ValueError("Provide target_id or target_prefix")

        if target_id:
            cypher = (
                f"MATCH (s {{id:$src}}), (t {{id:$tid}}) "
                f"MATCH p = shortestPath((s)-[*1..{max_hops}]-(t)) "
                f"RETURN [n IN nodes(p) | n.id] AS ids, "
                f"[r IN relationships(p) | type(r)] AS rels, length(p) AS hops "
                f"LIMIT {k}"
            )
            params = {"src": source_id, "tid": target_id}
        else:
            cypher = (
                f"MATCH (s {{id:$src}}) "
                f"MATCH (t) WHERE t.id STARTS WITH $tpfx AND t <> s "
                f"MATCH p = shortestPath((s)-[*1..{max_hops}]-(t)) "
                f"WITH p, length(p) AS hops ORDER BY hops ASC LIMIT {k} "
                f"RETURN [n IN nodes(p) | n.id] AS ids, "
                f"[r IN relationships(p) | type(r)] AS rels, hops"
            )
            params = {"src": source_id, "tpfx": target_prefix}

        rows = self._read(cypher, params)
        paths = []
        for r in rows:
            ids = r["ids"]
            paths.append({
                "node_ids": ids,
                "rel_types": r["rels"],
                "hops": r["hops"],
                "source_endpoint": endpoint_of(ids[0]) if ids else None,
                "target_endpoint": endpoint_of(ids[-1]) if ids else None,
            })
        paths.sort(key=lambda x: x["hops"])
        return {"source": source_id, "target": target_id or target_prefix,
                "count": len(paths), "paths": paths}

    def run_cypher(self, cypher: str, params: dict[str, Any] | None = None,
                   cap: int | None = None) -> dict[str, Any]:
        validate_read_only(cypher)
        safe = enforce_limit(cypher, self.row_cap if cap is None else min(cap, self.row_cap))
        rows = self._read(safe, params or {})
        return {"columns": list(rows[0].keys()) if rows else [], "rows": rows, "row_count": len(rows)}
