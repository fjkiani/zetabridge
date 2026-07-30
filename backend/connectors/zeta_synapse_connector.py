"""
Zeta Synapse Connector (Dataset A)
==================================
Read-only connector over the LIVE Zeta KG, scoped to the Synapse endpoint:
MSK SPECTRUM ovarian HGSOC tissue genomics (40 patients, masked IDs).

Conforms to the ZetaBridge ConnectorSpec / ConnectorRegistry framework.
Reads the real Zeta stores — NOT the seeded biotech.duckdb demo data.

License: Apache-2.0
"""
from __future__ import annotations

import logging
from typing import Any

from .registry import ConnectorSpec, ConnectorCategory, ConnectorStatus

try:
    from federation.zeta_store import get_zeta_store
except ImportError:  # allow import from repo root
    from backend.federation.zeta_store import get_zeta_store  # type: ignore

log = logging.getLogger("zetabridge.zeta_synapse")

ENDPOINT = "synapse"

SPEC = ConnectorSpec(
    name="zeta_synapse",
    display_name="Zeta Synapse (MSK SPECTRUM ovarian HGSOC)",
    category=ConnectorCategory.DATABASE,
    protocol="Zeta KG read API (JSON + Neo4j + Qdrant)",
    oss_component="Zeta Vault",
    license="Internal / RUO",
    description=(
        "Dataset A endpoint: MSK SPECTRUM ovarian high-grade serous carcinoma "
        "tissue genomics — 40 patients (masked IDs), biospecimens, oncogenic "
        "mutation calls and a copy-number panel. Read-only over the live Zeta KG."
    ),
    capabilities=["list_genes", "genomic_features", "gene_context", "cohort_summary"],
    data_modalities=["structured"],
    status=ConnectorStatus.ACTIVE,
    config_schema={"kg_entities": "str", "kg_edges": "str"},
)


class ZetaSynapseConnector:
    """Endpoint-scoped read API for Synapse (Dataset A)."""

    endpoint = ENDPOINT
    spec = SPEC

    def __init__(self):
        self.store = get_zeta_store()

    def health(self) -> dict:
        c = self.store.counts()
        return {
            "connector": self.spec.name,
            "healthy": c["by_endpoint"].get(ENDPOINT, 0) > 0,
            "n_entities": c["by_endpoint"].get(ENDPOINT, 0),
        }

    def list_genes(self) -> dict[str, dict]:
        """Genes genotyped on Synapse (mutation nodes ∪ panel_cna)."""
        return self.store.genotyped_genes(ENDPOINT)

    def genomic_features(self) -> list[dict]:
        return [
            {"id": e["id"], "name": e["name"], "attributes": e.get("attributes", {})}
            for e in self.store.genomic_features(ENDPOINT)
        ]

    def gene_context(self, gene: str) -> dict:
        """All Synapse evidence for a gene: alteration class(es), nodes, and
        the copy-number distribution across specimens (if panelled)."""
        gene = gene.upper()
        genes = self.list_genes()
        rec = genes.get(gene)
        if not rec:
            return {"gene": gene, "present": False, "endpoint": ENDPOINT}

        # copy-number distribution across specimens
        from collections import Counter
        cn_vals = []
        for e in self.store.entities(endpoint=ENDPOINT, etype="Biospecimen"):
            pc = e.get("attributes", {}).get("panel_cna", {}) or {}
            if gene in pc:
                cn_vals.append(pc[gene])
        cn_dist = dict(Counter(cn_vals))

        return {
            "gene": gene,
            "present": True,
            "endpoint": ENDPOINT,
            "disease_context": "ovarian_hgsoc",
            "alteration_classes": rec["alteration_classes"],
            "nodes": rec["nodes"],
            "copy_number_distribution": cn_dist,   # {0: neutral, -1: loss, 1: gain}
            "n_specimens_with_call": len(cn_vals),
        }

    def cohort_summary(self) -> dict:
        specimens = self.store.entities(endpoint=ENDPOINT, etype="Biospecimen")
        patients = {e.get("attributes", {}).get("patient_id") for e in specimens}
        patients.discard(None)
        return {
            "endpoint": ENDPOINT,
            "disease": "ovarian_hgsoc",
            "n_specimens": len(specimens),
            "n_patients": len(patients),
            "genotyped_genes": sorted(self.list_genes().keys()),
        }

    # ── ELT staging layer (Session 5) ────────────────────────────────────

    def list_tables(self) -> list[str]:
        """Return all distinct table names in the Synapse SourceNode staging layer."""
        return self.store.list_tables(ENDPOINT)

    def table_schema(self, table: str) -> dict | None:
        """Return the schema SourceNode for a Synapse table, or None if not staged."""
        node = self.store.schema_for_table(ENDPOINT, table)
        if not node:
            return None
        import json
        raw_str = node.get("attributes", {}).get("_raw_payload", "{}")
        try:
            return json.loads(raw_str) if isinstance(raw_str, str) else raw_str
        except Exception:
            return {"raw": raw_str}

    def raw_rows(self, table: str, limit: int = 100) -> list[dict]:
        """Return raw row payloads from a Synapse staging table."""
        return self.store.raw_rows(ENDPOINT, table, limit=limit)

    def source_node_summary(self) -> dict:
        """Summary of all Synapse SourceNodes in the staging layer."""
        counts = self.store.source_node_counts()
        return {
            "endpoint": ENDPOINT,
            "total_source_nodes": counts["by_endpoint"].get(ENDPOINT, 0),
            "tables": self.list_tables(),
        }
