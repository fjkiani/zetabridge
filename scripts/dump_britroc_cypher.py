#!/usr/bin/env python3
"""Emit a self-contained, idempotent Cypher script that recreates the BriTROC-1
EGA lineage graph in ANY Neo4j (including Neo4j Aura), without APOC.

The output file contains:
  * uniqueness constraints
  * batched `UNWIND [...] AS r MERGE ...` statements with the real data inlined

Load it with cypher-shell, e.g.:
    cypher-shell -a neo4j+s://<host> -u neo4j -p <password> -f britroc_graph.cypher

Re-running is safe (all MERGE). Generated from the same verified local
artifacts as the live load -- no fabrication, no dataset bytes.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.federation.britroc_graph_loader import BriTrocGraphLoader  # noqa: E402


class _Dummy:
    DAC = BriTrocGraphLoader.DAC
    ENDPOINT = BriTrocGraphLoader.ENDPOINT


def cy_literal(v) -> str:
    """Render a Python value as a Cypher literal."""
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    return json.dumps(str(v))  # JSON-quotes + escapes; valid Cypher string


def emit_unwind(fh, rows, var, cypher_tail, batch=500):
    """Write `UNWIND [ {..}, {..} ] AS r <tail>` in batches."""
    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        maps = []
        for r in chunk:
            pairs = ", ".join(f"{k}: {cy_literal(v)}" for k, v in r.items())
            maps.append("{" + pairs + "}")
        fh.write(f"UNWIND [\n  " + ",\n  ".join(maps) + f"\n] AS {var}\n{cypher_tail};\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/mnt/results/britroc_collection")
    ap.add_argument("--out", default="/mnt/results/britroc_collection/britroc_graph.cypher")
    args = ap.parse_args()

    art = BriTrocGraphLoader.read_artifacts(args.base)
    payload = BriTrocGraphLoader.build_payload(_Dummy(), art)

    with open(args.out, "w") as fh:
        fh.write("// BriTROC-1 EGA lineage graph -- idempotent load (generated)\n")
        fh.write("// Source: ZetaBridge crosslink/lineage artifacts (DAC EGAC00001000388)\n")
        fh.write("// Load: cypher-shell -a <uri> -u neo4j -p <pw> -f britroc_graph.cypher\n\n")

        fh.write("CREATE CONSTRAINT dataset_id IF NOT EXISTS FOR (n:Dataset) REQUIRE n.id IS UNIQUE;\n")
        fh.write("CREATE CONSTRAINT subject_id IF NOT EXISTS FOR (n:Subject) REQUIRE n.id IS UNIQUE;\n")
        fh.write("CREATE CONSTRAINT sample_id IF NOT EXISTS FOR (n:Sample) REQUIRE n.id IS UNIQUE;\n")
        fh.write("CREATE CONSTRAINT file_id IF NOT EXISTS FOR (n:File) REQUIRE n.id IS UNIQUE;\n\n")

        fh.write("// --- Dataset nodes ---\n")
        emit_unwind(fh, payload["datasets"], "r", "MERGE (d:Dataset {id:r.id}) SET d += r")
        fh.write("\n// --- Subject nodes ---\n")
        emit_unwind(fh, payload["subjects"], "r", "MERGE (s:Subject {id:r.id}) SET s += r")
        fh.write("\n// --- Sample nodes ---\n")
        emit_unwind(fh, payload["samples"], "r", "MERGE (sa:Sample {id:r.id}) SET sa += r")
        fh.write("\n// --- File nodes ---\n")
        emit_unwind(fh, payload["files"], "r", "MERGE (f:File {id:r.id}) SET f += r")

        fh.write("\n// --- (:Dataset)-[:HAS_FILE]->(:File) ---\n")
        emit_unwind(fh, payload["has_file_dataset"], "r",
                    "MATCH (d:Dataset {id:r.dataset}),(f:File {id:r.file}) MERGE (d)-[:HAS_FILE]->(f)")
        fh.write("\n// --- (:Sample)-[:HAS_FILE]->(:File) ---\n")
        emit_unwind(fh, payload["has_file_sample"], "r",
                    "MATCH (sa:Sample {id:r.sample}),(f:File {id:r.file}) MERGE (sa)-[:HAS_FILE]->(f)")
        fh.write("\n// --- (:Subject)-[:HAS_SAMPLE]->(:Sample) ---\n")
        emit_unwind(fh, payload["has_sample"], "r",
                    "MATCH (s:Subject {id:r.subject}),(sa:Sample {id:r.sample}) MERGE (s)-[:HAS_SAMPLE]->(sa)")
        fh.write("\n// --- (:Subject)-[:PROFILED_BY {assay}]->(:Dataset) ---\n")
        emit_unwind(fh, payload["profiled_by"], "r",
                    "MATCH (s:Subject {id:r.subject}),(d:Dataset {id:r.dataset}) "
                    "MERGE (s)-[e:PROFILED_BY {assay:r.assay}]->(d)")

    nbytes = os.path.getsize(args.out)
    print(f"wrote {args.out} ({nbytes/1e6:.2f} MB)")
    print("payload:", ", ".join(f"{k}={len(v)}" for k, v in payload.items()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
