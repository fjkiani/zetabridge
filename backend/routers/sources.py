"""Sources router — authenticated LIVE extraction from the three source systems.

Session 15. Where /api/graph and /api/signals read the *pre-extracted* federated
knowledge graph, this router invokes the live source systems on demand:

    A_MSK -> Synapse       B_SAS -> SAS Viya CAS       C_EGA -> EGA

Auth: same ``X-Zeta-Api-Key`` header as the rest of the agent API. Closed by
default (503) when no key is configured. The source credentials stay entirely
server-side (in ``cfg``); a caller never sees them.

Honesty contract: every response is the uniform SourceGateway envelope. When a
source is unreachable/unconfigured, ``data`` is ``null`` and ``error`` carries a
typed reason. Rows are never fabricated.
"""

from __future__ import annotations

import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from config import cfg

router = APIRouter(prefix="/api/sources", tags=["sources"])

_gateway = None


def _get_gateway():
    global _gateway
    if _gateway is None:
        from federation.source_gateway import SourceGateway

        _gateway = SourceGateway.from_env()
    return _gateway


async def require_api_key(x_zeta_api_key: Optional[str] = Header(default=None)) -> None:
    configured = cfg.ZETA_GRAPH_API_KEY
    if not configured:
        raise HTTPException(status_code=503, detail="Graph API key not configured on server.")
    if not x_zeta_api_key or x_zeta_api_key != configured:
        raise HTTPException(status_code=401, detail="Missing or invalid X-Zeta-Api-Key.")


async def _run(fn, *args):
    """Run a blocking gateway call off the event loop and return a plain dict."""
    env = await asyncio.to_thread(fn, *args)
    return env.to_dict()


@router.get("/health", dependencies=[Depends(require_api_key)])
async def sources_health():
    """Per-endpoint connect handshake + configured flags. No data extraction."""
    gw = _get_gateway()
    return await asyncio.to_thread(gw.health)


# --- Synapse (A_MSK) -------------------------------------------------------
@router.get("/synapse/whoami", dependencies=[Depends(require_api_key)])
async def synapse_whoami():
    return await _run(_get_gateway().synapse.whoami)


@router.get("/synapse/entity/{syn_id}", dependencies=[Depends(require_api_key)])
async def synapse_entity(syn_id: str):
    return await _run(_get_gateway().synapse.get_entity, syn_id)


@router.get("/synapse/table/{syn_id}", dependencies=[Depends(require_api_key)])
async def synapse_table(syn_id: str, limit: int = 50):
    return await _run(_get_gateway().synapse.query_table, syn_id, limit)


@router.get("/synapse/children/{parent_id}", dependencies=[Depends(require_api_key)])
async def synapse_children(parent_id: str, limit: int = 100):
    """List immediate children of a Project/Folder/Dataset — the crawl verb.

    An agent walks parent -> children recursively to reach every file. Respects
    the account's READ access (a controlled parent yields a typed 'unreachable',
    never fabricated rows)."""
    return await _run(_get_gateway().synapse.list_children, parent_id, limit)


@router.get("/synapse/download-diagnostics/{syn_id}", dependencies=[Depends(require_api_key)])
async def synapse_download_diagnostics(syn_id: str):
    """Go/no-go gate for byte download WITHOUT transferring the file.
    ``data.can_download`` is the boolean an agent checks before a multi-GB stream."""
    return await _run(_get_gateway().synapse.download_diagnostics, syn_id)


@router.get("/synapse/download-url/{syn_id}", dependencies=[Depends(require_api_key)])
async def synapse_download_url(syn_id: str):
    """Mint a short-lived pre-signed S3 URL for a Synapse file's bytes.

    TOKEN-HANDOFF (same contract as ARGO): returns ``data.url`` + expected
    ``md5``/``size``; the caller streams bytes DIRECTLY from object storage. No
    bytes proxy through this backend. A file the account can't read yields a
    typed 'unreachable' with ``data`` null — never a fabricated link."""
    return await _run(_get_gateway().synapse.resolve_download, syn_id)


