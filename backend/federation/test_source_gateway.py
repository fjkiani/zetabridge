"""Contract tests for the Session-15 live SourceGateway.

These run in CI **without any live credentials**. They mock the underlying
source clients (synapseclient / swat / httpx) at their seams and assert the
gateway's honesty contract holds on every path:

  * unconfigured (lib absent OR creds unset) -> typed 'unconfigured', data is None,
    NEVER an exception, NEVER fabricated data.
  * unreachable (simulated auth / TLS / network / timeout failure) -> typed
    'unreachable:<reason>', data is None.
  * live (mocked successful call) -> status 'live', real data passthrough,
    grounding populated, latency_ms numeric.

The **no-fabrication guard** (data is None on every non-live path) is the
analogue of the existing agent fabrication guard — it is what proves the
front-end "opportunities" surfaces can never show invented rows.

Run:
  cd backend && python3 -m pytest federation/test_source_gateway.py -q
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from federation import source_gateway as sg  # noqa: E402
from federation.source_gateway import (  # noqa: E402
    Envelope,
    SourceGateway,
    _clamp_limit,
    _reason,
)


# ── helpers ────────────────────────────────────────────────────────────────
class _FakeDF:
    """Minimal stand-in for a pandas DataFrame as the gateway uses it."""

    def __init__(self, rows):
        self._rows = rows
        self.columns = list(rows[0].keys()) if rows else []

    def __len__(self):
        return len(self._rows)

    def head(self, n):
        return _FakeDF(self._rows[:n])

    def to_dict(self, orient="records"):
        assert orient == "records"
        return list(self._rows)

    def tolist(self):
        return list(self._rows)


def _envelope_shape_ok(d: dict):
    """Every envelope, regardless of status, has the full uniform shape."""
    for key in ("endpoint", "source", "status", "action", "latency_ms",
                "data", "error", "grounding"):
        assert key in d, f"missing envelope key: {key}"
    assert d["status"] in ("live", "unreachable", "unconfigured")
    assert isinstance(d["latency_ms"], (int, float))
    assert isinstance(d["grounding"], dict)


# ── limit clamping ───────────────────────────────────────────────────────────
def test_clamp_limit_defaults_and_caps():
    assert _clamp_limit(None) == sg.DEFAULT_ROW_CAP
    assert _clamp_limit(0) == sg.DEFAULT_ROW_CAP
    assert _clamp_limit(-5) == sg.DEFAULT_ROW_CAP
    assert _clamp_limit(10) == 10
    assert _clamp_limit(10_000) == sg.MAX_ROW_CAP


# ── typed-reason mapping ─────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "msg,prefix",
    [
        ("SSL: CERTIFICATE_VERIFY_FAILED", "tls_ca_unavailable"),
        ("Could not find a suitable TLS CA certificate", "tls_ca_unavailable"),
        ("HTTP 401 Unauthorized", "auth"),
        ("403 Forbidden", "auth"),
        ("invalid credential", "auth"),
        ("Connection timed out", "timeout"),
        ("Failed to resolve host / getaddrinfo failed", "network"),
        ("some other weird error", "ValueError"),
    ],
)
def test_reason_typing(msg, prefix):
    assert _reason(ValueError(msg)).startswith(prefix)


# ── Synapse (A_MSK) ──────────────────────────────────────────────────────────
def test_synapse_unconfigured_when_lib_absent(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: False)
    env = SourceGateway().synapse.whoami().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unconfigured"
    assert env["data"] is None
    assert "synapseclient" in env["error"]


def test_synapse_unconfigured_when_token_unset(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SYNAPSE_AUTH_TOKEN", "", raising=False)
    env = SourceGateway().synapse.get_entity("syn25569736").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unconfigured"
    assert env["data"] is None
    assert env["grounding"]["syn_id"] == "syn25569736"


def test_synapse_unreachable_on_auth_failure(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SYNAPSE_AUTH_TOKEN", "bad-token", raising=False)
    client = SourceGateway().synapse

    def _boom():
        raise RuntimeError("401 Unauthorized: invalid authToken")

    monkeypatch.setattr(client, "_login", _boom)
    env = client.whoami().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert env["error"].startswith("auth")


def test_synapse_live_passthrough(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SYNAPSE_AUTH_TOKEN", "good-token", raising=False)
    client = SourceGateway().synapse

    class _FakeSyn:
        def getUserProfile(self):
            return {"ownerId": "3388648", "userName": "zeta", "firstName": "Z", "lastName": "B"}

    monkeypatch.setattr(client, "_login", lambda: _FakeSyn())
    env = client.whoami().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["ownerId"] == "3388648"
    assert env["data"]["userName"] == "zeta"
    assert env["grounding"]["ownerId"] == "3388648"
    assert isinstance(env["latency_ms"], float)


def test_synapse_query_table_live_and_clamped(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SYNAPSE_AUTH_TOKEN", "good-token", raising=False)
    client = SourceGateway().synapse

    rows = [{"a": 1, "b": 2}, {"a": 3, "b": 4}]

    class _FakeQ:
        def asDataFrame(self):
            return _FakeDF(rows)

    class _FakeSyn:
        def tableQuery(self, q, resultsAs="rowset"):
            assert "LIMIT" in q  # bounded query
            return _FakeQ()

    monkeypatch.setattr(client, "_login", lambda: _FakeSyn())
    env = client.query_table("syn39607857", limit=99999).to_dict()  # over the cap
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["n_rows"] == 2
    assert env["data"]["columns"] == ["a", "b"]
    assert env["grounding"]["limit"] == sg.MAX_ROW_CAP  # clamped


# ── SAS Viya CAS (B_SAS) ─────────────────────────────────────────────────────
def test_sas_unconfigured_when_host_unset(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SAS_CAS_HOST", "", raising=False)
    env = SourceGateway().sas.list_caslibs().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unconfigured"
    assert env["data"] is None


def test_sas_unreachable_on_tls(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SAS_CAS_HOST", "mpmprodvdmml.ondemand.sas.com", raising=False)
    monkeypatch.setattr(sg.cfg, "SAS_CAS_TOKEN", "tok", raising=False)
    client = SourceGateway().sas

    def _boom():
        raise RuntimeError("Could not find a suitable TLS CA certificate bundle")

    monkeypatch.setattr(client, "_connect", _boom)
    env = client.list_caslibs().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert env["error"].startswith("tls_ca_unavailable")


def test_sas_query_adam_live_passthrough(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SAS_CAS_HOST", "mpmprodvdmml.ondemand.sas.com", raising=False)
    monkeypatch.setattr(sg.cfg, "SAS_CAS_USER", "u", raising=False)
    monkeypatch.setattr(sg.cfg, "SAS_CAS_PASSWORD", "p", raising=False)
    client = SourceGateway().sas

    rows = [{"USUBJID": "01", "AEDECOD": "Neutropenia"},
            {"USUBJID": "02", "AEDECOD": "Anaemia"}]

    class _FakeCASTable:
        def head(self, n):
            return _FakeDF(rows[:n])

    class _FakeConn:
        def CASTable(self, table, caslib=None):
            return _FakeCASTable()

    monkeypatch.setattr(client, "_connect", lambda: _FakeConn())
    env = client.query_adam("CASUSER", "ADAE", limit=20).to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["table"] == "ADAE"
    assert env["data"]["n_rows"] == 2
    assert env["data"]["rows"][0]["AEDECOD"] == "Neutropenia"
    assert env["grounding"]["caslib"] == "CASUSER"


def test_sas_list_caslibs_live(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SAS_CAS_HOST", "h", raising=False)
    monkeypatch.setattr(sg.cfg, "SAS_CAS_TOKEN", "tok", raising=False)
    client = SourceGateway().sas

    class _FakeConn:
        def caslibinfo(self):
            return {"CASLibInfo": {"Name": _FakeDF([]) if False else _NameCol(["CASUSER", "Public"])}}

    monkeypatch.setattr(client, "_connect", lambda: _FakeConn())
    env = client.list_caslibs().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["caslibs"] == ["CASUSER", "Public"]
    assert env["data"]["n_caslibs"] == 2


class _NameCol:
    def __init__(self, vals):
        self._vals = vals

    def tolist(self):
        return list(self._vals)


# ── EGA (C_EGA) ──────────────────────────────────────────────────────────────
def test_ega_unconfigured_when_httpx_absent(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: False)
    env = SourceGateway().ega.list_files("EGAD00001011049", 3).to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unconfigured"
    assert env["data"] is None
    assert "httpx" in env["error"]


def test_ega_unreachable_on_network(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    import httpx

    def _boom(*a, **k):
        raise httpx.ConnectError("getaddrinfo failed: name resolution error")

    monkeypatch.setattr(httpx, "get", _boom)
    env = SourceGateway().ega.list_files("EGAD00001011049", 3).to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert env["error"].startswith("network")


def test_ega_list_files_live_passthrough(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    import httpx

    fake_files = [
        {
            "accession_id": "EGAF00008095047",
            "filesize": 795045329,
            "extension": "bam",
            "unencrypted_checksum": "c4ddcc36f80d8ed53df3a6a0a9246228",
            "unencrypted_checksum_type": "MD5",
            "locations": ["crg", "ebi"],
            "has_report": True,
        }
    ]

    class _FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return fake_files

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _FakeResp())
    env = SourceGateway().ega.list_files("EGAD00001011049", 3).to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["n_files"] == 1
    f = env["data"]["files"][0]
    assert f["accession_id"] == "EGAF00008095047"
    assert f["checksum"] == "c4ddcc36f80d8ed53df3a6a0a9246228"
    assert f["checksum_type"] == "MD5"
    assert f["locations"] == ["crg", "ebi"]
    assert env["grounding"]["dataset"] == "EGAD00001011049"


# ── no-fabrication guard (the critical invariant) ────────────────────────────
def test_no_fabrication_data_is_none_on_every_failure(monkeypatch):
    """Across ALL clients and ALL non-live statuses, data must be None.

    This is the machine-checkable version of 'the opportunity surfaces can
    never display invented rows'.
    """
    # 1) all libs absent -> unconfigured everywhere
    monkeypatch.setattr(sg, "_lib_present", lambda name: False)
    gw = SourceGateway()
    envs = [
        gw.synapse.whoami(),
        gw.synapse.get_entity("syn1"),
        gw.synapse.query_table("syn1", 5),
        gw.sas.list_caslibs(),
        gw.sas.query_adam("CASUSER", "ADAE", 5),
        gw.ega.list_files("EGAD00001011049", 5),
        gw.ega.file_metadata("EGAF00008095047"),
    ]
    for env in envs:
        d = env.to_dict()
        _envelope_shape_ok(d)
        assert d["status"] != "live"
        assert d["data"] is None, f"{d['source']}.{d['action']} fabricated data while {d['status']}"


def test_envelope_to_dict_is_plain():
    env = Envelope(endpoint="C_EGA", source="ega", status="live", action="x", data={"k": 1})
    d = env.to_dict()
    assert isinstance(d, dict)
    assert d["data"] == {"k": 1}


# ── facade wiring ────────────────────────────────────────────────────────────
def test_gateway_from_env_wires_three_clients():
    gw = SourceGateway.from_env()
    assert gw.synapse.endpoint == "A_MSK" and gw.synapse.source == "synapse"
    assert gw.sas.endpoint == "B_SAS" and gw.sas.source == "sas_cas"
    assert gw.ega.endpoint == "C_EGA" and gw.ega.source == "ega"
    assert sg.ENDPOINT_OF_SOURCE == {"synapse": "A_MSK", "sas_cas": "B_SAS", "ega": "C_EGA"}
