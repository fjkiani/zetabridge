"""Zeta Bridge — live source gateway (Session 15).

Read-only, honest, server-side connectors to the three *source* systems behind
the federated knowledge graph:

    A_MSK  -> Synapse            (synapseclient, personal-access JWT)
    B_SAS  -> SAS Viya CAS       (swat, Project Data Sphere clinical warehouse)
    C_EGA  -> EGA                (pyega3 / EGA REST, controlled-access genomics)

Design contract (this is the whole point of the module):
  * Every method returns a uniform ``Envelope`` dict — never a raw client object,
    never a bare exception.
  * On success:   status="live",   data=<real payload>,  error=None
  * On no creds:  status="unconfigured", data=None,       error="<what's missing>"
  * On failure:   status="unreachable",  data=None,       error="<typed reason>"
  * ``data`` is ``None`` on every non-live path. We NEVER fabricate rows. A caller
    (REST router, MCP tool, front-end) can trust that data present == data real.

Credentials live only here (read from ``cfg`` / env). They are never placed in an
envelope, so they can never leak to an API caller.
"""

from __future__ import annotations

import importlib.util
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

# config import works whether imported as ``federation.source_gateway`` (tests,
# REST app) or after ``backend`` is on sys.path (MCP server).
try:
    from config import cfg
except ImportError:  # pragma: no cover - import-path shim
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from config import cfg


ENDPOINT_OF_SOURCE = {"synapse": "A_MSK", "sas_cas": "B_SAS", "ega": "C_EGA"}

# per-call bounds — keep live extraction cheap and safe
DEFAULT_ROW_CAP = 50
MAX_ROW_CAP = 500
CONNECT_TIMEOUT_S = 20


def _lib_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


@dataclass
class Envelope:
    """Uniform, honest result for any live source call."""

    endpoint: str
    source: str
    status: str  # "live" | "unreachable" | "unconfigured"
    action: str
    latency_ms: float = 0.0
    data: Optional[Any] = None
    error: Optional[str] = None
    grounding: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _clamp_limit(limit: int | None) -> int:
    if not limit or limit < 1:
        return DEFAULT_ROW_CAP
    return min(int(limit), MAX_ROW_CAP)


# ---------------------------------------------------------------------------
# Synapse (A_MSK)
# ---------------------------------------------------------------------------
class SynapseClient:
    source = "synapse"
    endpoint = "A_MSK"

    def __init__(self) -> None:
        self._syn = None  # lazy login

    def configured(self) -> bool:
        return bool(cfg.SYNAPSE_AUTH_TOKEN)

    def _login(self):
        """Lazily build + cache an authenticated synapseclient. Raises on failure."""
        if self._syn is not None:
            return self._syn
        import synapseclient  # imported lazily so a missing lib is 'unconfigured'

        syn = synapseclient.Synapse(silent=True, skip_checks=True)
        syn.login(authToken=cfg.SYNAPSE_AUTH_TOKEN)
        self._syn = syn
        return syn

    def _envelope(self, action: str) -> Envelope:
        return Envelope(endpoint=self.endpoint, source=self.source, status="", action=action)

    def _guard(self, action: str) -> Optional[Envelope]:
        """Return an unconfigured envelope if we can't even attempt the call."""
        if not _lib_present("synapseclient"):
            env = self._envelope(action)
            env.status, env.error = "unconfigured", "synapseclient not installed"
            return env
        if not self.configured():
            env = self._envelope(action)
            env.status, env.error = "unconfigured", "SYNAPSE_AUTH_TOKEN not set"
            return env
        return None

    def whoami(self) -> Envelope:
        action = "whoami"
        guard = self._guard(action)
        if guard:
            return guard
        env = self._envelope(action)
        t0 = time.time()
        try:
            syn = self._login()
            prof = syn.getUserProfile()
            env.status = "live"
            env.data = {
                "ownerId": prof.get("ownerId"),
                "userName": prof.get("userName"),
                "firstName": prof.get("firstName"),
                "lastName": prof.get("lastName"),
            }
            env.grounding = {"ownerId": prof.get("ownerId")}
        except Exception as exc:  # noqa: BLE001 - typed, honest failure
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def get_entity(self, syn_id: str) -> Envelope:
        action = "get_entity"
        guard = self._guard(action)
        if guard:
            guard.grounding = {"syn_id": syn_id}
            return guard
        env = self._envelope(action)
        env.grounding = {"syn_id": syn_id}
        t0 = time.time()
        try:
            syn = self._login()
            ent = syn.get(syn_id, downloadFile=False)
            env.status = "live"
            env.data = {
                "id": ent.get("id"),
                "name": ent.get("name"),
                "concreteType": ent.get("concreteType"),
                "parentId": ent.get("parentId"),
                "createdOn": ent.get("createdOn"),
                "modifiedOn": ent.get("modifiedOn"),
                "versionNumber": ent.get("versionNumber"),
            }
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def query_table(self, syn_id: str, limit: int | None = None) -> Envelope:
        action = "query_table"
        n = _clamp_limit(limit)
        guard = self._guard(action)
        if guard:
            guard.grounding = {"syn_id": syn_id, "limit": n}
            return guard
        env = self._envelope(action)
        env.grounding = {"syn_id": syn_id, "limit": n}
        t0 = time.time()
        try:
            syn = self._login()
            q = syn.tableQuery(f"SELECT * FROM {syn_id} LIMIT {n}", resultsAs="rowset")
            df = q.asDataFrame()
            env.status = "live"
            env.data = {
                "columns": [str(c) for c in df.columns],
                "n_rows": int(len(df)),
                "rows": df.head(n).to_dict(orient="records"),
            }
            env.grounding["n_rows"] = int(len(df))
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env


