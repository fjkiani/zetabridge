"""Contract tests for the Session-16 ICGC ARGO (D_ARGO) client.

Run in CI **without any live credentials**. They mock httpx at the ArgoClient
seams and assert the same honesty contract the other source clients uphold:

  * unconfigured (httpx absent, OR token unset for a controlled download) ->
    typed 'unconfigured', data is None, never an exception, never fabricated.
  * unreachable (auth 401 / DACO 403 / not_found 404 / network / timeout) ->
    typed 'unreachable', data is None, error carries the typed reason.
  * live (mocked 200) -> status 'live', real data passthrough, grounding
    populated, latency_ms numeric.

Two httpx seams are exercised because the client uses two idioms:
  * ``list_entities`` -> ``httpx.Client()`` context-manager (client.get)
  * ``entity_metadata`` / ``resolve_download`` / ``storage_alive`` -> module
    ``httpx.get``

The **no-fabrication guard** (data is None on every non-live path) is the
machine-checkable version of "real functionality, not stubs": the ARGO surface
can never show an invented object, checksum, or pre-signed URL.

Run:
  cd backend && python3 -m pytest federation/test_argo_client.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from federation import source_gateway as sg  # noqa: E402
from federation.source_gateway import ArgoClient  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────
def _envelope_shape_ok(d: dict):
    for key in ("endpoint", "source", "status", "action", "latency_ms",
                "data", "error", "grounding"):
        assert key in d, f"missing envelope key: {key}"
    assert d["status"] in ("live", "unreachable", "unconfigured")
    assert isinstance(d["latency_ms"], (int, float))
    assert isinstance(d["grounding"], dict)
    assert d["endpoint"] == "D_ARGO"
    assert d["source"] == "argo"


class _FakeResp:
    """Minimal stand-in for an httpx.Response."""

    def __init__(self, status_code=200, payload=None, raise_exc=None):
        self.status_code = status_code
        self._payload = payload
        self._raise_exc = raise_exc

    def raise_for_status(self):
        if self._raise_exc is not None:
            raise self._raise_exc
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("http error", request=None, response=None)

    def json(self):
        return self._payload


class _FakeClient:
    """Context-manager stand-in for httpx.Client used by list_entities.

    ``pages`` is a list of payloads returned in sequence for successive
    ``client.get`` calls (one per page).
    """

    def __init__(self, pages):
        self._pages = list(pages)
        self._i = 0

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get(self, *a, **k):
        payload = self._pages[self._i] if self._i < len(self._pages) else {"content": []}
        self._i += 1
        return _FakeResp(200, payload)


def _configure_token(monkeypatch, token="FAKE-DACO-TOKEN-for-tests-only"):
    monkeypatch.setattr(sg.cfg, "ICGC_ARGO_TOKEN", token, raising=False)


# ── filename-convention parser (pure, no network) ───────────────────────────
def test_parse_filename_full_cram():
    out = ArgoClient._parse_filename("PTC-SA.DO233995.SA607369.wxs.20210127.aln.cram")
    assert out["project_code"] == "PTC-SA"
    assert out["donor_id"] == "DO233995"
    assert out["sample_id"] == "SA607369"
    assert out["experiment"] == "wxs"
    assert out["date"] == "20210127"
    assert out["extension"] == "cram"


def test_parse_filename_rna_seq_bam():
    out = ArgoClient._parse_filename(
        "POG-CA.DO256772.SA622233.rna-seq.20250320.star.transcriptome_aln.bam"
    )
    assert out["project_code"] == "POG-CA"
    assert out["donor_id"] == "DO256772"
    assert out["sample_id"] == "SA622233"
    assert out["experiment"] == "rna-seq"
    assert out["extension"] == "bam"


def test_parse_filename_partial_no_fabrication():
    """Only confidently-identifiable parts are returned; no invented fields."""
    out = ArgoClient._parse_filename("weirdname_without_convention.txt")
    assert "donor_id" not in out
    assert "sample_id" not in out
    assert out["extension"] == "txt"
    # project_code is best-effort first token; that is a real substring, not fabricated
    assert out["project_code"] == "weirdname_without_convention"


# ── configured() ────────────────────────────────────────────────────────────
def test_configured_reflects_token(monkeypatch):
    monkeypatch.setattr(sg.cfg, "ICGC_ARGO_TOKEN", "", raising=False)
    assert ArgoClient().configured() is False
    monkeypatch.setattr(sg.cfg, "ICGC_ARGO_TOKEN", "tok", raising=False)
    assert ArgoClient().configured() is True


# ── unconfigured: httpx absent ───────────────────────────────────────────────
def test_list_entities_unconfigured_when_httpx_absent(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: False)
    env = ArgoClient().list_entities(project="POG-CA").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unconfigured"
    assert env["data"] is None
    assert "httpx" in env["error"]


def test_storage_alive_unconfigured_when_httpx_absent(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: False)
    env = ArgoClient().storage_alive().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unconfigured"
    assert env["data"] is None


# ── unconfigured: token required but unset (download resolution) ─────────────
def test_resolve_download_unconfigured_without_token(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "ICGC_ARGO_TOKEN", "", raising=False)
    env = ArgoClient().resolve_download("obj-123").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unconfigured"
    assert env["data"] is None
    assert "ICGC_ARGO_TOKEN" in env["error"]
    # grounding preserved even on the guard path
    assert env["grounding"]["object_id"] == "obj-123"


# ── unreachable: network error (both seams) ──────────────────────────────────
def test_list_entities_unreachable_on_network(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectError("getaddrinfo failed: name resolution error")

    monkeypatch.setattr(httpx, "Client", _boom)
    env = ArgoClient().list_entities(project="POG-CA").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert env["error"].startswith("network")


def test_storage_alive_unreachable_on_network(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectError("getaddrinfo failed")

    monkeypatch.setattr(httpx, "get", _boom)
    env = ArgoClient().storage_alive().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert env["error"].startswith("network")


# ── unreachable: typed auth / DACO / not_found on resolve_download ───────────
def test_resolve_download_auth_401(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _configure_token(monkeypatch)
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(status_code=401))
    env = ArgoClient().resolve_download("obj-123").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert "auth" in env["error"] and "401" in env["error"]


def test_resolve_download_forbidden_daco_403(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _configure_token(monkeypatch)
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(status_code=403))
    env = ArgoClient().resolve_download("obj-123").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert "forbidden" in env["error"] and "DACO" in env["error"]


def test_resolve_download_not_found_404(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _configure_token(monkeypatch)
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(status_code=404))
    env = ArgoClient().resolve_download("missing-obj").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert "not_found" in env["error"]


def test_entity_metadata_not_found_404(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(status_code=404))
    env = ArgoClient().entity_metadata("missing-obj").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert "not_found" in env["error"]


# ── live passthrough: list_entities (context-manager seam + filtering) ───────
def test_list_entities_live_passthrough_and_filter(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _configure_token(monkeypatch)
    import httpx

    page = {
        "totalElements": 2474,
        "content": [
            {"id": "obj-cram", "fileName": "PTC-SA.DO233995.SA607369.wxs.20210127.aln.cram",
             "gnosId": "gnos-1", "projectCode": "PTC-SA", "access": "controlled"},
            {"id": "obj-bam", "fileName": "POG-CA.DO256772.SA622233.rna-seq.20250320.star.aln.bam",
             "gnosId": "gnos-2", "projectCode": "POG-CA", "access": "controlled"},
            {"id": "obj-tgz", "fileName": "PTC-SA.DO233995.SA607369.wxs.20210127.oxog_metrics.tgz",
             "gnosId": "gnos-3", "projectCode": "PTC-SA", "access": "controlled"},
        ],
    }
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient([page]))

    # filter to CRAM only
    env = ArgoClient().list_entities(file_type="cram", size=50).to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["total_in_registry"] == 2474
    ents = env["data"]["entities"]
    assert len(ents) == 1
    e = ents[0]
    assert e["object_id"] == "obj-cram"
    assert e["extension"] == "cram"
    assert e["donor_id"] == "DO233995"
    assert e["sample_id"] == "SA607369"
    assert e["access"] == "controlled"
    assert env["data"]["n_returned"] == 1


def test_list_entities_live_project_filter(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    import httpx

    page = {
        "totalElements": 3,
        "content": [
            {"id": "a", "fileName": "PTC-SA.DO1.SA1.wgs.20200101.aln.cram", "projectCode": "PTC-SA", "access": "controlled"},
            {"id": "b", "fileName": "POG-CA.DO2.SA2.wgs.20200101.aln.cram", "projectCode": "POG-CA", "access": "controlled"},
        ],
    }
    monkeypatch.setattr(httpx, "Client", lambda *a, **k: _FakeClient([page]))
    env = ArgoClient().list_entities(project="POG-CA").to_dict()
    assert env["status"] == "live"
    ents = env["data"]["entities"]
    assert len(ents) == 1 and ents[0]["object_id"] == "b"


# ── live passthrough: resolve_download mints the pre-signed spec ─────────────
def test_resolve_download_live_mints_url(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _configure_token(monkeypatch)
    import httpx

    spec = {
        "objectId": "000401b0-7343-5eda-98e7-55a830f06cc4",
        "objectMd5": "fb02f2f5977d4a0955d579511ca1072a",
        "objectSize": 9135124387,
        "parts": [
            {"partNumber": 1, "offset": 0, "partSize": 9135124387,
             "url": "https://object.genomeinformatics.org/data/abc?X-Amz-Signature=deadbeef"},
        ],
    }
    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(200, spec))
    env = ArgoClient().resolve_download("000401b0-7343-5eda-98e7-55a830f06cc4").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    d = env["data"]
    assert d["object_md5"] == "fb02f2f5977d4a0955d579511ca1072a"
    assert d["object_size"] == 9135124387
    assert d["n_parts"] == 1
    assert d["object_host"] == "object.genomeinformatics.org"
    assert d["parts"][0]["url"].startswith("https://object.genomeinformatics.org/")
    assert "direct-from-object-storage" in d["transport"]
    # grounding reflects the resolved host + parts
    assert env["grounding"]["object_host"] == "object.genomeinformatics.org"
    assert env["grounding"]["n_parts"] == 1


def test_storage_alive_live(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    import httpx

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp(200, "heliograph"))
    env = ArgoClient().storage_alive().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["ping"] == "ok"


# ── the no-fabrication guard for ARGO across every non-live path ─────────────
def test_argo_no_fabrication_data_is_none_on_every_failure(monkeypatch):
    # httpx absent -> unconfigured everywhere (even token-less registry reads)
    monkeypatch.setattr(sg, "_lib_present", lambda name: False)
    c = ArgoClient()
    envs = [
        c.list_entities(project="POG-CA"),
        c.entity_metadata("obj-1"),
        c.resolve_download("obj-1"),
        c.storage_alive(),
    ]
    for env in envs:
        d = env.to_dict()
        _envelope_shape_ok(d)
        assert d["status"] != "live"
        assert d["data"] is None, f"argo.{d['action']} fabricated data while {d['status']}"

    # token unset but httpx present -> download resolution still refuses (no URL invented)
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "ICGC_ARGO_TOKEN", "", raising=False)
    d = ArgoClient().resolve_download("obj-1").to_dict()
    assert d["status"] == "unconfigured"
    assert d["data"] is None


# ── facade wiring: ArgoClient is registered as D_ARGO / argo ─────────────────
def test_gateway_wires_argo_client():
    gw = sg.SourceGateway.from_env()
    assert gw.argo.endpoint == "D_ARGO" and gw.argo.source == "argo"
    assert sg.ENDPOINT_OF_SOURCE["argo"] == "D_ARGO"
