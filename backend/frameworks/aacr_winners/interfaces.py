"""Framework interfaces — load → biomarker → endpoint → kill → score → pick."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from .specs import Hypothesis, KillTest, Pick, PreReg, ScorecardRow


class CohortLoader(ABC):
    @abstractmethod
    def load_cohort(self) -> dict[str, Any]:
        """Return measured cohort summary + handle (not a full 990MB mutation frame)."""


class BiomarkerDefiner(ABC):
    @abstractmethod
    def define_biomarker(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """Define a biomarker with honesty gates (tissue TMB ≠ pTMB; MSI probe)."""


class EndpointDefiner(ABC):
    @abstractmethod
    def define_endpoint(self, name: str, **kwargs: Any) -> dict[str, Any]:
        """Define endpoint; GENIE DEAD is NEGATIVE-control only for enrichment winners."""


class KillBattery(ABC):
    @abstractmethod
    def run_kill_battery(
        self, hypotheses: list[Hypothesis], prereg: PreReg
    ) -> list[KillTest]:
        ...


class Scorer(ABC):
    @abstractmethod
    def score(
        self, hypotheses: list[Hypothesis], kills: list[KillTest]
    ) -> list[ScorecardRow]:
        ...


class Picker(ABC):
    @abstractmethod
    def pick(self, rows: list[ScorecardRow]) -> Pick:
        ...


class PlayPlugin(ABC):
    """Exploitative play as framework plugin (not a one-liner script)."""

    play_id: str

    @abstractmethod
    def build_hypotheses(self, cohort: dict[str, Any]) -> list[Hypothesis]:
        ...

    @abstractmethod
    def measure(self, cohort: dict[str, Any]) -> dict[str, Any]:
        """Compute play-specific measured tables (prevalence / OR / priors)."""
