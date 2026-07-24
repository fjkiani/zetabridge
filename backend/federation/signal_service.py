"""Signal service — read-only, grounded intelligence layer over the federated KG.

This is the *source of truth* for the Session-14 "value" surfaces. Every number
it returns is pulled from a real node or edge in the live Neo4j graph via
``GraphService._read`` (read-only). Nothing here fabricates, simulates, or
hard-codes analytical values.

Signal families and their NATIVE precomputed metrics (computed in prior sessions
and stored on the nodes):
  - DrugAESignal            -> rate_ratio        (drug -> adverse-event enrichment)
  - PharmacovigSignal       -> ror               (disproportionality / ROR)
  - GenomicAEBridge         -> bridge_score       (gene -> AE, cross-endpoint)
  - CrossTrialEscalationPattern -> consistency_score (reproducibility across trials)
  - AEOutlierSignal         -> rate_ratio / rr    (extreme AE outliers)

In addition to each family's native metric we expose a *derived* normalized
"strength" in [0, 1] (per-family min-max), clearly labelled ``strength_derived``
so it is never mistaken for a stored value. It exists only to order signals of
different families on a single scale in the UI.

License: Apache-2.0
"""

from __future__ import annotations

import json
import math
from typing import Any, Iterable, Optional

from federation.graph_service import GraphService, endpoint_of as _base_endpoint_of


# ── helpers ───────────────────────────────────────────────────────────────────

# Signal-local endpoint resolver. Extends the shared graph mapping with the
# clinical/genomic node prefixes that the signal layer traverses. AE terms,
# drugs, and drug/AE signals are clinical artifacts of the SAS trial endpoint;
# biomarker/genomic features anchor the MSK genomic endpoint. We keep this local
# so the shared graph_service.endpoint_of (used by other pages) is unchanged.
_SIGNAL_PREFIX_ENDPOINT = [
    ("ae:", "B_SAS"),
    ("drug:", "B_SAS"),
    ("drug_ae_signal:", "B_SAS"),
    ("cross_trial_esc:", "B_SAS"),
    ("pharmacovig", "B_SAS"),
    ("pvsignal:", "B_SAS"),
    ("ror_signal:", "B_SAS"),
    ("ae_outlier:", "B_SAS"),
    ("rare_ae_profile:", "B_SAS"),
    ("escalation_syndrome:", "B_SAS"),
    ("toxicity_syndrome:", "B_SAS"),
    ("biomarker:", "A_MSK"),
    ("genomic_ae_bridge:", "A_MSK"),  # anchored on the genomic side
]


def endpoint_of(node_id: str | None) -> str | None:
    """Resolve endpoint for a node id, extending the base map with signal prefixes."""
    base = _base_endpoint_of(node_id)
    if base:
        return base
    if not node_id:
        return None
    for pref, ep in _SIGNAL_PREFIX_ENDPOINT:
        if node_id.startswith(pref):
            return ep
    return None

def _to_float(v: Any) -> Optional[float]:
    """Best-effort float coercion; None if not numeric."""
    if v is None:
        return None
    try:
        f = float(v)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _parse_attrs(props: dict[str, Any]) -> dict[str, Any]:
    """Some signal nodes stash their payload in an ``attributes_json`` string.

    Return a merged dict of top-level props plus any parsed attributes_json.
    Top-level props win on key collisions (they are the canonical typed values).
    """
    merged: dict[str, Any] = {}
    aj = props.get("attributes_json")
    if isinstance(aj, str) and aj.strip():
        try:
            parsed = json.loads(aj)
            if isinstance(parsed, dict):
                merged.update(parsed)
        except (ValueError, TypeError):
            pass
    # legacy "attributes" field (KBGap)
    at = props.get("attributes")
    if isinstance(at, str) and at.strip():
        try:
            parsed = json.loads(at)
            if isinstance(parsed, dict):
                merged.update(parsed)
        except (ValueError, TypeError):
            pass
    for k, v in props.items():
        if k in ("attributes_json", "attributes"):
            continue
        merged[k] = v
    return merged


def _minmax_strength(values: list[Optional[float]]) -> list[Optional[float]]:
    """Per-family min-max normalization to [0,1]. Constant/degenerate -> 1.0 for
    present values (they are all equally 'the strongest available')."""
    present = [v for v in values if v is not None]
    if not present:
        return [None] * len(values)
    lo, hi = min(present), max(present)
    if hi <= lo:
        return [1.0 if v is not None else None for v in values]
    return [round((v - lo) / (hi - lo), 4) if v is not None else None for v in values]


