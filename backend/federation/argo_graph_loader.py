"""Zeta Bridge — ICGC ARGO subgraph loader (Session 16).

Loads a bounded set of real ICGC ARGO objects (from the SCORE registry, via
``ArgoClient``) into Neo4j as an *additive*, ``Argo*``-namespaced subgraph:

    (:ArgoProgram {code})
        -[:HAS_DONOR]->   (:ArgoDonor  {id, project_code})
        -[:HAS_SAMPLE]->  (:ArgoSample {id, donor_id})
        -[:HAS_FILE]->    (:ArgoFile   {object_id, file_name, access, file_type,
                                        experiment, gnos_id, md5})
    (:ArgoSample)-[:SERIAL_WITH]->(:ArgoSample)   # same-donor multi-sample pairs

Honesty + safety contract:
  * Every node/edge comes from a real ARGO API response (no fabrication).
  * Only ``Argo*`` labels are created/merged; the pre-existing MSK/SAS/EGA graph
    is never read for mutation and never modified. This keeps the "read-only /
    additive-only" guarantee for the existing federated graph.
  * Idempotent: all writes are ``MERGE`` on stable natural keys, so re-running
    updates in place rather than duplicating.
  * Node ids are namespaced: ``argo:program:<code>``, ``argo:donor:<DO*>``,
    ``argo:sample:<SA*>``, ``argo:file:<object_id>`` — so they never collide
    with existing endpoint ids and are safe to traverse via the graph tools.

This module is a one-shot loader invoked explicitly (pilot / on-demand), not on
the request path. It writes ONLY the ARGO subgraph; it is the single sanctioned
graph write in this feature.
"""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Any, Optional

from neo4j import GraphDatabase

try:
    from federation.source_gateway import ArgoClient
except ImportError:  # pragma: no cover - import-path shim
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from federation.source_gateway import ArgoClient


# Cypher: additive MERGE of one file's full lineage. Parameterised; no f-strings
# into the query body. SERIAL_WITH is handled in a second pass.
_MERGE_FILE_LINEAGE = """
MERGE (p:ArgoProgram {id: 'argo:program:' + $project_code})
  ON CREATE SET p.code = $project_code, p.endpoint = 'D_ARGO', p.source = 'argo'
WITH p
MERGE (d:ArgoDonor {id: 'argo:donor:' + $donor_id})
  ON CREATE SET d.donor_id = $donor_id, d.project_code = $project_code,
                d.endpoint = 'D_ARGO', d.source = 'argo'
MERGE (p)-[:HAS_DONOR]->(d)
WITH d
MERGE (s:ArgoSample {id: 'argo:sample:' + $sample_id})
  ON CREATE SET s.sample_id = $sample_id, s.donor_id = $donor_id,
                s.endpoint = 'D_ARGO', s.source = 'argo'
MERGE (d)-[:HAS_SAMPLE]->(s)
WITH s
MERGE (f:ArgoFile {id: 'argo:file:' + $object_id})
  SET f.object_id = $object_id, f.file_name = $file_name, f.access = $access,
      f.file_type = $file_type, f.experiment = $experiment, f.gnos_id = $gnos_id,
      f.sample_id = $sample_id, f.donor_id = $donor_id,
      f.endpoint = 'D_ARGO', f.source = 'argo'
MERGE (s)-[:HAS_FILE]->(f)
"""

_MERGE_SERIAL = """
MATCH (a:ArgoSample {id: 'argo:sample:' + $sa}), (b:ArgoSample {id: 'argo:sample:' + $sb})
MERGE (a)-[:SERIAL_WITH]->(b)
MERGE (b)-[:SERIAL_WITH]->(a)
"""

_COUNTS = """
MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH 'Argo')
WITH labels(n)[0] AS label, count(n) AS c
RETURN label, c ORDER BY label
"""

_REL_COUNTS = """
MATCH (:ArgoProgram)-[r:HAS_DONOR]->() WITH count(r) AS has_donor
MATCH (:ArgoDonor)-[r:HAS_SAMPLE]->() WITH has_donor, count(r) AS has_sample
MATCH (:ArgoSample)-[r:HAS_FILE]->() WITH has_donor, has_sample, count(r) AS has_file
OPTIONAL MATCH (:ArgoSample)-[r:SERIAL_WITH]->() WITH has_donor, has_sample, has_file, count(r) AS serial_with
RETURN has_donor, has_sample, has_file, serial_with
"""


