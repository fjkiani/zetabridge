"""Signal-Intelligence agents (Session 14).

Three domain agents built on the existing BaseAgent framework. They are the
"what does the data mean / strongest signals / what pharma got wrong" reasoning
layer, distinct from the generic platform-ops agents (Catalog/Query/Lineage/...).

Design contract (matches the approved plan):
  - DETERMINISTIC + GROUNDED. Every fact and number comes from ``SignalService``
    (real Cypher reads). The agents never invent values.
  - Each result carries a ``grounding`` list of the node/edge ids the findings
    came from, and a deterministic template ``summary``.
  - OPTIONAL Groq narration: if a GROQ_API_KEY is configured, ``summary`` is
    replaced by an LLM paraphrase of the grounded findings — but it is passed
    through a fabrication guard that rejects any number not present in the
    grounded numeric set, falling back to the deterministic summary on failure.

Agents:
  - SignalMinerAgent  — ranks & explains the strongest AE/pharmacovig signals.
  - BridgeHunterAgent — finds & explains cross-endpoint connections (genomics ->
    clinical toxicity), i.e. "search for opportunities / how they connect".
  - GapAuditorAgent   — surfaces blind spots (KBGaps) = "what pharma got wrong".

License: Apache-2.0
"""

from __future__ import annotations

import os
import re
from typing import Any

from agents.base import AgentContext, AgentResult, AgentStatus, BaseAgent, DataModality


# ── grounding / fabrication guard ──────────────────────────────────────────────

def _numbers_in(text: str) -> set[str]:
    """Extract numeric tokens from text as normalized strings (e.g. '0.63', '999', '51.3')."""
    out = set()
    for m in re.findall(r"-?\d+(?:\.\d+)?", text or ""):
        try:
            f = float(m)
            # normalize: drop trailing .0
            out.add(str(int(f)) if f == int(f) else str(round(f, 4)))
        except ValueError:
            pass
    return out


def _grounded_numbers(findings: list[dict]) -> set[str]:
    """All numeric values that appear anywhere in the grounded findings."""
    out: set[str] = set()

    def walk(v: Any):
        if isinstance(v, dict):
            for x in v.values():
                walk(x)
        elif isinstance(v, (list, tuple)):
            for x in v:
                walk(x)
        elif isinstance(v, (int, float)):
            f = float(v)
            out.add(str(int(f)) if f == int(f) else str(round(f, 4)))
        elif isinstance(v, str):
            out.update(_numbers_in(v))

    walk(findings)
    return out


def _fabrication_ok(summary: str, findings: list[dict]) -> bool:
    """True iff every number in the summary is present in the grounded findings.

    Small integers 0-31 (years, hop counts, ranks, small counts) are always
    allowed because narration legitimately uses them for phrasing.
    """
    grounded = _grounded_numbers(findings)
    for tok in _numbers_in(summary):
        if tok in grounded:
            continue
        try:
            if 0 <= float(tok) <= 31 and float(tok) == int(float(tok)):
                continue
        except ValueError:
            pass
        return False
    return True


def _maybe_narrate(prompt_facts: str, findings: list[dict], deterministic: str) -> tuple[str, bool]:
    """Optionally produce an LLM narration over grounded facts. Returns
    (summary, used_llm). Falls back to the deterministic summary if the key is
    absent, the call fails, or the output fails the fabrication guard."""
    key = os.environ.get("GROQ_API_KEY", "")
    if not key:
        return deterministic, False
    try:
        from groq import Groq  # optional dependency
        client = Groq(api_key=key)
        model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
        sys_prompt = (
            "You are a biomedical data analyst. Summarize ONLY the facts given. "
            "Do NOT introduce any number that is not in the facts. Be concise (<=120 words). "
            "No preamble, no markdown headers."
        )
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": sys_prompt},
                {"role": "user", "content": prompt_facts},
            ],
            temperature=0.2,
            max_tokens=320,
        )
        text = (resp.choices[0].message.content or "").strip()
        if text and _fabrication_ok(text, findings):
            return text, True
        return deterministic, False
    except Exception:
        return deterministic, False


# ── base for signal agents ─────────────────────────────────────────────────────

class _SignalAgentBase(BaseAgent):
    """Shared plumbing: holds a SignalService, standard result envelope."""

    def __init__(self, service):
        super().__init__()
        self._svc = service

    def _ok(self, findings: list[dict], summary: str, used_llm: bool,
            grounding: list[str], extra: dict | None = None) -> AgentResult:
        output = {
            "summary": summary,
            "findings": findings,
            "grounding": grounding,
            "used_llm": used_llm,
        }
        if extra:
            output.update(extra)
        return AgentResult(
            agent_name=self.name,
            status=AgentStatus.SUCCESS,
            output=output,
            data_modality=DataModality.STRUCTURED,
            artifacts={"findings": findings, "grounding": grounding},
        )


# ── SignalMiner ────────────────────────────────────────────────────────────────

