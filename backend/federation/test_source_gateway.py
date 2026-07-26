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


def test_synapse_list_children_live(monkeypatch):
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SYNAPSE_AUTH_TOKEN", "good-token", raising=False)
    client = SourceGateway().synapse

    class _FakeSyn:
        def restPOST(self, uri, body, **kw):
            assert uri == "/entity/children"
            return {"page": [
                {"id": "syn1", "name": "sub", "type": "org.sagebionetworks.repo.model.Folder"},
                {"id": "syn2", "name": "x.rds", "type": "org.sagebionetworks.repo.model.FileEntity",
                 "fileSizeBytes": 42, "md5": "abc"},
            ], "nextPageToken": None}

    monkeypatch.setattr(client, "_login", lambda: _FakeSyn())
    env = client.list_children("syn0").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["n_children"] == 2
    assert env["data"]["children"][0]["is_container"] is True
    assert env["data"]["children"][1]["type"] == "FileEntity"


def test_synapse_download_diagnostics_no_filehandle(monkeypatch):
    """A container (no dataFileHandleId) reports can_download False, not an error."""
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SYNAPSE_AUTH_TOKEN", "good-token", raising=False)
    client = SourceGateway().synapse

    class _FakeSyn:
        def get(self, syn_id, downloadFile=False):
            return {"id": syn_id, "name": "folder",
                    "concreteType": "org.sagebionetworks.repo.model.Folder"}

    monkeypatch.setattr(client, "_login", lambda: _FakeSyn())
    env = client.download_diagnostics("synFolder").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["can_download"] is False
    assert "no dataFileHandleId" in env["data"]["reason"]


def test_synapse_resolve_download_live_and_gated(monkeypatch):
    """Minting a URL yields a live handoff; a gated file yields typed unreachable,
    never a fabricated URL."""
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    monkeypatch.setattr(sg.cfg, "SYNAPSE_AUTH_TOKEN", "good-token", raising=False)
    client = SourceGateway().synapse

    class _FakeSyn:
        fileHandleEndpoint = "https://repo-prod.prod.sagebase.org/file/v1"

        def get(self, syn_id, downloadFile=False):
            return {"id": syn_id, "name": "x.rds", "dataFileHandleId": "999",
                    "concreteType": "org.sagebionetworks.repo.model.FileEntity"}

        def restPOST(self, uri, body, endpoint=None, **kw):
            assert uri == "/fileHandle/batch"
            return {"requestedFiles": [{
                "preSignedURL": "https://s3.amazonaws.com/proddata/x.rds?sig=abc",
                "fileHandle": {"contentSize": 1938268174, "contentMd5": "03b0d4a5",
                               "contentType": "application/octet-stream"}}]}

    monkeypatch.setattr(client, "_login", lambda: _FakeSyn())
    env = client.resolve_download("syn51091852").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["url"].startswith("https://s3.amazonaws.com/")
    assert env["data"]["md5"] == "03b0d4a5"
    assert env["data"]["object_host"] == "s3.amazonaws.com"

    # gated: no preSignedURL -> unreachable, data None, no fabricated URL
    class _FakeSynGated(_FakeSyn):
        def restPOST(self, uri, body, endpoint=None, **kw):
            return {"requestedFiles": [{"failureCode": "NOT_FOUND", "fileHandle": {}}]}

    monkeypatch.setattr(client, "_login", lambda: _FakeSynGated())
    env2 = client.resolve_download("syn51091852").to_dict()
    assert env2["status"] == "unreachable"
    assert env2["data"] is None
    assert "auth_or_gated" in env2["error"]


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


# ── EGA authoritative entitlement (anti-sandbagging) ─────────────────────────
def _cfg_ega_creds(monkeypatch, user="fahad@jedilabs.org", pw="pw"):
    monkeypatch.setattr(sg.cfg, "EGA_USERNAME", user, raising=False)
    monkeypatch.setattr(sg.cfg, "EGA_PASSWORD", pw, raising=False)
    monkeypatch.setattr(sg.cfg, "EGA_CREDENTIALS_FILE", "", raising=False)


