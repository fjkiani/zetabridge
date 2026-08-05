"""Spec objects for EnrichmentHypothesisFramework (W0–W4 conceptual contracts)."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class ScoreVerdict(str, Enum):
    ADVANCE = "ADVANCE"
    ADVANCE_AS_PRIOR = "ADVANCE_AS_PRIOR"
    KILL = "KILL"
    BLOCKED = "BLOCKED"


class KillVerdict(str, Enum):
    PASS = "PASS"  # survived the kill (still candidate)
    FAIL = "FAIL"  # killed
    BLOCKED = "BLOCKED"  # cannot run (missing labels / columns)


@dataclass
class PreReg:
    """W0-aligned pre-registration (must be sealed before scoring)."""

    schema_version: str = "winner_definition/0.1"
    primary_endpoint: str = "BLOCKED"  # GENIE has no trial OS/PFS
    population: str = "GENIE_R20_CRC_tissue_panel"
    biomarker_cuts: list[str] = field(default_factory=list)
    treatment_contrast: str = "BLOCKED"  # no treatment in GENIE clinical
    null_model: str = "assay_stratified_prevalence_null"
    held_out_protocol: dict[str, Any] = field(
        default_factory=lambda: {
            "split": "none_genie_prior_only",
            "seed": 42,
            "metric": "prevalence_or_within_assay",
        }
    )
    pre_reg_seed: int = 42
    success_rule: str = (
        "ADVANCE_AS_PRIOR only when kill battery passes assay bias + "
        "prognostic-only NEGATIVE control; never claim trial OS without IPD"
    )
    exploratory_policy: str = "plays_A_B_C_plugins; cut shopping forbidden for ADVANCE"
    not_8d04_unblock: bool = True
    not_guardant_ptmb: bool = True
    not_genie_pds_join_required: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Hypothesis:
    """W1 candidate enrichment hypothesis (≤5 in a run)."""

    id: str
    statement: str
    play: str  # A | B | C
    biomarker: str
    endpoint: str
    fields_required: list[str]
    fields_exist_on_disk: dict[str, bool] = field(default_factory=dict)
    claim_class: str = "genie_prior"  # genie_prior | needs_ipd | negative_control
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class KillTest:
    """Single adversarial kill result (W2)."""

    hypothesis_id: str
    kill_name: str
    verdict: KillVerdict
    n: Optional[int] = None
    method: str = ""
    detail: dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


@dataclass
class ScorecardRow:
    """W3 scoreboard row."""

    id: str
    hypothesis: str
    verdict: ScoreVerdict
    why_measured: str
    money: bool = False
    play: str = ""
    claim_class: str = ""
    kill_receipts: list[str] = field(default_factory=list)
    n: Optional[int] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["verdict"] = self.verdict.value
        return d


@dataclass
class Pick:
    """W4 ADVANCE-only shortlist + DAR/IPD gaps."""

    advances: list[ScorecardRow] = field(default_factory=list)
    dar_ipd_gaps: list[dict[str, str]] = field(default_factory=list)
    explicit_non_claims: list[str] = field(
        default_factory=lambda: [
            "No 8D-04 soft-unblock",
            "No GuardantOMNI plasma pTMB",
            "No invented MSI",
            "No fake GENIE×PDS patient merge",
            "No trial OS claim from GENIE alone",
        ]
    )
    not_8d04_unblock: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "advances": [a.to_dict() for a in self.advances],
            "dar_ipd_gaps": self.dar_ipd_gaps,
            "explicit_non_claims": self.explicit_non_claims,
            "not_8d04_unblock": self.not_8d04_unblock,
        }
