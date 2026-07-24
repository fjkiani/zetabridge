"""Shared helpers for Session 12 deep-traversal workers.

Lives in /mnt/shared-workspace/shared so every worker machine imports the same
code. Provides: Neo4j driver, numpy-safe JSON encoder, endpoint id-prefix
logic, and node-minting helpers that follow the Session 11 KG schema.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from neo4j import GraphDatabase, READ_ACCESS

NEO4J_URI = "neo4j+s://82886682.databases.neo4j.io"
NEO4J_AUTH = ("82886682", "27hISSc8gAc7neJoUkpMyhi3uNb8yOlOPbQ982E_0oE")

SESSION = 12
MINT_PLANNER = "zeta_custodian_session12"
STREAM = "session12_deep_traversal"

ENDPOINT_PREFIXES = {
    "A_MSK": ["genomicfeature:msk:", "biospecimen:msk:", "cohort:msk", "vault:synapse"],
    "B_SAS": ["patient:sas:", "trial:sas:", "arm:sas:", "clinical_table:sas:",
              "trial_design:sas:", "vault:sas"],
    "C_EGA": ["ega:file:", "ega:sample:", "specimen:britroc1:", "cohort:britroc",
              "vault:ega"],
}


def endpoint_of(node_id: str):
    if not node_id:
        return None
    for ep, prefixes in ENDPOINT_PREFIXES.items():
        if any(node_id.startswith(p) for p in prefixes):
            return ep
    return None


def driver():
    return GraphDatabase.driver(NEO4J_URI, auth=NEO4J_AUTH)


def read(drv, cypher, params=None, cap=100000):
    params = params or {}

    def _work(tx):
        res = tx.run(cypher, **params)
        out = []
        for i, rec in enumerate(res):
            if i >= cap:
                break
            out.append(rec.data())
        return out

    with drv.session(default_access_mode=READ_ACCESS) as s:
        runner = getattr(s, "execute_read", None) or s.read_transaction
        return runner(_work)


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def np_default(o):
    """numpy-safe JSON encoder (Session 11 lesson)."""
    import numpy as np
    if isinstance(o, (np.bool_,)):
        return bool(o)
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (set, frozenset)):
        return list(o)
    raise TypeError(f"not serializable: {type(o)}")


def mk_node(node_id, node_type, name, attributes, label):
    """Build a KG entity dict in the Session-11 schema, plus a `label` used at
    Neo4j push time (fixing the S11 mistake: S12 nodes get a proper :Label)."""
    return {
        "id": node_id,
        "type": node_type,
        "name": name,
        "label": label,
        "cross_refs": [],
        "verbatim_evidence": [],
        "attributes": attributes,
        "_session": SESSION,
        "_stream": STREAM,
        "_mint_planner": MINT_PLANNER,
        "_mint_timestamp": now_iso(),
    }


def mk_edge(source, relation, target, attributes=None):
    return {
        "source": source,
        "relation": relation,
        "target": target,
        "attributes": attributes or {},
        "_session": SESSION,
        "_mint_planner": MINT_PLANNER,
        "_mint_timestamp": now_iso(),
        "source_receipts": ["Session 12 deep traversal"],
    }


def dump_results(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f, default=np_default, indent=2)
