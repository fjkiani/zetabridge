"""Lineage router — /api/lineage endpoints, backed by the live Neo4j KG.

The legacy Marquez/OpenLineage client returned nothing (no Marquez server in
this deployment), which left the Overview Lineage Preview and the Lineage page
dark. These endpoints read lineage directly from the federated graph: nodes are
grouped by owning endpoint, edges by relation type, and ingestion "jobs" are
the `_stream` / `_mint_planner` provenance stamped on every record.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter(prefix="/api/lineage", tags=["lineage"])

_service = None


def _get_service():
    global _service
    if _service is None:
        from federation.graph_service import GraphService
        _service = GraphService.from_env()
    return _service


@router.get("/graph")
def lineage_graph() -> dict:
    """Lineage graph as nodes[]/edges[] arrays (frontend preview + full page).

    Datasets = distinct `_table` values; jobs = distinct `_stream` ingestion
    runs. Edges connect each ingestion stream to the tables it produced.
    """
    svc = _get_service()
    tables = svc._read(
        "MATCH (n) WHERE n._table IS NOT NULL "
        "WITH n._table AS t, coalesce(n._source_endpoint,'GRAPH') AS ep, count(n) AS c "
        "RETURN t, ep, c ORDER BY c DESC LIMIT 40"
    )
    streams = svc._read(
        "MATCH (n) WHERE n._stream IS NOT NULL "
        "WITH n._stream AS s, count(n) AS c RETURN s, c ORDER BY c DESC LIMIT 40"
    )
    links = svc._read(
        "MATCH (n) WHERE n._stream IS NOT NULL AND n._table IS NOT NULL "
        "RETURN DISTINCT n._stream AS s, n._table AS t LIMIT 120"
    )
    nodes: list[dict] = []
    for r in tables:
        nodes.append({"id": f"dataset:{r['t']}", "name": r["t"], "type": "dataset",
                      "endpoint": r["ep"], "count": r["c"]})
    for r in streams:
        nodes.append({"id": f"job:{r['s']}", "name": r["s"], "type": "job", "count": r["c"]})
    edges = [{"source": f"job:{r['s']}", "target": f"dataset:{r['t']}", "relation": "INGESTED"}
             for r in links]
    total_n = svc._read("MATCH (n) RETURN count(n) AS c")[0]["c"]
    total_e = svc._read("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
    return {"nodes": nodes, "edges": edges, "total_nodes": total_n, "total_edges": total_e}


@router.get("/stats")
def lineage_stats() -> dict:
    """Headline lineage counts for the Overview preview widget."""
    svc = _get_service()
    n = svc._read("MATCH (n) RETURN count(n) AS c")[0]["c"]
    e = svc._read("MATCH ()-[r]->() RETURN count(r) AS c")[0]["c"]
    datasets = svc._read(
        "MATCH (n) WHERE n._table IS NOT NULL RETURN count(DISTINCT n._table) AS c"
    )[0]["c"]
    jobs = svc._read(
        "MATCH (n) WHERE n._stream IS NOT NULL RETURN count(DISTINCT n._stream) AS c"
    )[0]["c"]
    return {"nodes": n, "edges": e, "datasets": datasets, "jobs": jobs}


# Legacy Marquez passthrough (kept for backward compat).
@router.get("")
async def lineage(dataset: str, source: str = "snowflake"):
    from lineage.marquez_client import get_lineage_graph
    return get_lineage_graph(dataset, source)
