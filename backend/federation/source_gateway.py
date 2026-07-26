"""Zeta Bridge — live source gateway (Session 15).

Read-only, honest, server-side connectors to the three *source* systems behind
the federated knowledge graph:

    A_MSK  -> Synapse            (synapseclient, personal-access JWT)
    B_SAS  -> SAS Viya CAS       (swat, Project Data Sphere clinical warehouse)
    C_EGA  -> EGA                (pyega3 / EGA REST, controlled-access genomics)
    D_ARGO -> ICGC ARGO          (Overture SONG/SCORE REST, DACO controlled genomics)

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


ENDPOINT_OF_SOURCE = {"synapse": "A_MSK", "sas_cas": "B_SAS", "ega": "C_EGA", "argo": "D_ARGO"}

# per-call bounds — keep live extraction cheap and safe
DEFAULT_ROW_CAP = 50
MAX_ROW_CAP = 500
CONNECT_TIMEOUT_S = 20


def _lib_present(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):  # pragma: no cover
        return False


def _json_dumps(obj: Any) -> str:
    """Serialize a request body to JSON (synapseclient.restPOST expects a str)."""
    import json as _json

    return _json.dumps(obj)


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

    def list_children(self, parent_id: str, limit: int | None = None) -> Envelope:
        """List immediate children of a container (Project/Folder/Dataset).

        This is the missing 'crawl' verb: an agent can walk parent -> children
        recursively to reach every file. Uses the authenticated REST children
        service, so it respects the account's READ access (a 403 on a controlled
        parent yields a typed 'unreachable', never fabricated rows).
        """
        action = "list_children"
        n = min(int(limit), 200) if limit and limit > 0 else 100
        guard = self._guard(action)
        if guard:
            guard.grounding = {"parent_id": parent_id, "limit": n}
            return guard
        env = self._envelope(action)
        env.grounding = {"parent_id": parent_id, "limit": n}
        t0 = time.time()
        try:
            syn = self._login()
            kids: list[dict[str, Any]] = []
            next_token = None
            types = ["folder", "file", "table", "entityview", "dataset", "link", "dockerrepo"]
            while len(kids) < n:
                body: dict[str, Any] = {"parentId": parent_id, "includeTypes": types,
                                        "sortBy": "NAME", "sortDirection": "ASC"}
                if next_token:
                    body["nextPageToken"] = next_token
                page = syn.restPOST("/entity/children", body=_json_dumps(body))
                for c in page.get("page", []):
                    kids.append({
                        "id": c.get("id"),
                        "name": c.get("name"),
                        "type": (c.get("type") or "").split(".")[-1],
                        "versionNumber": c.get("versionNumber"),
                        "benefactorId": c.get("benefactorId"),
                        "fileSizeBytes": c.get("fileSizeBytes"),
                        "md5": c.get("md5"),
                        "is_container": (c.get("type") or "").split(".")[-1] in ("Folder", "Project"),
                    })
                    if len(kids) >= n:
                        break
                next_token = page.get("nextPageToken")
                if not next_token:
                    break
            env.status = "live"
            env.data = {"parent_id": parent_id, "n_children": len(kids), "children": kids}
            env.grounding["n_children"] = len(kids)
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def download_diagnostics(self, syn_id: str) -> Envelope:
        """Go/no-go gate for byte download WITHOUT transferring the file.

        Resolves the entity + its fileHandle metadata (size, md5, contentType)
        and confirms a pre-signed URL can be minted. ``data.can_download`` is the
        boolean an agent checks before committing to a multi-GB stream. No bytes
        move here. A gated/no-access file yields ``can_download: False`` with a
        typed reason — never a false 'yes'.
        """
        action = "download_diagnostics"
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
            fh_id = ent.get("dataFileHandleId")
            ctype = (ent.get("concreteType") or "").split(".")[-1]
            if not fh_id:
                env.status = "live"
                env.data = {"syn_id": syn_id, "concreteType": ctype, "can_download": False,
                            "reason": f"entity has no dataFileHandleId (type={ctype}); not a file"}
                env.latency_ms = round((time.time() - t0) * 1000, 1)
                return env
            # Mint the pre-signed URL (proves access) but do NOT fetch bytes.
            url, size, md5, ct, err = self._filehandle_url(syn, syn_id, fh_id, ent)
            can = bool(url) and err is None
            env.status = "live"
            env.data = {
                "syn_id": syn_id,
                "name": ent.get("name"),
                "concreteType": ctype,
                "file_handle_id": fh_id,
                "size_bytes": size,
                "md5": md5,
                "content_type": ct,
                "can_download": can,
                "reason": None if can else (err or "could not mint pre-signed URL"),
            }
            env.grounding["can_download"] = can
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def _filehandle_url(self, syn, syn_id, fh_id, ent):
        """Return (presigned_url, size, md5, content_type, error).

        Uses the fileHandle batch service to mint a short-lived S3 URL for the
        entity's data file. Mirrors the ARGO token-handoff: the URL is returned
        so the caller streams DIRECT from object storage; bytes never proxy
        through this backend.
        """
        try:
            body = {
                "includeFileHandles": True,
                "includePreSignedURLs": True,
                "requestedFiles": [{"fileHandleId": fh_id, "associateObjectId": syn_id,
                                    "associateObjectType": "FileEntity"}],
            }
            res = syn.restPOST(
                "/fileHandle/batch",
                body=_json_dumps(body),
                endpoint=syn.fileHandleEndpoint,
            )
            results = res.get("requestedFiles", [])
            if not results:
                return None, None, None, None, "fileHandle/batch returned no results"
            r0 = results[0]
            url = r0.get("preSignedURL")
            fh = r0.get("fileHandle") or {}
            failure = r0.get("failureCode")
            if not url:
                return None, fh.get("contentSize"), fh.get("contentMd5"), \
                    fh.get("contentType"), f"no preSignedURL (failureCode={failure})"
            return url, fh.get("contentSize"), fh.get("contentMd5"), fh.get("contentType"), None
        except Exception as exc:  # noqa: BLE001
            return None, None, None, None, _reason(exc)

    def resolve_download(self, syn_id: str) -> Envelope:
        """Mint a short-lived pre-signed S3 URL for a Synapse file's bytes.

        TOKEN-HANDOFF (same contract as ARGO): returns ``data.url`` + expected
        ``md5``/``size``; the caller streams bytes DIRECTLY from object storage.
        We never proxy whole files through this backend. A file the account can't
        read yields a typed 'unreachable'/no-URL — never a fabricated link.
        """
        action = "resolve_download"
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
            fh_id = ent.get("dataFileHandleId")
            if not fh_id:
                ctype = (ent.get("concreteType") or "").split(".")[-1]
                env.status, env.error = "unreachable", f"not_a_file: entity {syn_id} has no dataFileHandleId (type={ctype})"
                env.latency_ms = round((time.time() - t0) * 1000, 1)
                return env
            url, size, md5, ct, err = self._filehandle_url(syn, syn_id, fh_id, ent)
            if not url:
                env.status, env.error = "unreachable", f"auth_or_gated: {err}"
                env.latency_ms = round((time.time() - t0) * 1000, 1)
                return env
            host = None
            try:
                host = url.split("/")[2]
            except Exception:  # noqa: BLE001
                host = None
            env.status = "live"
            env.data = {
                "syn_id": syn_id,
                "name": ent.get("name"),
                "file_handle_id": fh_id,
                "url": url,
                "object_host": host,
                "size_bytes": size,
                "md5": md5,
                "content_type": ct,
                "transport": "direct-from-object-storage; stream data.url directly, do not proxy through ZetaBridge; URL is short-lived",
            }
            env.grounding["object_host"] = host
            env.grounding["size_bytes"] = size
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
        """List files in an EGA dataset.

        When EGA credentials are configured, this prefers the AUTHORITATIVE
        auth'd endpoint (:8443/v2/metadata/datasets/{id}/files), which returns
        the real per-file records for a DAC-authorized dataset (679 files for
        EGAD00001011049, with fileId/fileSize/plainChecksum/...) — the exact set
        an agent can then byte-pull. If credentials are absent, or the account is
        not authorized for the dataset, it falls back to the PUBLIC metadata API
        (dataset/file *metadata* is public; file *content* stays controlled).

        Never fabricates: the ``access`` field records which surface answered.
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

        # Preferred path: authoritative auth'd file list (only when credentials
        # are present). Falls through to the public API on any failure/403.
        if self.configured():
            token, _tok_err = self._data_token()
            if token:
                recs, a_err = self._data_dataset_files(token, ds)
                if recs is not None:
                    slim = [
                        {
                            "accession_id": f.get("fileId"),
                            "filesize": f.get("fileSize"),
                            "display_file_name": f.get("displayFileName"),
                            "checksum": f.get("plainChecksum"),
                            "checksum_type": f.get("plainChecksumType"),
                            "file_status": f.get("fileStatus"),
                            "index_file_id": f.get("indexFileId"),
                        }
                        for f in recs[:n]
                        if isinstance(f, dict)
                    ]
                    env.status = "live"
                    env.data = {
                        "dataset": ds,
                        "access": "authorized",
                        "n_files": len(recs),
                        "files": slim,
                    }
                    env.grounding["n_files"] = len(recs)
                    env.grounding["access"] = "authorized"
                    env.latency_ms = round((time.time() - t0) * 1000, 1)
                    return env
                # a_err (e.g. 403 for an unauthorized dataset) -> fall back to
                # public metadata so the caller still sees what exists publicly.

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
            env.data = {
                "dataset": ds,
                "access": "public",
                "n_files": len(slim) if isinstance(slim, list) else None,
                "files": slim,
            }
            env.grounding["n_files"] = len(slim) if isinstance(slim, list) else None
            env.grounding["access"] = "public"
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
    # Reachable OIDC + private-metadata surface (port 443). Used to confirm the
    # DAC grant WITHOUT the (often-blocked) Data API port 8052.
    IDP_TOKEN_URL = "https://idp.ega-archive.org/realms/EGA/protocol/openid-connect/token"

    def _metadata_token(self) -> tuple[Optional[str], Optional[str]]:
        """OIDC password-grant token scoped for the private metadata API."""
        import httpx

        creds = {"username": cfg.EGA_USERNAME, "password": cfg.EGA_PASSWORD}
        if cfg.EGA_CREDENTIALS_FILE:
            import json as _json

            with open(cfg.EGA_CREDENTIALS_FILE) as fh:
                creds = _json.load(fh)
        try:
            r = httpx.post(
                self.IDP_TOKEN_URL,
                data={
                    "client_id": "metadata-api",
                    "grant_type": "password",
                    "username": creds["username"],
                    "password": creds["password"],
                },
                timeout=CONNECT_TIMEOUT_S,
            )
            r.raise_for_status()
            return r.json().get("access_token"), None
        except Exception as exc:  # noqa: BLE001
            return None, _reason(exc)

    def _port_open(self, host: str, port: int, timeout: float = 8.0) -> tuple[bool, str]:
        import socket

        t0 = time.time()
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True, f"reachable in {round((time.time()-t0)*1000)}ms"
        except Exception as exc:  # noqa: BLE001
            return False, _reason(exc)

    # --- EGA Data API byte transfer -----------------------------------------
    # The modern Data API serves file BYTES over the *reachable* AAI port (8443)
    # at ``/v2/files/{id}?destinationFormat=plain`` with HTTP Range support —
    # NOT the legacy 8052 transfer port (which is firewalled from many hosts,
    # Render included). Verified empirically: 206 + application/octet-stream +
    # BGZF magic. Token comes from the openid-connect-server on 8443 using the
    # pyega3 public client_id/secret (a metadata-api-scoped token is the wrong
    # audience for this route and returns 401).
    DATA_API_BASE = "https://ega.ebi.ac.uk:8443"
    DATA_TOKEN_URL = "https://ega.ebi.ac.uk:8443/ega-openid-connect-server/token"
    # AUTHORITATIVE entitlement + private-metadata surface, served over the same
    # reachable AAI port (8443) with the AAI (Data-API) token. Verified live
    # (no cache): GET /v2/metadata/datasets returns EXACTLY the account's
    # DAC-granted datasets (e.g. only EGAD00001011049 for this login), whereas
    # the PUBLIC metadata.ega-archive.org/datasets?authorized=true returns the
    # entire ~21k-dataset catalog and is therefore useless for entitlement.
    # GET /v2/metadata/files/{id} returns 200 for an authorized file and a clean
    # 403 for one the account cannot access — the real go/no-go signal.
    AUTHZ_DATASETS_URL = "https://ega.ebi.ac.uk:8443/v2/metadata/datasets"
    # Public pyega3 OAuth client (not a secret in any meaningful sense — shipped
    # inside the open-source EGA download client's default_server_file.json).
    DATA_CLIENT_ID = "f20cd2d3-682a-4568-a53e-4262ef54c8f4"
    DATA_CLIENT_SECRET = (
        "AMenuDLjVdVo4BSwi0QD54LL6NeVDEZRzEQUJ7hJOM3g4imDZBHHX0hNfKHPeQIGkskhtCmqAJtt_jm7EKq-rWw"
    )

    def _egadata_credentials(self) -> tuple[Optional[str], Optional[str]]:
        """Resolve (username, password) from cfg or the EGA credentials file.

        Whitespace is stripped from both fields: pasted EGA credentials commonly
        carry a leading newline / trailing space on the username, and EGA's token
        endpoint rejects the padded form with a 401 that *looks* like a bad
        password. Trimming here prevents that false auth failure.
        """
        username, password = cfg.EGA_USERNAME, cfg.EGA_PASSWORD
        if cfg.EGA_CREDENTIALS_FILE:
            import json as _json

            with open(cfg.EGA_CREDENTIALS_FILE) as fh:
                creds = _json.load(fh)
            username = creds.get("username", username)
            password = creds.get("password", password)
        username = username.strip() if isinstance(username, str) else username
        password = password.strip() if isinstance(password, str) else password
        return username, password

    def _data_token(self) -> tuple[Optional[str], Optional[str]]:
        """Password-grant token for the Data API byte route (openid-connect-server
        on port 8443, with the pyega3 client_id + client_secret)."""
        import httpx

        username, password = self._egadata_credentials()
        if not (username and password):
            return None, "auth: EGA_USERNAME/EGA_PASSWORD not configured"
        try:
            r = httpx.post(
                self.DATA_TOKEN_URL,
                data={
                    "grant_type": "password",
                    "client_id": self.DATA_CLIENT_ID,
                    "client_secret": self.DATA_CLIENT_SECRET,
                    "scope": "openid",
                    "username": username,
                    "password": password,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=CONNECT_TIMEOUT_S,
            )
            r.raise_for_status()
            tok = r.json().get("access_token")
            if not tok:
                return None, "auth: token endpoint returned no access_token"
            return tok, None
        except Exception as exc:  # noqa: BLE001
            return None, _reason(exc)

    def _authz_datasets(self, token: str) -> tuple[Optional[list[dict]], Optional[str]]:
        """Fetch the account's DAC-granted datasets from the AUTHORITATIVE
        auth'd endpoint (:8443/v2/metadata/datasets).

        Returns (records, error). Each record has keys datasetId, description,
        dacStableId. A server-side 500 here is a known intermittent EGA outage
        (ega-download-client #274), NOT a credential/code fault — it is surfaced
        as a typed ``network:`` reason, never silently swallowed.
        """
        import httpx

        try:
            r = httpx.get(
                self.AUTHZ_DATASETS_URL,
                headers={"Authorization": f"Bearer {token}"},
                timeout=CONNECT_TIMEOUT_S,
            )
            if r.status_code >= 500:
                return None, (
                    f"network: EGA metadata API returned HTTP {r.status_code} "
                    "(known intermittent server-side outage, not a credential fault)"
                )
            r.raise_for_status()
            body = r.json()
            recs = body if isinstance(body, list) else body.get("datasets", body)
            return (recs if isinstance(recs, list) else []), None
        except Exception as exc:  # noqa: BLE001
            return None, _reason(exc)

    def _data_file_metadata(self, token: str, file_id: str) -> tuple[int, Optional[dict], Optional[str]]:
        """Per-file entitlement probe against the auth'd metadata endpoint
        (:8443/v2/metadata/files/{id}).

        Returns (status_code, json_or_None, error_or_None):
          * 200 -> authorized; json carries fileId/fileSize/displayFileName/...
          * 403 -> NOT authorized for this file (genuine DAC boundary)
          * 404 -> no such file
          * 5xx -> intermittent EGA outage (typed, not a credential fault)
        """
        import httpx

        try:
            r = httpx.get(
                f"{self.DATA_API_BASE}/v2/metadata/files/{file_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=CONNECT_TIMEOUT_S,
            )
            if r.status_code == 200:
                try:
                    return 200, r.json(), None
                except Exception:  # noqa: BLE001
                    return 200, None, None
            if r.status_code == 403:
                return 403, None, f"auth: not authorized for file '{file_id}' (DAC grant required)"
            if r.status_code == 404:
                return 404, None, f"not_found: no such EGA file '{file_id}'"
            if r.status_code >= 500:
                return r.status_code, None, (
                    f"network: EGA metadata API HTTP {r.status_code} "
                    "(intermittent server-side outage, not a credential fault)"
                )
            return r.status_code, None, f"http: unexpected HTTP {r.status_code}"
        except Exception as exc:  # noqa: BLE001
            return -1, None, _reason(exc)

    def _data_dataset_files(self, token: str, dataset: str) -> tuple[Optional[list[dict]], Optional[str]]:
        """List files in an AUTHORIZED dataset via the auth'd endpoint
        (:8443/v2/metadata/datasets/{id}/files). 679 files for EGAD00001011049.
        Returns (records, error); 403 -> not authorized for the dataset."""
        import httpx

        try:
            r = httpx.get(
                f"{self.DATA_API_BASE}/v2/metadata/datasets/{dataset}/files",
                headers={"Authorization": f"Bearer {token}"},
                timeout=CONNECT_TIMEOUT_S,
            )
            if r.status_code == 403:
                return None, f"auth: not authorized for dataset '{dataset}' (DAC grant required)"
            if r.status_code >= 500:
                return None, (
                    f"network: EGA metadata API HTTP {r.status_code} "
                    "(intermittent server-side outage, not a credential fault)"
                )
            r.raise_for_status()
            body = r.json()
            recs = body if isinstance(body, list) else body.get("files", body)
            return (recs if isinstance(recs, list) else []), None
        except Exception as exc:  # noqa: BLE001
            return None, _reason(exc)

    def authorized_datasets(self) -> Envelope:
        """AUTHORITATIVE entitlement report: exactly which datasets THIS account
        can access, from the auth'd :8443/v2/metadata/datasets endpoint.

        This is the anti-sandbagging verb. An agent calls it FIRST to learn what
        it may crawl, instead of dead-ending on a 403 or being misled by the
        public catalog (which lists ~21k datasets regardless of access). Fetches
        NO bytes. ``data.authorized_datasets`` is the ground truth.
        """
        action = "authorized_datasets"
        env = self._envelope(action)
        t0 = time.time()
        if not self.configured():
            env.status, env.error = "unconfigured", "EGA_USERNAME/EGA_PASSWORD (or EGA_CREDENTIALS_FILE) not set"
            env.latency_ms = round((time.time() - t0) * 1000, 1)
            return env
        if not _lib_present("httpx"):
            env.status, env.error = "unconfigured", "httpx not installed"
            env.latency_ms = round((time.time() - t0) * 1000, 1)
            return env

        token, tok_err = self._data_token()
        if not token:
            env.status, env.error = "unreachable", tok_err or "auth: token request failed"
            env.latency_ms = round((time.time() - t0) * 1000, 1)
            return env

        recs, err = self._authz_datasets(token)
        if recs is None:
            env.status, env.error = "unreachable", err or "authorized-datasets query failed"
            env.latency_ms = round((time.time() - t0) * 1000, 1)
            return env

        slim = [
            {
                "dataset_id": d.get("datasetId") or d.get("egaStableId") or d.get("accessionId"),
                "description": d.get("description"),
                "dac_stable_id": d.get("dacStableId"),
            }
            for d in recs
            if isinstance(d, dict)
        ]
        env.status = "live"
        env.data = {
            "n_authorized": len(slim),
            "authorized_datasets": slim,
            "source_endpoint": self.AUTHZ_DATASETS_URL,
            "note": (
                "Authoritative DAC grants for this account. An empty list means "
                "the login is valid but has no dataset access. This is NOT the "
                "public catalog."
            ),
        }
        env.grounding = {"n_authorized": len(slim), "dataset_ids": [s["dataset_id"] for s in slim]}
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def file_access_probe(self, file_id: str) -> Envelope:
        """Per-file go/no-go WITHOUT transferring bytes: auth'd metadata probe
        (200=authorized, 403=DAC boundary) plus, only when authorized, a tiny
        HTTP Range HEAD-equivalent (bytes=0-0) against the byte route to confirm
        the transport actually yields 206 octet-stream.

        Honesty contract: ``data.can_download`` is True ONLY when the metadata
        probe is 200 AND the 1-byte range probe returns 206 with octet-stream.
        A 403 anywhere yields status=unreachable, data=null, typed auth reason —
        never a fabricated 'ok'.
        """
        action = "file_access_probe"
        env = self._envelope(action)
        env.grounding = {"file_id": file_id}
        t0 = time.time()
        if not self.configured():
            env.status, env.error = "unconfigured", "EGA_USERNAME/EGA_PASSWORD (or EGA_CREDENTIALS_FILE) not set"
            env.latency_ms = round((time.time() - t0) * 1000, 1)
            return env
        if not _lib_present("httpx"):
            env.status, env.error = "unconfigured", "httpx not installed"
            env.latency_ms = round((time.time() - t0) * 1000, 1)
            return env
        import httpx

        token, tok_err = self._data_token()
        if not token:
            env.status, env.error = "unreachable", tok_err or "auth: token request failed"
            env.latency_ms = round((time.time() - t0) * 1000, 1)
            return env

        meta_status, meta_json, meta_err = self._data_file_metadata(token, file_id)
        result: dict[str, Any] = {"metadata_status": meta_status}
        if meta_status != 200:
            env.status = "unreachable"
            env.data = None
            env.error = meta_err or f"auth: file metadata HTTP {meta_status}"
            env.grounding["metadata_status"] = meta_status
            env.latency_ms = round((time.time() - t0) * 1000, 1)
            return env

        if isinstance(meta_json, dict):
            result["display_file_name"] = meta_json.get("displayFileName")
            result["file_size"] = meta_json.get("fileSize")
            result["dataset_id"] = meta_json.get("datasetId")
            result["plain_checksum"] = meta_json.get("plainChecksum")

        # Confirm transport actually yields bytes: 1-byte bounded range probe.
        byte_status = None
        byte_ct = None
        byte_ok = False
        try:
            url = f"{self.DATA_API_BASE}/v2/files/{file_id}?destinationFormat=plain"
            rr = httpx.get(
                url,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/octet-stream",
                         "Range": "bytes=0-0"},
                timeout=CONNECT_TIMEOUT_S,
            )
            byte_status = rr.status_code
            byte_ct = rr.headers.get("content-type", "")
            byte_ok = rr.status_code in (200, 206) and "octet-stream" in byte_ct.lower()
        except Exception as exc:  # noqa: BLE001
            result["byte_probe_error"] = _reason(exc)

        result["byte_probe_status"] = byte_status
        result["byte_probe_content_type"] = byte_ct
        result["can_download"] = bool(meta_status == 200 and byte_ok)
        env.data = result
        env.grounding["can_download"] = result["can_download"]
        env.status = "live" if result["can_download"] else "unreachable"
        if not result["can_download"]:
            env.error = "byte transport did not yield 206 octet-stream despite authorized metadata"
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    # Bounded slice size for internal fetches. EGA's re-encryption service only
    # returns correctly-offset plaintext for *bounded* ranges; open-ended
    # (``bytes=0-``) or whole-file-in-one-request responses are shifted or carry
    # a 16-byte IV prefix. pyega3 uses 100 MiB slices — we mirror that.
    SLICE_SIZE = 100 * 1024 * 1024

    def _plaintext_size(self, file_id: str) -> tuple[Optional[int], Optional[str]]:
        """True unencrypted (plaintext) byte length for a file.

        EGA metadata ``filesize`` includes the 16-byte Crypt4GH IV; the Data API
        serves ``filesize - 16`` bytes of plaintext (confirmed: an explicit small
        Range reports ``/<plaintext_size>`` in Content-Range). We read the
        metadata filesize and subtract the IV.
        """
        import httpx

        try:
            r = httpx.get(f"{self.META_BASE}/files/{file_id}", timeout=CONNECT_TIMEOUT_S)
            if r.status_code == 404:
                return None, f"not_found: no such EGA file '{file_id}'"
            r.raise_for_status()
            try:
                j = r.json()
            except Exception:  # noqa: BLE001 - non-JSON body (e.g. empty/HTML)
                return None, f"not_found: metadata for '{file_id}' returned no JSON body"
            if isinstance(j, list):
                j = j[0] if j else {}
            if not isinstance(j, dict):
                return None, f"not_found: unexpected metadata shape for '{file_id}'"
            size = j.get("filesize") or j.get("size")
            if size is None:
                return None, f"not_found: no filesize in metadata for '{file_id}'"
            plain = int(size) - 16  # 16-byte IV not present in plain mode
            return (plain if plain > 0 else int(size)), None
        except Exception as exc:  # noqa: BLE001
            return None, _reason(exc)

    @staticmethod
    def _parse_range(range_header: Optional[str], total: int) -> tuple[int, int]:
        """Parse a single ``bytes=start-end`` header against a known total.

        Returns an inclusive (start, end) clamped to [0, total-1]. Absent/open
        ranges default to the whole file. We serve the whole file via internal
        bounded slices, so an absent or open-ended client Range is fine here.
        """
        start, end = 0, total - 1
        if range_header:
            rh = range_header.strip().lower().replace("bytes=", "")
            part = rh.split(",")[0].strip()
            if "-" in part:
                a, _, b = part.partition("-")
                if a.strip():
                    start = int(a)
                if b.strip():
                    end = int(b)
        start = max(0, start)
        end = min(end, total - 1)
        if end < start:
            end = total - 1
        return start, end

    def open_byte_stream(self, file_id: str, range_header: Optional[str] = None):
        """Open a server-side byte stream for an EGA file over the Data API.

        Returns a tuple ``(ok, meta, byte_iter, closer, error)``:
          * ``ok``        — True when the plaintext size + a first bounded slice
                            were obtained (bytes will flow).
          * ``meta``      — status_code (206 for a partial range else 200),
                            content_length (bytes to be served), content_range,
                            accept_ranges.
          * ``byte_iter`` — a generator yielding the requested plaintext byte
                            range as bounded slices concatenated in order.
          * ``closer``    — a no-arg callable to release resources.
          * ``error``     — typed reason string on failure (else None).

        Why bounded slices: EGA's re-encryption service returns correctly-offset
        plaintext only for *bounded* ``bytes=start-end`` ranges. An open-ended
        ``bytes=0-`` (or no Range) response prepends the 16-byte Crypt4GH IV, and
        a single whole-file range collapses to a shifted 200. So we always fetch
        in bounded ``SLICE_SIZE`` chunks — exactly the strategy pyega3 uses — and
        stream them out as one contiguous octet-stream. This guarantees the
        caller receives valid BAM bytes regardless of the Range they send.

        Honesty contract: a slice that returns non-2xx or a non-octet-stream body
        raises inside the generator (surfaced as a broken stream) rather than
        yielding an HTML error page as if it were BAM.
        """
        import httpx

        if not self.configured():
            return False, {}, None, None, "unconfigured: EGA credentials not set"

        token, tok_err = self._data_token()
        if not token:
            return False, {}, None, None, tok_err or "auth: token request failed"

        total, size_err = self._plaintext_size(file_id)
        if total is None:
            return False, {}, None, None, size_err or "metadata: could not resolve file size"

        # Reject an unsatisfiable range (start past EOF) with a 416 signal rather
        # than a confusing upstream failure.
        if range_header:
            rh = range_header.strip().lower().replace("bytes=", "").split(",")[0].strip()
            a = rh.partition("-")[0].strip()
            if a and int(a) >= total:
                return False, {"total_size": total}, None, None, (
                    f"range_not_satisfiable: start {a} >= file size {total}"
                )

        start, end = self._parse_range(range_header, total)
        want = end - start + 1
        is_partial = bool(range_header) and (start != 0 or end != total - 1)

        url = f"{self.DATA_API_BASE}/v2/files/{file_id}?destinationFormat=plain"
        base_headers = {"Authorization": f"Bearer {token}", "Accept": "application/octet-stream"}

        client = httpx.Client(timeout=httpx.Timeout(CONNECT_TIMEOUT_S, read=None))

        def _closer() -> None:
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass

        # Prime the first slice so we can fail fast (auth/404/etc.) BEFORE the
        # router commits to a 200/206 streaming response.
        def _open_slice(s: int, e: int):
            h = dict(base_headers)
            h["Range"] = f"bytes={s}-{e}"
            req = client.build_request("GET", url, headers=h)
            resp = client.send(req, stream=True, follow_redirects=True)
            return resp

        first_end = min(start + self.SLICE_SIZE - 1, end)
        try:
            first_resp = _open_slice(start, first_end)
        except Exception as exc:  # noqa: BLE001
            _closer()
            return False, {}, None, None, _reason(exc)

        ct = first_resp.headers.get("content-type", "")
        status = first_resp.status_code
        if status not in (200, 206):
            body = b""
            try:
                for chunk in first_resp.iter_bytes():
                    body += chunk
                    if len(body) > 2048:
                        break
            except Exception:  # noqa: BLE001
                pass
            first_resp.close()
            _closer()
            snippet = body[:200].decode("utf-8", "replace").replace("\n", " ").strip()
            reason = "auth" if status in (401, 403) else ("network" if status >= 500 else "http")
            return False, {}, None, None, f"{reason}: upstream HTTP {status}: {snippet}"
        if "application/octet-stream" not in ct.lower():
            first_resp.close()
            _closer()
            return (
                False, {}, None, None,
                f"unexpected_content_type: upstream returned '{ct}' not octet-stream "
                "(likely an auth/redirect page, not file bytes)",
            )

        def _byte_iter():
            try:
                # slice 1 (already open)
                for chunk in first_resp.iter_bytes(chunk_size=1024 * 1024):
                    yield chunk
                first_resp.close()
                # remaining slices
                pos = first_end + 1
                while pos <= end:
                    s_end = min(pos + self.SLICE_SIZE - 1, end)
                    resp = _open_slice(pos, s_end)
                    try:
                        if resp.status_code not in (200, 206):
                            raise RuntimeError(f"slice {pos}-{s_end} HTTP {resp.status_code}")
                        for chunk in resp.iter_bytes(chunk_size=1024 * 1024):
                            yield chunk
                    finally:
                        resp.close()
                    pos = s_end + 1
            finally:
                _closer()

        meta = {
            "status_code": 206 if is_partial else 200,
            "content_type": "application/octet-stream",
            "content_length": want,
            "content_range": f"bytes {start}-{end}/{total}" if is_partial else None,
            "accept_ranges": "bytes",
            "total_size": total,
        }
        return True, meta, _byte_iter(), _closer, None

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
        legacy_ok, legacy_msg = self._port_open(self.DATA_HOST, self.DATA_PORT)
        result["egress"] = {
            # The Data API serves BYTES over the AAI port (8443) via
            # /v2/files/{id}. This is the real transport gate.
            f"{self.AAI_HOST}:{self.AAI_PORT}": {"open": aai_ok, "detail": aai_msg},
            # Legacy 8052 transfer port — informational only; not required by the
            # modern /v2/files byte route and often firewalled.
            f"{self.DATA_HOST}:{self.DATA_PORT} (legacy, not required)": {
                "open": legacy_ok,
                "detail": legacy_msg,
            },
        }
        # Byte transport is possible when the 8443 data host is reachable.
        data_ok = aai_ok

        # Auth + DAC-grant check via the AUTHORITATIVE auth'd endpoint
        # (:8443/v2/metadata/datasets), served over the SAME reachable AAI port
        # (8443) with the Data-API (AAI) token. This returns EXACTLY the account's
        # DAC grants. We deliberately do NOT use metadata.ega-archive.org/datasets
        # ?authorized=true: that endpoint returns the entire ~21k public catalog
        # regardless of access, which would produce a FALSE-POSITIVE entitlement
        # (a coincidental "authorized" for any queried dataset). Verified live.
        auth_ok = False
        n_authorized = 0
        dataset_authorized = None
        auth_error = None
        file_probe_status = None
        if not _lib_present("httpx"):
            auth_error = "httpx not installed"
        else:
            token, tok_err = self._data_token()
            if not token:
                auth_error = tok_err or "token request failed"
            else:
                auth_ok = True
                recs, ds_err = self._authz_datasets(token)
                if recs is None:
                    auth_error = ds_err or "authorized-datasets query failed"
                else:
                    ids = [
                        (d.get("datasetId") or d.get("egaStableId") or d.get("accessionId"))
                        for d in recs
                        if isinstance(d, dict)
                    ]
                    ids = [i for i in ids if i]
                    n_authorized = len(ids)
                    dataset_authorized = ds in ids
                    result["authorized_datasets"] = ids[:25]
                    # Per-file 403-vs-200 confirmation on the smallest authorized
                    # file gives an agent an unambiguous, byte-free go/no-go for
                    # the requested dataset (not just a dataset-list membership).
                    if dataset_authorized:
                        files, f_err = self._data_dataset_files(token, ds)
                        if files:
                            sized = sorted(
                                (int(f.get("fileSize") or 0), f.get("fileId"))
                                for f in files if f.get("fileId")
                            )
                            sized = [s for s in sized if s[0] > 0]
                            if sized:
                                probe_fid = sized[0][1]
                                st, _mj, _me = self._data_file_metadata(token, probe_fid)
                                file_probe_status = st
                                result["file_probe"] = {
                                    "file_id": probe_fid,
                                    "metadata_status": st,
                                    "authorized": st == 200,
                                }
                                result["n_files_in_dataset"] = len(files)

        result["auth_ok"] = auth_ok
        result["n_authorized_datasets"] = n_authorized
        result["dataset_authorized"] = dataset_authorized
        if auth_error:
            result["auth_error"] = auth_error

        # Entitlement verdict (independent of transport): the account CAN be
        # authorized for the dataset even when the byte-transfer port is blocked.
        result["entitled"] = bool(auth_ok and dataset_authorized)
        # Byte download is possible IFF entitled AND the Data API port is open
        # from this host.
        can_download = bool(data_ok and auth_ok and dataset_authorized)
        result["can_download"] = can_download
        env.data = result
        if can_download:
            env.status = "live"
        else:
            env.status = "unreachable"
            reasons = []
            if not auth_ok:
                reasons.append("auth_failed")
            elif dataset_authorized is False:
                reasons.append(f"no_dac_grant_for:{ds}")
            if not data_ok:
                # Distinguish "entitled but transport blocked" from "not entitled".
                # Transport now rides the 8443 Data API host (/v2/files).
                if result["entitled"]:
                    reasons.append(
                        f"entitled_but_data_host_unreachable({self.AAI_HOST}:{self.AAI_PORT}): "
                        "bytes must be pulled from a host with open egress to this port"
                    )
                else:
                    reasons.append(f"data_host_unreachable({self.AAI_HOST}:{self.AAI_PORT})")
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
# ICGC ARGO (D_ARGO) — Overture SONG/SCORE; DACO controlled-access genomics
# ---------------------------------------------------------------------------
class ArgoClient:
    """Read-only connector to the ICGC ARGO data platform (Overture stack).

    Transport contract (verified by probe, Session 16):
      * Metadata + object registry live at ``{API_BASE}/storage-api`` (SONG/SCORE).
      * ``resolve_download`` mints a short-lived **pre-signed URL** to object
        storage (``object.genomeinformatics.org``). Bytes are streamed DIRECTLY
        from object storage by the caller/worker — they are *never* proxied
        through this backend (that would reintroduce the dyno bandwidth
        bottleneck). This method returns the URL spec only, not bytes.
      * Controlled data requires a DACO-approved token (``ICGC_ARGO_TOKEN``).
        Without it, download resolution returns 401 -> typed ``unreachable:auth``.

    Honesty contract is identical to the other clients: ``data`` is ``None`` on
    every non-live path; the token never appears in an envelope.
    """

    source = "argo"
    endpoint = "D_ARGO"

    def __init__(self) -> None:
        self._api_base = (getattr(cfg, "ICGC_ARGO_API_BASE", "") or "https://api.platform.icgc-argo.org").rstrip("/")
        self._object_host = getattr(cfg, "ICGC_ARGO_OBJECT_HOST", "") or "object.genomeinformatics.org"

    def configured(self) -> bool:
        return bool(getattr(cfg, "ICGC_ARGO_TOKEN", ""))

    def _envelope(self, action: str) -> Envelope:
        return Envelope(endpoint=self.endpoint, source=self.source, status="", action=action)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {cfg.ICGC_ARGO_TOKEN}"}

    def _guard(self, action: str, *, need_token: bool) -> Optional[Envelope]:
        """Return an unconfigured envelope if we can't even attempt the call.

        ``need_token``: some registry reads are public; controlled-data download
        resolution requires the DACO token. Missing httpx is always unconfigured.
        """
        if not _lib_present("httpx"):
            env = self._envelope(action)
            env.status, env.error = "unconfigured", "httpx not installed"
            return env
        if need_token and not self.configured():
            env = self._envelope(action)
            env.status, env.error = "unconfigured", "ICGC_ARGO_TOKEN not set"
            return env
        return None

    @staticmethod
    def _parse_filename(file_name: str) -> dict[str, Any]:
        """Derive donor/sample/experiment/workflow from the ARGO filename convention.

        Convention observed in the registry:
            {PROJECT}.{DONOR:DO*}.{SAMPLE:SA*}.{experiment}.{date}.{workflow}.{...}.{ext}
        e.g. ``PTC-SA.DO233995.SA607369.wxs.20210127.aln.cram``
        Returns only the parts it can confidently identify (no fabrication).
        """
        parts = file_name.split(".")
        out: dict[str, Any] = {}
        if parts:
            out["project_code"] = parts[0]
        donor = next((p for p in parts if p.startswith("DO") and p[2:3].isdigit()), None)
        sample = next((p for p in parts if p.startswith("SA") and p[2:3].isdigit()), None)
        if donor:
            out["donor_id"] = donor
        if sample:
            out["sample_id"] = sample
        experiment = next((p for p in parts if p.lower() in {"wgs", "wxs", "rna-seq", "rna_seq"}), None)
        if experiment:
            out["experiment"] = experiment.lower()
        date = next((p for p in parts if len(p) == 8 and p.isdigit()), None)
        if date:
            out["date"] = date
        out["extension"] = parts[-1] if len(parts) > 1 else None
        return out

    def _slim_entity(self, e: dict[str, Any]) -> dict[str, Any]:
        """Real registry fields + derived relationships. No fabricated values."""
        fn = e.get("fileName", "") or ""
        slim = {
            "object_id": e.get("id"),
            "file_name": fn,
            "gnos_id": e.get("gnosId"),
            "project_code": e.get("projectCode"),
            "access": e.get("access"),
        }
        slim.update({k: v for k, v in self._parse_filename(fn).items() if k not in slim or slim.get(k) is None})
        return slim

    def list_entities(
        self,
        project: str | None = None,
        access: str | None = None,
        file_type: str | None = None,
        size: int | None = None,
    ) -> Envelope:
        """List/search the SCORE object registry (``/storage-api/entities``).

        Registry listing works without the token; controlled *content* still
        requires the token at download-resolution time. ``file_type`` filters
        client-side on the filename extension (e.g. ``bam``, ``cram``, ``vcf``).
        """
        action = "list_entities"
        n = _clamp_limit(size)
        env = self._envelope(action)
        env.grounding = {"project": project, "access": access, "file_type": file_type, "size": n}
        guard = self._guard(action, need_token=False)
        if guard:
            guard.grounding = env.grounding
            return guard
        import httpx

        filtering = bool(project or access or file_type)
        page_size = 500 if filtering else n  # registry pages; big pages when filtering
        max_pages = 10 if filtering else 1   # bounded scan so a rare type still fills n
        ft = file_type.lower().lstrip(".") if file_type else None
        t0 = time.time()
        try:
            headers = self._headers() if self.configured() else {}
            rows: list[dict[str, Any]] = []
            total = None
            with httpx.Client(timeout=CONNECT_TIMEOUT_S, follow_redirects=True) as client:
                for page in range(max_pages):
                    r = client.get(
                        f"{self._api_base}/storage-api/entities",
                        params={"size": page_size, "page": page},
                        headers=headers,
                    )
                    r.raise_for_status()
                    payload = r.json()
                    if total is None and isinstance(payload, dict):
                        total = payload.get("totalElements")
                    content = payload.get("content", []) if isinstance(payload, dict) else payload
                    if not content:
                        break
                    for e in content:
                        x = self._slim_entity(e)
                        if project and (x.get("project_code") or "").upper() != project.upper():
                            continue
                        if access and (x.get("access") or "").lower() != access.lower():
                            continue
                        if ft and (x.get("extension") or "").lower() != ft:
                            continue
                        rows.append(x)
                        if len(rows) >= n:
                            break
                    if len(rows) >= n or len(content) < page_size:
                        break
            rows = rows[:n]
            env.status = "live"
            env.data = {
                "total_in_registry": total,
                "n_returned": len(rows),
                "pages_scanned": page + 1,
                "entities": rows,
            }
            env.grounding["n_returned"] = len(rows)
        except Exception as exc:  # noqa: BLE001 - typed, honest failure
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def entity_metadata(self, object_id: str) -> Envelope:
        """Fetch one object's registry metadata + derived relationships."""
        action = "entity_metadata"
        env = self._envelope(action)
        env.grounding = {"object_id": object_id}
        guard = self._guard(action, need_token=False)
        if guard:
            guard.grounding = env.grounding
            return guard
        import httpx

        t0 = time.time()
        try:
            headers = self._headers() if self.configured() else {}
            r = httpx.get(
                f"{self._api_base}/storage-api/entities/{object_id}",
                headers=headers,
                timeout=CONNECT_TIMEOUT_S,
                follow_redirects=True,
            )
            if r.status_code == 404:
                env.status, env.error = "unreachable", f"not_found: object {object_id}"
                env.latency_ms = round((time.time() - t0) * 1000, 1)
                return env
            r.raise_for_status()
            env.status = "live"
            env.data = self._slim_entity(r.json())
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def resolve_download(self, object_id: str, offset: int = 0, length: int = -1) -> Envelope:
        """Mint a short-lived pre-signed SCORE download URL (controlled data).

        Returns the SCORE spec (objectId, objectMd5, parts[].url) — the caller
        then streams bytes DIRECTLY from object storage. This method never
        transfers bytes through the backend. Requires the DACO token; a
        non-entitled/invalid token yields ``unreachable:auth`` (upstream 401).
        """
        action = "resolve_download"
        env = self._envelope(action)
        env.grounding = {"object_id": object_id, "offset": offset, "length": length}
        guard = self._guard(action, need_token=True)
        if guard:
            guard.grounding = env.grounding
            return guard
        import httpx

        t0 = time.time()
        try:
            r = httpx.get(
                f"{self._api_base}/storage-api/download/{object_id}",
                params={"offset": offset, "length": length, "external": "true"},
                headers=self._headers(),
                timeout=CONNECT_TIMEOUT_S,
                follow_redirects=True,
            )
            if r.status_code == 401:
                env.status, env.error = "unreachable", "auth: 401 invalid or expired ICGC_ARGO_TOKEN"
                env.latency_ms = round((time.time() - t0) * 1000, 1)
                return env
            if r.status_code == 403:
                env.status, env.error = "unreachable", "forbidden: token lacks DACO controlled-data access"
                env.latency_ms = round((time.time() - t0) * 1000, 1)
                return env
            if r.status_code == 404:
                env.status, env.error = "unreachable", f"not_found: object {object_id}"
                env.latency_ms = round((time.time() - t0) * 1000, 1)
                return env
            r.raise_for_status()
            spec = r.json()
            parts = spec.get("parts", []) or []
            # Expose the pre-signed URL(s) + host so the worker streams direct-from-S3.
            slim_parts = [
                {
                    "partNumber": p.get("partNumber"),
                    "offset": p.get("offset"),
                    "partSize": p.get("partSize"),
                    "url": p.get("url"),
                }
                for p in parts
            ]
            host = None
            if slim_parts and slim_parts[0].get("url"):
                try:
                    host = slim_parts[0]["url"].split("/")[2]
                except Exception:  # noqa: BLE001
                    host = None
            env.status = "live"
            env.data = {
                "object_id": spec.get("objectId"),
                "object_md5": spec.get("objectMd5"),
                "object_size": spec.get("objectSize"),
                "object_host": host or self._object_host,
                "n_parts": len(slim_parts),
                "parts": slim_parts,
                "transport": "direct-from-object-storage; stream parts[].url directly, do not proxy through ZetaBridge",
            }
            env.grounding["object_host"] = host or self._object_host
            env.grounding["n_parts"] = len(slim_parts)
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env

    def storage_alive(self) -> Envelope:
        """Cheap SCORE liveness probe (``/storage-api/download/ping``); no data."""
        action = "storage_alive"
        env = self._envelope(action)
        guard = self._guard(action, need_token=False)
        if guard:
            return guard
        import httpx

        t0 = time.time()
        try:
            r = httpx.get(
                f"{self._api_base}/storage-api/download/ping",
                timeout=CONNECT_TIMEOUT_S,
                follow_redirects=True,
            )
            r.raise_for_status()
            env.status = "live"
            env.data = {"ping": "ok"}
        except Exception as exc:  # noqa: BLE001
            env.status, env.error = "unreachable", _reason(exc)
        env.latency_ms = round((time.time() - t0) * 1000, 1)
        return env


# ---------------------------------------------------------------------------
# Gateway facade
# ---------------------------------------------------------------------------
class SourceGateway:
    """Single entry point the REST router and MCP server both call."""

    def __init__(self) -> None:
        self.synapse = SynapseClient()
        self.sas = SasCasClient()
        self.ega = EgaClient()
        self.argo = ArgoClient()

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
        argo = self.argo.storage_alive()

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
                _slim(argo, self.argo.configured()),
            ],
            "any_live": any(e.status == "live" for e in (syn, sas, ega, argo)),
        }
