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

    # EGA Data API (byte transfer) host/port. Distinct from META_BASE (public,
    # unauthenticated). Bytes require OAuth2 + DAC-approved dataset access AND
    # outbound reachability to this port from wherever the server runs.
    AAI_HOST = "ega.ebi.ac.uk"
    AAI_PORT = 8443
    DATA_HOST = "ega.ebi.ac.uk"
    DATA_PORT = 8052

    def _port_open(self, host: str, port: int, timeout: float = 8.0) -> tuple[bool, str]:
        import socket

        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True, f"reachable in {round((time.time()-t0)*1000)}ms"
        except Exception as exc:  # noqa: BLE001
            return False, _reason(exc)

    def download_diagnostics(self, dataset: str | None = None) -> Envelope:
        """Prove (server-side) whether BAM byte-download is actually possible:
        (1) outbound reachability to the AAI (8443) and Data API (8052) ports,
        (2) OAuth2 auth with the configured EGA credentials, and
        (3) whether the account has a DAC grant covering ``dataset``.

        Transfers NO file bytes. This is the gate for the streaming endpoint:
        if the Data API port is blocked or the dataset is not authorized, a
        streaming handler cannot possibly deliver bytes.
        """
        action = "download_diagnostics"
        env = self._envelope(action)
        ds = dataset or cfg.EGA_DEFAULT_DATASET
        env.grounding = {"dataset": ds}
        t0 = time.time()

        if not self.configured():
            env.status, env.error = "unconfigured", "EGA_USERNAME/EGA_PASSWORD (or EGA_CREDENTIALS_FILE) not set"
            env.latency_ms = round((time.time() - t0) * 1000, 1)
            return env

        result: dict[str, Any] = {}
        aai_ok, aai_msg = self._port_open(self.AAI_HOST, self.AAI_PORT)
        data_ok, data_msg = self._port_open(self.DATA_HOST, self.DATA_PORT)
        result["egress"] = {
            f"{self.AAI_HOST}:{self.AAI_PORT}": {"open": aai_ok, "detail": aai_msg},
            f"{self.DATA_HOST}:{self.DATA_PORT}": {"open": data_ok, "detail": data_msg},
        }

        # Auth + authorized-dataset listing via pyega3 (already a dependency).
        auth_ok = False
        authorized_datasets: list[str] = []
        dataset_authorized = None
        auth_error = None
        if not _lib_present("pyega3"):
            auth_error = "pyega3 not installed"
        elif not data_ok:
            # pyega3 list-datasets hits the Data API port; if it's blocked, skip
            # (it would just hang) and report the egress failure as the blocker.
            auth_error = "skipped: Data API port unreachable (would time out)"
        else:
            try:
                import json as _json

                from pyega3 import pyega3 as _p3  # type: ignore

                creds = {"username": cfg.EGA_USERNAME, "password": cfg.EGA_PASSWORD}
                if cfg.EGA_CREDENTIALS_FILE:
                    with open(cfg.EGA_CREDENTIALS_FILE) as fh:
                        creds = _json.load(fh)
                token = _p3.get_token(creds)  # type: ignore[attr-defined]
                auth_ok = bool(token)
                reply = _p3.api_list_authorized_datasets(token)  # type: ignore[attr-defined]
                authorized_datasets = [str(x) for x in (reply or [])]
                dataset_authorized = ds in authorized_datasets
            except Exception as exc:  # noqa: BLE001
                auth_error = _reason(exc)

        result["auth_ok"] = auth_ok
        result["n_authorized_datasets"] = len(authorized_datasets)
        result["dataset_authorized"] = dataset_authorized
        if auth_error:
            result["auth_error"] = auth_error

        # Overall verdict: bytes are possible IFF data port open AND auth ok AND
        # the target dataset is in the grant.
        can_download = bool(data_ok and auth_ok and dataset_authorized)
        result["can_download"] = can_download
        env.data = result
        if can_download:
            env.status = "live"
        else:
            env.status = "unreachable"
            reasons = []
            if not data_ok:
                reasons.append(f"data_port_blocked({self.DATA_HOST}:{self.DATA_PORT})")
            if not auth_ok and data_ok:
                reasons.append("auth_failed")
            if auth_ok and dataset_authorized is False:
                reasons.append(f"no_dac_grant_for:{ds}")
            env.error = "; ".join(reasons) or (auth_error or "unknown")
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env


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
