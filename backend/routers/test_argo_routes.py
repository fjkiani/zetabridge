"""Contract tests for the Session-16 /api/sources/argo/* routes.

Run in CI without live creds. The source gateway is replaced by a fake so we
test the router contract (auth, envelope passthrough, param wiring, and the
typed _argo_http_error mapping) in isolation from real network calls.

Asserts:
  * require_api_key: 401 without/with wrong key; 503 when ZETA_GRAPH_API_KEY
    unset.
  * argo/health: returns configured flag + status, no data extraction.
  * argo/entities: envelope passthrough + param wiring (project/access/
    file_type/size).
  * argo/entity/{id}: 200 live; 404 when the gateway reports not_found; the
    router never invents a body (data=None on the non-live path is mapped to a
    typed HTTP error, not a fake 200).
  * argo/download-url/{id}: mints the pre-signed spec on live; 401 on auth,
    403 on DACO-forbidden, 503 on unconfigured (no token) — and NEVER returns a
    URL on a failure path.

Run:
  cd backend && env ZETA_GRAPH_API_KEY=test-key-abc123 \
      python3 -m pytest routers/test_argo_routes.py -q
"""

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

API_KEY = "test-key-abc123"
HDR = {"X-Zeta-Api-Key": API_KEY}


# ── canned envelope helpers (D_ARGO shaped) ──────────────────────────────────
class _FakeEnv:
    def __init__(self, **kw):
        self._d = kw

    def to_dict(self):
        return self._d


def _live(action, **data):
    return _FakeEnv(endpoint="D_ARGO", source="argo", status="live", action=action,
                    latency_ms=15.0, data=data or {"ok": True}, error=None,
                    grounding={"action": action})


def _unreachable(action, error):
    return _FakeEnv(endpoint="D_ARGO", source="argo", status="unreachable",
                    action=action, latency_ms=9.0, data=None, error=error, grounding={})


def _unconfigured(action, error):
    return _FakeEnv(endpoint="D_ARGO", source="argo", status="unconfigured",
                    action=action, latency_ms=0.0, data=None, error=error, grounding={})


# ── configurable fake ArgoClient ─────────────────────────────────────────────
class _FakeArgo:
    """Behaviour toggled per-test via class attributes on a fresh instance."""

    def __init__(self, configured=True, entity_mode="live", download_mode="live"):
        self._configured = configured
        self._entity_mode = entity_mode
        self._download_mode = download_mode
        self.calls = {}

    def configured(self):
        return self._configured

    def storage_alive(self):
        return _live("storage_alive", ping="ok")

    def list_entities(self, project, access, file_type, size):
        self.calls["list_entities"] = (project, access, file_type, size)
        return _live("list_entities", total_in_registry=2474, n_returned=1,
                     entities=[{"object_id": "obj-cram", "file_name": "x.cram",
                                "project_code": project, "access": access}])

    def entity_metadata(self, object_id):
        self.calls["entity_metadata"] = (object_id,)
        if self._entity_mode == "not_found":
            return _unreachable("entity_metadata", f"not_found: object {object_id}")
        return _live("entity_metadata", object_id=object_id, file_name="x.cram")

    def resolve_download(self, object_id, offset, length):
        self.calls["resolve_download"] = (object_id, offset, length)
        if self._download_mode == "auth":
            return _unreachable("resolve_download", "auth: 401 invalid or expired ICGC_ARGO_TOKEN")
        if self._download_mode == "daco":
            return _unreachable("resolve_download", "forbidden: token lacks DACO controlled-data access")
        if self._download_mode == "unconfigured":
            return _unconfigured("resolve_download", "unconfigured: ICGC_ARGO_TOKEN not set")
        return _live("resolve_download", object_id=object_id, object_md5="fb02f2f5",
                     object_size=9135124387, object_host="object.genomeinformatics.org",
                     n_parts=1, parts=[{"partNumber": 1, "offset": offset, "partSize": 100,
                                        "url": "https://object.genomeinformatics.org/data/abc?X-Amz-Signature=xyz"}],
                     transport="direct-from-object-storage; stream parts[].url directly")


class _FakeGateway:
    def __init__(self, argo):
        self.argo = argo


def _make_client(monkeypatch, argo, api_key=API_KEY):
    if api_key is not None:
        monkeypatch.setenv("ZETA_GRAPH_API_KEY", api_key)
    from config import cfg
    monkeypatch.setattr(cfg, "ZETA_GRAPH_API_KEY", api_key or "", raising=False)
    from routers import sources as sources_router
    gw = _FakeGateway(argo)
    monkeypatch.setattr(sources_router, "_gateway", gw, raising=False)
    monkeypatch.setattr(sources_router, "_get_gateway", lambda: gw)
    app = FastAPI()
    app.include_router(sources_router.router)
    return TestClient(app, raise_server_exceptions=True)


