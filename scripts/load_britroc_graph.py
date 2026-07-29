#!/usr/bin/env python3
"""Entrypoint: load the BriTROC-1 EGA lineage graph into Neo4j and validate.

Usage:
    NEO4J_URI=bolt://127.0.0.1:7687 NEO4J_USER=neo4j NEO4J_PASSWORD=... \
        python scripts/load_britroc_graph.py --base /path/to/britroc_collection

Idempotent: safe to re-run (all MERGE). Reads only local verified artifacts;
no dataset bytes are downloaded and nothing is fabricated. Prints acceptance
checks at the end; exits non-zero if a hard acceptance criterion fails.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

# allow "python scripts/load_britroc_graph.py" from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from backend.federation.britroc_graph_loader import BriTrocGraphLoader  # noqa: E402

# Known-good reference numbers (verified from the source artifacts this session)
EXPECTED_FILES_PER_DATASET = {
    "EGAD00001001937": 132, "EGAD00001001938": 60, "EGAD00001004172": 127,
    "EGAD00001004173": 600, "EGAD00001004174": 320, "EGAD00001004189": 111,
    "EGAD00001011049": 679, "EGAD00001011058": 6348,
}
EXPECTED_TOTAL_FILES = 8377
EXPECTED_SUBJECTS = 339          # 66 JBLAB + 273 PATIENT_INT
EXPECTED_MULTIMODAL = 142        # subjects with >=3 assays
EXPECTED_DATASETS = 8
BYTE_VERIFIED_DATASETS = {"EGAD00001001937", "EGAD00001004189", "EGAD00001011049"}


def validate(loader: BriTrocGraphLoader) -> tuple[bool, list[str]]:
    ok = True
    lines: list[str] = []

    def check(name: str, got, want, hard=True):
        nonlocal ok
        passed = got == want
        if hard and not passed:
            ok = False
        lines.append(f"[{'PASS' if passed else 'FAIL'}] {name}: got={got} want={want}")

    with loader._driver.session() as s:
        n_ds = s.run("MATCH (d:Dataset) RETURN count(d) AS c").single()["c"]
        n_file = s.run("MATCH (f:File) RETURN count(f) AS c").single()["c"]
        n_subj = s.run("MATCH (s:Subject) RETURN count(s) AS c").single()["c"]
        n_mm = s.run("MATCH (s:Subject {multimodal:true}) RETURN count(s) AS c").single()["c"]

        check("Dataset count", n_ds, EXPECTED_DATASETS)
        check("File count", n_file, EXPECTED_TOTAL_FILES)
        check("Subject count", n_subj, EXPECTED_SUBJECTS)
        check("Multimodal (>=3 assays) subjects", n_mm, EXPECTED_MULTIMODAL)

        # per-dataset HAS_FILE reconciliation
        per = {r["id"]: r["c"] for r in s.run(
            "MATCH (d:Dataset)-[:HAS_FILE]->(f:File) RETURN d.id AS id, count(f) AS c"
        ).data()}
        for did, want in EXPECTED_FILES_PER_DATASET.items():
            check(f"HAS_FILE {did}", per.get(did, 0), want)

        # orphans: files with no dataset, or no sample
        orphan_ds = s.run(
            "MATCH (f:File) WHERE NOT ( (:Dataset)-[:HAS_FILE]->(f) ) RETURN count(f) AS c"
        ).single()["c"]
        check("Files with no Dataset (orphans)", orphan_ds, 0)
        orphan_samp = s.run(
            "MATCH (f:File) WHERE NOT ( (:Sample)-[:HAS_FILE]->(f) ) RETURN count(f) AS c"
        ).single()["c"]
        # not every file necessarily has a sample_accession; report but don't hard-fail
        check("Files with no Sample (soft)", orphan_samp, 0, hard=False)
        samp_no_subj = s.run(
            "MATCH (sa:Sample) WHERE NOT ( (:Subject)-[:HAS_SAMPLE]->(sa) ) RETURN count(sa) AS c"
        ).single()["c"]
        check("Samples with no Subject (soft)", samp_no_subj, 0, hard=False)

        # namespace integrity: no SHARES_ASSAYS_WITH crossing namespaces
        cross_ns = s.run(
            "MATCH (a:Subject)-[:SHARES_ASSAYS_WITH]-(b:Subject) WHERE a.namespace <> b.namespace RETURN count(*) AS c"
        ).single()["c"]
        check("Cross-namespace SHARES edges", cross_ns, 0)

        # pullability
        pull = s.run("MATCH (f:File {pullable:true}) RETURN count(f) AS c").single()["c"]
        check("Pullable files", pull, EXPECTED_TOTAL_FILES)
        bv_datasets = {r["id"] for r in s.run(
            "MATCH (d:Dataset {byte_verified:true}) RETURN d.id AS id"
        ).data()}
        check("Byte-verified datasets", bv_datasets, BYTE_VERIFIED_DATASETS)

        # 5-assay core sanity: number of subjects carrying 5 assays
        n5 = s.run("MATCH (s:Subject {n_assays:5}) RETURN count(s) AS c").single()["c"]
        lines.append(f"[INFO] subjects with exactly 5 assays: {n5}")

        # deep-WGS cost sanity
        huge = s.run("MATCH (d:Dataset {id:'EGAD00001004189'}) RETURN d.size_tier AS t, d.total_gb AS gb").single()
        lines.append(f"[INFO] deepWGS EGAD00001004189 size_tier={huge['t']} total_gb={huge['gb']}")
        check("deepWGS size tier", huge["t"], "huge")

    return ok, lines


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="/mnt/results/britroc_collection",
                    help="directory holding crosslink/, lineage/, manifests/, byte_proof.json")
    ap.add_argument("--uri", default=os.environ.get("NEO4J_URI", "bolt://127.0.0.1:7687"))
    ap.add_argument("--user", default=os.environ.get("NEO4J_USER", "neo4j"))
    ap.add_argument("--password", default=os.environ.get("NEO4J_PASSWORD"))
    ap.add_argument("--summary-out", default=None, help="optional path to write a JSON load summary")
    args = ap.parse_args()

    if not args.password:
        print("ERROR: NEO4J_PASSWORD not set (env or --password)", file=sys.stderr)
        return 2

    loader = BriTrocGraphLoader(args.uri, args.user, args.password)
    try:
        loader._driver.verify_connectivity()
        print(f"[connect] {args.uri} OK")

        art = BriTrocGraphLoader.read_artifacts(args.base)
        print(f"[read] file_rows={len(art['file_rows'])} subjects={len(art['subject_matrix'])} "
              f"datasets={len(art['dataset_summary'])}")

        loader.ensure_constraints()
        print("[schema] uniqueness constraints ensured")

        payload = loader.build_payload(art)
        print("[transform] "
              + ", ".join(f"{k}={len(v)}" for k, v in payload.items()))

        counts = loader.load(payload)
        print("[load] " + ", ".join(f"{k}={v}" for k, v in counts.items()))

        totals = loader.graph_totals()
        print(f"[totals] nodes={totals['nodes']} edges={totals['edges']}")
        print("  labels: " + ", ".join(f"{r['label']}={r['c']}" for r in totals["labels"]))
        print("  rels:   " + ", ".join(f"{r['type']}={r['c']}" for r in totals["rels"]))

        ok, lines = validate(loader)
        print("\n=== ACCEPTANCE CHECKS ===")
        for ln in lines:
            print("  " + ln)
        print("=== RESULT:", "ALL HARD CHECKS PASS" if ok else "FAILURES PRESENT", "===")

        if args.summary_out:
            with open(args.summary_out, "w") as fh:
                json.dump({"counts": counts, "totals": totals,
                           "acceptance": lines, "ok": ok}, fh, indent=2)
            print(f"[summary] wrote {args.summary_out}")

        return 0 if ok else 1
    finally:
        loader.close()


if __name__ == "__main__":
    raise SystemExit(main())
