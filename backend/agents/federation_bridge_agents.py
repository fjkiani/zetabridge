"""
Federation Bridge Agents — the multi-hop exploitation pipeline
==============================================================
Three BaseAgent subclasses wired into the scaffold Orchestrator DAG:

  SynapseAgent  (Agent 1) : queries Synapse Node A for a gene locus + context
  PDSAgent      (Agent 2) : receives A's payload via orchestrator dependency
                            injection (dep_synapse_agent), translates schema
                            (gene symbol -> PDS biomarker), interrogates PDS Node B
  SynthesisAgent          : deterministic graph traversal over the gene bridge ->
                            ranked, evidence-tagged HYPOTHESES; optional constrained
                            LLM narrative (invents no statistics)

Context is preserved across the hop by the orchestrator's AgentContext.fork()
and working_memory, plus explicit dependency-output injection. Nothing is dropped.

License: Apache-2.0
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from .base import BaseAgent, AgentContext, AgentResult, AgentStatus, DataModality

try:
    from connectors.zeta_synapse_connector import ZetaSynapseConnector
    from connectors.zeta_pds_connector import ZetaPDSConnector
    from federation.bridge_resolver import BridgeResolver
except ImportError:  # repo-root import fallback
    from backend.connectors.zeta_synapse_connector import ZetaSynapseConnector
    from backend.connectors.zeta_pds_connector import ZetaPDSConnector
    from backend.federation.bridge_resolver import BridgeResolver

log = logging.getLogger("zetabridge.federation_agents")


# ── Agent 1: Synapse ─────────────────────────────────────────────────────────
class SynapseAgent(BaseAgent):
    """Agent 1 — queries the Synapse endpoint (Dataset A)."""

    @property
    def name(self) -> str:
        return "synapse_agent"

    @property
    def description(self) -> str:
        return "Queries Synapse (MSK ovarian HGSOC genomics) for a gene locus and its cohort context"

    @property
    def capabilities(self) -> list[str]:
        return ["query_gene", "list_genes", "cohort_summary"]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        action = task.get("action", "query_gene")
        conn = ZetaSynapseConnector()
        if action == "list_genes":
            out = {"genes": sorted(conn.list_genes().keys())}
        elif action == "cohort_summary":
            out = conn.cohort_summary()
        else:  # query_gene
            gene = (task.get("gene") or ctx.working_memory.get("bridge_gene") or "KRAS").upper()
            out = conn.gene_context(gene)
            ctx.working_memory["bridge_gene"] = gene          # preserve for next hop
            ctx.working_memory["synapse_payload"] = out
            ctx.data_refs[f"synapse::{gene}"] = out           # data_refs is a dict
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS, output=out,
            data_modality=DataModality.STRUCTURED, artifacts={"synapse": out},
        )


# ── Agent 2: PDS (consumes Agent 1's payload) ────────────────────────────────
class PDSAgent(BaseAgent):
    """Agent 2 — translates Agent 1's payload and interrogates PDS (Dataset B)."""

    @property
    def name(self) -> str:
        return "pds_agent"

    @property
    def description(self) -> str:
        return "Translates a Synapse gene payload to the PDS schema and interrogates PDS biomarker/trial context"

    @property
    def capabilities(self) -> list[str]:
        return ["interrogate_biomarker", "survival_capability", "trial_summary"]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        conn = ZetaPDSConnector()

        # ── schema translation on the fly ────────────────────────────────
        # Prefer the dependency-injected Synapse payload (orchestrator sets
        # dep_synapse_agent); fall back to shared working_memory.
        dep = task.get("dep_synapse_agent") or ctx.working_memory.get("synapse_payload") or {}
        gene = (task.get("gene") or dep.get("gene") or ctx.working_memory.get("bridge_gene") or "KRAS").upper()

        action = task.get("action", "interrogate_biomarker")
        if action == "survival_capability":
            out = conn.survival_capability()
        elif action == "trial_summary":
            out = conn.trial_summary()
        else:
            biomarker = conn.biomarker_context(gene)
            survival = conn.survival_capability()
            out = {
                "gene": gene,
                "translated_from_synapse": bool(dep),
                "synapse_alteration_classes": dep.get("alteration_classes"),
                "pds_biomarker": biomarker,
                "survival_capability": survival,
            }
            ctx.working_memory["pds_payload"] = out
            ctx.data_refs[f"pds::{gene}"] = out               # data_refs is a dict
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS, output=out,
            data_modality=DataModality.STRUCTURED, artifacts={"pds": out},
        )


