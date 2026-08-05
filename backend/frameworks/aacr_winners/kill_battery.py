"""Adversarial kill battery (W2) — framework implementation."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

import pyarrow as pa

from .assay import AssayStratifier
from .specs import Hypothesis, KillTest, KillVerdict, PreReg


def _dead_assoc_by_tmb_high(table: pa.Table) -> dict[str, Any]:
    """Prognostic-only check: TMB-high vs DEAD (no treatment contrast)."""
    cols = table.column_names
    if "DEAD" not in cols or "tmb_bin" not in cols:
        return {"ok": False, "error": "missing DEAD or tmb_bin"}

    dead = table.column("DEAD").to_pylist()
    bins = table.column("tmb_bin").to_pylist()
    # normalize DEAD to 0/1
    def as01(x):
        if x is None:
            return None
        if isinstance(x, bool):
            return int(x)
        s = str(x).strip().upper()
        if s in ("1", "TRUE", "YES", "DEAD", "DECEASED"):
            return 1
        if s in ("0", "FALSE", "NO", "ALIVE"):
            return 0
        try:
            return int(float(s))
        except Exception:
            return None

    high_dead = high_n = low_dead = low_n = 0
    for d, b in zip(dead, bins):
        v = as01(d)
        if v is None:
            continue
        if b == "High (>16)":
            high_n += 1
            high_dead += v
        else:
            low_n += 1
            low_dead += v
    rate_high = high_dead / high_n if high_n else None
    rate_other = low_dead / low_n if low_n else None
    return {
        "ok": True,
        "n_high": high_n,
        "n_other": low_n,
        "dead_rate_tmb_high": rate_high,
        "dead_rate_not_high": rate_other,
        "delta_dead_rate": (
            (rate_high - rate_other)
            if rate_high is not None and rate_other is not None
            else None
        ),
        "treatment_contrast": "BLOCKED",
    }


class FrameworkKillBattery:
    """Minimum kills: assay bias, prognostic-only, label leakage, join blocked, fit blocked."""

    def run_kill_battery(
        self,
        hypotheses: list[Hypothesis],
        prereg: PreReg,
        cohort: dict[str, Any],
        play_measures: dict[str, dict[str, Any]],
    ) -> list[KillTest]:
        table: pa.Table = cohort["table"]
        strat = AssayStratifier().stratify(table)
        progn = _dead_assoc_by_tmb_high(table)
        out: list[KillTest] = []

        for h in hypotheses:
            # 1) Assay / panel bias
            if "tmb" in h.biomarker.lower() or h.play == "A":
                if not strat.get("ok"):
                    out.append(
                        KillTest(
                            h.id,
                            "assay_panel_bias",
                            KillVerdict.BLOCKED,
                            n=0,
                            method="AssayStratifier",
                            error=strat.get("error"),
                        )
                    )
                elif strat.get("assay_bias_flag"):
                    # Bias present is expected — PASS when hypothesis is assay-stratified prior.
                    # FAIL only if hypothesis claims an assay-agnostic TMB winner.
                    claims_agnostic = (
                        "assay-agnostic enrichment winner" in h.statement.lower()
                        and "not an assay-agnostic" not in h.statement.lower()
                    ) or (
                        h.claim_class == "needs_ipd"
                        and "tmb" in h.biomarker.lower()
                    )
                    if claims_agnostic:
                        out.append(
                            KillTest(
                                h.id,
                                "assay_panel_bias",
                                KillVerdict.FAIL,
                                n=strat.get("n"),
                                method="tmb_high_rate_span>=0.25",
                                detail={
                                    "span": strat.get("tmb_high_rate_span_ge_min"),
                                    "note": "assay-agnostic TMB claim killed",
                                },
                            )
                        )
                    else:
                        out.append(
                            KillTest(
                                h.id,
                                "assay_panel_bias",
                                KillVerdict.PASS,
                                n=strat.get("n"),
                                method="stratified_prior_acknowledges_bias",
                                detail={
                                    "span": strat.get("tmb_high_rate_span_ge_min"),
                                    "n_assays_ge_min": strat.get("n_assays_ge_min"),
                                },
                            )
                        )
                else:
                    out.append(
                        KillTest(
                            h.id,
                            "assay_panel_bias",
                            KillVerdict.PASS,
                            n=strat.get("n"),
                            method="AssayStratifier",
                            detail={"span": strat.get("tmb_high_rate_span_ge_min")},
                        )
                    )
            else:
                out.append(
                    KillTest(
                        h.id,
                        "assay_panel_bias",
                        KillVerdict.PASS,
                        n=cohort.get("n"),
                        method="n/a_non_tmb",
                        detail={"note": "non-TMB hypothesis"},
                    )
                )

            # 2) Prognostic-only check
            if h.endpoint.lower() in ("dead", "os_proxy_dead", "prognostic_dead") or (
                h.play == "A" and "prognostic" in h.statement.lower()
            ):
                if not progn.get("ok"):
                    out.append(
                        KillTest(
                            h.id,
                            "prognostic_only",
                            KillVerdict.BLOCKED,
                            error=progn.get("error"),
                            method="DEAD_x_tmb_high",
                        )
                    )
                else:
                    # If hypothesis is labeled NEGATIVE control → PASS (expected kill pattern documented)
                    if h.claim_class == "negative_control":
                        out.append(
                            KillTest(
                                h.id,
                                "prognostic_only",
                                KillVerdict.PASS,
                                n=(progn.get("n_high") or 0) + (progn.get("n_other") or 0),
                                method="NEGATIVE_CONTROL_documented",
                                detail=progn,
                            )
                        )
                    else:
                        # Claiming enrichment winner from DEAD alone → FAIL
                        out.append(
                            KillTest(
                                h.id,
                                "prognostic_only",
                                KillVerdict.FAIL,
                                n=(progn.get("n_high") or 0) + (progn.get("n_other") or 0),
                                method="DEAD_without_treatment_contrast",
                                detail=progn,
                            )
                        )
            else:
                # Predictive OS claim without IPD
                if h.claim_class == "needs_ipd" and "os" in h.endpoint.lower():
                    out.append(
                        KillTest(
                            h.id,
                            "prognostic_only",
                            KillVerdict.FAIL,
                            n=0,
                            method="trial_OS_requires_IPD",
                            detail={"endpoint": h.endpoint},
                        )
                    )
                else:
                    out.append(
                        KillTest(
                            h.id,
                            "prognostic_only",
                            KillVerdict.PASS,
                            n=cohort.get("n"),
                            method="prior_or_prevalence_not_os_claim",
                        )
                    )

            # 3) Label leakage / messy endpoint
            if h.fields_exist_on_disk and not all(h.fields_exist_on_disk.values()):
                missing = [k for k, v in h.fields_exist_on_disk.items() if not v]
                out.append(
                    KillTest(
                        h.id,
                        "label_leakage_messy_endpoint",
                        KillVerdict.FAIL,
                        n=0,
                        method="required_fields_missing",
                        detail={"missing": missing},
                    )
                )
            else:
                # MSI invention check
                if "msi" in h.biomarker.lower() and not cohort.get("msi_available"):
                    out.append(
                        KillTest(
                            h.id,
                            "label_leakage_messy_endpoint",
                            KillVerdict.FAIL,
                            n=cohort.get("msi_non_null", 0),
                            method="invented_or_null_MSI",
                        )
                    )
                else:
                    out.append(
                        KillTest(
                            h.id,
                            "label_leakage_messy_endpoint",
                            KillVerdict.PASS,
                            n=cohort.get("n"),
                            method="fields_present_no_msi_invention",
                        )
                    )

            # 4) ID-join GENIE ∩ PDS — framework lane does NOT require join
            out.append(
                KillTest(
                    h.id,
                    "genie_pds_id_join",
                    KillVerdict.PASS,
                    n=0,
                    method="JOIN_NOT_REQUIRED_for_genie_priors",
                    detail={
                        "note": "Lane uses GENIE priors + separate IPD; JOIN_IMPOSSIBLE documented elsewhere",
                        "NOT_genie_pds_join": True,
                    },
                )
            )

            # 5) fit_A / fit_B vs outcome — BLOCKED (no soft-unblock 8D-04)
            out.append(
                KillTest(
                    h.id,
                    "fit_A_fit_B_vs_outcome",
                    KillVerdict.BLOCKED,
                    n=0,
                    method="NOT_8D04_unblock",
                    detail={"NOT_8D04_unblock": True},
                    error="BLOCKED: PATH fit ranks not computed in this framework lane",
                )
            )

            # 6) Play-B measure gate: driver OR table must actually compute
            if h.play == "B" and h.claim_class == "genie_prior":
                bmeas = play_measures.get("B") or {}
                if not bmeas.get("ok"):
                    out.append(
                        KillTest(
                            h.id,
                            "play_b_measure",
                            KillVerdict.FAIL,
                            n=0,
                            method="mutation_stream_or_join",
                            error=str(bmeas.get("error") or "play_B_measure_failed"),
                        )
                    )
                else:
                    out.append(
                        KillTest(
                            h.id,
                            "play_b_measure",
                            KillVerdict.PASS,
                            n=bmeas.get("n_flagged_samples"),
                            method="duckdb_stream_or_tables",
                            detail={
                                "n_genes": len(bmeas.get("genes") or []),
                                "stream_receipt_sha": bmeas.get("stream_receipt_sha"),
                            },
                        )
                    )

        return out