# ── family metric configuration ───────────────────────────────────────────────
# label -> (native metric prop names in priority order, human family key)
_FAMILY_METRIC = {
    "DrugAESignal": (["rate_ratio", "rr"], "drug_ae"),
    "PharmacovigSignal": (["ror"], "pharmacovig"),
    "GenomicAEBridge": (["bridge_score"], "genomic_bridge"),
    "CrossTrialEscalationPattern": (["consistency_score"], "cross_trial"),
    "AEOutlierSignal": (["rate_ratio", "rr", "max_rr"], "outlier"),
}
_FAMILY_METRIC_NAME = {
    "drug_ae": "rate_ratio",
    "pharmacovig": "ror",
    "genomic_bridge": "bridge_score",
    "cross_trial": "consistency_score",
    "outlier": "rate_ratio",
}
_FAMILY_LABEL = {v[1]: k for k, v in _FAMILY_METRIC.items()}


class SignalService:
    """Grounded, read-only intelligence queries over the federated KG."""

    def __init__(self, graph: GraphService):
        self._g = graph

    @classmethod
    def from_env(cls) -> "SignalService":
        return cls(GraphService.from_env())

    def close(self) -> None:
        self._g.close()

    # -- low level -------------------------------------------------------------
    def _read(self, cypher: str, params: dict[str, Any] | None = None) -> list[dict]:
        return self._g._read(cypher, params)

    # -- counts ----------------------------------------------------------------
    def counts_by_family(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for label in list(_FAMILY_METRIC) + ["RareAEProfile", "EscalationSyndrome",
                                             "ToxicitySyndrome", "KBGap", "ZetaKBGap"]:
            rows = self._read(f"MATCH (x:`{label}`) RETURN count(x) AS n")
            out[label] = rows[0]["n"] if rows else 0
        return out

    # -- overview (headline value metrics) -------------------------------------
    def overview(self) -> dict[str, Any]:
        totals = self._read(
            "MATCH (n) WITH count(n) AS nodes "
            "CALL () { MATCH ()-[r]->() RETURN count(r) AS rels } "
            "RETURN nodes, rels"
        )
        t = totals[0] if totals else {"nodes": 0, "rels": 0}

        # endpoint node counts by id-prefix family
        ep_prefixes = {
            "A_MSK": ["genomicfeature:msk:", "biospecimen:msk:", "cohort:msk", "vault:synapse"],
            "B_SAS": ["patient:sas:", "trial:sas:", "arm:sas:", "clinical_table:sas:",
                      "trial_design:sas:", "vault:sas"],
            "C_EGA": ["ega:file:", "ega:sample:", "specimen:britroc1:", "cohort:britroc", "vault:ega"],
        }
        endpoints = {}
        for ep, prefixes in ep_prefixes.items():
            conds = " OR ".join(f"n.id STARTS WITH '{p}'" for p in prefixes)
            rows = self._read(f"MATCH (n) WHERE {conds} RETURN count(n) AS n")
            endpoints[ep] = rows[0]["n"] if rows else 0

        counts = self.counts_by_family()
        n_signals = sum(counts.get(l, 0) for l in _FAMILY_METRIC)
        n_gaps = counts.get("KBGap", 0) + counts.get("ZetaKBGap", 0)

        # top broker (S12 StructuralBridge by betweenness) — grounded
        broker = self._top_broker()
        reach = self._reachability_headline()

        return {
            "totals": {"nodes": t["nodes"], "relationships": t["rels"]},
            "endpoints": endpoints,
            "counts_by_family": counts,
            "n_signals": n_signals,
            "n_blind_spots": n_gaps,
            "headline": {
                "cross_endpoint_reachability": reach,   # e.g. "100% within 6 hops"
                "top_broker": broker,                   # {node, betweenness, endpoint_span}
            },
        }

    def _top_broker(self) -> dict[str, Any] | None:
        rows = self._read(
            "MATCH (b:StructuralBridge) RETURN b.attributes_json AS aj, b.name AS name "
            "ORDER BY b.name LIMIT 50"
        )
        best = None
        for r in rows:
            a = {}
            try:
                a = json.loads(r["aj"]) if r["aj"] else {}
            except (ValueError, TypeError):
                a = {}
            bw = _to_float(a.get("betweenness"))
            if bw is None:
                continue
            cand = {
                "node": a.get("node"),
                "betweenness": round(bw, 5),
                "degree": a.get("degree"),
                "endpoint_span": a.get("endpoint_span"),
                "kind": a.get("kind"),
            }
            if best is None or bw > best["betweenness"]:
                best = cand
        return best

    def _reachability_headline(self) -> dict[str, Any]:
        rows = self._read(
            "MATCH (r:ReachabilityProfile) RETURN r.attributes_json AS aj"
        )
        pairs, all_full = 0, True
        max_hops = 0
        for r in rows:
            try:
                a = json.loads(r["aj"]) if r["aj"] else {}
            except (ValueError, TypeError):
                a = {}
            frac = _to_float(a.get("reach_fraction"))
            if frac is not None:
                pairs += 1
                if frac < 1.0:
                    all_full = False
            mh = a.get("max_hops")
            if isinstance(mh, (int, float)):
                max_hops = max(max_hops, int(mh))
        return {"directed_pairs": pairs, "all_100pct": all_full, "max_hops": max_hops}

    # -- ranked signals --------------------------------------------------------
    def _load_family(self, label: str, cap: int = 500) -> list[dict[str, Any]]:
        metric_props, family_key = _FAMILY_METRIC[label]
        rows = self._read(
            f"MATCH (x:`{label}`) RETURN x AS props, labels(x) AS labels LIMIT {cap}"
        )
        items: list[dict[str, Any]] = []
        for r in rows:
            props = dict(r["props"])
            attrs = _parse_attrs(props)
            native = None
            native_prop = None
            for mp in metric_props:
                native = _to_float(attrs.get(mp))
                if native is not None:
                    native_prop = mp
                    break
            slug = props.get("id")
            items.append({
                "slug": slug,
                "family": family_key,
                "label": label,
                "name": props.get("name") or self._compose_name(label, attrs),
                "native_metric": _FAMILY_METRIC_NAME.get(family_key, native_prop),
                "native_value": native,
                "attrs": attrs,
                "endpoint": endpoint_of(slug),
                "session": props.get("_session") or props.get("session"),
            })
        # derived strength per family
        strengths = _minmax_strength([it["native_value"] for it in items])
        for it, s in zip(items, strengths):
            it["strength_derived"] = s
        # sort by native value desc (None last)
        items.sort(key=lambda it: (it["native_value"] is None, -(it["native_value"] or 0)))
        return items

    @staticmethod
    def _compose_name(label: str, attrs: dict[str, Any]) -> str:
        if label == "DrugAESignal":
            return f"{attrs.get('exp_drug')} -> {attrs.get('ae_term')}"
        if label == "PharmacovigSignal":
            return f"ROR signal: {attrs.get('ae_term')} ({attrs.get('trial')})"
        if label == "GenomicAEBridge":
            return f"{attrs.get('gene')} -> {attrs.get('ae_term')}"
        if label == "CrossTrialEscalationPattern":
            return f"{attrs.get('ae_term')} across {attrs.get('n_trials')} trials"
        if label == "AEOutlierSignal":
            return f"Outlier: {attrs.get('ae_term')}"
        return label

    def top_signals(self, family: str = "all", limit: int = 20) -> dict[str, Any]:
        if family == "all":
            # Load each family already sorted strongest-first, then round-robin
            # interleave so the hub shows variety on the first screen instead of
            # one family (e.g. capped RR=999 drug signals) crowding out the rest.
            per_family = {fk: self._load_family(label) for fk, label in _FAMILY_LABEL.items()}
            total = sum(len(v) for v in per_family.values())
            merged: list[dict[str, Any]] = []
            order = list(per_family.keys())
            idx = 0
            while len(merged) < total:
                progressed = False
                for fk in order:
                    lst = per_family[fk]
                    if idx < len(lst):
                        merged.append(lst[idx])
                        progressed = True
                idx += 1
                if not progressed:
                    break
            return {"family": "all", "count": total, "signals": merged[:limit]}
        label = _FAMILY_LABEL.get(family)
        if not label:
            raise ValueError(f"Unknown family '{family}'. Valid: {list(_FAMILY_LABEL)} or 'all'.")
        items = self._load_family(label)
        return {"family": family, "count": len(items), "signals": items[:limit]}

    # -- single signal detail with connecting nodes/edges ----------------------
    def signal_detail(self, slug: str) -> dict[str, Any] | None:
        rows = self._read(
            "MATCH (x {id:$id}) RETURN x AS props, labels(x) AS labels", {"id": slug}
        )
        if not rows:
            return None
        props = dict(rows[0]["props"])
        labels = rows[0]["labels"]
        attrs = _parse_attrs(props)

        # pull 1-hop connections so the FE can render the signal in context
        conn = self._read(
            "MATCH (x {id:$id})-[r]-(m) "
            "RETURN type(r) AS rel, startNode(r).id AS src, endNode(r).id AS dst, "
            "m.id AS other_id, m.name AS other_name, labels(m) AS other_labels LIMIT 100",
            {"id": slug},
        )
        edges, nodes = [], {}
        for c in conn:
            edges.append({"rel": c["rel"], "source": c["src"], "target": c["dst"]})
            oid = c["other_id"]
            if oid and oid not in nodes:
                nodes[oid] = {
                    "id": oid, "name": c["other_name"],
                    "labels": c["other_labels"], "endpoint": endpoint_of(oid),
                }
        # include the signal node itself
        nodes[slug] = {"id": slug, "name": props.get("name"), "labels": labels,
                       "endpoint": endpoint_of(slug)}
        return {
            "slug": slug,
            "labels": labels,
            "name": props.get("name"),
            "attrs": attrs,
            "provenance": {
                "session": props.get("_session") or props.get("session"),
                "mint_planner": props.get("_mint_planner") or props.get("mint_planner"),
            },
            "connections": {"nodes": list(nodes.values()), "edges": edges},
        }

    # -- cross-endpoint bridges (genomic -> clinical) --------------------------
    def bridges(self) -> dict[str, Any]:
        """GenomicAEBridge with the gene->AE edge path resolved end to end."""
        rows = self._read(
            "MATCH (g)-[:HAS_BRIDGE_SCORE]->(b:GenomicAEBridge)-[:BRIDGES_TO_AE]->(ae) "
            "RETURN b AS props, g.id AS gene_node, g.name AS gene_name, "
            "ae.id AS ae_node, ae.name AS ae_name "
            "ORDER BY b.name"
        )
        out = []
        for r in rows:
            props = dict(r["props"])
            attrs = _parse_attrs(props)
            out.append({
                "slug": props.get("id"),
                "gene": attrs.get("gene"),
                "ae_term": attrs.get("ae_term"),
                "bridge_score": _to_float(attrs.get("bridge_score")),
                "recurrence_pct": _to_float(attrs.get("recurrence_pct")),
                "gene_node": {"id": r["gene_node"], "name": r["gene_name"],
                              "endpoint": endpoint_of(r["gene_node"])},
                "ae_node": {"id": r["ae_node"], "name": r["ae_name"],
                            "endpoint": endpoint_of(r["ae_node"])},
                "path": [r["gene_node"], props.get("id"), r["ae_node"]],
            })
        out.sort(key=lambda z: (z["bridge_score"] is None, -(z["bridge_score"] or 0)))
        # derived strength across bridges
        strengths = _minmax_strength([b["bridge_score"] for b in out])
        for b, s in zip(out, strengths):
            b["strength_derived"] = s
        return {"count": len(out), "bridges": out}

    # -- blind spots (what pharma got wrong) -----------------------------------
    def gaps(self) -> dict[str, Any]:
        out = []
        for label in ("KBGap", "ZetaKBGap"):
            rows = self._read(
                f"MATCH (x:`{label}`) RETURN x AS props, labels(x) AS labels"
            )
            for r in rows:
                props = dict(r["props"])
                attrs = _parse_attrs(props)
                # ZetaKBGap uses a different schema (kb_claim/blocker/how) than
                # KBGap (gap_type/current_tier); normalize both into one shape.
                gap_type = attrs.get("gap_type") or attrs.get("entity_type") or attrs.get("role")
                impact = (attrs.get("impact") or attrs.get("blocks") or attrs.get("unlocks")
                          or attrs.get("blocker") or attrs.get("kb_claim") or attrs.get("how"))
                out.append({
                    "slug": props.get("id"),
                    "name": props.get("name"),
                    "gap_type": gap_type,
                    "current_tier": attrs.get("current_tier"),
                    "impact": impact,
                    "closes_gap": attrs.get("closes_gap") or attrs.get("how"),
                    "attrs": attrs,
                    "session": props.get("_session") or props.get("session") or props.get("bridge_session"),
                })
        return {"count": len(out), "gaps": out}
