"""
PDS Ingestor (plumbing/pds_ingestor.py)
========================================
Pure ELT daemon — no analysis, no LLM calls.

Extracts raw metadata and staging data from the SAS/PDS solid-tumor clinical
trials endpoint (Dataset B: 23 materialized trials, 16,865 patients) and emits
raw JSON records for the SourceNode loader.

Two extraction paths (tried in order):
1. LIVE: if SAS_CAS_HOST + SAS_CAS_USER + SAS_CAS_PASSWORD in env → swat.CAS()
   → iterate all caslibs → fetch table list + column dictionaries + first 500 rows.
2. LOCAL FALLBACK (always runs): parse sas_extraction_consolidated.json (94 caslibs,
   17,264 patients) + existing KG trial/patient entities → emit as raw records.

Output: list of raw dicts, each with:
  _source_endpoint, _table, _pk, _raw_payload, name

License: Apache-2.0
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("zetabridge.pds_ingestor")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
REPO_ROOT = Path(__file__).resolve().parent.parent
KG_ENTITIES = Path("/workspace/zeta_vault/kg/zeta_entities.json")
KG_EDGES    = Path("/workspace/zeta_vault/kg/zeta_edges.json")
CONSOLIDATED_JSON = Path("/mnt/results/zeta_vault/sas_extraction_consolidated.json")
OUTPUT_PATH = Path("/workspace/zetabridge/plumbing/pds_raw_records.json")

PDS_STREAM = "zeta_ingest"  # PDS entities use this stream


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


def _make_record(table: str, pk: str, payload: dict, name: str = "") -> dict:
    return {
        "_source_endpoint": "pds",
        "_table": table,
        "_pk": pk,
        "_raw_payload": payload,
        "_ingested_at": _now_iso(),
        "name": name or f"pds:{table}:{pk}",
    }


# ---------------------------------------------------------------------------
# Path 1: Live SAS CAS
# ---------------------------------------------------------------------------

def _live_cas_extract(host: str, user: str, password: str) -> list[dict]:
    """Connect to SAS CAS and extract table schemas + first rows from all caslibs."""
    try:
        import swat
        conn = swat.CAS(host, 443, user, password, protocol="https")
        log.info(f"SAS CAS connected: {host}")
    except Exception as e:
        log.warning(f"SAS CAS connection failed: {e}")
        return []

    records: list[dict] = []

    try:
        # List all caslibs
        caslibs_result = conn.caslibinfo()
        caslibs = caslibs_result.get("CASLibInfo", {})
        if hasattr(caslibs, "to_dict"):
            caslibs_list = caslibs.to_dict("records")
        else:
            caslibs_list = []
        log.info(f"Found {len(caslibs_list)} caslibs")

        for caslib_row in caslibs_list:
            caslib_name = caslib_row.get("Name", "")
            if not caslib_name:
                continue

            # Caslib metadata record
            records.append(_make_record(
                "pds_caslib_inventory",
                caslib_name,
                caslib_row,
                f"caslib:{caslib_name}",
            ))

            # List tables in caslib
            try:
                tables_result = conn.tableinfo(caslib=caslib_name)
                tables = tables_result.get("TableInfo", {})
                if hasattr(tables, "to_dict"):
                    tables_list = tables.to_dict("records")
                else:
                    tables_list = []

                for tbl_row in tables_list:
                    tbl_name = tbl_row.get("Name", "")
                    if not tbl_name:
                        continue

                    # Table metadata
                    records.append(_make_record(
                        "pds_table_inventory",
                        f"{caslib_name}::{tbl_name}",
                        {**tbl_row, "caslib": caslib_name},
                        f"{caslib_name}::{tbl_name}",
                    ))

                    # Column dictionary
                    try:
                        col_result = conn.columninfo(table={"name": tbl_name, "caslib": caslib_name})
                        cols = col_result.get("ColumnInfo", {})
                        if hasattr(cols, "to_dict"):
                            col_list = cols.to_dict("records")
                        else:
                            col_list = []
                        schema_payload = {
                            "caslib": caslib_name,
                            "table": tbl_name,
                            "columns": col_list,
                            "n_columns": len(col_list),
                        }
                        records.append(_make_record(
                            "pds_table_schema",
                            f"{caslib_name}::{tbl_name}::schema",
                            schema_payload,
                            f"{caslib_name}::{tbl_name}::schema",
                        ))
                    except Exception as e:
                        log.debug(f"Column info failed for {caslib_name}::{tbl_name}: {e}")

                    # First 500 rows
                    try:
                        tbl_obj = conn.CASTable(tbl_name, caslib=caslib_name)
                        df = tbl_obj[:500].to_frame()
                        for i, row in df.iterrows():
                            row_payload = {"caslib": caslib_name, "table": tbl_name, "row_index": i, "values": row.to_dict()}
                            records.append(_make_record(
                                f"pds_rows_{caslib_name}_{tbl_name}",
                                f"{caslib_name}::{tbl_name}::{i}",
                                row_payload,
                                f"{caslib_name}::{tbl_name}::row_{i}",
                            ))
                    except Exception as e:
                        log.debug(f"Row fetch failed for {caslib_name}::{tbl_name}: {e}")

            except Exception as e:
                log.debug(f"Table list failed for caslib {caslib_name}: {e}")

    except Exception as e:
        log.warning(f"CAS extraction error: {e}")
    finally:
        try:
            conn.close()
        except Exception:
            pass

    log.info(f"Live CAS extraction: {len(records)} records")
    return records


# ---------------------------------------------------------------------------
# Path 2: Local consolidated JSON fallback
# ---------------------------------------------------------------------------

def _local_fallback_extract(ents: list[dict], edges: list[dict]) -> list[dict]:
    """Extract PDS data from local consolidated JSON + KG entities."""
    records: list[dict] = []

    # --- Caslib inventory from consolidated JSON ---
    if CONSOLIDATED_JSON.exists():
        with open(CONSOLIDATED_JSON) as f:
            consolidated = json.load(f)

        caslibs = consolidated.get("caslibs", [])
        if isinstance(caslibs, list):
            caslib_list = caslibs
        elif isinstance(caslibs, dict):
            caslib_list = list(caslibs.values())
        else:
            caslib_list = []

        log.info(f"Consolidated JSON: {len(caslib_list)} caslibs")

        for caslib in caslib_list:
            if isinstance(caslib, dict):
                caslib_name = caslib.get("name", caslib.get("caslib", str(caslib_list.index(caslib))))
            else:
                caslib_name = str(caslib)
                caslib = {"name": caslib_name, "raw": caslib}

            records.append(_make_record(
                "pds_caslib_inventory",
                caslib_name,
                caslib,
                f"caslib:{caslib_name}",
            ))

        # Aggregate stats
        agg = consolidated.get("aggregate", {})
        records.append(_make_record(
            "pds_aggregate_stats",
            "aggregate",
            agg,
            "PDS Aggregate Statistics",
        ))
    else:
        log.warning(f"Consolidated JSON not found at {CONSOLIDATED_JSON}")

    # --- Trial entities from KG ---
    trials = [e for e in ents if e.get("type") == "Trial"]
    log.info(f"KG trials: {len(trials)}")

    for trial in trials:
        attrs = trial.get("attributes", {})
        payload = {
            "id": trial["id"],
            "name": trial.get("name", ""),
            "source_vault": attrs.get("source_vault", ""),
            "cancer_type": attrs.get("cancer_type", ""),
            "sponsor": attrs.get("sponsor", ""),
            "year": attrs.get("year"),
            "trial_id": attrs.get("trial_id", ""),
            "n_files": attrs.get("n_files"),
            "total_bytes": attrs.get("total_bytes"),
            "adam_tables": attrs.get("adam_tables", []),
            "biological_relevance": attrs.get("biological_relevance", ""),
            "extraction_status": attrs.get("extraction_status", ""),
            "n_tables_loaded": attrs.get("n_tables_loaded"),
            "n_patients": attrs.get("n_patients"),
            "n_arms": attrs.get("n_arms"),
            "arm_patient_counts": attrs.get("arm_patient_counts", {}),
            "n_ae_terms": attrs.get("n_ae_terms"),
            "ae_events": attrs.get("ae_events"),
            "ae_coding": attrs.get("ae_coding", ""),
            "ae_grade_distribution": attrs.get("ae_grade_distribution", {}),
            "survival_status_n": attrs.get("survival_status_n"),
            "survival_event_col": attrs.get("survival_event_col", ""),
            "survival_time_col": attrs.get("survival_time_col", ""),
        }
        records.append(_make_record("pds_trial_metadata", trial["id"], payload, trial.get("name", trial["id"])))

    # --- Trial schema (data dictionary from adam_tables) ---
    extracted_trials = [t for t in trials if t.get("attributes", {}).get("extraction_status") == "extracted"]
    for trial in extracted_trials:
        attrs = trial.get("attributes", {})
        adam_tables = attrs.get("adam_tables", [])
        schema_payload = {
            "trial_id": trial["id"],
            "cancer_type": attrs.get("cancer_type", ""),
            "sponsor": attrs.get("sponsor", ""),
            "adam_tables": adam_tables,
            "ae_coding": attrs.get("ae_coding", ""),
            "columns_inferred": {
                "patient_id": {"type": "string", "description": "Masked patient identifier"},
                "arm": {"type": "string", "description": "Treatment arm code (trial-local)"},
                "age": {"type": "string", "description": "Age or age-group code (trial-specific encoding)"},
                "race": {"type": "string", "description": "Race code (trial-specific encoding)"},
                "os_event": {"type": "integer", "description": "Overall survival event flag (0/1)"},
                "n_ae_recorded": {"type": "integer", "description": "Number of AE events recorded"},
                "ae_term": {"type": "string", "description": "AE term (MedDRA code or text PT)"},
                "ae_grade": {"type": "integer", "description": "AE grade (1-5 CTCAE)"},
            },
            "caveats": [
                "Demographics NOT harmonized across trials — age/race may be codes or text",
                "Arm codes are trial-local numeric IDs, not drug names",
                "Survival: status column present, duration column absent",
            ],
        }
        records.append(_make_record("pds_trial_schema", f"{trial['id']}::schema", schema_payload, f"{trial['id']}::schema"))

    # --- Hollow trial stubs (annotated no-data records) ---
    hollow_trials = [t for t in trials if t.get("attributes", {}).get("extraction_status") != "extracted"]
    for trial in hollow_trials:
        attrs = trial.get("attributes", {})
        payload = {
            "id": trial["id"],
            "name": trial.get("name", ""),
            "extraction_status": attrs.get("extraction_status", "unknown"),
            "reason": "No loadable tables found in caslib during Session 3 extraction",
            "adam_tables": attrs.get("adam_tables", []),
            "cancer_type": attrs.get("cancer_type", ""),
        }
        records.append(_make_record("pds_hollow_trial_stub", trial["id"], payload, f"hollow:{trial.get('name', trial['id'])}"))

    # --- TreatmentArm entities ---
    arms = [e for e in ents if e.get("type") == "TreatmentArm"]
    log.info(f"KG TreatmentArms: {len(arms)}")
    for arm in arms:
        attrs = arm.get("attributes", {})
        payload = {
            "id": arm["id"],
            "name": arm.get("name", ""),
            "trial": attrs.get("trial", ""),
            "arm_code": attrs.get("arm_code", ""),
            "n_patients": attrs.get("n_patients"),
            "cancer_type": attrs.get("cancer_type", ""),
        }
        records.append(_make_record("pds_treatment_arm", arm["id"], payload, arm.get("name", arm["id"])))

    # --- AdverseEventTerm entities (shared AE terms) ---
    ae_terms = [e for e in ents if e.get("type") == "AdverseEventTerm"]
    log.info(f"KG AdverseEventTerms: {len(ae_terms)}")
    for ae in ae_terms:
        attrs = ae.get("attributes", {})
        payload = {
            "id": ae["id"],
            "name": ae.get("name", ""),
            "coding_type": attrs.get("coding_type", ""),
            "total_events": attrs.get("total_events"),
            "n_trials": attrs.get("n_trials"),
            "grade_distribution": attrs.get("grade_distribution", {}),
        }
        records.append(_make_record("pds_ae_term", ae["id"], payload, ae.get("name", ae["id"])))

    # --- Biomarker nodes ---
    biomarkers = [e for e in ents if "biomarker" in e.get("id", "").lower()]
    log.info(f"KG biomarker nodes: {len(biomarkers)}")
    for bm in biomarkers:
        attrs = bm.get("attributes", {})
        payload = {
            "id": bm["id"],
            "name": bm.get("name", ""),
            "type": bm.get("type", ""),
            "attributes": attrs,
        }
        records.append(_make_record("pds_biomarker_flag", bm["id"], payload, bm.get("name", bm["id"])))

    # --- Demographic codebook (inferred from TrialPatient sample) ---
    patients = [e for e in ents if e.get("type") == "TrialPatient"]
    log.info(f"KG TrialPatients: {len(patients)}")

    # Build per-trial codebooks from patient attribute distributions
    trial_codebooks: dict[str, dict] = {}
    for p in patients:
        attrs = p.get("attributes", {})
        trial_id = attrs.get("trial", "")
        if not trial_id:
            continue
        if trial_id not in trial_codebooks:
            trial_codebooks[trial_id] = {
                "trial_id": trial_id,
                "cancer_type": attrs.get("cancer_type", ""),
                "age_values": set(),
                "race_values": set(),
                "arm_values": set(),
                "os_event_values": set(),
                "n_patients_sampled": 0,
            }
        cb = trial_codebooks[trial_id]
        cb["n_patients_sampled"] += 1
        if attrs.get("age") is not None:
            cb["age_values"].add(str(attrs["age"]))
        if attrs.get("race") is not None:
            cb["race_values"].add(str(attrs["race"]))
        if attrs.get("arm") is not None:
            cb["arm_values"].add(str(attrs["arm"]))
        if attrs.get("os_event") is not None:
            cb["os_event_values"].add(str(attrs["os_event"]))

    for trial_id, cb in trial_codebooks.items():
        # Convert sets to sorted lists for JSON serialization
        payload = {
            "trial_id": cb["trial_id"],
            "cancer_type": cb["cancer_type"],
            "n_patients_sampled": cb["n_patients_sampled"],
            "age_observed_values": sorted(cb["age_values"])[:20],
            "race_observed_values": sorted(cb["race_values"])[:20],
            "arm_observed_values": sorted(cb["arm_values"])[:20],
            "os_event_observed_values": sorted(cb["os_event_values"]),
            "note": "Values are verbatim from source — may be codes or text depending on trial",
        }
        records.append(_make_record("pds_demographic_codebook", f"{trial_id}::codebook", payload, f"{trial_id}::codebook"))

    # --- PDS edges ---
    pds_types = {"Trial", "TrialPatient", "TreatmentArm", "AdverseEventTerm"}
    pds_ids = {e["id"] for e in ents if e.get("type") in pds_types}
    pds_edges = [ed for ed in edges if ed.get("source") in pds_ids or ed.get("target") in pds_ids]
    log.info(f"PDS-related edges: {len(pds_edges)}")

    # Sample edges (don't mint all 91k — just schema-level edge types)
    edge_types = Counter(ed.get("relation", "") for ed in pds_edges)
    for rel, count in edge_types.items():
        sample_edges = [ed for ed in pds_edges if ed.get("relation") == rel][:5]
        payload = {
            "relation": rel,
            "total_count": count,
            "sample_edges": [{"source": ed.get("source"), "target": ed.get("target"), "attributes": ed.get("attributes", {})} for ed in sample_edges],
        }
        records.append(_make_record("pds_edge_schema", f"edge_type::{rel}", payload, f"edge_type:{rel}"))

    log.info(f"Local fallback extraction: {len(records)} records")
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

    # Path 1: live CAS (if creds available)
    cas_host = env.get("SAS_CAS_HOST", "")
    cas_user = env.get("SAS_CAS_USER", "")
    cas_pass = env.get("SAS_CAS_PASSWORD", "")
    if cas_host and cas_user and cas_pass:
        log.info(f"SAS CAS creds found — attempting live extraction from {cas_host}")
        live_records = _live_cas_extract(cas_host, cas_user, cas_pass)
        records.extend(live_records)
    else:
        log.info("No SAS CAS creds in env — using local fallback only")

    # Path 2: local fallback (always)
    local_records = _local_fallback_extract(ents, edges)
    records.extend(local_records)

    # Dedup
    records = _dedup(records)
    log.info(f"Total unique PDS raw records: {len(records)}")

    # Write output
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w") as f:
        json.dump(records, f, indent=2, default=str)
    log.info(f"Written to {OUTPUT_PATH}")

    return records


if __name__ == "__main__":
    records = run()
    print(f"\nPDS ingestor complete: {len(records)} raw records")
    from collections import Counter
    tables = Counter(r["_table"] for r in records)
    for t, n in tables.most_common():
        print(f"  {t}: {n}")
