"""Session 11 Neo4j push — additive MERGE of new nodes + edges only.
Matches existing schema: id/type/name/stream/mint_planner scalars + attributes_json string."""
import json, time
from neo4j import GraphDatabase

URI = "neo4j+s://82886682.databases.neo4j.io"
AUTH = ("82886682", "27hISSc8gAc7neJoUkpMyhi3uNb8yOlOPbQ982E_0oE")
SHARED = "/mnt/shared-workspace/shared"

# Collect the session-11 additions (same dedup logic as integration)
KG_DIR = "/mnt/results/zeta_vault/kg"
ents = json.load(open(f"{KG_DIR}/zeta_entities.json"))
edges = json.load(open(f"{KG_DIR}/zeta_edges.json"))
new_nodes = [n for n in ents if n.get("_session") == 11]
new_edges = [e for e in edges if e.get("_session") == 11]
print(f"[NEO] Session-11 nodes to push: {len(new_nodes)}, edges: {len(new_edges)}")

def node_row(n):
    return {
        "id": n["id"],
        "type": n.get("type", "?"),
        "name": n.get("name", ""),
        "stream": n.get("_stream", "session11_deep_graph"),
        "mint_planner": n.get("_mint_planner", "zeta_custodian_session11"),
        "session": 11,
        "mint_timestamp": n.get("_mint_timestamp", ""),
        "attributes_json": json.dumps(n.get("attributes", {})),
        "cross_refs": json.dumps(n.get("cross_refs", {})),
        "source_file": (n.get("source_receipts") or ["Session 11 deep graph analysis"])[0],
        "verbatim": json.dumps(n.get("verbatim_evidence", [])),
    }

def edge_row(e):
    rel = e.get("relation", "REL")
    return {
        "source": e["source"],
        "target": e["target"],
        "relation": rel,
        "reltype": rel.upper(),  # Neo4j relationship type follows existing UPPERCASE convention
        "attributes_json": json.dumps(e.get("attributes", {})),
        "session": 11,
        "mint_planner": e.get("_mint_planner", "zeta_custodian_session11"),
        "mint_timestamp": e.get("_mint_timestamp", ""),
    }

node_rows = [node_row(n) for n in new_nodes]
edge_rows = [edge_row(e) for e in new_edges]

NODE_CYPHER = """
UNWIND $rows AS row
MERGE (n {id: row.id})
SET n.type = row.type,
    n.name = row.name,
    n.stream = row.stream,
    n.mint_planner = row.mint_planner,
    n.session = row.session,
    n.mint_timestamp = row.mint_timestamp,
    n.attributes_json = row.attributes_json,
    n.cross_refs = row.cross_refs,
    n.source_file = row.source_file,
    n.verbatim = row.verbatim
"""

# Typed relationships matching existing KG convention: Neo4j rel-type = UPPERCASE(relation).
# Uses APOC dynamic relationship creation. apoc.merge.relationship is idempotent.
EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (s {id: row.source})
MATCH (t {id: row.target})
CALL apoc.merge.relationship(
    s, row.reltype, {relation: row.relation},
    {attributes_json: row.attributes_json, session: row.session,
     mint_planner: row.mint_planner, mint_timestamp: row.mint_timestamp},
    t, {}
) YIELD rel
RETURN count(rel) AS c
"""

def batched(rows, size=500):
    for i in range(0, len(rows), size):
        yield rows[i:i+size]

drv = GraphDatabase.driver(URI, auth=AUTH)
t0 = time.time()
with drv.session() as s:
    before_n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    before_e = s.run("MATCH ()-[e]->() RETURN count(e) AS c").single()["c"]
    print(f"[NEO] Before: {before_n} nodes, {before_e} edges")

    # Push nodes
    for bi, batch in enumerate(batched(node_rows)):
        s.run(NODE_CYPHER, rows=batch)
        print(f"[NEO]   node batch {bi+1}: +{len(batch)}")

    # Push edges
    for bi, batch in enumerate(batched(edge_rows)):
        s.run(EDGE_CYPHER, rows=batch)
        print(f"[NEO]   edge batch {bi+1}: +{len(batch)}")

    after_n = s.run("MATCH (n) RETURN count(n) AS c").single()["c"]
    after_e = s.run("MATCH ()-[e]->() RETURN count(e) AS c").single()["c"]
    print(f"[NEO] After:  {after_n} nodes (+{after_n-before_n}), {after_e} edges (+{after_e-before_e})")

    # verify a sample of session-11 nodes landed
    s11n = s.run("MATCH (n {session: 11}) RETURN count(n) AS c").single()["c"]
    s11e = s.run("MATCH ()-[r {session: 11}]->() RETURN count(r) AS c").single()["c"]
    print(f"[NEO] Session-11 in graph: {s11n} nodes, {s11e} edges")
    # type breakdown
    print("[NEO] Session-11 node types in Neo4j:")
    for rec in s.run("MATCH (n {session: 11}) RETURN n.type AS t, count(*) AS c ORDER BY c DESC"):
        print(f"      {rec['t']}: {rec['c']}")
drv.close()
print(f"[NEO] DONE in {time.time()-t0:.1f}s")
