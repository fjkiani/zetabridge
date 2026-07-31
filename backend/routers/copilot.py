"""Co-pilot chat — uses legacy CoPilot when legacy stack is initialized."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


class CoPilotChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    params: dict = {}


@router.post("/chat")
async def copilot_chat(req: CoPilotChatRequest):
    # Capability intents (outcome anchors + Efficacy Predictor) are served by the
    # lightweight orchestrator path even when the legacy agent stack is off.
    try:
        from agents.orchestrator import classify_intent, _handle_capability_intent, Intent
        intent = classify_intent(req.message)
        if intent in (Intent.CAPABILITY_ANCHOR, Intent.CAPABILITY_EFFICACY):
            cap = _handle_capability_intent(intent, req.message, req.params or {})
            return {"response": {"summary": cap.get("summary"), "intent": intent.value,
                                 "data": cap.get("results"), "render_type": "text",
                                 "suggestions": _capability_suggestions(intent)}}
    except Exception:
        pass  # fall through to legacy copilot

    try:
        import legacy_app as leg

        if leg._copilot is None:
            raise HTTPException(
                503,
                "Co-pilot not initialized (enable USE_LEGACY_STORE=1 for full agent co-pilot)",
            )
        return await leg._copilot.chat(
            req.message, req.session_id or "session-demo", req.params
        )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(503, str(exc)) from exc


def _capability_suggestions(intent) -> list[str]:
    from agents.orchestrator import Intent
    if intent == Intent.CAPABILITY_ANCHOR:
        return ["what outcomes do we have for britroc?",
                "which cohorts are efficacy-ready?",
                "predict os from LST_score and CCNE1"]
    return ["predict pfs from fraction_genome_altered",
            "predict platinum sensitivity from CCNE1",
            "what outcomes do we have for pds?"]


@router.get("/sessions/{session_id}/history")
async def copilot_history(session_id: str):
    try:
        import legacy_app as leg

        if leg._copilot is None:
            return []
        return leg._copilot.get_session_history(session_id)
    except Exception:
        return []
