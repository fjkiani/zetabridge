"""
Brenus extension-point stub routes.

These routes return HTTP 501 Not Implemented until the Brenus integration
sprint wires them to the governed trial-domain DuckDB tables and YAML canon.

They are defined here so:
  1. The OpenAPI spec documents them as real endpoints
  2. The generated TypeScript client includes them (non-ornamental)
  3. Frontend components can reference them without import errors
  4. The integration agent knows exactly where to splice in Brenus logic

Integration instructions for each route are in the docstring.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api", tags=["brenus-stubs"])

_NOT_IMPLEMENTED = JSONResponse(
    status_code=501,
    content={
        "detail": "Not implemented — Brenus integration pending",
        "integration_status": "stub",
        "docs": "See backend/routers/brenus_stubs.py for integration instructions",
    },
)


@router.get("/trials")
async def list_trials():
    """
    BRENUS INTEGRATION HOOK.

    Returns all governed trial packages from the Brenus DuckDB trials table.

    When wired:
        SELECT nct_id, alias, phase, status, sponsor, line_of_therapy,
               msi_status, primary_endpoint, enrollment_target
        FROM trials
        ORDER BY start_date DESC;

    Admissibility: no gate — trial metadata is always external_safe.
    """
    return _NOT_IMPLEMENTED


@router.get("/trials/{nct_id}")
async def get_trial(nct_id: str):
    """
    BRENUS INTEGRATION HOOK.

    Returns a single trial package with arms, endpoints, and eligibility.

    When wired:
        SELECT t.*, a.arm_id, a.label, a.population, a.msi_status, a.intervention
        FROM trials t
        LEFT JOIN trial_arms a ON t.nct_id = a.nct_id
        WHERE t.nct_id = ?;
    """
    return _NOT_IMPLEMENTED


@router.get("/claims")
async def list_claims(
    nct_id: str | None = None,
    confidence: str | None = None,
    blocked: bool | None = None,
):
    """
    BRENUS INTEGRATION HOOK.

    Returns governed claims from the Brenus DuckDB claims table.

    Admissibility enforcement (MUST implement before wiring):
        - BLOCKED claims: return {"value": null, "blocked": true, "blocker_id": "..."}
          NEVER return the actual value of a blocked claim.
        - QUARANTINED artifacts: return NULL_TOKEN in consumer output.
        - T3 sources: cap admissibility at supplementary_only.
        - NDA-required fields: return {"value": null, "nda_required": true, "nda_id": "..."}

    When wired:
        SELECT c.claim_id, c.field_path,
               CASE WHEN c.blocked THEN NULL ELSE c.value END as value,
               c.confidence, c.blocked, c.blocker_id, c.source_id
        FROM claims c
        WHERE (? IS NULL OR c.nct_id = ?)
          AND (? IS NULL OR c.confidence = ?)
          AND (? IS NULL OR c.blocked = ?);
    """
    return _NOT_IMPLEMENTED


@router.get("/blockers")
async def list_blockers(status: str | None = None):
    """
    BRENUS INTEGRATION HOOK.

    Returns blockers from the Brenus DuckDB blockers table.

    When wired:
        SELECT blocker_id, domain, status, impact, resolution_path, last_updated
        FROM blockers
        WHERE (? IS NULL OR status = ?)
        ORDER BY last_updated DESC;
    """
    return _NOT_IMPLEMENTED


@router.get("/consumers/{consumer_id}/claims")
async def consumer_claims(consumer_id: str):
    """
    BRENUS INTEGRATION HOOK.

    Returns external_safe claims for a specific consumer (deck, outreach, escape map).

    Admissibility gate (MUST enforce):
        - Only return claims with admissibility >= external_safe
        - BLOCKED claims → 403 Forbidden (not 200 with null value)
        - QUARANTINED artifacts → NULL_TOKEN in response
        - NDA-required fields → {"value": null, "nda_required": true}

    When wired:
        SELECT c.claim_id, c.field_path, c.value, c.confidence, c.source_id
        FROM claims c
        JOIN consumer_claim_map m ON c.claim_id = m.claim_id
        WHERE m.consumer_id = ?
          AND c.blocked = FALSE
          AND c.admissibility >= 'external_safe';
    """
    return _NOT_IMPLEMENTED
