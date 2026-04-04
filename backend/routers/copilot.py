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


@router.get("/sessions/{session_id}/history")
async def copilot_history(session_id: str):
    try:
        import legacy_app as leg

        if leg._copilot is None:
            return []
        return leg._copilot.get_session_history(session_id)
    except Exception:
        return []
