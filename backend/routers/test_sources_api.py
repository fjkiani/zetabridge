"""Contract tests for the Session-15 /api/sources router.

Run in CI without live creds. The source gateway is replaced by a fake so we
test the router contract (auth, envelope passthrough, param wiring) in isolation
from real network calls.

Asserts:
  * require_api_key: 503 when ZETA_GRAPH_API_KEY unset, 401 without/with wrong
    key, 200 with the right key.
  * each route returns the uniform envelope dict from the gateway.
  * no-fabrication passthrough: an 'unreachable' gateway result surfaces with
    data=None (the router never invents a body).

Run:
  cd backend && env ZETA_GRAPH_API_KEY=test-key-abc123 \
      python3 -m pytest routers/test_sources_api.py -q
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API_KEY = "test-key-abc123"
HDR = {"X-Zeta-Api-Key": API_KEY}


# ── a fake gateway that records calls and returns canned envelopes ───────────
class _FakeEnv:
    def __init__(self, **kw):
        self._d = kw

    def to_dict(self):
        return self._d


def _live(action, **data):
    return _FakeEnv(endpoint="C_EGA", source="ega", status="live", action=action,
                    latency_ms=12.3, data=data or {"ok": True}, error=None,
                    grounding={"action": action})


def _unreachable(action):
    return _FakeEnv(endpoint="B_SAS", source="sas_cas", status="unreachable",
                    action=action, latency_ms=8.0, data=None,
                    error="tls_ca_unavailable: SWATError: ...", grounding={})


class _FakeSynapse:
    def whoami(self):
        return _live("whoami", ownerId="3388648")

    def get_entity(self, syn_id):
        return _live("get_entity", id=syn_id)

    def query_table(self, syn_id, limit):
        return _live("query_table", n_rows=2, _limit=limit)


class _FakeSas:
    def list_caslibs(self):
        return _unreachable("list_caslibs")  # exercise honest unreachable path

    def query_adam(self, caslib, table, limit):
        return _live("query_adam", caslib=caslib, table=table, _limit=limit)


class _FakeEga:
    def list_files(self, dataset, limit):
        return _live("list_files", dataset=dataset, _limit=limit)

    def file_metadata(self, file_id):
        return _live("file_metadata", accession_id=file_id)

    def authorized_datasets(self):
        return _live("authorized_datasets", n_authorized=1,
                     authorized_datasets=[{"dataset_id": "EGAD00001011049",
                                           "description": "Shallow whole genome sequencing",
                                           "dac_stable_id": "EGAC00001000388"}])

    def file_access_probe(self, file_id):
        return _live("file_access_probe", metadata_status=200, can_download=True,
                     file_id=file_id)


class _FakeGateway:
    def __init__(self):
        self.synapse = _FakeSynapse()
        self.sas = _FakeSas()
        self.ega = _FakeEga()

    def health(self):
        return {"endpoints": [{"endpoint": "C_EGA", "source": "ega", "status": "live",
                               "configured": False, "latency_ms": 10.0, "error": None}],
                "any_live": True}


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ZETA_GRAPH_API_KEY", API_KEY)
    from config import cfg
    monkeypatch.setattr(cfg, "ZETA_GRAPH_API_KEY", API_KEY, raising=False)
    from routers import sources as sources_router
    # force the router to use our fake gateway
    monkeypatch.setattr(sources_router, "_gateway", _FakeGateway(), raising=False)
    monkeypatch.setattr(sources_router, "_get_gateway", lambda: _FakeGateway())
    app = FastAPI()
    app.include_router(sources_router.router)
    return TestClient(app, raise_server_exceptions=True)


# ── auth ─────────────────────────────────────────────────────────────────────
def test_missing_key_401(client):
    r = client.get("/api/sources/health")
    assert r.status_code == 401


def test_wrong_key_401(client):
    r = client.get("/api/sources/health", headers={"X-Zeta-Api-Key": "nope"})
    assert r.status_code == 401


def test_key_unset_503(monkeypatch):
    from config import cfg
    monkeypatch.setattr(cfg, "ZETA_GRAPH_API_KEY", "", raising=False)
    from routers import sources as sources_router
    monkeypatch.setattr(sources_router, "_get_gateway", lambda: _FakeGateway())
    app = FastAPI()
    app.include_router(sources_router.router)
    c = TestClient(app)
    r = c.get("/api/sources/health", headers={"X-Zeta-Api-Key": "anything"})
    assert r.status_code == 503


# ── envelope passthrough per route ───────────────────────────────────────────
def test_health_ok(client):
    r = client.get("/api/sources/health", headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["any_live"] is True
    assert j["endpoints"][0]["endpoint"] == "C_EGA"


def test_synapse_whoami(client):
    r = client.get("/api/sources/synapse/whoami", headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "live"
    assert j["data"]["ownerId"] == "3388648"


def test_synapse_entity(client):
    r = client.get("/api/sources/synapse/entity/syn25569736", headers=HDR)
    assert r.status_code == 200
    assert r.json()["data"]["id"] == "syn25569736"


def test_synapse_table_limit_wiring(client):
    r = client.get("/api/sources/synapse/table/syn39607857", params={"limit": 25}, headers=HDR)
    assert r.status_code == 200
    assert r.json()["data"]["_limit"] == 25


def test_sas_caslibs_unreachable_passthrough(client):
    """Honest unreachable surfaces with data=None (router invents nothing)."""
    r = client.get("/api/sources/sas/caslibs", headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "unreachable"
    assert j["data"] is None
    assert j["error"].startswith("tls_ca_unavailable")


def test_sas_adam_param_wiring(client):
    r = client.get("/api/sources/sas/adam",
                   params={"caslib": "CASUSER", "table": "ADAE", "limit": 20}, headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["data"]["caslib"] == "CASUSER"
    assert j["data"]["table"] == "ADAE"
    assert j["data"]["_limit"] == 20


def test_ega_files(client):
    r = client.get("/api/sources/ega/files",
                   params={"dataset": "EGAD00001011049", "limit": 3}, headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "live"
    assert j["data"]["dataset"] == "EGAD00001011049"


def test_ega_file_metadata(client):
    r = client.get("/api/sources/ega/file/EGAF00008095047", headers=HDR)
    assert r.status_code == 200
    assert r.json()["data"]["accession_id"] == "EGAF00008095047"


def test_ega_authorized_datasets(client):
    r = client.get("/api/sources/ega/authorized-datasets", headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["data"]["n_authorized"] == 1
    assert j["data"]["authorized_datasets"][0]["dataset_id"] == "EGAD00001011049"


def test_ega_file_access_probe(client):
    r = client.get("/api/sources/ega/file/EGAF00008095569/access-probe", headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["data"]["can_download"] is True
    assert j["data"]["metadata_status"] == 200
