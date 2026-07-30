"""Vault (Qdrant) access router — read-only /api/vault endpoints over the
federated ``zeta_vault`` vector collection.

Same auth contract as the graph layer: third-party agents send
``X-Zeta-Api-Key`` (== ``cfg.ZETA_GRAPH_API_KEY``). We REUSE
``routers.graph.require_api_key`` so there is exactly ONE auth surface and no
drift. Qdrant credentials never leave the server.

The discovery verb is ``GET /api/vault/manifest`` — an agent calls it once and
learns the collection config, the filterable fields WITH their live value
vocabularies, the available search modes, and worked examples. It cannot
blind-guess what to send to ``POST /api/vault/search``.

Status codes:
  501  vault not configured server-side (no QDRANT_URL/KEY) or requested mode
       unavailable (e.g. dense without OpenRouter keys) — an honest, typed
       "not available" rather than a fabricated result.
  400  bad request (e.g. filtering on a non-indexed field, empty dense query).
  502  Qdrant reachable-but-erroring / query failure.
"""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from routers.graph import require_api_key  # single shared auth surface

router = APIRouter(prefix="/api/vault", tags=["vault"])

_service = None


def _get_service():
    """Lazy singleton VaultService. Raises RuntimeError if unconfigured."""
    global _service
    if _service is None:
        from federation.vault_store import get_vault_service

        _service = get_vault_service()
    return _service


class VaultSearchRequest(BaseModel):
    query: str = ""
    mode: str = "filter"
    filters: Optional[dict[str, Any]] = None
    limit: int = Field(default=10, ge=1, le=100)


@router.get("/health", dependencies=[Depends(require_api_key)])
async def vault_health():
    try:
        return _get_service().health()
    except RuntimeError as exc:  # not configured
        raise HTTPException(status_code=501, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Vault unavailable: {exc}")


@router.get("/manifest", dependencies=[Depends(require_api_key)])
async def vault_manifest():
    try:
        return _get_service().manifest()
    except RuntimeError as exc:  # not configured
        raise HTTPException(status_code=501, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Vault unavailable: {exc}")


@router.post("/search", dependencies=[Depends(require_api_key)])
async def vault_search(req: VaultSearchRequest):
    from federation.vault_store import VaultModeUnavailable

    try:
        svc = _get_service()
    except RuntimeError as exc:  # not configured
        raise HTTPException(status_code=501, detail=str(exc))
    try:
        return svc.search(query=req.query, mode=req.mode, filters=req.filters, limit=req.limit)
    except VaultModeUnavailable as exc:  # mode wired but not enabled
        raise HTTPException(status_code=501, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Vault query failed: {exc}")
