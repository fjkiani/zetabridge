#!/usr/bin/env python3
"""
emit_model_router_payload.py
Deterministic INTEGRATION RECORD for the route-policy TS package (Directive 2).

It independently re-implements the anti-EGFR routing rules in Python from the SAME
published/PDS-derived anchors and emits modelRouter.payload.json = canonical
profiles + their expected routing decisions. The TS `node:test` suite loads this
file and asserts routeModel(profile) reproduces every expected decision, giving a
CROSS-LANGUAGE concordance check (the secondary evaluation loop for Directive 2).

Anchors MUST stay identical to lib/route-policy/src/routePolicy.ts ANCHORS.
RUO - research use only.
"""
from __future__ import annotations

import json
from pathlib import Path

ANCHORS = {
    "selectionHrRasWt": 0.732,
    "selectionHrRasMut": 1.046,
    "interactionHrRatio": 1.43,
    "adjuvantKrasWtDfsHr": 1.18,
    "albertsKrasWtDfsHr": 1.21,
    "pacceePfsHr": 1.27,
    "pacceOsHr": 1.43,
    "confThreshold": 0.85,
    "strongThreshold": 0.20,
}
POLICY_VERSION = "1.0.0"
NON_METASTATIC = {"ADJUVANT", "STAGE_III", "RESECTED", "NEO_ADJUVANT"}


def benefit_score(hr: float) -> float:
    # match JS Math.max(0, Math.round((1-hr)*1000)/1000)
    return max(0.0, round((1.0 - hr) * 1000) / 1000)


def score_anti_egfr(profile: dict) -> dict:
    conf = profile.get("confidence")
    used_pooled = conf is None or conf < ANCHORS["confThreshold"]
    reason: list[str] = []
    if used_pooled:
        reason.append("POOLED_FALLBACK")

    if not profile["antiEgfr"]:
        reason.append("ANTI_EGFR_NOT_IN_REGIMEN")
        return dict(score=0.0, tier="NOT_APPLICABLE", reasonCodes=reason, usedPooledFallback=used_pooled)

    if profile["bev"]:
        reason.append("ANTI_EGFR_PLUS_BEV_HARD_BLOCK")
        return dict(score=0.0, tier="HARD_BLOCK", reasonCodes=reason, usedPooledFallback=used_pooled)

    if profile["setting"] in NON_METASTATIC:
        reason.append("ADJUVANT_ANTI_EGFR_NO_BENEFIT")
        return dict(score=0.0, tier="POOR", reasonCodes=reason, usedPooledFallback=used_pooled)

    if profile["setting"] == "UNKNOWN":
        reason.append("SETTING_ASSUMED_METASTATIC")

    if profile["ras"] == "WT":
        s = benefit_score(ANCHORS["selectionHrRasWt"])
        reason.append("METASTATIC_RAS_WT_ANTI_EGFR_BENEFIT")
        tier = "STRONG" if s >= ANCHORS["strongThreshold"] else ("MODERATE" if s > 0 else "POOR")
        return dict(score=s, tier=tier, reasonCodes=reason, usedPooledFallback=used_pooled)

    if profile["ras"] == "MUT":
        s = benefit_score(ANCHORS["selectionHrRasMut"])
        reason.append("METASTATIC_RAS_MUT_NO_BENEFIT")
        return dict(score=s, tier="POOR", reasonCodes=reason, usedPooledFallback=used_pooled)

    reason.append("RAS_STATUS_REQUIRED")
    return dict(score=0.0, tier="INDETERMINATE", reasonCodes=reason, usedPooledFallback=used_pooled)


def action_for(tier: str, reason: list[str]) -> str:
    if tier == "HARD_BLOCK":
        return "BLOCK_REGIMEN"
    if "ADJUVANT_ANTI_EGFR_NO_BENEFIT" in reason:
        return "DOWNGRADE_ANTI_EGFR_ADJUVANT"
    if tier == "NOT_APPLICABLE":
        return "ANTI_EGFR_NOT_IN_REGIMEN"
    if tier == "STRONG":
        return "PREFER_ANTI_EGFR"
    if "METASTATIC_RAS_MUT_NO_BENEFIT" in reason:
        return "AVOID_ANTI_EGFR_RAS_MUT"
    if "RAS_STATUS_REQUIRED" in reason:
        return "REQUIRE_RAS_TESTING"
    return "REVIEW"


CASES = [
    ("adjuvant_kraswt", {"setting": "ADJUVANT", "ras": "WT", "antiEgfr": True, "bev": False}),
    ("adjuvant_krasmut", {"setting": "ADJUVANT", "ras": "MUT", "antiEgfr": True, "bev": False}),
    ("stage3_kraswt", {"setting": "STAGE_III", "ras": "WT", "antiEgfr": True, "bev": False}),
    ("metastatic_kraswt", {"setting": "METASTATIC", "ras": "WT", "antiEgfr": True, "bev": False, "confidence": 0.95}),
    ("metastatic_krasmut", {"setting": "METASTATIC", "ras": "MUT", "antiEgfr": True, "bev": False, "confidence": 0.95}),
    ("antiegfr_bev_block", {"setting": "METASTATIC", "ras": "WT", "antiEgfr": True, "bev": True}),
    ("lowconf_kraswt", {"setting": "METASTATIC", "ras": "WT", "antiEgfr": True, "bev": False, "confidence": 0.70}),
    ("ras_unknown", {"setting": "METASTATIC", "ras": "UNKNOWN", "antiEgfr": True, "bev": False, "confidence": 0.95}),
    ("break_no_antiegfr", {"setting": "METASTATIC", "ras": "MUT", "antiEgfr": False, "bev": True}),
]


def build_payload() -> dict:
    cases = []
    for name, prof in CASES:
        r = score_anti_egfr(prof)
        act = action_for(r["tier"], r["reasonCodes"])
        cases.append({
            "name": name,
            "profile": prof,
            "expected": {
                "action": act,
                "efficacyScore": r["score"],
                "tier": r["tier"],
                "usedPooledFallback": r["usedPooledFallback"],
                "requiredReasonCodes": r["reasonCodes"],
            },
        })
    return {
        "artifact": "modelRouter.payload",
        "purpose": "cross-language concordance record for @workspace/route-policy (Directive 2)",
        "policyVersion": POLICY_VERSION,
        "anchors": ANCHORS,
        "integrity_note": (
            "Adjuvant downgrade to 0.0/POOR is anchored on the DIRECT N0147/Alberts "
            "no-benefit finding, NOT on the null treatment x RAS interaction."
        ),
        "cases": cases,
        "ruo": "RUO - research use only. Evidence-anchored routing logic, not medical advice.",
    }


if __name__ == "__main__":
    out = Path(__file__).with_name("modelRouter.payload.json")
    out.write_text(json.dumps(build_payload(), indent=2, sort_keys=True) + "\n")
    print("wrote", out.name)
    for c in build_payload()["cases"]:
        e = c["expected"]
        print(f"  {c['name']:22s} -> {e['action']:28s} score={e['efficacyScore']:.3f} tier={e['tier']}")
