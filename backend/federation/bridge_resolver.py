"""
Cross-Endpoint Entity Resolution — the Bridge Resolver
======================================================
The honest "Synthetic Foreign Key" is the **normalized HGNC gene symbol**.

For every candidate cross-endpoint link the resolver returns EITHER:
  - a bridge edge  (when a shared gene symbol is genotyped on both endpoints), or
  - a refusal      (patient↔patient, marker↔survival, or biological-equivalence)

Refusals are DATA, not silent drops: they are written to a refusal ledger with a
machine-readable reason. This is what makes the federation auditable and keeps it
from fabricating identity or outcomes that the underlying data cannot support.

Verified data facts this enforces (2026-07-21):
  - Synapse (MSK ovarian) and PDS (SAS solid-tumor) share ZERO patients / ID namespace.
  - PDS has survival STATUS but NO duration -> no time-to-event join.
  - Shared genotyped gene symbols across endpoints = {KRAS} (asymmetric alteration class).

License: Apache-2.0
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("zetabridge.bridge_resolver")

MINT_TS = "2026-07-21T05:00:00Z"


@dataclass
class BridgeEdge:
    """An honest cross-endpoint edge anchored on a shared gene symbol."""
    source: str            # PDS-side node id
    relation: str          # e.g. "shares_gene_target"
    target: str            # Synapse-side node id (or endpoint anchor)
    bridge_gene: str
    bridge_method: str = "hgnc_symbol"
    disease_context_a: str = ""      # synapse side
    disease_context_b: str = ""      # pds side
    alteration_class_a: list = field(default_factory=list)  # synapse
    alteration_class_b: list = field(default_factory=list)  # pds
    mismatch_flags: list = field(default_factory=list)
    evidence_tier: str = ""
    evidence: dict = field(default_factory=dict)
    _mint_planner: str = "zeta_custodian"
    _mint_timestamp: str = MINT_TS
    bridge_session: str = "session4"

    def to_kg_edge(self) -> dict:
        """Serialize to the Zeta KG edge schema (key is 'relation')."""
        attrs = {
            "bridge_gene": self.bridge_gene,
            "bridge_method": self.bridge_method,
            "disease_context_a": self.disease_context_a,
            "disease_context_b": self.disease_context_b,
            "alteration_class_a": self.alteration_class_a,
            "alteration_class_b": self.alteration_class_b,
            "mismatch_flags": self.mismatch_flags,
            "evidence_tier": self.evidence_tier,
            "evidence": self.evidence,
            "bridge_session": self.bridge_session,
        }
        return {
            "source": self.source,
            "relation": self.relation,
            "target": self.target,
            "source_receipts": [
                "zeta_store::synapse::genotyped_genes",
                "zeta_store::pds::biomarker_context",
            ],
            "attributes": attrs,
            "_mint_planner": self._mint_planner,
            "_mint_timestamp": self._mint_timestamp,
        }


@dataclass
class Refusal:
    """A logged refusal — a join the naive 'synthetic FK' would have minted."""
    join_class: str            # patient_link | marker_to_survival | biological_equivalence
    requested: str             # human description of the requested join
    verdict: str = "refused"
    reason: str = ""
    evidence: dict = field(default_factory=dict)
    _logged_by: str = "zeta_custodian"
    _timestamp: str = MINT_TS


class BridgeResolver:
    """Resolves cross-endpoint candidates into edges or refusals."""

    def __init__(self, synapse_conn, pds_conn):
        self.syn = synapse_conn
        self.pds = pds_conn
        self.refusals: list[Refusal] = []

    # ── the honest join: shared gene symbol ──────────────────────────────
    def shared_genes(self) -> list[str]:
        a = set(self.syn.list_genes().keys())
        b = set(self.pds.list_biomarkers().keys())
        return sorted(a & b)

    def resolve_gene(self, gene: str) -> BridgeEdge | None:
        """Build an honest bridge edge for a gene genotyped on BOTH endpoints.

        Returns None (and logs a refusal) if the gene is not truly shared.
        """
        gene = gene.upper()
        a = self.syn.gene_context(gene)
        b = self.pds.biomarker_context(gene)
        if not (a.get("present") and b.get("present")):
            self.refusals.append(Refusal(
                join_class="unshared_gene",
                requested=f"bridge on gene {gene}",
                reason=f"{gene} not genotyped on both endpoints "
                       f"(synapse={a.get('present')}, pds={b.get('present')})",
            ))
            return None

        # mismatch detection — carried as first-class attributes, never hidden
        mismatch = []
        if a["disease_context"] != b["disease_context"]:
            mismatch.append(f"disease:{a['disease_context']}!={b['disease_context']}")
        if set(a["alteration_classes"]) != set(b["alteration_classes"]):
            mismatch.append(
                f"alteration:{'/'.join(a['alteration_classes'])}"
                f"!={'/'.join(b['alteration_classes'])}"
            )

        # evidence tier: shared symbol is real (tier: ontology); mismatch downgrades usability
        tier = "ontology_shared_symbol"
        if mismatch:
            tier += "__context_mismatch"

        # Synapse-side anchor: prefer a GenomicFeature node; else the endpoint
        # DataVault anchor (KRAS lives only in panel_cna on biospecimens, so we
        # anchor to the vault and cite the specimen-level distribution as evidence).
        syn_anchor = a["nodes"][0] if a.get("nodes") else "vault:synapse_msk_spectrum"
        pds_anchor = b["nodes"][0] if b.get("nodes") else "vault:sas_pds"

        return BridgeEdge(
            source=pds_anchor,
            relation="shares_gene_target",
            target=syn_anchor,
            bridge_gene=gene,
            disease_context_a=a["disease_context"],
            disease_context_b=b["disease_context"],
            alteration_class_a=a["alteration_classes"],
            alteration_class_b=b["alteration_classes"],
            mismatch_flags=mismatch,
            evidence_tier=tier,
            evidence={
                "synapse": {
                    "copy_number_distribution": a.get("copy_number_distribution"),
                    "n_specimens_with_call": a.get("n_specimens_with_call"),
                },
                "pds": {
                    "trials": b.get("trials"),
                    "combined_mutant_fraction": b.get("combined_mutant_fraction"),
                },
            },
        )

    def build_bridge(self) -> list[BridgeEdge]:
        """Mint bridge edges for all honestly-shared genes."""
        edges = []
        for g in self.shared_genes():
            e = self.resolve_gene(g)
            if e:
                edges.append(e)
        return edges

    # ── the refused joins: minted as data, not silently dropped ───────────
    def refuse_patient_link(self) -> Refusal:
        r = Refusal(
            join_class="patient_link",
            requested="synthetic foreign key mapping MSK patient -> PDS patient",
            reason=("Synapse (MSK ovarian, 40 masked pts) and PDS (SAS trials, "
                    "trial-local masked pts) share zero patients and zero ID "
                    "namespace. Any patient-level key invents identity."),
            evidence={"shared_patient_ids": 0, "shared_id_namespace": False},
        )
        self.refusals.append(r)
        return r

    def refuse_marker_to_survival(self) -> Refusal:
        cap = self.pds.survival_capability()
        r = Refusal(
            join_class="marker_to_survival",
            requested="link Synapse genomic marker -> PDS survival OUTCOME (time-to-event)",
            reason=cap["reason"],
            evidence={
                "pds_has_survival_duration": cap["has_survival_duration"],
                "time_to_event_joinable": cap["time_to_event_joinable"],
            },
        )
        self.refusals.append(r)
        return r

    def refuse_biological_equivalence(self, gene: str) -> Refusal:
        a = self.syn.gene_context(gene)
        b = self.pds.biomarker_context(gene)
        r = Refusal(
            join_class="biological_equivalence",
            requested=f"treat {gene} on Synapse as biologically equivalent to {gene} on PDS",
            reason=(f"{gene} is measured as {a.get('alteration_classes')} in "
                    f"{a.get('disease_context')} on Synapse vs "
                    f"{b.get('alteration_classes')} in {b.get('disease_context')} "
                    f"on PDS. Same gene, different alteration and disease — an "
                    f"annotated ontology link is allowed; equivalence is not."),
        )
        self.refusals.append(r)
        return r

    def refusal_ledger(self) -> list[dict]:
        return [asdict(r) for r in self.refusals]
