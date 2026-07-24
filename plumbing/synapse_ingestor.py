"""
Synapse Ingestor (plumbing/synapse_ingestor.py)
================================================
Pure ELT daemon — no analysis, no LLM calls.

Extracts raw metadata and staging data from the MSK SPECTRUM Synapse endpoint
(Dataset A: ovarian HGSOC, 40 patients, syn25956755) and emits raw JSON records
for the SourceNode loader.

Two extraction paths (tried in order):
1. LIVE: if SYNAPSE_AUTH_TOKEN in env → authenticate → walk project → fetch
   table schemas + rows + entity headers.
2. KG FALLBACK (always runs): extract all Synapse-stream entities from the
   live KG JSON + enrich with public Synapse REST metadata where available.

Output: list of raw dicts, each with:
  _source_endpoint, _table, _pk, _raw_payload, name

License: Apache-2.0
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

log = logging.getLogger("zetabridge.synapse_ingestor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
KG_ENTITIES = Path("/workspace/zeta_vault/kg/zeta_entities.json")
KG_EDGES    = Path("/workspace/zeta_vault/kg/zeta_edges.json")
OUTPUT_PATH = Path("/workspace/zetabridge/plumbing/synapse_raw_records.json")

SYNAPSE_STREAM = "synapse_msk_spectrum"
MSK_PROJECT_ID = "syn25956755"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_kg() -> tuple[list[dict], list[dict]]:
    with open(KG_ENTITIES) as f:
        ents = json.load(f)
    with open(KG_EDGES) as f:
        edges = json.load(f)
    return ents, edges


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_record(
    table: str,
    pk: str,
    payload: dict,
    name: str = "",
) -> dict:
    return {
        "_source_endpoint": "synapse",
        "_table": table,
        "_pk": pk,
        "_raw_payload": payload,
        "_ingested_at": _now_iso(),
        "name": name or f"synapse:{table}:{pk}",
    }


# ---------------------------------------------------------------------------
# Path 1: Live Synapse API
# ---------------------------------------------------------------------------

def _live_synapse_extract(token: str) -> list[dict]:
    """Authenticate to Synapse and walk the MSK SPECTRUM project."""
    try:
        import synapseclient
        syn = synapseclient.Synapse(silent=True)
        syn.login(authToken=token, silent=True)
        log.info("Synapse live login OK")
    except Exception as e:
        log.warning(f"Synapse live login failed: {e}")
        return []

    records: list[dict] = []

    # Walk project children
    try:
        children = list(syn.getChildren(MSK_PROJECT_ID, includeTypes=["folder", "file", "table", "entityview"]))
        log.info(f"Project children: {len(children)}")
        for child in children:
            eid = child.get("id", "")
            ename = child.get("name", eid)
            etype = child.get("type", "")
            payload = {
                "entity_id": eid,
                "entity_name": ename,
                "entity_type": etype,
                "parent_id": MSK_PROJECT_ID,
                "created_on": child.get("createdOn", ""),
                "modified_on": child.get("modifiedOn", ""),
            }
            records.append(_make_record("synapse_entity_headers", eid, payload, ename))

            # For tables: fetch schema + first 500 rows
            if "table" in etype.lower() or "entityview" in etype.lower():
                try:
                    cols = syn.getTableColumns(eid)
                    col_list = [{"name": c.name, "columnType": c.columnType, "maximumSize": getattr(c, "maximumSize", None)} for c in cols]
                    schema_payload = {"entity_id": eid, "entity_name": ename, "columns": col_list}
                    records.append(_make_record("synapse_table_schema", f"{eid}_schema", schema_payload, f"{ename}_schema"))

                    # Fetch rows
                    results = syn.tableQuery(f"SELECT * FROM {eid} LIMIT 500", resultsAs="rowset")
                    rows = results.asRowSet().rows if hasattr(results, "asRowSet") else []
                    for i, row in enumerate(rows):
                        row_payload = {"entity_id": eid, "row_index": i, "values": row.get("values", [])}
                        records.append(_make_record(f"synapse_table_{eid}", str(i), row_payload, f"{ename}_row_{i}"))
                except Exception as e:
                    log.warning(f"Table {eid} schema/rows failed: {e}")
    except Exception as e:
        log.warning(f"Project walk failed: {e}")

    log.info(f"Live Synapse extraction: {len(records)} records")
    return records


# ---------------------------------------------------------------------------
# Path 2: KG Fallback (always runs)
# ---------------------------------------------------------------------------

def _kg_fallback_extract(ents: list[dict], edges: list[dict]) -> list[dict]:
    """Extract all Synapse-stream entities from the KG as raw SourceNode records."""
    records: list[dict] = []

    synapse_ents = [e for e in ents if e.get("_stream") == SYNAPSE_STREAM]
    log.info(f"KG fallback: {len(synapse_ents)} Synapse-stream entities")

    # --- Entity header records ---
    for e in synapse_ents:
        payload = {
            "id": e["id"],
            "type": e["type"],
            "name": e.get("name", ""),
            "source_receipts": e.get("source_receipts", []),
            "verbatim_evidence": e.get("verbatim_evidence", []),
            "cross_refs": e.get("cross_refs", {}),
            "attributes": e.get("attributes", {}),
            "_stream": e.get("_stream", ""),
            "_mint_timestamp": e.get("_mint_timestamp", ""),
        }
        records.append(_make_record("kg_synapse_entity", e["id"], payload, e.get("name", e["id"])))

    # --- Biospecimen rows (one record per specimen) ---
    bios = [e for e in synapse_ents if e.get("type") == "Biospecimen"]
    for bio in bios:
        attrs = bio.get("attributes", {})
        payload = {
            "sample_id": attrs.get("sample_id", bio["id"]),
            "patient_id": attrs.get("patient_id", ""),
            "assay": attrs.get("assay", ""),
            "cancer_type": attrs.get("cancer_type", ""),
            "source_vault": attrs.get("source_vault", ""),
            "n_cn_segments": attrs.get("n_cn_segments"),
            "n_somatic_mutations": attrs.get("n_somatic_mutations"),
            "panel_cna": attrs.get("panel_cna", {}),
        }
        records.append(_make_record("msk_spectrum_biospecimen", bio["id"], payload, attrs.get("sample_id", bio["id"])))

    # --- GenomicFeature rows (one record per gene/alteration) ---
    gfs = [e for e in synapse_ents if e.get("type") == "GenomicFeature"]
    for gf in gfs:
        attrs = gf.get("attributes", {})
        payload = {
            "gene": attrs.get("gene", ""),
            "feature_type": attrs.get("feature_type", ""),
            "n_mutations": attrs.get("n_mutations"),
            "n_samples": attrs.get("n_samples"),
            "variant_classifications": attrs.get("variant_classifications", []),
            "example_hgvsp": attrs.get("example_hgvsp", ""),
            "oncogenicity": attrs.get("oncogenicity", ""),
            "recurrence_pct": attrs.get("recurrence_pct"),
        }
        records.append(_make_record("msk_spectrum_genomic_features", gf["id"], payload, attrs.get("gene", gf["id"])))

    # --- CNA panel schema (column definitions from biospecimen panel_cna keys) ---
    all_cna_genes: set[str] = set()
    for bio in bios:
        cna = bio.get("attributes", {}).get("panel_cna", {}) or {}
        all_cna_genes.update(cna.keys())
    cna_schema = {
        "table": "msk_spectrum_cna_panel",
        "columns": [{"name": g, "type": "integer", "values": [-1, 0, 1], "description": "Copy-number call: -1=loss, 0=neutral, 1=gain"} for g in sorted(all_cna_genes)],
        "n_genes": len(all_cna_genes),
        "n_specimens": len(bios),
    }
    records.append(_make_record("msk_spectrum_cna_schema", "cna_panel_schema", cna_schema, "MSK SPECTRUM CNA Panel Schema"))

    # --- Mutation schema (column definitions from GenomicFeature attributes) ---
    mut_schema = {
        "table": "msk_spectrum_mutations",
        "columns": [
            {"name": "gene", "type": "string"},
            {"name": "feature_type", "type": "string", "values": ["oncogenic_mutation", "copy_number_alteration"]},
            {"name": "n_mutations", "type": "integer"},
            {"name": "n_samples", "type": "integer"},
            {"name": "variant_classifications", "type": "array"},
            {"name": "example_hgvsp", "type": "string"},
            {"name": "oncogenicity", "type": "string"},
            {"name": "recurrence_pct", "type": "float"},
        ],
        "n_genes": len(gfs),
    }
    records.append(_make_record("msk_spectrum_mutation_schema", "mutation_schema", mut_schema, "MSK SPECTRUM Mutation Schema"))

    # --- Clinical metadata schema (from cohort entity) ---
    cohort = next((e for e in synapse_ents if e.get("type") == "PatientCohort"), None)
    if cohort:
        attrs = cohort.get("attributes", {})
        clinical_schema = {
            "table": "msk_spectrum_clinical_metadata",
            "columns": [
                {"name": "sample_id", "type": "string", "description": "Masked specimen ID (SHAH_H...)"},
                {"name": "patient_id", "type": "string", "description": "Masked patient ID"},
                {"name": "cancer_type", "type": "string", "values": ["ovarian_hgsoc"]},
                {"name": "assay", "type": "string"},
                {"name": "n_cn_segments", "type": "integer"},
                {"name": "n_somatic_mutations", "type": "integer"},
            ],
            "n_samples": attrs.get("n_samples"),
            "n_patients": attrs.get("n_patients"),
            "cancer_type": attrs.get("cancer_type"),
            "note": attrs.get("note", ""),
        }
        records.append(_make_record("msk_spectrum_clinical_schema", "clinical_metadata_schema", clinical_schema, "MSK SPECTRUM Clinical Metadata Schema"))

    # --- Edges from Synapse entities ---
    synapse_ids = {e["id"] for e in synapse_ents}
    synapse_edges = [ed for ed in edges if ed.get("source") in synapse_ids or ed.get("target") in synapse_ids]
    for ed in synapse_edges:
        payload = {
            "source": ed.get("source", ""),
            "relation": ed.get("relation", ""),
            "target": ed.get("target", ""),
            "attributes": ed.get("attributes", {}),
        }
        pk = f"{ed.get('source','')}_{ed.get('relation','')}_{ed.get('target','')}"
        records.append(_make_record("kg_synapse_edges", pk, payload, f"edge:{pk[:60]}"))

    log.info(f"KG fallback extraction: {len(records)} records")
    return records


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def _dedup(records: list[dict]) -> list[dict]:
    """Deduplicate by (table, pk) — last write wins."""
    seen: dict[str, dict] = {}
    for r in records:
        key = f"{r['_table']}::{r['_pk']}"
        seen[key] = r
    return list(seen.values())


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(env: dict | None = None) -> list[dict]:
    if env is None:
        env = {}
        env_path = Path("/workspace/.env")
        if env_path.exists():
            for line in env_path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip().strip('"').strip("'")

    ents, edges = _load_kg()
    records: list[dict] = []

    # Path 1: live (if token available)
    token = env.get("SYNAPSE_AUTH_TOKEN", "")
    if token:
        log.info("SYNAPSE_AUTH_TOKEN found — attempting live extraction")
        live_records = _live_synapse_extract(token)
        records.extend(live_records)
    else:
        log.info("No SYNAPSE_AUTH_TOKEN — using KG fallback only")

    # Path 2: KG fallback (always)
    kg_records = _kg_fallback_extract(ents, edges)
    records.extend(kg_records)

    # Dedup
    records = _dedup(records)
    log.info(f"Total unique Synapse raw records: {len(records)}")

    # Write output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(records, f, indent=2, default=str)
    log.info(f"Written to {OUTPUT_PATH}")

    return records


if __name__ == "__main__":
    records = run()
    print(f"\nSynapse ingestor complete: {len(records)} raw records")
    # Breakdown by table
    from collections import Counter
    tables = Counter(r["_table"] for r in records)
    for t, n in tables.most_common():
        print(f"  {t}: {n}")