class ArgoGraphLoader:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._argo = ArgoClient()

    @classmethod
    def from_env(cls) -> "ArgoGraphLoader":
        uri = os.environ.get("NEO4J_URI")
        user = os.environ.get("NEO4J_USER")
        pw = os.environ.get("NEO4J_PASSWORD")
        if not (uri and user and pw):
            raise RuntimeError("NEO4J_URI / NEO4J_USER / NEO4J_PASSWORD not configured")
        return cls(uri, user, pw)

    def close(self) -> None:
        try:
            self._driver.close()
        except Exception:
            pass

    def fetch_entities(
        self,
        project: Optional[str] = None,
        access: Optional[str] = "controlled",
        file_type: Optional[str] = None,
        size: int = 200,
    ) -> list[dict[str, Any]]:
        """Pull real ARGO objects to load. Returns [] and prints the typed error
        if the API is unreachable (no fabrication)."""
        env = self._argo.list_entities(project=project, access=access, file_type=file_type, size=size)
        if env.status != "live" or not env.data:
            print(f"[argo-loader] entity fetch not live: {env.status} :: {env.error}")
            return []
        # only load rows that carry the full lineage we need
        rows = [
            e for e in env.data["entities"]
            if e.get("project_code") and e.get("donor_id") and e.get("sample_id") and e.get("object_id")
        ]
        print(f"[argo-loader] fetched {env.data['n_returned']} objects; {len(rows)} have full lineage")
        return rows

    def load(self, entities: list[dict[str, Any]]) -> dict[str, Any]:
        """MERGE the file lineage for each entity, then add SERIAL_WITH edges for
        same-donor multi-sample pairs. Idempotent and additive."""
        if not entities:
            return {"loaded_files": 0, "serial_pairs": 0, "node_counts": {}, "rel_counts": {}}

        donor_samples: dict[str, set] = defaultdict(set)
        loaded = 0
        with self._driver.session() as s:
            for e in entities:
                s.execute_write(
                    lambda tx, e=e: tx.run(
                        _MERGE_FILE_LINEAGE,
                        project_code=e["project_code"],
                        donor_id=e["donor_id"],
                        sample_id=e["sample_id"],
                        object_id=e["object_id"],
                        file_name=e.get("file_name"),
                        access=e.get("access"),
                        file_type=e.get("extension"),
                        experiment=e.get("experiment"),
                        gnos_id=e.get("gnos_id"),
                    )
                )
                donor_samples[e["donor_id"]].add(e["sample_id"])
                loaded += 1

            # SERIAL_WITH: link distinct samples of the same donor (serial timepoints)
            serial_pairs = 0
            for donor, samples in donor_samples.items():
                sl = sorted(samples)
                for i in range(len(sl)):
                    for j in range(i + 1, len(sl)):
                        s.execute_write(
                            lambda tx, a=sl[i], b=sl[j]: tx.run(_MERGE_SERIAL, sa=a, sb=b)
                        )
                        serial_pairs += 1

        counts = self._read(_COUNTS)
        node_counts = {r["label"]: r["c"] for r in counts}
        rel_rows = self._read(_REL_COUNTS)
        rel_counts = rel_rows[0] if rel_rows else {}
        return {
            "loaded_files": loaded,
            "serial_pairs": serial_pairs,
            "node_counts": node_counts,
            "rel_counts": rel_counts,
        }

    def _read(self, cypher: str) -> list[dict]:
        with self._driver.session() as s:
            return [r.data() for r in s.execute_read(lambda tx: list(tx.run(cypher)))]


def load_pilot_cohort(project: Optional[str] = None, size: int = 200) -> dict[str, Any]:
    """Convenience: fetch controlled objects (optionally one project) and load
    them. Returns the load summary. Intended for explicit pilot invocation."""
    loader = ArgoGraphLoader.from_env()
    try:
        ents = loader.fetch_entities(project=project, access="controlled", size=size)
        return loader.load(ents)
    finally:
        loader.close()


if __name__ == "__main__":
    import json
    import sys

    proj = sys.argv[1] if len(sys.argv) > 1 else None
    sz = int(sys.argv[2]) if len(sys.argv) > 2 else 200
    print(json.dumps(load_pilot_cohort(project=proj, size=sz), indent=2))
