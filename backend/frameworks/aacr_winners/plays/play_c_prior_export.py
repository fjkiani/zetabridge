"""Play C: Export prior tables consumable by IPD lane (prevalence by assay)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from ..assay import AssayStratifier
from ..interfaces import PlayPlugin
from ..specs import Hypothesis

HANDOFF_SCHEMA_ID = "ipd_prior_handoff/0.1.0"


class PlayCPriorExport(PlayPlugin):
    play_id = "C"

    def __init__(self, out_dir: Optional[str | Path] = None):
        self.out_dir = Path(out_dir) if out_dir else None

    def build_hypotheses(self, cohort: dict[str, Any]) -> list[Hypothesis]:
        cols = set(cohort.get("columns") or [])
        return [
            Hypothesis(
                id="H_C1",
                statement=(
                    "Export assay-stratified tissue_panel_TMB prevalence prior table "
                    "for IPD lane consumption (no GENIE∩PDS join)"
                ),
                play="C",
                biomarker="tissue_panel_TMB",
                endpoint="prior_table",
                fields_required=["SEQ_ASSAY_ID", "tmb_bin"],
                fields_exist_on_disk={
                    "SEQ_ASSAY_ID": "SEQ_ASSAY_ID" in cols,
                    "tmb_bin": "tmb_bin" in cols,
                },
                claim_class="genie_prior",
                notes="Handoff JSON schema ipd_prior_handoff/0.1.0",
            ),
        ]

    def measure(self, cohort: dict[str, Any]) -> dict[str, Any]:
        strata = AssayStratifier().stratify(cohort["table"])
        payload = {
            "schema_id": HANDOFF_SCHEMA_ID,
            "built_utc": datetime.now(timezone.utc).isoformat(),
            "source": "aacr_winners_framework_play_C",
            "matrix_path": cohort.get("matrix_path"),
            "sha256_parquet": cohort.get("sha256_parquet"),
            "n_crc": cohort.get("n"),
            "tmb_label": "tissue_panel_TMB",
            "IS_GUARDANT_PTMB": False,
            "msi_available": bool(cohort.get("msi_available")),
            "msi_non_null": cohort.get("msi_non_null", 0),
            "NOT_8D04_unblock": True,
            "NOT_genie_pds_join": True,
            "assay_strata_n_ge_100": strata.get("assay_strata_n_ge_min", []),
            "tmb_high_rate_span_ge_min": strata.get("tmb_high_rate_span_ge_min"),
            "consumers": ["parallel_ipd_winners", "IPD enrichment design priors"],
            "usage": (
                "Prior-only. Do not merge on patient ID with PDS. "
                "Use for assay-bias-aware TMB cut discussions in IPD protocols."
            ),
        }
        body = json.dumps(payload, sort_keys=True, default=str).encode()
        payload["receipt_sha"] = hashlib.sha256(body).hexdigest()

        artifacts = []
        if self.out_dir:
            self.out_dir.mkdir(parents=True, exist_ok=True)
            outp = self.out_dir / "IPD_PRIOR_HANDOFF.json"
            outp.write_text(json.dumps(payload, indent=2, default=str))
            artifacts.append(str(outp))
            # re-hash file
            payload["receipt_sha"] = hashlib.sha256(outp.read_bytes()).hexdigest()

        return {
            "play": "C",
            "ok": True,
            "handoff": payload,
            "artifacts": artifacts,
            "schema_id": HANDOFF_SCHEMA_ID,
        }