class SignalMinerAgent(_SignalAgentBase):
    @property
    def name(self) -> str:
        return "signal_miner"

    @property
    def description(self) -> str:
        return ("Ranks and explains the strongest adverse-event and pharmacovigilance signals "
                "across the federated trial data, grounded in real graph nodes.")

    @property
    def capabilities(self) -> list[str]:
        return ["rank_signals", "explain_signal", "drug_toxicity_profile"]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        action = task.get("action", "rank")
        family = task.get("family", "all")
        limit = int(task.get("limit", 8))

        if action == "explain":
            slug = task.get("slug", "")
            detail = self._svc.signal_detail(slug)
            if not detail:
                return AgentResult(agent_name=self.name, status=AgentStatus.FAILED,
                                   error=f"Signal not found: {slug}")
            findings = [detail]
            grounding = [slug] + [n["id"] for n in detail["connections"]["nodes"] if n.get("id")]
            det = (f"{detail.get('name')}: "
                   + (detail["attrs"].get("interpretation") or
                      f"connects {len(detail['connections']['nodes'])} nodes across the graph."))
            summary, used = _maybe_narrate(str(detail), findings, det)
            return self._ok(findings, summary, used, grounding)

        # rank
        res = self._svc.top_signals(family=family, limit=limit)
        sigs = res["signals"]
        findings = sigs
        grounding = [s["slug"] for s in sigs if s.get("slug")]
        if sigs:
            top = sigs[0]
            det = (f"Strongest signal: {top['name']} "
                   f"({top['native_metric']}={top['native_value']}). "
                   f"Showing top {len(sigs)} of {res['count']} "
                   f"{'signals' if family=='all' else family} signals ranked by "
                   f"{'cross-family strength' if family=='all' else top['native_metric']}.")
        else:
            det = "No signals found for that family."
        facts = "Strongest signals:\n" + "\n".join(
            f"- {s['name']}: {s['native_metric']}={s['native_value']} (endpoint {s['endpoint']})"
            for s in sigs)
        summary, used = _maybe_narrate(facts, findings, det)
        return self._ok(findings, summary, used, grounding, extra={"family": family, "count": res["count"]})


# ── BridgeHunter ─────────────────────────────────────────────────────────────

class BridgeHunterAgent(_SignalAgentBase):
    @property
    def name(self) -> str:
        return "bridge_hunter"

    @property
    def description(self) -> str:
        return ("Finds and explains cross-endpoint connections — how MSK genomics link to SAS "
                "clinical toxicity (GenomicAEBridge) — the federated 'opportunity' signal.")

    @property
    def capabilities(self) -> list[str]:
        return ["list_bridges", "explain_bridge", "cross_endpoint_paths"]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        limit = int(task.get("limit", 10))
        res = self._svc.bridges()
        bridges = res["bridges"][:limit]
        findings = bridges
        grounding: list[str] = []
        for b in bridges:
            grounding.extend([x for x in b.get("path", []) if x])
        if bridges:
            top = bridges[0]
            det = (f"Strongest genomic-to-clinical bridge: {top['gene']} -> {top['ae_term']} "
                   f"(bridge_score={top['bridge_score']}). This links the MSK genomic endpoint "
                   f"({top['gene_node']['id']}) to the SAS clinical adverse-event endpoint "
                   f"({top['ae_node']['id']}). {res['count']} such cross-endpoint bridges exist.")
        else:
            det = "No cross-endpoint bridges found."
        facts = "Genomic->clinical bridges:\n" + "\n".join(
            f"- {b['gene']} -> {b['ae_term']}: bridge_score={b['bridge_score']}" for b in bridges)
        summary, used = _maybe_narrate(facts, findings, det)
        return self._ok(findings, summary, used, grounding, extra={"count": res["count"]})


# ── GapAuditor ─────────────────────────────────────────────────────────────

class GapAuditorAgent(_SignalAgentBase):
    @property
    def name(self) -> str:
        return "gap_auditor"

    @property
    def description(self) -> str:
        return ("Surfaces the federated knowledge-graph's blind spots (KBGaps) — the concrete "
                "things the source pharma data got wrong or never captured — and what fixing them unlocks.")

    @property
    def capabilities(self) -> list[str]:
        return ["list_gaps", "explain_gap", "prioritize_gaps"]

    async def _execute(self, ctx: AgentContext, task: dict[str, Any]) -> AgentResult:
        res = self._svc.gaps()
        gaps = res["gaps"]
        findings = gaps
        grounding = [g["slug"] for g in gaps if g.get("slug")]
        if gaps:
            types = {}
            for g in gaps:
                types[g.get("gap_type") or "other"] = types.get(g.get("gap_type") or "other", 0) + 1
            type_str = ", ".join(f"{k}: {v}" for k, v in sorted(types.items(), key=lambda x: -x[1]))
            det = (f"{res['count']} blind spots identified across the federated data. "
                   f"By category — {type_str}. These are gaps in what the source trials/genomics "
                   f"captured (e.g. missing AE onset timing, un-ingested serial labs, drug metadata gaps).")
        else:
            det = "No blind spots recorded."
        facts = "Blind spots (what the data is missing):\n" + "\n".join(
            f"- [{g.get('gap_type')}] {g.get('name')}" for g in gaps)
        summary, used = _maybe_narrate(facts, findings, det)
        return self._ok(findings, summary, used, grounding, extra={"count": res["count"]})


# ── factory ────────────────────────────────────────────────────────────────

def build_signal_agents(service) -> dict[str, BaseAgent]:
    """Instantiate the three signal agents bound to a SignalService."""
    return {
        "signal_miner": SignalMinerAgent(service),
        "bridge_hunter": BridgeHunterAgent(service),
        "gap_auditor": GapAuditorAgent(service),
    }