# ---------------------------------------------------------------------------
# SAS Viya CAS (B_SAS)
# ---------------------------------------------------------------------------
class SasCasClient:
    source = "sas_cas"
    endpoint = "B_SAS"

    def __init__(self) -> None:
        self._conn = None

    def configured(self) -> bool:
        # need a host and (token OR user+password)
        if not cfg.SAS_CAS_HOST:
            return False
        return bool(cfg.SAS_CAS_TOKEN or (cfg.SAS_CAS_USER and cfg.SAS_CAS_PASSWORD))

    def _connect(self):
        if self._conn is not None:
            return self._conn
        import swat  # lazy

        kwargs: dict[str, Any] = {
            "hostname": cfg.SAS_CAS_HOST,
            "port": cfg.SAS_CAS_PORT,
            "protocol": cfg.SAS_CAS_PROTOCOL,
        }
        if cfg.SAS_CAS_CADATA:
            kwargs["cafile"] = cfg.SAS_CAS_CADATA
        if cfg.SAS_CAS_TOKEN:
            conn = swat.CAS(**kwargs, password=cfg.SAS_CAS_TOKEN)
        else:
            conn = swat.CAS(**kwargs, username=cfg.SAS_CAS_USER, password=cfg.SAS_CAS_PASSWORD)
        self._conn = conn
        return conn

    def _envelope(self, action: str) -> Envelope:
        return Envelope(endpoint=self.endpoint, source=self.source, status="", action=action)

    def _guard(self, action: str) -> Optional[Envelope]:
        if not _lib_present("swat"):
            env = self._envelope(action)
            env.status, env.error = "unconfigured", "swat (SAS CAS client) not installed"
            return env
        if not self.configured():
            env = self._envelope(action)
            env.status, env.error = (
                "unconfigured",
                "SAS_CAS_HOST + (SAS_CAS_TOKEN or SAS_CAS_USER/PASSWORD) not set",
            )
            return env
        return None

    def list_caslibs(self) -> Envelope:
        action = "list_caslibs"
        guard = self._guard(action)
        if guard:
            return guard
        env = self._envelope(action)
        env.grounding = {"host": cfg.SAS_CAS_HOST}
        t0 = time.time()
        try:
            conn = self._connect()
            res = conn.caslibinfo()
            df = res["CASLibInfo"]
            libs = [str(x) for x in df["Name"].tolist()]
            env.status = "live"
            env.data = {"n_caslibs": len(libs), "caslibs": libs}
            env.grounding["n_caslibs"] = len(libs)
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def query_adam(self, caslib: str, table: str, limit: int | None = None) -> Envelope:
        action = "query_adam"
        n = _clamp_limit(limit)
        guard = self._guard(action)
        if guard:
            guard.grounding = {"caslib": caslib, "table": table, "limit": n}
            return guard
        env = self._envelope(action)
        env.grounding = {"caslib": caslib, "table": table, "limit": n}
        t0 = time.time()
        try:
            conn = self._connect()
            # load the CAS table reference then fetch a bounded slice
            tbl = conn.CASTable(table, caslib=caslib)
            df = tbl.head(n)
            env.status = "live"
            env.data = {
                "caslib": caslib,
                "table": table,
                "columns": [str(c) for c in df.columns],
                "n_rows": int(len(df)),
                "rows": df.to_dict(orient="records"),
            }
            env.grounding["n_rows"] = int(len(df))
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env


