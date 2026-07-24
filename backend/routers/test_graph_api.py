"""HTTP integration tests for the /api/graph router (live Neo4j, in-process TestClient).

Run:
  cd backend && env NEO4J_URI=... NEO4J_USER=... NEO4J_PASSWORD=... \
      ZETA_GRAPH_API_KEY=test-key-abc123 python3 -m pytest routers/test_graph_api.py -q
"""

import os

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

API_KEY = os.environ.get("ZETA_GRAPH_API_KEY", "test-key-abc123")
HDR = {"X-Zeta-Api-Key": API_KEY}


@pytest.fixture(scope="module")
def client():
    # Build a minimal app with only the graph router (avoids legacy store startup).
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from routers import graph as graph_router

    app = FastAPI()
    app.include_router(graph_router.router)
    return TestClient(app)


def test_auth_required(client):
    r = client.get("/api/graph/health")  # no key
    assert r.status_code == 401
    r = client.get("/api/graph/health", headers={"X-Zeta-Api-Key": "wrong"})
    assert r.status_code == 401


def test_health(client):
    r = client.get("/api/graph/health", headers=HDR)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["nodes"] > 30000
    assert body["relationships"] > 100000


def test_schema(client):
    r = client.get("/api/graph/schema", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert "labels" in body and "relationship_types" in body
    assert "EXPERIENCED_AE" in body["relationship_types"]
    assert set(body["endpoint_prefixes"].keys()) == {"A_MSK", "B_SAS", "C_EGA"}


def test_get_node(client):
    r = client.get("/api/graph/node/genomicfeature:msk:mut:NF1", headers=HDR)
    assert r.status_code == 200
    body = r.json()
    assert body["_endpoint"] == "A_MSK"
    assert body["_degree"] > 0


def test_search(client):
    r = client.post("/api/graph/search", headers=HDR, json={"prefix": "trial:sas:", "limit": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    assert all(n["id"].startswith("trial:sas:") for n in body["nodes"])


def test_neighbors_bridge_edge(client):
    r = client.post("/api/graph/neighbors", headers=HDR,
                    json={"id": "genomicfeature:msk:mut:NF1", "hops": 2, "cap": 500})
    assert r.status_code == 200
    body = r.json()
    assert body["n_nodes"] > 0
    types = {e["type"] for e in body["edges"]}
    assert "BRIDGE_EDGE" in types  # hand-verified: NF1 2-hop reaches AE via bridge


def test_paths_cross_endpoint(client):
    r = client.post("/api/graph/paths", headers=HDR,
                    json={"source_id": "ega:file:EGAF00008095080",
                          "target_prefix": "trial:sas:", "max_hops": 5, "k": 5})
    assert r.status_code == 200
    body = r.json()
    assert body["count"] >= 1
    p = body["paths"][0]
    assert p["source_endpoint"] == "C_EGA"
    assert p["target_endpoint"] == "B_SAS"


def test_cypher_read_allowed(client):
    r = client.post("/api/graph/cypher", headers=HDR,
                    json={"cypher": "MATCH (n:ZetaTrial) RETURN count(n) AS c"})
    assert r.status_code == 200
    body = r.json()
    assert body["rows"][0]["c"] >= 1


def test_cypher_write_forbidden_and_no_mutation(client):
    # get count before
    before = client.post("/api/graph/cypher", headers=HDR,
                         json={"cypher": "MATCH (n) RETURN count(n) AS c"}).json()["rows"][0]["c"]
    # attempt a write
    r = client.post("/api/graph/cypher", headers=HDR,
                    json={"cypher": "CREATE (n:HackNode {id:'evil-s12'}) RETURN n"})
    assert r.status_code == 403
    # count unchanged
    after = client.post("/api/graph/cypher", headers=HDR,
                        json={"cypher": "MATCH (n) RETURN count(n) AS c"}).json()["rows"][0]["c"]
    assert before == after
    # and the node truly does not exist
    chk = client.post("/api/graph/cypher", headers=HDR,
                      json={"cypher": "MATCH (n {id:'evil-s12'}) RETURN count(n) AS c"}).json()
    assert chk["rows"][0]["c"] == 0