# ── Synthesis Agent (consumes both hops) ─────────────────────────────────────
class SynthesisAgent(BaseAgent):
    """Synthesis engine — deterministic hypotheses + optional LLM narrative."""

    @property
    def name(self) -> str:
        return "synthesis_agent"

    @property
    def description(self) -> str:
        return "Traverses the gene bridge to surface ranked, evidence-tagged cross-endpoint hypotheses"

    @property
    def capabilities(self) -> list[str]:
        return ["synthesize", "rank_hypotheses"]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        syn = ZetaSynapseConnector()
        pds = ZetaPDSConnector()
        resolver = BridgeResolver(syn, pds)

        # ── deterministic layer = source of truth ────────────────────────
        bridge_edges = resolver.build_bridge()
        # always log the canonical refusals (patient link + marker->survival)
        resolver.refuse_patient_link()
        resolver.refuse_marker_to_survival()
        for e in bridge_edges:
            resolver.refuse_biological_equivalence(e.bridge_gene)

        hypotheses = self._generate_hypotheses(bridge_edges, pds)
        result = {
            "shared_genes": resolver.shared_genes(),
            "n_bridge_edges": len(bridge_edges),
            "bridge_edges": [e.to_kg_edge() for e in bridge_edges],
            "hypotheses": hypotheses,
            "refusal_ledger": resolver.refusal_ledger(),
        }

        # ── optional constrained narrative (invents no statistics) ────────
        if task.get("narrative", True):
            result["narrative"] = self._narrate(hypotheses, result["refusal_ledger"])

        ctx.working_memory["synthesis"] = result
        return AgentResult(
            agent_name=self.name, status=AgentStatus.SUCCESS, output=result,
            data_modality=DataModality.STRUCTURED, artifacts={"synthesis": result},
        )

    # ── deterministic hypothesis generator ───────────────────────────────
    def _generate_hypotheses(self, bridge_edges, pds_conn) -> list[dict]:
        """Rank hypotheses strictly from graph evidence. No invented stats.

        Ranking key = (has_actionable_signal, evidence_strength, -n_mismatch).
        Every hypothesis carries source node ids, evidence tier, and caveats.
        """
        hyps = []
        for e in bridge_edges:
            gene = e.bridge_gene
            n_mismatch = len(e.mismatch_flags)
            pds_ev = e.evidence.get("pds", {})
            syn_ev = e.evidence.get("synapse", {})
            mut_frac = pds_ev.get("combined_mutant_fraction")

            # signal strength is bounded by what the data supports
            actionable = n_mismatch == 0
            statement = (
                f"{gene} is genotyped on BOTH endpoints, but as "
                f"{'/'.join(e.alteration_class_a)} in {e.disease_context_a} (Synapse) "
                f"vs {'/'.join(e.alteration_class_b)} in {e.disease_context_b} (PDS). "
            )
            if not actionable:
                statement += (
                    "This is a same-gene ontology bridge only; the alteration class "
                    "and disease context differ, so no direct biological inference "
                    "is valid. Testable next step: assay KRAS MUTATION status on the "
                    "MSK ovarian cohort to make the two sides comparable."
                )
                caveats = e.mismatch_flags + [
                    "no patient-level linkage (disjoint cohorts)",
                    "no survival-duration on PDS -> no outcome correlation",
                ]
                tier = e.evidence_tier
            else:
                statement += "Alteration class and disease align; bridge is directly usable."
                caveats = []
                tier = e.evidence_tier

            hyps.append({
                "hypothesis_id": f"HYP-{gene}",
                "bridge_gene": gene,
                "statement": statement,
                "evidence_tier": tier,
                "actionable_without_new_data": actionable,
                "supporting_nodes": {"pds": e.source, "synapse": e.target},
                "synapse_evidence": syn_ev,
                "pds_evidence": {"combined_mutant_fraction": mut_frac,
                                 "trials": pds_ev.get("trials")},
                "mismatch_flags": e.mismatch_flags,
                "caveats": caveats,
                "rank_key": (1 if actionable else 0, 1.0 if mut_frac else 0.0, -n_mismatch),
            })
        hyps.sort(key=lambda h: h["rank_key"], reverse=True)
        for i, h in enumerate(hyps, 1):
            h["rank"] = i
            h.pop("rank_key", None)
        return hyps

    # ── constrained LLM narrative (OpenRouter; degrades gracefully) ───────
    def _narrate(self, hypotheses: list[dict], refusals: list[dict]) -> dict:
        """Rewrite deterministic evidence into prose. The LLM may NOT introduce
        any number or claim absent from `hypotheses`/`refusals`. If no key or
        the call fails, return the deterministic summary verbatim."""
        deterministic_summary = self._deterministic_summary(hypotheses, refusals)
        keys = [os.environ.get(k, "") for k in
                ("OPENROUTER_API_KEY", "OPENROUTER_API_KEY_V2",
                 "OPENROUTER_API_KEY_V3", "OPENROUTER_API_KEY_V4")]
        keys = [k for k in keys if k]
        if not keys:
            return {"mode": "deterministic_only", "text": deterministic_summary}

        import httpx
        model = os.environ.get("SYNTH_LLM_MODEL", "meta-llama/llama-3.3-70b-instruct")
        payload_facts = json.dumps({"hypotheses": hypotheses, "refusals": refusals},
                                   indent=2)[:6000]
        prompt = (
            "You are a data-federation analyst. Using ONLY the JSON facts below, "
            "write a concise narrative (<=180 words) of what the Synapse<->PDS gene "
            "bridge does and does not support. You MUST NOT introduce any number, "
            "gene, disease, or claim not present in the JSON. Explicitly state the "
            "refused joins and why. Cite gene symbols and node ids from the JSON.\n\n"
            f"FACTS:\n{payload_facts}"
        )
        last_err = None
        for api_key in keys:  # try each key until one has credit
            try:
                with httpx.Client(timeout=60.0) as client:
                    resp = client.post(
                        "https://openrouter.ai/api/v1/chat/completions",
                        headers={"Authorization": f"Bearer {api_key}",
                                 "Content-Type": "application/json"},
                        json={"model": model, "temperature": 0,
                              "messages": [{"role": "user", "content": prompt}]},
                    )
                    resp.raise_for_status()
                    text = resp.json()["choices"][0]["message"]["content"].strip()
                return {"mode": "llm_constrained", "model": model, "text": text,
                        "deterministic_fallback": deterministic_summary}
            except Exception as exc:
                last_err = str(exc)
                log.warning("Synthesis LLM key failed (%s), trying next", last_err[:80])
                continue
        return {"mode": "deterministic_only", "text": deterministic_summary,
                "llm_error": last_err}

    @staticmethod
    def _deterministic_summary(hypotheses: list[dict], refusals: list[dict]) -> str:
        lines = []
        for h in hypotheses:
            lines.append(f"[{h['rank']}] {h['statement']}")
        lines.append("")
        lines.append("Refused joins (fabrication-guard):")
        for r in refusals:
            lines.append(f"  - {r['join_class']}: {r['reason']}")
        return "\n".join(lines)
