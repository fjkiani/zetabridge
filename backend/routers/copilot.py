"""Co-pilot chat — uses legacy CoPilot when legacy stack is initialized."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/copilot", tags=["copilot"])


class CoPilotChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    params: dict = {}


# Lightweight capability-intent detection (no heavy agent stack required).
_EFFICACY_KW = ["predict", "efficacy", "cox", "hazard ratio", "survival model",
                "train a model", "logistic", "concordance", "c-index", "auc", "model outcomes"]
_ANCHOR_KW = ["outcome", "anchor", "cohort", "what outcomes", "which cohorts", "publication",
              "do we have", "trial", "platinum sensitivity data", "dataset"]


def _detect_capability_intent(text: str) -> str | None:
    q = text.lower()
    if any(k in q for k in _EFFICACY_KW):
        return "capability_efficacy"
    if any(k in q for k in _ANCHOR_KW):
        return "capability_anchor"
    return None


def _run_capability(intent: str, message: str, params: dict) -> dict:
    """Call the capability router logic directly (anchors + Efficacy Predictor)."""
    from routers import capability as cap
    q = message.lower()
    if intent == "capability_anchor":
        idx = cap._load_index()
        sources = idx.get("sources", [])
        hit = None
        for s in sources:
            keys = [s["source_id"].lower(), s["name"].lower(), s.get("cancer_type", "").lower()]
            if any(k and k in q for k in keys):
                hit = s
                break
        if hit:
            pub = hit.get("publication", {})
            ov = hit.get("outcome_vars", {})
            ov_l = list(ov) if isinstance(ov, dict) else ov
            summary = (f"{hit['name']} ({hit['source_id']}): {hit['cohort_n']} patients, "
                       f"{hit['cancer_type']}, trial={'yes' if hit['is_trial'] else 'no'}. "
                       f"Outcomes: {', '.join(ov_l)}. Publication: {pub.get('cite', pub.get('doi', 'n/a'))}. "
                       f"Efficacy-ready: {hit['efficacy_ready']}.")
            return {"summary": summary, "results": hit}
        lines = [f"- {s['name']} ({s['source_id']}): {s['cohort_n']} pts, {s['cancer_type']}, "
                 f"efficacy_ready={s['efficacy_ready']}" for s in sources]
        return {"summary": f"ZetaBridge has {len(sources)} outcome-anchored sources:\n" + "\n".join(lines),
                "results": {"n_sources": len(sources), "sources": sources}}
    # efficacy
    cohort = params.get("cohort") or ("spectrum" if ("spectrum" in q or "synapse" in q) else
                                      ("pds" if "pds" in q or "trial" in q else "britroc"))
    analysis = params.get("analysis") or ("platinum_sensitivity" if ("platinum" in q or "sensitivity" in q or "resistance" in q)
                                          else ("pfs" if ("pfs" in q or "progression" in q) else "os"))
    feats = params.get("features")
    if not feats:
        if cohort == "spectrum":
            feats = ["is_fbi"]
        elif cohort == "pds":
            feats = ["arm_ind"]
        else:
            cand = ["LST_score", "fraction_genome_altered", "CCNE1", "KRAS", "MYC", "age"]
            feats = [c for c in cand if c.lower() in q] or ["LST_score", "fraction_genome_altered"]
    res = cap.run_efficacy(cap.EfficacyRequest(cohort=cohort, analysis=analysis, features=feats,
                                               cv_folds=int(params.get("cv_folds", 5))))
    if res["model"] == "cox_ph":
        hr_s = ", ".join(f"{k} HR={v}" for k, v in res.get("hazard_ratios", {}).items())
        summary = (f"Efficacy Predictor — {cohort} → {analysis} (Cox PH, n={res['n']}, events={res['events']}): "
                   f"CV concordance={res.get('cv_concordance_mean')}. {hr_s}. "
                   f"PH ok={res.get('ph_assumption_ok')}. Discovery-only.")
    else:
        summary = (f"Efficacy Predictor — {cohort} → {analysis} (logistic, n={res['n']}, events={res['events']}): "
                   f"CV AUC={res.get('cv_auc_mean')}. Discovery-only.")
    return {"summary": summary, "results": res}


@router.post("/chat")
async def copilot_chat(req: CoPilotChatRequest):
    # Capability intents (outcome anchors + Efficacy Predictor) served directly,
    # even when the legacy agent stack is off.
    intent = _detect_capability_intent(req.message)
    if intent:
        try:
            cap = _run_capability(intent, req.message, req.params or {})
            sugg = (["predict os from LST_score and CCNE1", "which cohorts are efficacy-ready?"]
                    if intent == "capability_anchor" else
                    ["what outcomes do we have for britroc?", "predict platinum sensitivity from CCNE1"])
            return {"response": {"summary": cap["summary"], "intent": intent,
                                 "data": cap["results"], "render_type": "text", "suggestions": sugg}}
        except Exception as exc:
            raise HTTPException(400, f"capability query failed: {exc}") from exc

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