# ── auth ─────────────────────────────────────────────────────────────────────
def test_argo_missing_key_401(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo())
    assert c.get("/api/sources/argo/health").status_code == 401


def test_argo_wrong_key_401(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo())
    assert c.get("/api/sources/argo/health", headers={"X-Zeta-Api-Key": "nope"}).status_code == 401


def test_argo_key_unset_503(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo(), api_key=None)
    r = c.get("/api/sources/argo/health", headers={"X-Zeta-Api-Key": "anything"})
    assert r.status_code == 503


# ── health ───────────────────────────────────────────────────────────────────
def test_argo_health_configured(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo(configured=True))
    r = c.get("/api/sources/argo/health", headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["endpoint"] == "D_ARGO"
    assert j["source"] == "argo"
    assert j["configured"] is True
    assert j["status"] == "live"


def test_argo_health_unconfigured_flag(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo(configured=False))
    r = c.get("/api/sources/argo/health", headers=HDR)
    assert r.status_code == 200
    assert r.json()["configured"] is False


# ── entities: passthrough + param wiring ─────────────────────────────────────
def test_argo_entities_param_wiring(monkeypatch):
    argo = _FakeArgo()
    c = _make_client(monkeypatch, argo)
    r = c.get("/api/sources/argo/entities",
              params={"project": "POG-CA", "access": "controlled", "file_type": "cram", "size": 25},
              headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "live"
    assert j["data"]["total_in_registry"] == 2474
    # params threaded through to the client in the right order
    assert argo.calls["list_entities"] == ("POG-CA", "controlled", "cram", 25)


def test_argo_entities_defaults(monkeypatch):
    argo = _FakeArgo()
    c = _make_client(monkeypatch, argo)
    r = c.get("/api/sources/argo/entities", headers=HDR)
    assert r.status_code == 200
    # defaults: project/access/file_type None, size 50
    assert argo.calls["list_entities"] == (None, None, None, 50)


# ── entity metadata: live + typed 404 (no fabricated body) ───────────────────
def test_argo_entity_live(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo(entity_mode="live"))
    r = c.get("/api/sources/argo/entity/obj-cram", headers=HDR)
    assert r.status_code == 200
    assert r.json()["data"]["object_id"] == "obj-cram"


def test_argo_entity_not_found_404(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo(entity_mode="not_found"))
    r = c.get("/api/sources/argo/entity/missing", headers=HDR)
    assert r.status_code == 404
    assert "not_found" in r.json()["detail"]


# ── download-url: mints spec live; typed failures never leak a URL ───────────
def test_argo_download_url_live_mints_spec(monkeypatch):
    argo = _FakeArgo(download_mode="live")
    c = _make_client(monkeypatch, argo)
    r = c.get("/api/sources/argo/download-url/000401b0", params={"offset": 0, "length": 100}, headers=HDR)
    assert r.status_code == 200
    j = r.json()
    assert j["status"] == "live"
    assert j["data"]["object_md5"] == "fb02f2f5"
    assert j["data"]["parts"][0]["url"].startswith("https://object.genomeinformatics.org/")
    assert argo.calls["resolve_download"] == ("000401b0", 0, 100)


def test_argo_download_url_default_range(monkeypatch):
    argo = _FakeArgo(download_mode="live")
    c = _make_client(monkeypatch, argo)
    r = c.get("/api/sources/argo/download-url/000401b0", headers=HDR)
    assert r.status_code == 200
    # default offset 0, length -1 (whole object)
    assert argo.calls["resolve_download"] == ("000401b0", 0, -1)


def test_argo_download_url_auth_401(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo(download_mode="auth"))
    r = c.get("/api/sources/argo/download-url/obj", headers=HDR)
    assert r.status_code == 401
    assert "auth" in r.json()["detail"]


def test_argo_download_url_daco_403(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo(download_mode="daco"))
    r = c.get("/api/sources/argo/download-url/obj", headers=HDR)
    assert r.status_code == 403
    assert "DACO" in r.json()["detail"] or "forbidden" in r.json()["detail"].lower()


def test_argo_download_url_unconfigured_503(monkeypatch):
    c = _make_client(monkeypatch, _FakeArgo(download_mode="unconfigured"))
    r = c.get("/api/sources/argo/download-url/obj", headers=HDR)
    assert r.status_code == 503
