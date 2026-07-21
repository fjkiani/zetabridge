"""
Zeta PDS Connector (Dataset B)
==============================
Read-only connector over the LIVE Zeta KG, scoped to the PDS endpoint:
SAS solid-tumor clinical-trials warehouse (Project Data Sphere-style).

Conforms to the ZetaBridge ConnectorSpec / ConnectorRegistry framework.
Reads the real Zeta stores — NOT the seeded biotech.duckdb demo data.

CRITICAL DATA PROPERTY: PDS carries survival *status* but NO survival
*duration* (0 patients have an os_time). Any time-to-event / hazard join is
therefore impossible and is refused upstream by the bridge resolver.

License: Apache-2.0
"""
from __future__ import annotations

import logging
from typing import Any

from .registry import ConnectorSpec, ConnectorCategory, ConnectorStatus

try:
    from federation.zeta_store import get_zeta_store
except ImportError:
    from backend.federation.zeta_store import get_zeta_store  # type: ignore

log = logging.getLogger("zetabridge.zeta_pds")

ENDPOINT = "pds"

SPEC = ConnectorSpec(
    name="zeta_pds",
    display_name="Zeta PDS (SAS solid-tumor clinical trials)",
    category=ConnectorCategory.DATABASE,
    protocol="Zeta KG read API (JSON + Neo4j + Qdrant)",
    oss_component="Zeta Vault",
    license="Internal / RUO",
    description=(
        "Dataset B endpoint: SAS solid-tumor clinical-trials warehouse — trials, "
        "treatment arms, adverse-event terms, trial patients (trial-local masked "
        "IDs), and biomarker stratifications. Survival STATUS only, NO duration. "
        "Read-only over the live Zeta KG."
    ),
    capabilities=["list_biomarkers", "biomarker_context", "trial_summary", "survival_capability"],
    data_modalities=["structured"],
    status=ConnectorStatus.ACTIVE,
    config_schema={"kg_entities": "str", "kg_edges": "str"},
)


class ZetaPDSConnector:
    """Endpoint-scoped read API for PDS (Dataset B)."""

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

    def list_biomarkers(self) -> dict[str, dict]:
        """Genes materialized as biomarker stratifications on PDS."""
        return self.store.genotyped_genes(ENDPOINT)

    def biomarker_context(self, gene: str) -> dict:
        """All PDS evidence for a biomarker gene: which trials stratify on it,
        wildtype/mutant counts, disease context."""
        gene = gene.upper()
        markers = self.list_biomarkers()
        rec = markers.get(gene)
        if not rec:
            return {"gene": gene, "present": False, "endpoint": ENDPOINT}

        # find trials that stratify on this biomarker
        trials = []
        for node_id in rec["nodes"]:
            for ed in self.store.edges_touching(node_id):
                if ed.get("relation") == "stratified_by_biomarker":
                    a = ed.get("attributes", {})
                    trials.append({
                        "trial": ed["source"].split(":")[-1],
                        "wildtype": a.get("wildtype"),
                        "mutant": a.get("mutant"),
                        "failed": a.get("failed"),
                    })
        wt = sum(t["wildtype"] or 0 for t in trials)
        mut = sum(t["mutant"] or 0 for t in trials)
        return {
            "gene": gene,
            "present": True,
            "endpoint": ENDPOINT,
            "disease_context": rec["disease_context"],
            "alteration_classes": rec["alteration_classes"],  # mutation-status
            "nodes": rec["nodes"],
            "trials": trials,
            "combined_wildtype": wt,
            "combined_mutant": mut,
            "combined_mutant_fraction": round(mut / (wt + mut), 3) if (wt + mut) else None,
        }

    def trial_summary(self) -> dict:
        trials = self.store.entities(endpoint=ENDPOINT, etype="Trial")
        return {
            "endpoint": ENDPOINT,
            "n_trials": len(trials),
            "biomarker_genes": sorted(self.list_biomarkers().keys()),
        }

    def survival_capability(self) -> dict:
        """Explicit, machine-readable statement of the survival-join limitation.

        Used by the bridge resolver to REFUSE any time-to-event join with a
        concrete reason rather than silently failing.
        """
        # scan trial-patient nodes for any os_time / survival-duration field
        has_duration = False
        patients = self.store.entities(endpoint=ENDPOINT, etype="TrialPatient")
        for e in patients[:2000]:  # sample; schema is homogeneous
            a = e.get("attributes", {})
            if any(k.lower() in ("os_time", "os_months", "survival_days", "dthdy", "lkadt_p")
                   and a.get(k) not in (None, "", "NA") for k in a):
                has_duration = True
                break
        return {
            "endpoint": ENDPOINT,
            "has_survival_status": True,
            "has_survival_duration": has_duration,
            "time_to_event_joinable": has_duration,
            "reason": (
                "PDS carries survival status without a duration/time column; "
                "no hazard ratio or Kaplan-Meier is computable."
            ) if not has_duration else "duration present",
        }
