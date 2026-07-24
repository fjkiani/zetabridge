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

from fastapi import APIRouter, Depends, Header, HTTPException

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


# --- SAS Viya CAS (B_SAS) --------------------------------------------------
@router.get("/sas/caslibs", dependencies=[Depends(require_api_key)])
async def sas_caslibs():
    return await _run(_get_gateway().sas.list_caslibs)


@router.get("/sas/adam", dependencies=[Depends(require_api_key)])
async def sas_adam(caslib: str, table: str, limit: int = 50):
    return await _run(_get_gateway().sas.query_adam, caslib, table, limit)


# --- EGA (C_EGA) -----------------------------------------------------------
@router.get("/ega/files", dependencies=[Depends(require_api_key)])
async def ega_files(dataset: Optional[str] = None, limit: int = 50):
    return await _run(_get_gateway().ega.list_files, dataset, limit)


@router.get("/ega/file/{file_id}", dependencies=[Depends(require_api_key)])
async def ega_file(file_id: str):
    return await _run(_get_gateway().ega.file_metadata, file_id)


@router.get("/ega/download-diagnostics", dependencies=[Depends(require_api_key)])
async def ega_download_diagnostics(dataset: Optional[str] = None):
    """Gate check for byte-download: server-side egress to the EGA Data API port,
    OAuth2 auth, and whether the account has a DAC grant for ``dataset``.
    Transfers no bytes. ``data.can_download`` is the go/no-go for streaming."""
    return await _run(_get_gateway().ega.download_diagnostics, dataset)


@router.get("/ega/file/{file_id}/size", dependencies=[Depends(require_api_key)])
async def ega_file_size(file_id: str):
    """Authenticated file-size lookup via the EGA Data API v2 (port 8443).

    Unlike /ega/file/{id} (public metadata), this exercises the controlled-access
    Data API with server-side credentials over the v2 API on 8443 (the reachable
    port), so a 'live' status here proves the byte channel below authenticates.
    """
    return await _run(_get_gateway().ega.download_size, file_id)


@router.get("/ega/file/{file_id}/download", dependencies=[Depends(require_api_key)])
async def ega_file_download(file_id: str):
    """Stream decrypted file bytes for ``file_id`` from the EGA Data API v2 (8443).

    Server-side authenticated proxy: the caller supplies only X-Zeta-Api-Key and
    never sees the EGA credentials. Returns application/octet-stream. Auth is
    forced before the body starts, so auth/config failures return a typed JSON
    error (503 unconfigured / 502 upstream) instead of a truncated stream.

    Uses pyega3's v2 client (url_api https://ega.ebi.ac.uk:8443/v2), which is
    reachable from Render; the legacy 8052 Data API path is firewall-blocked and
    is NOT used here.
    """
    from fastapi.responses import StreamingResponse

    gw = _get_gateway()
    if not gw.ega.configured():
        raise HTTPException(status_code=503, detail="EGA credentials not configured on server.")
    try:
        gen = await asyncio.to_thread(lambda: gw.ega.iter_file_bytes(file_id))
    except RuntimeError as exc:
        msg = str(exc)
        code = 503 if msg.startswith("unconfigured") else 502
        raise HTTPException(status_code=code, detail=f"EGA download failed: {msg}")

    headers = {"Content-Disposition": f'attachment; filename="{file_id}"'}
    return StreamingResponse(gen, media_type="application/octet-stream", headers=headers)
