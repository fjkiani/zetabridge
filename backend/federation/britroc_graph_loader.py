"""BriTROC-1 EGA knowledge-graph loader (ZetaBridge / C_EGA source).

Loads the already-extracted file->sample->subject->dataset lineage of the 8
BriTROC-1 HGSOC copy-number-signatures EGA datasets (DAC EGAC00001000388) into
Neo4j as an idempotent property graph, modelled so downstream agents can answer:

    * "What datasets/files exist, and can I pull them (and at what cost)?"
    * "What modalities/assays exist for a given patient?"

The heavy extraction (cross-linking every file to a subject via the EGA public
metadata API) was done upstream; this module is the modelling + MERGE load. It
reads only local, verified artifacts (crosslink CSVs, lineage matrix, byte
proof, dataset summary) -- it does NOT fabricate anything and does NOT download
dataset bytes. Pullability is derived from the proven per-dataset HTTP 200s;
byte_verified is set only for files with a real BGZF Range probe on record.

All writes are additive MERGE (idempotent): re-running converges to the same
graph rather than duplicating nodes/edges.
"""
from __future__ import annotations

import csv
import glob
import json
import os
from typing import Any

from neo4j import GraphDatabase

# ---------------------------------------------------------------------------
# Static, authoritative reference maps (from datasets_summary.json /
# lineage_summary.json, verified this session). Kept explicit so the load is
# self-documenting and does not silently trust the polluted CSV `extension`
# column.
# ---------------------------------------------------------------------------

# dataset -> (assay code, human technology label, authoritative file_type)
DATASET_META: dict[str, dict[str, str]] = {
    "EGAD00001001937": {"assay": "TAmSeq_fixative", "technology": "TAm-Seq 48-amplicon (fixative method-development study)", "file_type": "bam"},
    "EGAD00001001938": {"assay": "WGS_HiSeq2500", "technology": "WGS Covaris/TruSeq, HiSeq 2500 SE50", "file_type": "bam"},
    "EGAD00001004172": {"assay": "TAmSeq_germline", "technology": "TAm-Seq germline (matched normal)", "file_type": "bam"},
    "EGAD00001004173": {"assay": "TAmSeq_tumor", "technology": "TAm-Seq tumor", "file_type": "bam"},
    "EGAD00001004174": {"assay": "sWGS_tumor", "technology": "shallow WGS tumor", "file_type": "bam"},
    "EGAD00001004189": {"assay": "deepWGS_TN", "technology": "deep WGS matched tumor/normal (~60x/40x)", "file_type": "bam"},
    "EGAD00001011049": {"assay": "sWGS_CN", "technology": "shallow WGS copy-number", "file_type": "bam"},
    "EGAD00001011058": {"assay": "TAmSeq_tagged", "technology": "TAm-Seq tagged-amplicon", "file_type": "fq.gz"},
}

# real file extensions that we trust from the CSV; anything else (the epoch-ms
# junk) is overridden by the dataset's authoritative file_type.
REAL_EXTENSIONS = {"bam", "fq.gz", "fastq.gz", "cram", "vcf", "vcf.gz", "bai"}

# tumor/normal classification from the public `phenotype` field.
NORMAL_TOKENS = {"blood", "normal", "germline"}
TUMOR_TOKENS = {
    "efo1001958",           # ovarian carcinoma EFO term
    "high grade serous",    # HGSOC free text
    "primary", "tumor", "tumour", "relapse", "metastasis",
    "efo1001515", "efo0001075",
}


def size_tier(nbytes: int) -> str:
    gb = nbytes / 1e9
    if gb < 1:
        return "small"
    if gb < 50:
        return "medium"
    if gb < 500:
        return "large"
    return "huge"


def classify_phenotype(phenotype: str) -> str:
    p = (phenotype or "").strip().lower()
    if not p:
        return "unknown"
    for t in NORMAL_TOKENS:
        if t in p:
            return "normal"
    for t in TUMOR_TOKENS:
        if t in p:
            return "tumor"
    return "unknown"


def derive_file_type(raw_ext: str, dataset_id: str) -> tuple[str, str]:
    """Return (file_type, source). Trust the CSV extension only if it is a real
    known extension; otherwise fall back to the dataset's authoritative type."""
    ext = (raw_ext or "").strip().lower()
    if ext in REAL_EXTENSIONS:
        return ext, "extension"
    ftype = DATASET_META.get(dataset_id, {}).get("file_type", "unknown")
    return ftype, "derived_from_assay"


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

