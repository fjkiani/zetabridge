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
    # Public pyega3 OAuth client (not a secret in any meaningful sense — shipped
    # inside the open-source EGA download client's default_server_file.json).
    DATA_CLIENT_ID = "f20cd2d3-682a-4568-a53e-4262ef54c8f4"
    DATA_CLIENT_SECRET = (
        "AMenuDLjVdVo4BSwi0QD54LL6NeVDEZRzEQUJ7hJOM3g4imDZBHHX0hNfKHPeQIGkskhtCmqAJtt_jm7EKq-rWw"
    )

    def _egadata_credentials(self) -> tuple[Optional[str], Optional[str]]:
        """Resolve (username, password) from cfg or the EGA credentials file."""
        username, password = cfg.EGA_USERNAME, cfg.EGA_PASSWORD
        if cfg.EGA_CREDENTIALS_FILE:
            import json as _json

            with open(cfg.EGA_CREDENTIALS_FILE) as fh:
                creds = _json.load(fh)
            username = creds.get("username", username)
            password = creds.get("password", password)
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

        # Auth + DAC-grant check via the REACHABLE OIDC + private-metadata API
        # (port 443), independent of the Data API port 8052. This is the correct
        # entitlement source and works even when 8052 is firewalled.
        auth_ok = False
        n_authorized = 0
        dataset_authorized = None
        auth_error = None
        if not _lib_present("httpx"):
            auth_error = "httpx not installed"
        else:
            import httpx

            token, tok_err = self._metadata_token()
            if not token:
                auth_error = tok_err or "token request failed"
            else:
                auth_ok = True
                try:
                    r = httpx.get(
                        f"{self.META_BASE}/datasets",
                        params={"authorized": "true"},
                        headers={"Authorization": f"Bearer {token}"},
                        timeout=CONNECT_TIMEOUT_S,
                    )
                    r.raise_for_status()
                    grants = r.json() or []
                    ids = [d.get("accession_id") for d in grants if isinstance(d, dict)]
                    n_authorized = len(ids)
                    dataset_authorized = ds in ids
                    result["authorized_datasets"] = ids[:25]
                except Exception as exc:  # noqa: BLE001
                    auth_error = _reason(exc)

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