def test_ega_authorized_datasets_authoritative(monkeypatch):
    """authorized_datasets() reflects the account's REAL DAC grant (exactly
    EGAD00001011049), from the auth'd endpoint — not the ~21k public catalog."""
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _cfg_ega_creds(monkeypatch)
    client = SourceGateway().ega
    monkeypatch.setattr(client, "_data_token", lambda: ("tok", None))
    monkeypatch.setattr(
        client, "_authz_datasets",
        lambda tok: (
            [{"datasetId": "EGAD00001011049",
              "description": "Shallow whole genome sequencing",
              "dacStableId": "EGAC00001000388"}],
            None,
        ),
    )
    env = client.authorized_datasets().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["n_authorized"] == 1
    ds = env["data"]["authorized_datasets"][0]
    assert ds["dataset_id"] == "EGAD00001011049"
    assert ds["dac_stable_id"] == "EGAC00001000388"
    assert env["grounding"]["dataset_ids"] == ["EGAD00001011049"]


def test_ega_authorized_datasets_unconfigured(monkeypatch):
    monkeypatch.setattr(sg.cfg, "EGA_USERNAME", "", raising=False)
    monkeypatch.setattr(sg.cfg, "EGA_PASSWORD", "", raising=False)
    monkeypatch.setattr(sg.cfg, "EGA_CREDENTIALS_FILE", "", raising=False)
    env = SourceGateway().ega.authorized_datasets().to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unconfigured"
    assert env["data"] is None


def test_ega_username_whitespace_trimmed(monkeypatch):
    """A pasted username with a leading newline / trailing space must be trimmed
    (else EGA returns a 401 that masquerades as a bad password)."""
    _cfg_ega_creds(monkeypatch, user="\nfahad@jedilabs.org ", pw=" NewPassword123456! ")
    u, p = SourceGateway().ega._egadata_credentials()
    assert u == "fahad@jedilabs.org"
    assert p == "NewPassword123456!"


def test_ega_file_access_probe_authorized(monkeypatch):
    """BriTROC file: metadata 200 + byte probe 206 octet-stream -> can_download."""
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _cfg_ega_creds(monkeypatch)
    client = SourceGateway().ega
    monkeypatch.setattr(client, "_data_token", lambda: ("tok", None))
    monkeypatch.setattr(
        client, "_data_file_metadata",
        lambda tok, fid: (200, {"displayFileName": "JBLAB-4261.bam",
                                "fileSize": 122927119,
                                "datasetId": "EGAD00001011049",
                                "plainChecksum": "abc"}, None),
    )
    import httpx

    class _Resp:
        status_code = 206
        headers = {"content-type": "application/octet-stream"}

    monkeypatch.setattr(httpx, "get", lambda *a, **k: _Resp())
    env = client.file_access_probe("EGAF00008095569").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["can_download"] is True
    assert env["data"]["metadata_status"] == 200
    assert env["data"]["byte_probe_status"] == 206


def test_ega_file_access_probe_hercules_403_no_fabrication(monkeypatch):
    """HERCULES file: metadata 403 -> unreachable, data=None, typed auth reason.
    This is the genuine DAC boundary — never a fabricated 'ok'."""
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _cfg_ega_creds(monkeypatch)
    client = SourceGateway().ega
    monkeypatch.setattr(client, "_data_token", lambda: ("tok", None))
    monkeypatch.setattr(
        client, "_data_file_metadata",
        lambda tok, fid: (403, None, f"auth: not authorized for file '{fid}' (DAC grant required)"),
    )
    env = client.file_access_probe("EGAF00004723292").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"] is None
    assert env["error"].startswith("auth")
    assert env["grounding"]["metadata_status"] == 403


