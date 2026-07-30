"""
Zeta Loader (plumbing/zeta_loader.py)
======================================
Idempotent ELT loader — takes raw records from synapse_ingestor / pds_ingestor
and mints them as SourceNode entities into all 3 stores:

  1. KG JSON  — UPSERT by id (replace if exists, append if new)
  2. Qdrant   — UPSERT by uuid5(id) — BM25 sparse only (no dense embed)
  3. Neo4j    — MERGE on id, SET attrs on match

Idempotency guarantee: running 50 times produces the same result as running once.
Only _ingested_at updates on re-run.

Constraints:
  - crispro_kb_v3 NEVER touched (1418 points invariant)
  - Neo4j cross-links MUST remain 0 after every batch
  - No LLM calls, no dense embeddings

License: Apache-2.0
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("zetabridge.zeta_loader")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
KG_ENTITIES_PATH = Path("/workspace/zeta_vault/kg/zeta_entities.json")
KG_EDGES_PATH    = Path("/workspace/zeta_vault/kg/zeta_edges.json")
KG_RESULTS_PATH  = Path("/mnt/results/zeta_vault/kg/zeta_entities.json")
KG_EDGES_RESULTS = Path("/mnt/results/zeta_vault/kg/zeta_edges.json")

SYNAPSE_RECORDS = Path("/workspace/zetabridge/plumbing/synapse_raw_records.json")
PDS_RECORDS     = Path("/workspace/zetabridge/plumbing/pds_raw_records.json")

QDRANT_COLLECTION = "zeta_vault"
CRISPRO_COLLECTION = "crispro_kb_v3"
CRISPRO_EXPECTED = 1418

UUID_NS = uuid.NAMESPACE_URL


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_env() -> dict:
    env = {}
    env_path = Path("/workspace/.env")
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _node_id(record: dict) -> str:
    """Deterministic SourceNode id from (endpoint, table, pk)."""
    ep = record["_source_endpoint"]
    tbl = record["_table"]
    pk = record["_pk"]
    return f"source:{ep}:{tbl}:{pk}"


def _point_id(node_id: str) -> str:
    """Deterministic Qdrant point_id = uuid5(NAMESPACE_URL, node_id)."""
    return str(uuid.uuid5(UUID_NS, f"zeta_vault::{node_id}"))


def _make_entity(record: dict, node_id: str) -> dict:
    """Build a KG entity dict from a raw record."""
    now = _now_iso()
    raw = record.get("_raw_payload", {})
    # Truncate very large payloads to avoid bloating the KG JSON
    raw_str = json.dumps(raw, default=str)
    if len(raw_str) > 8000:
        raw_str = raw_str[:8000] + "...[truncated]"

    return {
        "id": node_id,
        "type": "SourceNode",
        "name": record.get("name", node_id),
        "source_receipts": [record["_source_endpoint"]],
        "verbatim_evidence": [f"raw_payload:{record['_table']}:{record['_pk']}"],
        "cross_refs": {},
        "attributes": {
            "_source_endpoint": record["_source_endpoint"],
            "_ingested_at": now,
            "_raw_payload": raw_str,
            "_table": record["_table"],
            "_pk": record["_pk"],
        },
        "_stream": f"elt_session5_{record['_source_endpoint']}",
        "_mint_planner": "zeta_custodian",
        "_mint_timestamp": now,
    }


# ---------------------------------------------------------------------------
# Store 1: KG JSON UPSERT
# ---------------------------------------------------------------------------

def upsert_kg(
    records: list[dict],
    entities_path: Path = KG_ENTITIES_PATH,
) -> tuple[int, int]:
    """
    UPSERT records into KG JSON.
    Returns (n_inserted, n_updated).
    """
    with open(entities_path) as f:
        ents = json.load(f)

    # Build index by id
    idx: dict[str, int] = {e["id"]: i for i, e in enumerate(ents)}

    n_inserted = 0
    n_updated = 0

    for record in records:
        node_id = _node_id(record)
        entity = _make_entity(record, node_id)

        if node_id in idx:
            # Update: only refresh _ingested_at and _raw_payload
            existing = ents[idx[node_id]]
            existing["attributes"]["_ingested_at"] = entity["attributes"]["_ingested_at"]
            existing["attributes"]["_raw_payload"] = entity["attributes"]["_raw_payload"]
            n_updated += 1
        else:
            ents.append(entity)
            idx[node_id] = len(ents) - 1
            n_inserted += 1

    # Write atomically: workspace first, then cp to results
    tmp_path = entities_path.with_suffix(".json.tmp")
    with open(tmp_path, "w") as f:
        json.dump(ents, f, separators=(",", ":"))
    shutil.move(str(tmp_path), str(entities_path))

    # Mirror to results
    try:
        shutil.copy(str(entities_path), str(KG_RESULTS_PATH))
    except Exception as e:
        log.warning(f"Results mirror failed: {e}")

    log.info(f"KG JSON: inserted={n_inserted}, updated={n_updated}, total={len(ents)}")
    return n_inserted, n_updated


# ---------------------------------------------------------------------------
# Store 2: Qdrant BM25 UPSERT (sparse only, no dense embed)
# ---------------------------------------------------------------------------

def upsert_qdrant(
    records: list[dict],
    env: dict,
    batch_size: int = 200,
) -> tuple[int, int]:
    """
    UPSERT records into Qdrant zeta_vault as BM25 sparse vectors only.
    Returns (n_upserted, n_failed).
    """
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import PointStruct, SparseVector, SparseVectorParams, VectorParams
    except ImportError:
        log.error("qdrant_client not installed")
        return 0, len(records)

    client = QdrantClient(
        url=env["QDRANT_URL"],
        api_key=env["QDRANT_API_KEY"],
        timeout=60,
    )

    # Verify crispro invariant before touching anything
    try:
        crispro_count = client.get_collection(CRISPRO_COLLECTION).points_count
        assert crispro_count == CRISPRO_EXPECTED, f"crispro_kb_v3 count={crispro_count} != {CRISPRO_EXPECTED}"
    except Exception as e:
        log.error(f"crispro invariant check failed: {e}")
        return 0, len(records)

    # Build BM25 sparse vectors using fastembed
    try:
        from fastembed import SparseTextEmbedding
        import os
        os.environ.setdefault("FASTEMBED_CACHE_PATH", "/workspace/.fastembed_cache")
        sparse_model = SparseTextEmbedding(model_name="Qdrant/bm25")
    except Exception as e:
        log.error(f"fastembed BM25 init failed: {e}")
        return 0, len(records)

    n_upserted = 0
    n_failed = 0

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        texts = []
        for r in batch:
            # Build searchable text from record fields
            parts = [
                r.get("name", ""),
                r.get("_table", ""),
                r.get("_source_endpoint", ""),
                r.get("_pk", ""),
            ]
            raw = r.get("_raw_payload", {})
            if isinstance(raw, dict):
                # Add key names and string values (truncated)
                for k, v in list(raw.items())[:20]:
                    parts.append(str(k))
                    if isinstance(v, str):
                        parts.append(v[:100])
            texts.append(" ".join(p for p in parts if p))

        try:
            sparse_embeddings = list(sparse_model.embed(texts))
            points = []
            for j, (record, sparse_emb) in enumerate(zip(batch, sparse_embeddings)):
                node_id = _node_id(record)
                point_id = _point_id(node_id)
                raw = record.get("_raw_payload", {})
                raw_str = json.dumps(raw, default=str)
                if len(raw_str) > 2000:
                    raw_str = raw_str[:2000] + "...[truncated]"

                payload = {
                    "entity_id": node_id,
                    "entity_type": "SourceNode",
                    "text": texts[j][:500],
                    "_source_endpoint": record["_source_endpoint"],
                    "_table": record["_table"],
                    "_pk": record["_pk"],
                    "_ingested_at": record.get("_ingested_at", _now_iso()),
                    "fingerprint": {
                        "primary_key": node_id,
                        "source_vault": record["_source_endpoint"],
                        "biological_state": "raw_staging",
                        "genomic_signature": "",
                        "temporal_marker": record.get("_ingested_at", ""),
                    },
                }

                # Build sparse vector
                indices = sparse_emb.indices.tolist() if hasattr(sparse_emb.indices, "tolist") else list(sparse_emb.indices)
                values = sparse_emb.values.tolist() if hasattr(sparse_emb.values, "tolist") else list(sparse_emb.values)

                point = PointStruct(
                    id=point_id,
                    vector={"bm25": SparseVector(indices=indices, values=values)},
                    payload=payload,
                )
                points.append(point)

            client.upsert(collection_name=QDRANT_COLLECTION, points=points, wait=True)
            n_upserted += len(points)
            log.info(f"Qdrant batch {i//batch_size + 1}: upserted {len(points)} points")

        except Exception as e:
            log.error(f"Qdrant batch {i//batch_size + 1} failed: {e}")
            n_failed += len(batch)

    # Final crispro invariant check
    try:
        crispro_count_after = client.get_collection(CRISPRO_COLLECTION).points_count
        assert crispro_count_after == CRISPRO_EXPECTED, f"crispro VIOLATED after upsert: {crispro_count_after}"
        log.info(f"crispro_kb_v3 invariant: {crispro_count_after} (OK)")
    except Exception as e:
        log.error(f"crispro post-check failed: {e}")

    log.info(f"Qdrant: upserted={n_upserted}, failed={n_failed}")
    return n_upserted, n_failed


# ---------------------------------------------------------------------------
# Store 3: Neo4j MERGE
# ---------------------------------------------------------------------------

def upsert_neo4j(
    records: list[dict],
    env: dict,
    batch_size: int = 500,
) -> tuple[int, int]:
    """
    MERGE records into Neo4j as :ZetaVault:ZetaSourceNode nodes.
    Returns (n_merged, n_failed).
    """
    try:
        from neo4j import GraphDatabase
    except ImportError:
        log.error("neo4j driver not installed")
        return 0, len(records)

    driver = GraphDatabase.driver(
        env["NEO4J_URI"],
        auth=(env["NEO4J_USER"], env["NEO4J_PASSWORD"]),
    )

    n_merged = 0
    n_failed = 0

    cypher = """
    UNWIND $batch AS row
    MERGE (n:ZetaVault:ZetaSourceNode {id: row.id})
    ON CREATE SET
        n.type = 'SourceNode',
        n.name = row.name,
        n._source_endpoint = row._source_endpoint,
        n._ingested_at = row._ingested_at,
        n._table = row._table,
        n._pk = row._pk,
        n._stream = row._stream,
        n._mint_planner = row._mint_planner,
        n._mint_timestamp = row._mint_timestamp,
        n._raw_payload = row._raw_payload
    ON MATCH SET
        n._ingested_at = row._ingested_at,
        n._raw_payload = row._raw_payload
    """

    for i in range(0, len(records), batch_size):
        batch = records[i : i + batch_size]
        now = _now_iso()
        batch_params = []
        for record in batch:
            node_id = _node_id(record)
            raw = record.get("_raw_payload", {})
            raw_str = json.dumps(raw, default=str)
            if len(raw_str) > 4000:
                raw_str = raw_str[:4000] + "...[truncated]"
            batch_params.append({
                "id": node_id,
                "name": record.get("name", node_id),
                "_source_endpoint": record["_source_endpoint"],
                "_ingested_at": record.get("_ingested_at", now),
                "_table": record["_table"],
                "_pk": record["_pk"],
                "_stream": f"elt_session5_{record['_source_endpoint']}",
                "_mint_planner": "zeta_custodian",
                "_mint_timestamp": now,
                "_raw_payload": raw_str,
            })

        try:
            with driver.session() as session:
                result = session.run(cypher, batch=batch_params)
                summary = result.consume()
                created = summary.counters.nodes_created
                n_merged += len(batch)
                log.info(f"Neo4j batch {i//batch_size + 1}: {len(batch)} merged, {created} created")
        except Exception as e:
            log.error(f"Neo4j batch {i//batch_size + 1} failed: {e}")
            n_failed += len(batch)

    # Cross-link invariant check
    try:
        with driver.session() as session:
            cross = session.run(
                "MATCH (z:ZetaVault)-[r]-(o) WHERE NOT o:ZetaVault RETURN count(r) AS c"
            ).single()["c"]
            assert cross == 0, f"CROSS-LINK VIOLATION: {cross}"
            log.info(f"Neo4j cross-links: {cross} (OK)")
    except Exception as e:
        log.error(f"Neo4j cross-link check failed: {e}")

    driver.close()
    log.info(f"Neo4j: merged={n_merged}, failed={n_failed}")
    return n_merged, n_failed


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def run(
    synapse_records_path: Path = SYNAPSE_RECORDS,
    pds_records_path: Path = PDS_RECORDS,
    stores: list[str] | None = None,  # None = all 3
) -> dict:
    """
    Load all raw records into the 3 stores.
    Returns a summary dict with counts.
    """
    if stores is None:
        stores = ["kg", "qdrant", "neo4j"]

    env = _load_env()

    # Load raw records
    with open(synapse_records_path) as f:
        synapse_records = json.load(f)
    with open(pds_records_path) as f:
        pds_records = json.load(f)

    all_records = synapse_records + pds_records
    log.info(f"Total records to load: {len(all_records)} (synapse={len(synapse_records)}, pds={len(pds_records)})")

    summary = {
        "total_records": len(all_records),
        "synapse_records": len(synapse_records),
        "pds_records": len(pds_records),
        "kg": {},
        "qdrant": {},
        "neo4j": {},
    }

    # Store 1: KG JSON
    if "kg" in stores:
        log.info("=== KG JSON UPSERT ===")
        n_ins, n_upd = upsert_kg(all_records)
        summary["kg"] = {"inserted": n_ins, "updated": n_upd}

    # Store 2: Qdrant
    if "qdrant" in stores:
        log.info("=== Qdrant BM25 UPSERT ===")
        n_upserted, n_failed = upsert_qdrant(all_records, env)
        summary["qdrant"] = {"upserted": n_upserted, "failed": n_failed}

    # Store 3: Neo4j
    if "neo4j" in stores:
        log.info("=== Neo4j MERGE ===")
        n_merged, n_failed = upsert_neo4j(all_records, env)
        summary["neo4j"] = {"merged": n_merged, "failed": n_failed}

    log.info(f"Load complete: {json.dumps(summary, indent=2)}")
    return summary


if __name__ == "__main__":
    import sys
    stores = sys.argv[1:] if len(sys.argv) > 1 else None
    summary = run(stores=stores)
    print("\n=== LOAD SUMMARY ===")
    print(json.dumps(summary, indent=2))
