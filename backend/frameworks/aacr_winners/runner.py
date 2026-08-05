"""EnrichmentHypothesisFramework runner — orchestrates plays → kills → scoreboard."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .adapter import GenieMatrixAdapter
from .biomarker_endpoint import define_biomarker, define_endpoint
from .kill_battery import FrameworkKillBattery
from .plays import PlayAAssayTMB, PlayBDriverComutation, PlayCPriorExport
from .score_pick import pick as pick_fn
from .score_pick import score as score_fn
from .specs import Hypothesis, KillTest, Pick, PreReg, ScorecardRow

SCHEMA_VERSION = "aacr-winners-framework/0.1.0"


def _sha(obj: Any) -> str:
    return hashlib.sha256(
        json.dumps(obj, sort_keys=True, default=str).encode()
    ).hexdigest()


def _md_scoreboard(rows: list[ScorecardRow], built: str) -> str:
    lines = [
        "# WINNERS SCOREBOARD — AACR Framework Lane",
        "",
        f"**Built UTC:** {built}",
        f"**Schema:** `{SCHEMA_VERSION}`",
        "**NOT_8D04_unblock:** true · **NOT_genie_pds_join_required:** true",
        "**RUO:** Research Use Only.",
        "",
        "---",
        "",
        "## Decisions",
        "",
        "| ID | Play | Hypothesis | Verdict | Why (measured) | Money? |",
        "|----|------|------------|---------|----------------|--------|",
    ]
    for r in rows:
        hyp = r.hypothesis.replace("|", "/")[:120]
        lines.append(
            f"| {r.id} | {r.play} | {hyp} | **{r.verdict.value}** | {r.why_measured[:160]} | {'No' if not r.money else 'YES'} |"
        )
    lines += [
        "",
        "## ADVANCE as GENIE-priors vs need IPD",
        "",
        "- **ADVANCE_AS_PRIOR:** assay-calibrated TMB prevalence + driver×TMB OR tables → feed IPD lane",
        "- **NEED IPD:** any predictive OS / treatment×marker claim (H_B2)",
        "- **KILL (negative control):** prognostic DEAD×TMB (H_A2) — not a money pick",
        "",
        "## Explicit non-claims",
        "",
        "- No 8D-04 soft-unblock",
        "- No GuardantOMNI plasma pTMB",
        "- No invented MSI",
        "- No fake GENIE×PDS patient merge",
        "- No trial OS from GENIE alone",
        "",
        "**RUO:** Research enrichment / IPD engineering only. Not clinical care.",
        "",
    ]
    return "\n".join(lines)


def _md_kills(kills: list[KillTest]) -> str:
    lines = [
        "# FALSE WINNER KILLS — AACR Framework",
        "",
        "| Hypothesis | Kill | Verdict | n | Method |",
        "|------------|------|---------|---|--------|",
    ]
    for k in kills:
        lines.append(
            f"| {k.hypothesis_id} | {k.kill_name} | {k.verdict.value} | {k.n} | {k.method} |"
        )
    lines.append("")
    return "\n".join(lines)


def _md_hypotheses(hyps: list[Hypothesis]) -> str:
    lines = [
        "# CANDIDATE ENRICHMENT HYPOTHESES — AACR Framework",
        "",
        f"**Count:** {len(hyps)} (≤5)",
        "",
    ]
    for h in hyps:
        lines += [
            f"## {h.id} (Play {h.play})",
            "",
            f"- **Statement:** {h.statement}",
            f"- **Claim class:** `{h.claim_class}`",
            f"- **Biomarker:** {h.biomarker}",
            f"- **Endpoint:** {h.endpoint}",
            f"- **Fields on disk:** {json.dumps(h.fields_exist_on_disk)}",
            f"- **Notes:** {h.notes}",
            "",
        ]
    return "\n".join(lines)


def _md_money(p: Pick) -> str:
    lines = [
        "# WINNERS FOR MONEY — AACR Framework (ADVANCE / ADVANCE_AS_PRIOR only)",
        "",
        "**NOTE:** GENIE framework lane advances **priors**, not standalone IPD money OS lifts.",
        "",
    ]
    if not p.advances:
        lines.append("_No ADVANCE rows._")
    for a in p.advances:
        lines += [
            f"## {a.id}",
            f"- Verdict: **{a.verdict.value}**",
            f"- {a.hypothesis}",
            f"- Why: {a.why_measured}",
            "",
        ]
    lines += ["## DAR / IPD gaps", ""]
    for g in p.dar_ipd_gaps:
        lines.append(f"- **{g['gap']}** — `{g['status']}` — blocks: {g['blocks']}")
    lines += ["", "## Non-claims", ""]
    for c in p.explicit_non_claims:
        lines.append(f"- {c}")
    lines.append("")
    return "\n".join(lines)


class EnrichmentHypothesisFramework:
    """
    Interfaces: load_cohort → define_biomarker/endpoint → plays → kill → score → pick.
    """

    def __init__(
        self,
        matrix_path: Optional[str] = None,
        mutation_path: Optional[str] = None,
        artifact_dir: Optional[str | Path] = None,
        skip_mutation_stream: bool = False,
    ):
        self.matrix_adapter = GenieMatrixAdapter(matrix_path)
        self.mutation_path = mutation_path
        self.artifact_dir = Path(artifact_dir) if artifact_dir else None
        self.skip_mutation_stream = skip_mutation_stream
        self.kill_battery = FrameworkKillBattery()
        self.prereg = PreReg(
            biomarker_cuts=[
                "tmb_bin High (>16) within SEQ_ASSAY_ID",
                "driver gene flags (KRAS/NRAS/BRAF/TP53/MMR pack)",
            ],
            pre_reg_seed=42,
        )

    def load_cohort(self) -> dict[str, Any]:
        return self.matrix_adapter.load_cohort()

    def define_biomarker(self, name: str, cohort: dict[str, Any], **kw: Any) -> dict[str, Any]:
        return define_biomarker(name, cohort, **kw)

    def define_endpoint(self, name: str, cohort: dict[str, Any], **kw: Any) -> dict[str, Any]:
        return define_endpoint(name, cohort, **kw)

    def run(self) -> dict[str, Any]:
        built = datetime.now(timezone.utc).isoformat()
        cohort = self.load_cohort()

        # Seal pre-reg before scoring
        prereg_dict = self.prereg.to_dict()

        play_a = PlayAAssayTMB()
        play_c = PlayCPriorExport(out_dir=self.artifact_dir)
        plays_measure: dict[str, Any] = {}

        hyps: list[Hypothesis] = []
        hyps.extend(play_a.build_hypotheses(cohort))
        plays_measure["A"] = play_a.measure(cohort)

        if self.skip_mutation_stream:
            play_b = PlayBDriverComutation(mutation_path=self.mutation_path)
            hyps.extend(play_b.build_hypotheses(cohort))
            plays_measure["B"] = {
                "play": "B",
                "ok": False,
                "error": "SKIPPED_mutation_stream_by_flag",
                "claim_limit": "prevalence_and_OR_within_GENIE_only__no_trial_OS",
            }
        else:
            play_b = PlayBDriverComutation(mutation_path=self.mutation_path)
            hyps.extend(play_b.build_hypotheses(cohort))
            plays_measure["B"] = play_b.measure(cohort)

        hyps.extend(play_c.build_hypotheses(cohort))
        plays_measure["C"] = play_c.measure(cohort)

        if len(hyps) > 5:
            hyps = hyps[:5]

        kills = self.kill_battery.run_kill_battery(
            hyps, self.prereg, cohort, plays_measure
        )
        rows = score_fn(hyps, kills)
        money = pick_fn(rows)

        result = {
            "ok": True,
            "schema_version": SCHEMA_VERSION,
            "built_utc": built,
            "pre_reg": prereg_dict,
            "cohort": {
                "n": cohort.get("n"),
                "matrix_path": cohort.get("matrix_path"),
                "sha256_parquet": cohort.get("sha256_parquet"),
                "msi_available": cohort.get("msi_available"),
                "msi_non_null": cohort.get("msi_non_null"),
                "tmb_label": cohort.get("tmb_label"),
                "IS_GUARDANT_PTMB": cohort.get("IS_GUARDANT_PTMB"),
                "NOT_8D04_unblock": True,
            },
            "hypotheses": [h.to_dict() for h in hyps],
            "kills": [k.to_dict() for k in kills],
            "scoreboard": [r.to_dict() for r in rows],
            "pick": money.to_dict(),
            "play_measures": {
                k: {kk: vv for kk, vv in v.items() if kk != "flag_rows"}
                for k, v in plays_measure.items()
            },
            "artifacts": [],
            "warnings": list(cohort.get("warnings") or []),
            "receipt_sha": None,
            "n": len(hyps),
        }
        # drop huge nested table from measures if any
        if "A" in result["play_measures"]:
            a = result["play_measures"]["A"]
            if isinstance(a.get("assay_strata"), dict):
                # keep ge_min only for compactness
                strata = a["assay_strata"]
                a["assay_strata"] = {
                    "ok": strata.get("ok"),
                    "n": strata.get("n"),
                    "n_assays_ge_min": strata.get("n_assays_ge_min"),
                    "tmb_high_rate_span_ge_min": strata.get("tmb_high_rate_span_ge_min"),
                    "assay_bias_flag": strata.get("assay_bias_flag"),
                    "assay_strata_n_ge_min": strata.get("assay_strata_n_ge_min"),
                }

        result["receipt_sha"] = _sha(
            {
                "scoreboard": result["scoreboard"],
                "kills": result["kills"],
                "cohort_n": result["cohort"]["n"],
            }
        )

        if self.artifact_dir:
            self._write_artifacts(result, hyps, kills, rows, money, built)

        return result

    def _write_artifacts(
        self,
        result: dict[str, Any],
        hyps: list[Hypothesis],
        kills: list[KillTest],
        rows: list[ScorecardRow],
        money: Pick,
        built: str,
    ) -> None:
        d = self.artifact_dir
        assert d is not None
        d.mkdir(parents=True, exist_ok=True)

        # W0
        import yaml  # optional; fallback json

        w0 = d / "WINNER_DEFINITION.yaml"
        try:
            import yaml as _yaml

            w0.write_text(_yaml.safe_dump(result["pre_reg"], sort_keys=False))
        except Exception:
            w0.write_text(json.dumps(result["pre_reg"], indent=2))

        (d / "CANDIDATE_ENRICHMENT_HYPOTHESES.md").write_text(_md_hypotheses(hyps))
        (d / "CANDIDATE_ENRICHMENT_HYPOTHESES.json").write_text(
            json.dumps([h.to_dict() for h in hyps], indent=2)
        )
        (d / "FALSE_WINNER_KILLS.md").write_text(_md_kills(kills))
        (d / "FALSE_WINNER_KILLS.json").write_text(
            json.dumps([k.to_dict() for k in kills], indent=2)
        )
        (d / "WINNERS_SCOREBOARD.md").write_text(_md_scoreboard(rows, built))
        (d / "WINNERS_SCOREBOARD.json").write_text(
            json.dumps(
                {
                    "built_utc": built,
                    "schema_version": SCHEMA_VERSION,
                    "receipt_sha": result["receipt_sha"],
                    "rows": [r.to_dict() for r in rows],
                    "NOT_8D04_unblock": True,
                },
                indent=2,
            )
        )
        (d / "WINNERS_FOR_MONEY.md").write_text(_md_money(money))
        (d / "FRAMEWORK_RUN_RECEIPT.json").write_text(
            json.dumps(result, indent=2, default=str)
        )

        # Play B measure snippet
        if "B" in result.get("play_measures", {}):
            (d / "PLAY_B_DRIVER_TMB_OR.json").write_text(
                json.dumps(result["play_measures"]["B"], indent=2, default=str)
            )

        arts = [
            "WINNER_DEFINITION.yaml",
            "CANDIDATE_ENRICHMENT_HYPOTHESES.md",
            "CANDIDATE_ENRICHMENT_HYPOTHESES.json",
            "FALSE_WINNER_KILLS.md",
            "FALSE_WINNER_KILLS.json",
            "WINNERS_SCOREBOARD.md",
            "WINNERS_SCOREBOARD.json",
            "WINNERS_FOR_MONEY.md",
            "FRAMEWORK_RUN_RECEIPT.json",
            "IPD_PRIOR_HANDOFF.json",
            "PLAY_B_DRIVER_TMB_OR.json",
        ]
        result["artifacts"] = [str(d / a) for a in arts if (d / a).exists()]


def run_framework(
    artifact_dir: str | Path,
    matrix_path: Optional[str] = None,
    mutation_path: Optional[str] = None,
    skip_mutation_stream: bool = False,
) -> dict[str, Any]:
    fw = EnrichmentHypothesisFramework(
        matrix_path=matrix_path,
        mutation_path=mutation_path,
        artifact_dir=artifact_dir,
        skip_mutation_stream=skip_mutation_stream,
    )
    return fw.run()


def main() -> None:
    import argparse

    p = argparse.ArgumentParser(description="AACR GENIE winners framework runner (RUO)")
    p.add_argument(
        "--artifact-dir",
        required=True,
        help="Brenus aacr_framework_winners output dir",
    )
    p.add_argument("--matrix", default=None)
    p.add_argument("--mutations", default=None)
    p.add_argument(
        "--skip-mutation-stream",
        action="store_true",
        help="Skip DuckDB MAF stream (still emits H_B hypotheses as needs_ipd/blocked)",
    )
    args = p.parse_args()
    out = run_framework(
        args.artifact_dir,
        matrix_path=args.matrix,
        mutation_path=args.mutations,
        skip_mutation_stream=args.skip_mutation_stream,
    )
    print(
        json.dumps(
            {
                "ok": out["ok"],
                "n": out["n"],
                "receipt_sha": out["receipt_sha"],
                "scoreboard": out["scoreboard"],
                "artifacts": out.get("artifacts"),
            },
            indent=2,
            default=str,
        )
    )


if __name__ == "__main__":
    main()