# --- SAS Viya CAS (B_SAS) --------------------------------------------------
@router.get("/sas/caslibs", dependencies=[Depends(require_api_key)])
async def sas_caslibs():
    return await _run(_get_gateway().sas.list_caslibs)


@router.get("/sas/adam", dependencies=[Depends(require_api_key)])
async def sas_adam(caslib: str, table: str, limit: int = 50):
    return await _run(_get_gateway().sas.query_adam, caslib, table, limit)


# --- EGA (C_EGA) -----------------------------------------------------------
@router.get("/ega/authorized-datasets", dependencies=[Depends(require_api_key)])
async def ega_authorized_datasets():
    """AUTHORITATIVE entitlement: exactly which EGA datasets THIS account can
    access (from the auth'd :8443/v2/metadata/datasets endpoint — NOT the ~21k
    public catalog). The anti-sandbagging verb: call it FIRST to learn what is
    crawlable instead of dead-ending on a 403. Fetches no bytes."""
    return await _run(_get_gateway().ega.authorized_datasets)


@router.get("/ega/files", dependencies=[Depends(require_api_key)])
async def ega_files(dataset: Optional[str] = None, limit: int = 50):
    return await _run(_get_gateway().ega.list_files, dataset, limit)


@router.get("/ega/file/{file_id}", dependencies=[Depends(require_api_key)])
async def ega_file(file_id: str):
    return await _run(_get_gateway().ega.file_metadata, file_id)


@router.get("/ega/file/{file_id}/access-probe", dependencies=[Depends(require_api_key)])
async def ega_file_access_probe(file_id: str):
    """Per-file go/no-go WITHOUT transferring bytes: auth'd metadata probe
    (200=authorized, 403=DAC boundary) plus a 1-byte Range probe confirming the
    byte transport yields 206 octet-stream. ``data.can_download`` is the honest
    verdict; a 403 returns status=unreachable + data=null (no fabrication)."""
    return await _run(_get_gateway().ega.file_access_probe, file_id)


@router.get("/ega/download-diagnostics", dependencies=[Depends(require_api_key)])
async def ega_download_diagnostics(dataset: Optional[str] = None):
    """Gate check for byte-download: server-side egress to the EGA Data API port,
    OAuth2 auth, and whether the account has a DAC grant for ``dataset``.
    Transfers no bytes. ``data.can_download`` is the go/no-go for streaming."""
    return await _run(_get_gateway().ega.download_diagnostics, dataset)


@router.get("/ega/file/{file_id}/download", dependencies=[Depends(require_api_key)])
async def ega_file_download(file_id: str, request: Request):
    """Stream an EGA file's raw bytes (``application/octet-stream``) server-side.

    The EGA credentials stay entirely on the server; the caller never sees them.
    HTTP ``Range`` is passed through to the EGA Data API, so callers can resume /
    slice (206 Partial Content) exactly as the upstream supports. Honesty
    contract: on any upstream failure we return a typed HTTP error (403/502/503/
    504) — we never stream an HTML/JSON error body as if it were file bytes.

    Enables the raw-BAM FIFO loop: GET here -> stream to disk -> QDNAseq -> rm.
    """
    gw = _get_gateway()
    range_header = request.headers.get("range")

    ok, meta, byte_iter, closer, error = await asyncio.to_thread(
        gw.ega.open_byte_stream, file_id, range_header
    )
    if not ok:
        reason = error or "unknown"
        low = reason.lower()
        if low.startswith("unconfigured"):
            code = 503
        elif low.startswith("not_found"):
            code = 404
        elif low.startswith("range_not_satisfiable"):
            code = 416
        elif low.startswith("auth"):
            code = 403
        elif low.startswith("timeout"):
            code = 504
        else:  # network / http / unexpected_content_type / tls / other
            code = 502
        raise HTTPException(status_code=code, detail=f"EGA download failed: {reason}")

    def _iter():
        try:
            for chunk in byte_iter:
                yield chunk
        finally:
            if closer:
                closer()

    # Byte-accurate headers so the caller sees a faithful proxy of the plaintext
    # BAM. 206 + Content-Range when the caller asked for a partial range.
    resp_headers = {"Accept-Ranges": meta.get("accept_ranges") or "bytes"}
    if meta.get("content_length") is not None:
        resp_headers["Content-Length"] = str(meta["content_length"])
    if meta.get("content_range"):
        resp_headers["Content-Range"] = meta["content_range"]
    resp_headers["Content-Disposition"] = f'attachment; filename="{file_id}.bam"'

    return StreamingResponse(
        _iter(),
        status_code=meta.get("status_code", 200),
        media_type="application/octet-stream",
        headers=resp_headers,
    )


