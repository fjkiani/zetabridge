"""Score + pick from kill receipts (W3/W4)."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .specs import (
    Hypothesis,
    KillTest,
    KillVerdict,
    Pick,
    ScorecardRow,
    ScoreVerdict,
)


def score(
    hypotheses: list[Hypothesis], kills: list[KillTest]
) -> list[ScorecardRow]:
    by_h: dict[str, list[KillTest]] = defaultdict(list)
    for k in kills:
        by_h[k.hypothesis_id].append(k)

    rows: list[ScorecardRow] = []
    for h in hypotheses:
        ks = by_h.get(h.id, [])
        fails = [k for k in ks if k.verdict == KillVerdict.FAIL]
        blocked = [k for k in ks if k.verdict == KillVerdict.BLOCKED]
        # fit_A blocked is expected — ignore for ADVANCE decision if sole blocker named fit_A
        material_blocks = [
            k
            for k in blocked
            if k.kill_name not in ("fit_A_fit_B_vs_outcome",)
        ]

        if fails:
            verdict = ScoreVerdict.KILL
            why = "; ".join(f"{k.kill_name}:{k.method}" for k in fails)
            money = False
        elif h.claim_class == "needs_ipd":
            verdict = ScoreVerdict.BLOCKED
            why = "needs IPD outcomes / treatment contrast; GENIE prior insufficient"
            money = False
        elif h.claim_class == "negative_control":
            verdict = ScoreVerdict.KILL
            why = (
                "NEGATIVE CONTROL documented — prognostic DEAD pattern; "
                "not an enrichment money pick"
            )
            money = False
        elif h.claim_class == "genie_prior" and not material_blocks:
            verdict = ScoreVerdict.ADVANCE_AS_PRIOR
            why = "survived assay+leakage kills; ADVANCE as GENIE prior only (no trial OS)"
            money = False  # prior feeds IPD money lane — not GENIE money alone
        elif material_blocks:
            verdict = ScoreVerdict.BLOCKED
            why = "; ".join(f"{k.kill_name}:{k.error or k.method}" for k in material_blocks)
            money = False
        else:
            verdict = ScoreVerdict.BLOCKED
            why = "insufficient evidence"
            money = False

        rows.append(
            ScorecardRow(
                id=h.id,
                hypothesis=h.statement,
                verdict=verdict,
                why_measured=why,
                money=money,
                play=h.play,
                claim_class=h.claim_class,
                kill_receipts=[k.kill_name for k in ks],
                n=next((k.n for k in ks if k.n), None),
            )
        )
    return rows


def pick(rows: list[ScorecardRow]) -> Pick:
    advances = [
        r
        for r in rows
        if r.verdict in (ScoreVerdict.ADVANCE, ScoreVerdict.ADVANCE_AS_PRIOR)
    ]
    gaps = [
        {
            "gap": "Trial OS/PFS treatment×marker lift",
            "status": "needs_IPD",
            "blocks": "predictive ADVANCE / money #1 from GENIE alone",
        },
        {
            "gap": "GENIE MSI clinical columns",
            "status": "absent_R20_public",
            "blocks": "MSI-high IO prior from GENIE clinical",
        },
        {
            "gap": "GENIE↔PDS patient IDs",
            "status": "JOIN_IMPOSSIBLE",
            "blocks": "patient-level GENIE feature→trial outcome",
        },
        {
            "gap": "8D-04 / fit_A held-out",
            "status": "LOCKED",
            "blocks": "PATH ranking soft-unblock (explicit non-goal)",
        },
    ]
    return Pick(advances=advances, dar_ipd_gaps=gaps)
