"""
AACR Project GENIE → winners EnrichmentHypothesisFramework (RUO).

Conceptual alignment with genie_winners_mcp tool IDs (genie.* / winners.*) —
this package is the **framework layer**, not the MCP server (break-track owns that).

Does NOT soft-unblock 8D-04. Tissue panel TMB ≠ plasma pTMB. MSI only if present.
"""

from .specs import (
    Hypothesis,
    KillTest,
    KillVerdict,
    Pick,
    PreReg,
    ScorecardRow,
    ScoreVerdict,
)

# runner imported lazily via run_framework to avoid runpy double-import warning

__all__ = [
    "Hypothesis",
    "KillTest",
    "KillVerdict",
    "Pick",
    "PreReg",
    "ScorecardRow",
    "ScoreVerdict",
    "EnrichmentHypothesisFramework",
    "run_framework",
]

__schema_version__ = "aacr-winners-framework/0.1.0"


def __getattr__(name: str):
    if name in ("EnrichmentHypothesisFramework", "run_framework"):
        from .runner import EnrichmentHypothesisFramework, run_framework

        return {
            "EnrichmentHypothesisFramework": EnrichmentHypothesisFramework,
            "run_framework": run_framework,
        }[name]
    raise AttributeError(name)