# ---------------------------------------------------------------------------
# EGA (C_EGA) — controlled access; listing + metadata only in this cut
# ---------------------------------------------------------------------------
class EgaClient:
    source = "ega"
    endpoint = "C_EGA"
    # EGA metadata API (public metadata; file *content* stays controlled)
    META_BASE = "https://metadata.ega-archive.org"

    def configured(self) -> bool:
        return bool(
            cfg.EGA_CREDENTIALS_FILE or (cfg.EGA_USERNAME and cfg.EGA_PASSWORD)
        )

    def _envelope(self, action: str) -> Envelope:
        return Envelope(endpoint=self.endpoint, source=self.source, status="", action=action)

    def list_files(self, dataset: str | None = None, limit: int | None = None) -> Envelope:
        """List files in an EGA dataset via the public metadata API.

        Dataset/file *metadata* is public; file *content* is controlled-access
        and intentionally NOT fetched here. Works without credentials for
        metadata; credentials gate content download (a later phase).
        """
        action = "list_files"
        ds = dataset or cfg.EGA_DEFAULT_DATASET
        n = _clamp_limit(limit)
        env = self._envelope(action)
        env.grounding = {"dataset": ds, "limit": n}
        if not _lib_present("httpx"):
            env.status, env.error = "unconfigured", "httpx not installed"
            return env
        import httpx

        t0 = time.time()
        try:
            url = f"{self.META_BASE}/datasets/{ds}/files"
            r = httpx.get(url, params={"limit": n}, timeout=CONNECT_TIMEOUT_S)
            r.raise_for_status()
            payload = r.json()
            files = payload if isinstance(payload, list) else payload.get("files", payload)
            if isinstance(files, list):
                # real EGA metadata schema: accession_id / filesize / extension /
                # unencrypted_checksum / locations. File *content* stays controlled.
                slim = [
                    {
                        "accession_id": f.get("accession_id") or f.get("egaf") or f.get("id"),
                        "filesize": f.get("filesize") or f.get("size"),
                        "extension": f.get("extension"),
                        "checksum": f.get("unencrypted_checksum"),
                        "checksum_type": f.get("unencrypted_checksum_type"),
                        "locations": f.get("locations"),
                        "has_report": f.get("has_report"),
                    }
                    for f in files[:n]
                ]
            else:
                slim = files
            env.status = "live"
            env.data = {"dataset": ds, "n_files": len(slim) if isinstance(slim, list) else None, "files": slim}
            env.grounding["n_files"] = len(slim) if isinstance(slim, list) else None
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def file_metadata(self, file_id: str) -> Envelope:
        action = "file_metadata"
        env = self._envelope(action)
        env.grounding = {"file_id": file_id}
        if not _lib_present("httpx"):
            env.status, env.error = "unconfigured", "httpx not installed"
            return env
        import httpx

        t0 = time.time()
        try:
            url = f"{self.META_BASE}/files/{file_id}"
            r = httpx.get(url, timeout=CONNECT_TIMEOUT_S)
            r.raise_for_status()
            env.status = "live"
            env.data = r.json()
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    # --- content download (the "later phase") ------------------------------
    # Server-side authenticated byte transfer. Credentials stay in cfg/env and
    # are never returned to the caller. We reuse the battle-tested pyega3 client
    # (auth token flow, retry/resume, correct current Data API host+port) rather
    # than hand-rolling the OAuth + slicing, which drifts when EGA changes ports.
    def _pyega3_data_client(self):
        """Build an authenticated pyega3 DataClient from server-side creds.

        Returns (data_client, file_id_ok) or raises. Credentials come from
        cfg.EGA_CREDENTIALS_FILE if set, else cfg.EGA_USERNAME/PASSWORD.
        """
        from pyega3.libs.auth_client import AuthClient
        from pyega3.libs.credentials import Credentials
        from pyega3.libs.data_client import DataClient
        from pyega3.libs.server_config import ServerConfig

        if cfg.EGA_CREDENTIALS_FILE:
            creds = Credentials.from_file(cfg.EGA_CREDENTIALS_FILE)
        else:
            creds = Credentials(username=cfg.EGA_USERNAME, password=cfg.EGA_PASSWORD)

        server_config = ServerConfig.from_file(ServerConfig.default_config_path())
        standard_headers = {"Client-Version": "zetabridge-proxy", "Session-Id": "zetabridge"}
        auth_client = AuthClient(server_config.url_auth, server_config.client_secret, standard_headers)
        auth_client.credentials = creds
        data_client = DataClient(
            server_config.url_api,
            server_config.url_api_ticket,
            server_config.url_api_stats,
            auth_client,
            standard_headers,
            connections=1,
            metadata_url=server_config.url_api_metadata,
            api_version=server_config.api_version,
        )
        return data_client

    def download_size(self, file_id: str) -> Envelope:
        """Authenticated file size lookup via the Data API (proves auth works)."""
        action = "download_size"
        env = self._envelope(action)
        env.grounding = {"file_id": file_id}
        if not self.configured():
            env.status, env.error = "unconfigured", "EGA credentials not set server-side"
            return env
        if not _lib_present("pyega3"):
            env.status, env.error = "unconfigured", "pyega3 not installed"
            return env
        t0 = time.time()
        try:
            dc = self._pyega3_data_client()
            meta = dc.get_json(f"/files/{file_id}")
            size = None
            if isinstance(meta, dict):
                size = meta.get("fileSize") or meta.get("unencryptedSize") or meta.get("size")
            env.status = "live"
            env.data = {"file_id": file_id, "size_bytes": size, "raw": meta}
            env.grounding["size_bytes"] = size
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def iter_file_bytes(self, file_id: str, chunk_size: int = 8 * 1024 * 1024):
        """Yield decrypted file bytes for ``file_id`` from the EGA Data API.

        Generator for streaming straight to an HTTP response. Auth is server-side.
        Raises on misconfiguration/auth so the route can surface a typed error
        *before* the response body starts. destinationFormat=plain -> unencrypted.
        """
        if not self.configured():
            raise RuntimeError("unconfigured: EGA credentials not set server-side")
        if not _lib_present("pyega3"):
            raise RuntimeError("unconfigured: pyega3 not installed")
        dc = self._pyega3_data_client()
        # Force token acquisition now so an auth failure raises before streaming.
        _ = dc.auth_client.token
        path = f"/files/{file_id}?destinationFormat=plain"
        with dc.get_stream(path) as r:
            for chunk in r.iter_content(chunk_size=chunk_size):
                if chunk:
                    yield chunk


