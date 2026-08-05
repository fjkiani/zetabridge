"""Play B: Driver co-mutation packs × TMB strata — prevalence + OR within GENIE."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Optional

from ..adapter.mutation_stream import DRIVER_PACK_DEFAULT, MutationFlagStream
from ..interfaces import PlayPlugin
from ..specs import Hypothesis


def _or_with_ci(a: int, b: int, c: int, d: int) -> dict[str, Any]:
    """
    OR for 2x2: exposed/outcome table
      TMB-high & mut | TMB-high & ~mut
      ~high & mut    | ~high & ~mut
    Uses Haldane-Anscombe +0.5 correction if any zero.
    """
    if min(a, b, c, d) == 0:
        a, b, c, d = a + 0.5, b + 0.5, c + 0.5, d + 0.5
    or_v = (a / b) / (c / d)
    # Woolf CI
    se = math.sqrt(1 / a + 1 / b + 1 / c + 1 / d)
    lo = math.exp(math.log(or_v) - 1.96 * se)
    hi = math.exp(math.log(or_v) + 1.96 * se)
    return {
        "odds_ratio": or_v,
        "ci95": [lo, hi],
        "table": {"a": a, "b": b, "c": c, "d": d},
        "n": a + b + c + d,
    }


class PlayBDriverComutation(PlayPlugin):
    play_id = "B"

    def __init__(
        self,
        mutation_path: Optional[str] = None,
        genes: tuple[str, ...] = DRIVER_PACK_DEFAULT,
        max_samples_for_stream: Optional[int] = None,
    ):
        self.mutation_path = mutation_path
        self.genes = genes
        self.max_samples_for_stream = max_samples_for_stream
        self._last_stream: dict[str, Any] = {}

    def build_hypotheses(self, cohort: dict[str, Any]) -> list[Hypothesis]:
        cols = set(cohort.get("columns") or [])
        fields = {
            "SAMPLE_ID": "SAMPLE_ID" in cols,
            "tmb_bin": "tmb_bin" in cols,
            "SEQ_ASSAY_ID": "SEQ_ASSAY_ID" in cols,
            "data_mutations_extended.txt": True,  # existence checked at measure()
        }
        return [
            Hypothesis(
                id="H_B1",
                statement=(
                    "KRAS/NRAS/BRAF/TP53/MMR gene co-mutation packs × tissue_panel_TMB "
                    "strata: prevalence + enrichment OR within GENIE (prior only)"
                ),
                play="B",
                biomarker="driver_comutation_pack",
                endpoint="enrichment_or",
                fields_required=list(fields.keys()),
                fields_exist_on_disk=fields,
                claim_class="genie_prior",
                notes="No trial OS claim without IPD",
            ),
            Hypothesis(
                id="H_B2",
                statement=(
                    "Driver×TMB predictive OS enrichment for anti-EGFR / IO "
                    "(REQUIRES IPD — GENIE cannot ADVANCE this alone)"
                ),
                play="B",
                biomarker="driver_comutation_pack",
                endpoint="trial_OS",
                fields_required=["IPD_outcomes"],
                fields_exist_on_disk={"IPD_outcomes": False},
                claim_class="needs_ipd",
                notes="Hand to Lane B IPD scoreboard",
            ),
        ]

    def measure(self, cohort: dict[str, Any]) -> dict[str, Any]:
        table = cohort["table"]
        sample_ids = table.column("SAMPLE_ID").to_pylist()
        tmb_bins = table.column("tmb_bin").to_pylist()
        assays = (
            table.column("SEQ_ASSAY_ID").to_pylist()
            if "SEQ_ASSAY_ID" in table.column_names
            else [None] * len(sample_ids)
        )

        sid_to_meta = {
            str(s): {"tmb_bin": b, "SEQ_ASSAY_ID": a}
            for s, b, a in zip(sample_ids, tmb_bins, assays)
            if s is not None
        }

        streamer = MutationFlagStream(self.mutation_path)
        # Gene-filtered DuckDB stream (no full pandas MAF). Join to CRC matrix in Python.
        # Avoid pushing 24k SAMPLE_ID IN-list into DuckDB — gene filter alone is enough.
        stream = streamer.stream_flags(
            sample_ids=None,
            genes=self.genes,
            limit_samples=self.max_samples_for_stream,
        )
        self._last_stream = stream
        if not stream.get("ok"):
            return {"play": "B", "ok": False, "error": stream.get("error"), "stream": stream}

        # Index flags by SAMPLE_ID for O(1) join to matrix
        flags_by_sid = {
            rec["SAMPLE_ID"]: rec for rec in (stream.get("flag_rows") or [])
        }

        # Denominator = all matrix samples (mut=0 if absent from flag stream)
        or_by_gene = {}
        prev_by_gene_tmb = defaultdict(lambda: {"high": [0, 0], "other": [0, 0]})
        # high: [mut, total]; other: [mut, total]

        for sid, meta in sid_to_meta.items():
            rec = flags_by_sid.get(sid) or {}
            high = meta["tmb_bin"] == "High (>16)"
            key = "high" if high else "other"
            for g in self.genes:
                mut = int(rec.get(f"mut_{g}", 0)) if rec else 0
                prev_by_gene_tmb[g][key][1] += 1
                prev_by_gene_tmb[g][key][0] += mut

        for g, d in prev_by_gene_tmb.items():
            # a=mut&high, b=~mut&high, c=mut&~high, d=~mut&~high
            mut_h, tot_h = d["high"]
            mut_o, tot_o = d["other"]
            a = mut_h
            b = tot_h - mut_h
            c = mut_o
            dlt = tot_o - mut_o
            or_by_gene[g] = {
                **_or_with_ci(a, b, c, dlt),
                "prevalence_tmb_high": (mut_h / tot_h) if tot_h else None,
                "prevalence_not_high": (mut_o / tot_o) if tot_o else None,
                "n_tmb_high": tot_h,
                "n_not_high": tot_o,
            }

        return {
            "play": "B",
            "ok": True,
            "n_flagged_samples_stream": stream.get("n"),
            "n_flagged_samples": sum(1 for s in sid_to_meta if s in flags_by_sid),
            "n_matrix": cohort.get("n"),
            "genes": list(self.genes),
            "or_mut_given_tmb_high_vs_not": or_by_gene,
            "stream_receipt_sha": stream.get("receipt_sha"),
            "method": "duckdb_gene_filter_stream + CRC_matrix_tmb_bin join",
            "claim_limit": "prevalence_and_OR_within_GENIE_only__no_trial_OS",
            "warnings": (stream.get("warnings") or [])
            + [
                "stream n may include non-CRC barcodes; OR denominators are CRC matrix only"
            ],
        }
