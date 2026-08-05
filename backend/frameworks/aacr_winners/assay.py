"""AssayStratifier — mandatory SEQ_ASSAY_ID stratification before any TMB claim."""

from __future__ import annotations

from typing import Any, Optional

import pyarrow.compute as pc
import pyarrow as pa


class AssayStratifier:
    """
    Mandatory gate: never claim TMB enrichment without SEQ_ASSAY_ID strata.

    Aligns with genie.assay_tmb_strata.
    """

    def __init__(
        self,
        assay_col: str = "SEQ_ASSAY_ID",
        tmb_bin_col: str = "tmb_bin",
        tmb_mut_per_mb_col: str = "tmb_mut_per_mb",
        high_label: str = "High (>16)",
        min_n: int = 100,
    ):
        self.assay_col = assay_col
        self.tmb_bin_col = tmb_bin_col
        self.tmb_mut_per_mb_col = tmb_mut_per_mb_col
        self.high_label = high_label
        self.min_n = min_n

    def stratify(self, table: pa.Table) -> dict[str, Any]:
        cols = table.column_names
        if self.assay_col not in cols:
            return {
                "ok": False,
                "error": f"MISSING_ASSAY_COL: {self.assay_col} required before TMB claims",
                "n": 0,
                "strata": [],
            }
        if self.tmb_bin_col not in cols:
            return {
                "ok": False,
                "error": f"MISSING_TMB_BIN: {self.tmb_bin_col}",
                "n": 0,
                "strata": [],
            }

        assays = table.column(self.assay_col).to_pylist()
        bins = table.column(self.tmb_bin_col).to_pylist()
        tmbs = (
            table.column(self.tmb_mut_per_mb_col).to_pylist()
            if self.tmb_mut_per_mb_col in cols
            else [None] * len(assays)
        )

        from collections import defaultdict

        buckets: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"n": 0, "n_tmb_high": 0, "tmb_vals": []}
        )
        for assay, b, t in zip(assays, bins, tmbs):
            if assay is None or str(assay).strip() == "":
                continue
            key = str(assay)
            buckets[key]["n"] += 1
            if b == self.high_label:
                buckets[key]["n_tmb_high"] += 1
            if t is not None:
                try:
                    buckets[key]["tmb_vals"].append(float(t))
                except (TypeError, ValueError):
                    pass

        strata = []
        for assay, d in buckets.items():
            n = d["n"]
            n_high = d["n_tmb_high"]
            vals = sorted(d["tmb_vals"])
            med = vals[len(vals) // 2] if vals else None
            strata.append(
                {
                    "SEQ_ASSAY_ID": assay,
                    "n": n,
                    "n_tmb_high": n_high,
                    "tmb_high_rate": (n_high / n) if n else None,
                    "median_tmb_mut_per_mb": med,
                }
            )
        strata.sort(key=lambda x: (-x["n"], x["SEQ_ASSAY_ID"]))
        ge_min = [s for s in strata if s["n"] >= self.min_n]

        # Assay bias signal: wide dispersion of high-rate across large assays
        rates = [s["tmb_high_rate"] for s in ge_min if s["tmb_high_rate"] is not None]
        rate_span = (max(rates) - min(rates)) if len(rates) >= 2 else 0.0

        return {
            "ok": True,
            "n": table.num_rows,
            "assay_col": self.assay_col,
            "tmb_label": "tissue_panel_TMB",
            "NOT_guardant_ptmb": True,
            "min_n": self.min_n,
            "n_assays_total": len(strata),
            "n_assays_ge_min": len(ge_min),
            "assay_strata_n_ge_min": ge_min,
            "assay_strata_all": strata,
            "tmb_high_rate_span_ge_min": rate_span,
            "assay_bias_flag": rate_span >= 0.25,
            "method": "SEQ_ASSAY_ID_groupby_tmb_bin_high",
        }