# --- ICGC ARGO (D_ARGO) ----------------------------------------------------
# Overture SONG/SCORE, DACO controlled-access genomics. The download path is a
# TOKEN-HANDOFF: this router mints a short-lived pre-signed URL; the caller then
# streams bytes DIRECTLY from object storage. We never proxy whole BAM/CRAM
# files through this backend (that would reintroduce the dyno bandwidth wall).
def _argo_http_error(env_dict: dict) -> None:
    """Map a non-live ARGO envelope to a typed HTTP error (raises)."""
    if env_dict.get("status") == "live":
        return
    reason = (env_dict.get("error") or "unknown").lower()
    if reason.startswith("unconfigured"):
        code = 503
    elif "not_found" in reason:
        code = 404
    elif reason.startswith("forbidden") or "daco" in reason:
        code = 403
    elif reason.startswith("auth") or "401" in reason:
        code = 401
    elif reason.startswith("timeout"):
        code = 504
    else:  # network / http / other
        code = 502
    raise HTTPException(status_code=code, detail=f"ARGO {env_dict.get('action')} failed: {env_dict.get('error')}")


@router.get("/argo/health", dependencies=[Depends(require_api_key)])
async def argo_health():
    """Token-configured flag + a cheap SCORE liveness ping. No data extraction."""
    gw = _get_gateway()
    env = await _run(gw.argo.storage_alive)
    return {
        "endpoint": "D_ARGO",
        "source": "argo",
        "configured": gw.argo.configured(),
        "status": env.get("status"),
        "latency_ms": env.get("latency_ms"),
        "error": env.get("error"),
    }


@router.get("/argo/entities", dependencies=[Depends(require_api_key)])
async def argo_entities(
    project: Optional[str] = None,
    access: Optional[str] = None,
    file_type: Optional[str] = None,
    size: int = 50,
):
    """List/search the SCORE object registry. ``file_type`` filters on extension
    (e.g. ``cram``, ``bam``, ``vcf``); ``access`` on ``controlled``/``open``."""
    return await _run(_get_gateway().argo.list_entities, project, access, file_type, size)


@router.get("/argo/entity/{object_id}", dependencies=[Depends(require_api_key)])
async def argo_entity(object_id: str):
    """One object's registry metadata + derived donor/sample/experiment fields."""
    env = await _run(_get_gateway().argo.entity_metadata, object_id)
    _argo_http_error(env)
    return env


@router.get("/argo/download-url/{object_id}", dependencies=[Depends(require_api_key)])
async def argo_download_url(object_id: str, offset: int = 0, length: int = -1):
    """Mint a short-lived pre-signed SCORE download URL for a controlled object.

    Returns the envelope with ``data.parts[].url`` — the caller/worker streams
    bytes DIRECTLY from ``data.object_host`` (object storage), NOT through this
    backend. Requires the DACO token; an invalid/non-entitled token yields 401.
    Honesty contract: no bytes are transferred here, and no URL is returned on a
    failure path (``data`` is null)."""
    env = await _run(_get_gateway().argo.resolve_download, object_id, offset, length)
    _argo_http_error(env)
    return env