def test_ega_download_diagnostics_uses_authoritative_source(monkeypatch):
    """download_diagnostics must derive entitlement from the auth'd
    v2/metadata/datasets (real grants), NOT the public 21k catalog. Authorized
    dataset + open port + file probe 200 -> can_download True."""
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _cfg_ega_creds(monkeypatch)
    client = SourceGateway().ega
    monkeypatch.setattr(client, "_port_open", lambda host, port, timeout=8.0: (True, "reachable in 150ms"))
    monkeypatch.setattr(client, "_data_token", lambda: ("tok", None))
    monkeypatch.setattr(
        client, "_authz_datasets",
        lambda tok: ([{"datasetId": "EGAD00001011049", "description": "x", "dacStableId": "EGAC00001000388"}], None),
    )
    monkeypatch.setattr(
        client, "_data_dataset_files",
        lambda tok, ds: ([{"fileId": "EGAF00008095569", "fileSize": 122927119}], None),
    )
    monkeypatch.setattr(client, "_data_file_metadata", lambda tok, fid: (200, {"fileId": fid}, None))
    env = client.download_diagnostics("EGAD00001011049").to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["auth_ok"] is True
    assert env["data"]["dataset_authorized"] is True
    assert env["data"]["can_download"] is True
    assert env["data"]["file_probe"]["metadata_status"] == 200
    assert "EGAD00001011049" in env["data"]["authorized_datasets"]


def test_ega_download_diagnostics_unauthorized_dataset(monkeypatch):
    """A dataset NOT in the account's grants (e.g. HERCULES) -> not entitled,
    can_download False, honest no_dac_grant reason, data present but data=None
    on the envelope's failure fields is preserved (status unreachable)."""
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _cfg_ega_creds(monkeypatch)
    client = SourceGateway().ega
    monkeypatch.setattr(client, "_port_open", lambda host, port, timeout=8.0: (True, "reachable"))
    monkeypatch.setattr(client, "_data_token", lambda: ("tok", None))
    monkeypatch.setattr(
        client, "_authz_datasets",
        lambda tok: ([{"datasetId": "EGAD00001011049", "description": "x", "dacStableId": "y"}], None),
    )
    env = client.download_diagnostics("EGAD00001006456").to_dict()  # HERCULES
    _envelope_shape_ok(env)
    assert env["status"] == "unreachable"
    assert env["data"]["dataset_authorized"] is False
    assert env["data"]["can_download"] is False
    assert "no_dac_grant_for:EGAD00001006456" in env["error"]


def test_ega_list_files_authorized_path(monkeypatch):
    """With creds + an authorized dataset, list_files uses the auth'd file list
    (679-style records: fileId/fileSize/plainChecksum) and tags access=authorized."""
    monkeypatch.setattr(sg, "_lib_present", lambda name: True)
    _cfg_ega_creds(monkeypatch)
    client = SourceGateway().ega
    monkeypatch.setattr(client, "_data_token", lambda: ("tok", None))
    monkeypatch.setattr(
        client, "_data_dataset_files",
        lambda tok, ds: (
            [{"fileId": "EGAF00008095569", "fileSize": 122927119,
              "displayFileName": "JBLAB-4261.bam", "plainChecksum": "abc",
              "plainChecksumType": "MD5", "fileStatus": "available",
              "indexFileId": "EGAF00008095570"}],
            None,
        ),
    )
    env = client.list_files("EGAD00001011049", 3).to_dict()
    _envelope_shape_ok(env)
    assert env["status"] == "live"
    assert env["data"]["access"] == "authorized"
    assert env["data"]["n_files"] == 1
    f = env["data"]["files"][0]
    assert f["accession_id"] == "EGAF00008095569"
    assert f["display_file_name"] == "JBLAB-4261.bam"
    assert f["checksum"] == "abc"


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
def test_gateway_from_env_wires_all_clients():
    gw = SourceGateway.from_env()
    assert gw.synapse.endpoint == "A_MSK" and gw.synapse.source == "synapse"
    assert gw.sas.endpoint == "B_SAS" and gw.sas.source == "sas_cas"
    assert gw.ega.endpoint == "C_EGA" and gw.ega.source == "ega"
    assert gw.argo.endpoint == "D_ARGO" and gw.argo.source == "argo"
    assert sg.ENDPOINT_OF_SOURCE == {
        "synapse": "A_MSK", "sas_cas": "B_SAS", "ega": "C_EGA", "argo": "D_ARGO",
    }