class BriTrocGraphLoader:
    DAC = "EGAC00001000388"
    ENDPOINT = "C_EGA"

    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver = GraphDatabase.driver(uri, auth=(user, password))

    @classmethod
    def from_env(cls) -> "BriTrocGraphLoader":
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

    # -- read staged artifacts -------------------------------------------------
    @staticmethod
    def read_artifacts(base: str) -> dict[str, Any]:
        """Load the local, verified ETL inputs. Raises if a required input is
        missing (no silent empties)."""
        crosslink_glob = os.path.join(base, "crosslink", "EGAD*.csv")
        files = sorted(glob.glob(crosslink_glob))
        if not files:
            raise FileNotFoundError(f"no crosslink CSVs at {crosslink_glob}")

        file_rows: list[dict[str, str]] = []
        for fn in files:
            with open(fn) as fh:
                file_rows.extend(csv.DictReader(fh))

        summ = json.load(open(os.path.join(base, "manifests", "datasets_summary.json")))
        dataset_summary = {d["datasetId"]: d for d in summ["datasets"]}

        byte_proof = json.load(open(os.path.join(base, "byte_proof.json")))["datasets"]

        subj_matrix = list(csv.DictReader(open(os.path.join(base, "lineage", "subject_assay_matrix.csv"))))

        return {
            "file_rows": file_rows,
            "dataset_summary": dataset_summary,
            "byte_proof": byte_proof,
            "subject_matrix": subj_matrix,
        }

    # -- schema ---------------------------------------------------------------
    def ensure_constraints(self) -> None:
        stmts = [
            "CREATE CONSTRAINT dataset_id IF NOT EXISTS FOR (n:Dataset) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT subject_id IF NOT EXISTS FOR (n:Subject) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT sample_id IF NOT EXISTS FOR (n:Sample) REQUIRE n.id IS UNIQUE",
            "CREATE CONSTRAINT file_id IF NOT EXISTS FOR (n:File) REQUIRE n.id IS UNIQUE",
        ]
        with self._driver.session() as s:
            for st in stmts:
                s.run(st)

    # -- build node/edge payloads (pure transform, testable) ------------------
    def build_payload(self, art: dict[str, Any]) -> dict[str, Any]:
        file_rows = art["file_rows"]
        dsum = art["dataset_summary"]
        bproof = art["byte_proof"]
        smatrix = art["subject_matrix"]

        assay_cols = [c for c in smatrix[0].keys() if c not in {"namespace", "subject_id", "n_files", "n_assays"}]

        # --- Dataset nodes ---
        datasets = []
        for did, meta in DATASET_META.items():
            ds = dsum.get(did, {})
            bp = bproof.get(did, {})
            proof = bp.get("byte_proof") or {}
            tb = int(ds.get("total_bytes", 0))
            datasets.append({
                "id": did,
                "assay": meta["assay"],
                "technology": meta["technology"],
                "file_type": meta["file_type"],
                "n_files": int(ds.get("n_files", 0)),
                "total_bytes": tb,
                "total_gb": round(tb / 1e9, 3),
                "size_tier": size_tier(tb),
                "description": (ds.get("description") or "")[:2000],
                "files_http": int(ds.get("files_http", 0)) or None,
                "dac": self.DAC,
                "byte_verified": bool(bp.get("byte_verified")),
                "byte_proof_file": proof.get("file_id"),
                "endpoint": self.ENDPOINT,
            })

        # --- Subject nodes + PROFILED_BY edges from the assay matrix ---
        subjects = []
        profiled_by = []
        # map assay-code -> dataset id (one dataset per assay here)
        assay_to_dataset = {m["assay"]: did for did, m in DATASET_META.items()}
        for r in smatrix:
            ns = r["namespace"]
            sid = r["subject_id"]
            node_id = f"{ns}:{sid}"
            assays = {a for a in assay_cols if str(r.get(a, "0")).strip() in {"1", "true", "True"}}
            n_assays = int(r.get("n_assays", len(assays)))
            subjects.append({
                "id": node_id,
                "subject_id": sid,
                "namespace": ns,
                "n_files": int(r.get("n_files", 0)),
                "n_assays": n_assays,
                "multimodal": n_assays >= 3,
                "endpoint": self.ENDPOINT,
            })
            for a in assays:
                did = assay_to_dataset.get(a)
                if did:
                    profiled_by.append({"subject": node_id, "dataset": did, "assay": a})

        # NOTE: an explicit (:Subject)-[:SHARES_ASSAYS_WITH]->(:Subject) edge was
        # considered and deliberately dropped. This cohort is so densely
        # co-profiled that any threshold collapses into a near-complete clique
        # (>=2 shared assays -> C(265,2)=34,980 edges; >=4 -> C(142,2)=10,011),
        # which is a low-information hairball. "What modalities exist for
        # patient X" is answered directly and cheaply by the Subject.n_assays /
        # Subject.multimodal properties plus the (:Subject)-[:PROFILED_BY]->(:Dataset)
        # edges, so we keep those and avoid the clique.

        # --- File + Sample nodes and their edges ---
        files = []
        samples: dict[str, dict[str, Any]] = {}
        has_file_dataset = []      # (dataset, file)
        has_file_sample = []       # (sample, file)
        has_sample = []            # (subject, sample)
        seen_subject_sample: set[tuple[str, str]] = set()

        for row in file_rows:
            did = row["datasetId"]
            fid = row["fileId"]
            raw_ext = row.get("extension", "")
            ftype, ftype_src = derive_file_type(raw_ext, did)
            fsize = int(row.get("filesize", 0) or 0)
            bp = bproof.get(did, {})
            proof = bp.get("byte_proof") or {}
            is_proof_file = proof.get("file_id") == fid
            files.append({
                "id": fid,
                "dataset": did,
                "file_type": ftype,
                "file_type_source": ftype_src,
                "filesize": fsize,
                "size_tier": size_tier(fsize),
                "md5": row.get("md5") or None,
                "title": row.get("title") or None,
                "pullable": (int(row.get("sample_http", 0) or 0) == 200),
                "pull_method": "ega_range_c4gh",  # bounded Crypt4GH Range; plaintext = filesize-16
                "byte_verified": bool(is_proof_file and bp.get("byte_verified")),
                "endpoint": self.ENDPOINT,
            })
            has_file_dataset.append({"dataset": did, "file": fid})

            samp_acc = row.get("sample_accession")
            ns = "JBLAB" if str(row.get("subject_id", "")).startswith("JBLAB") else "PATIENT_INT"
            subj_node = f"{ns}:{row.get('subject_id')}" if row.get("subject_id") else None
            if samp_acc:
                sample_class = classify_phenotype(row.get("phenotype", ""))
                if samp_acc not in samples:
                    samples[samp_acc] = {
                        "id": samp_acc,
                        "sample_accession": samp_acc,
                        "sample_class": sample_class,
                        "phenotype_raw": (row.get("phenotype") or "")[:200],
                        "title": row.get("title") or None,
                        "endpoint": self.ENDPOINT,
                    }
                has_file_sample.append({"sample": samp_acc, "file": fid})
                if subj_node and (subj_node, samp_acc) not in seen_subject_sample:
                    seen_subject_sample.add((subj_node, samp_acc))
                    has_sample.append({"subject": subj_node, "sample": samp_acc})

        return {
            "datasets": datasets,
            "subjects": subjects,
            "samples": list(samples.values()),
            "files": files,
            "profiled_by": profiled_by,
            "has_file_dataset": has_file_dataset,
            "has_file_sample": has_file_sample,
            "has_sample": has_sample,
        }

    # -- load (batched UNWIND MERGE) ------------------------------------------
    @staticmethod
    def _batches(rows: list[dict], n: int = 1000):
        for i in range(0, len(rows), n):
            yield rows[i:i + n]

    def load(self, payload: dict[str, Any]) -> dict[str, int]:
        q = {
            "datasets": (
                "UNWIND $rows AS r MERGE (d:Dataset {id:r.id}) SET d += r"
            ),
            "subjects": (
                "UNWIND $rows AS r MERGE (s:Subject {id:r.id}) SET s += r"
            ),
            "samples": (
                "UNWIND $rows AS r MERGE (sa:Sample {id:r.id}) SET sa += r"
            ),
            "files": (
                "UNWIND $rows AS r MERGE (f:File {id:r.id}) SET f += r"
            ),
            "has_file_dataset": (
                "UNWIND $rows AS r MATCH (d:Dataset {id:r.dataset}), (f:File {id:r.file}) MERGE (d)-[:HAS_FILE]->(f)"
            ),
            "has_file_sample": (
                "UNWIND $rows AS r MATCH (sa:Sample {id:r.sample}), (f:File {id:r.file}) MERGE (sa)-[:HAS_FILE]->(f)"
            ),
            "has_sample": (
                "UNWIND $rows AS r MATCH (s:Subject {id:r.subject}), (sa:Sample {id:r.sample}) MERGE (s)-[:HAS_SAMPLE]->(sa)"
            ),
            "profiled_by": (
                "UNWIND $rows AS r MATCH (s:Subject {id:r.subject}), (d:Dataset {id:r.dataset}) "
                "MERGE (s)-[e:PROFILED_BY {assay:r.assay}]->(d)"
            ),
        }
        counts: dict[str, int] = {}
        order = ["datasets", "subjects", "samples", "files",
                 "has_file_dataset", "has_file_sample", "has_sample",
                 "profiled_by"]
        with self._driver.session() as s:
            for key in order:
                rows = payload[key]
                for batch in self._batches(rows):
                    s.run(q[key], rows=batch)
                counts[key] = len(rows)
        return counts

    def graph_totals(self) -> dict[str, Any]:
        with self._driver.session() as s:
            labels = s.run(
                "MATCH (n) UNWIND labels(n) AS l RETURN l AS label, count(*) AS c ORDER BY label"
            ).data()
            rels = s.run(
                "MATCH ()-[r]->() RETURN type(r) AS type, count(*) AS c ORDER BY type"
            ).data()
            tot = s.run("MATCH (n) RETURN count(n) AS nodes").single()["nodes"]
            edges = s.run("MATCH ()-[r]->() RETURN count(r) AS edges").single()["edges"]
        return {"nodes": tot, "edges": edges, "labels": labels, "rels": rels}
