"""Play A: Assay-calibrated TMB strata → prognostic DEAD only as NEGATIVE control."""

from __future__ import annotations

from typing import Any

from ..assay import AssayStratifier
from ..biomarker_endpoint import define_biomarker, define_endpoint
from ..interfaces import PlayPlugin
from ..specs import Hypothesis


class PlayAAssayTMB(PlayPlugin):
    play_id = "A"

    def build_hypotheses(self, cohort: dict[str, Any]) -> list[Hypothesis]:
        cols = set(cohort.get("columns") or [])
        tmb_bm = define_biomarker("tissue_panel_TMB", cohort)
        dead_ep = define_endpoint("DEAD", cohort)
        prev_ep = define_endpoint("prevalence", cohort)

        fields = {
            "SEQ_ASSAY_ID": "SEQ_ASSAY_ID" in cols,
            "tmb_bin": "tmb_bin" in cols,
            "DEAD": "DEAD" in cols,
            "tmb_mut_per_mb": "tmb_mut_per_mb" in cols,
        }

        return [
            Hypothesis(
                id="H_A1",
                statement=(
                    "Assay-stratified tissue_panel_TMB-high prevalence is a GENIE prior "
                    "for IPD design talks (NOT an assay-agnostic enrichment winner)"
                ),
                play="A",
                biomarker="tissue_panel_TMB",
                endpoint="prevalence",
                fields_required=list(fields.keys()),
                fields_exist_on_disk=fields,
                claim_class="genie_prior",
                notes=f"biomarker_ok={tmb_bm.get('ok')}; endpoint_ok={prev_ep.get('ok')}",
            ),
            Hypothesis(
                id="H_A2",
                statement=(
                    "NEGATIVE CONTROL: tissue_panel_TMB-high association with DEAD is "
                    "prognostic-only — kill if treated as predictive enrichment winner"
                ),
                play="A",
                biomarker="tissue_panel_TMB",
                endpoint="DEAD",
                fields_required=["tmb_bin", "DEAD", "SEQ_ASSAY_ID"],
                fields_exist_on_disk={
                    "tmb_bin": "tmb_bin" in cols,
                    "DEAD": "DEAD" in cols,
                    "SEQ_ASSAY_ID": "SEQ_ASSAY_ID" in cols,
                },
                claim_class="negative_control",
                notes=f"endpoint_role={dead_ep.get('role')}",
            ),
        ]

    def measure(self, cohort: dict[str, Any]) -> dict[str, Any]:
        table = cohort["table"]
        strata = AssayStratifier().stratify(table)
        return {
            "play": "A",
            "assay_strata": strata,
            "tmb_label": "tissue_panel_TMB",
            "IS_GUARDANT_PTMB": False,
            "msi_available": cohort.get("msi_available", False),
        }