def _reason(exc: Exception) -> str:
    """Map an exception to a short, honest, typed reason string."""
    name = type(exc).__name__
    msg = str(exc).strip().replace("\n", " ")
    low = msg.lower()
    if "certificate" in low or "ssl" in low or "tls" in low or "ca cert" in low:
        return f"tls_ca_unavailable: {name}: {msg[:200]}"
    if "401" in low or "403" in low or "unauthor" in low or "forbidden" in low or "credential" in low or "login" in low:
        return f"auth: {name}: {msg[:200]}"
    if "timed out" in low or "timeout" in low:
        return f"timeout: {name}: {msg[:200]}"
    if "connect" in low or "resolve" in low or "network" in low or "getaddrinfo" in low:
        return f"network: {name}: {msg[:200]}"
    return f"{name}: {msg[:220]}"


# ---------------------------------------------------------------------------
# Gateway facade
# ---------------------------------------------------------------------------
class SourceGateway:
    """Single entry point the REST router and MCP server both call."""

    def __init__(self) -> None:
        self.synapse = SynapseClient()
        self.sas = SasCasClient()
        self.ega = EgaClient()

    @classmethod
    def from_env(cls) -> "SourceGateway":
        return cls()

    def health(self) -> dict[str, Any]:
        """Report configured/live status per endpoint WITHOUT extracting data.

        For Synapse/SAS this attempts a real handshake (whoami / caslib list) so
        'live' means actually reachable. EGA reports configured-for-content plus
        metadata reachability.
        """
        syn = self.synapse.whoami()
        sas = self.sas.list_caslibs()
        ega = self.ega.list_files(limit=1)

        def _slim(env: Envelope, extra_configured: bool) -> dict[str, Any]:
            d = env.to_dict()
            return {
                "endpoint": d["endpoint"],
                "source": d["source"],
                "status": d["status"],
                "configured": extra_configured,
                "latency_ms": d["latency_ms"],
                "error": d["error"],
            }

        return {
            "endpoints": [
                _slim(syn, self.synapse.configured()),
                _slim(sas, self.sas.configured()),
                _slim(ega, self.ega.configured()),
            ],
            "any_live": any(e.status == "live" for e in (syn, sas, ega)),
        }
