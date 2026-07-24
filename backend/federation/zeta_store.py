"""
ZetaStore — read-only access layer over the LIVE Zeta Knowledge Graph.
=====================================================================
This is the single source of truth the Synapse and PDS connectors read from.

IMPORTANT: This reads the REAL Zeta vault (KG JSON + Neo4j + Qdrant), NOT the
seeded/simulated `backend/data/biotech/biotech.duckdb` that ships with the
scaffold. The seeded DuckDB is synthetic TCGA-style demo data and is never
touched or conflated with these live stores.

Endpoints:
  - "synapse" : MSK SPECTRUM ovarian HGSOC tissue genomics (Dataset A)
  - "pds"     : SAS solid-tumor clinical-trials warehouse   (Dataset B)

License: Apache-2.0 (matches ZetaBridge backend)
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from typing import Any, Iterable

log = logging.getLogger("zetabridge.zeta_store")

# Default KG location (source of truth). Overridable via env for portability.
KG_ENTITIES = os.environ.get(
    "ZETA_KG_ENTITIES", "/workspace/zeta_vault/kg/zeta_entities.json"
)
KG_EDGES = os.environ.get(
    "ZETA_KG_EDGES", "/workspace/zeta_vault/kg/zeta_edges.json"
)


def _endpoint_of(entity: dict) -> str:
    """Classify a KG entity to an endpoint by id / stream / receipts.

    Deterministic, evidence-based — no guessing. Returns 'synapse', 'pds',
    'ega', or 'meta'.
    """
    eid = entity.get("id", "")
    stream = (entity.get("_stream", "") or "").lower()
    blob = (
        json.dumps(entity.get("source_receipts", "")).lower()
        + json.dumps(entity.get("cross_refs", "")).lower()
        + json.dumps(entity.get("attributes", {}).get("source_vault", "")).lower()
    )
    if ":msk:" in eid or "msk" in stream or "spectrum" in blob:
        return "synapse"
    if eid.startswith("trial:sas") or ":sas:" in eid or "sas" in stream or "pds" in blob:
        return "pds"
    if "ega" in eid or "britroc" in eid or "ega" in stream:
        return "ega"
    return "meta"


class ZetaStore:
    """In-memory read view over the Zeta KG, indexed by endpoint."""

    def __init__(self, entities_path: str = KG_ENTITIES, edges_path: str = KG_EDGES):
        self.entities_path = entities_path
        self.edges_path = edges_path
        self._entities: list[dict] = []
        self._edges: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._loaded = False

    def load(self) -> "ZetaStore":
        if self._loaded:
            return self
        with open(self.entities_path) as f:
            self._entities = json.load(f)
        with open(self.edges_path) as f:
            self._edges = json.load(f)
        self._by_id = {e["id"]: e for e in self._entities}
        # tag endpoint once
        for e in self._entities:
            e.setdefault("_endpoint", _endpoint_of(e))
        self._loaded = True
        log.info(
            "ZetaStore loaded: %d entities / %d edges",
            len(self._entities), len(self._edges),
        )
        return self

    # ── generic accessors ────────────────────────────────────────────────
    def get(self, entity_id: str) -> dict | None:
        return self._by_id.get(entity_id)

    def entities(self, endpoint: str | None = None, etype: str | None = None) -> list[dict]:
        out = self._entities
        if endpoint:
            out = [e for e in out if e.get("_endpoint") == endpoint]
        if etype:
            out = [e for e in out if e.get("type") == etype]
        return out

    def edges_touching(self, entity_id: str) -> list[dict]:
        return [
            ed for ed in self._edges
            if ed.get("source") == entity_id or ed.get("target") == entity_id
        ]

    def counts(self) -> dict:
        from collections import Counter
        return {
            "entities": len(self._entities),
            "edges": len(self._edges),
            "by_endpoint": dict(Counter(e.get("_endpoint") for e in self._entities)),
        }

    # ── gene-symbol join surface (the honest bridge key) ──────────────────
    @staticmethod
    def _gene_of(entity: dict) -> str:
        a = entity.get("attributes", {})
        return (a.get("gene_symbol") or a.get("gene") or "").upper()

    def genomic_features(self, endpoint: str) -> list[dict]:
        return [e for e in self.entities(endpoint=endpoint) if e.get("type") == "GenomicFeature"]

    def genotyped_genes(self, endpoint: str) -> dict[str, dict]:
        """Return {GENE_SYMBOL: evidence} for genes measured on an endpoint.

        Synapse: union of oncogenic-mutation GenomicFeature nodes AND the
        `panel_cna` copy-number panel carried on Biospecimen nodes.
        PDS: materialized biomarker GenomicFeature nodes.
        Evidence records the *alteration class* so the bridge can flag
        measurement mismatch (mutation vs copy-number).
        """
        genes: dict[str, dict] = {}
        if endpoint == "synapse":
            # oncogenic mutation nodes
            for e in self.genomic_features("synapse"):
                g = self._gene_of(e)
                if not g:
                    continue
                cls = "mutation" if ":mut:" in e["id"] else (
                    "copy_number" if ":cna:" in e["id"] else "unknown")
                genes.setdefault(g, {"gene": g, "alteration_classes": set(), "nodes": [], "disease_context": "ovarian_hgsoc"})
                genes[g]["alteration_classes"].add(cls)
                genes[g]["nodes"].append(e["id"])
            # panel_cna copy-number genes carried on biospecimens
            for e in self.entities(endpoint="synapse", etype="Biospecimen"):
                pc = e.get("attributes", {}).get("panel_cna", {}) or {}
                for g in pc:
                    G = g.upper()
                    genes.setdefault(G, {"gene": G, "alteration_classes": set(), "nodes": [], "disease_context": "ovarian_hgsoc"})
                    genes[G]["alteration_classes"].add("copy_number")
        elif endpoint == "pds":
            for e in self.genomic_features("pds"):
                g = self._gene_of(e)
                if not g:
                    continue
                dc = e.get("attributes", {}).get("disease_context", "") or "unknown"
                genes.setdefault(g, {"gene": g, "alteration_classes": set(), "nodes": [], "disease_context": dc})
                genes[g]["alteration_classes"].add("mutation")  # PDS biomarkers are mutation-status
                genes[g]["nodes"].append(e["id"])
        # make JSON-serializable
        for g in genes.values():
            g["alteration_classes"] = sorted(g["alteration_classes"])
        return genes


@lru_cache(maxsize=1)
def get_zeta_store() -> ZetaStore:
    """Process-wide singleton (KG is large; load once)."""
    return ZetaStore().load()
