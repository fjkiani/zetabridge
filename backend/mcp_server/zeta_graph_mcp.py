"""Zeta Bridge Graph — MCP stdio server.

Exposes the federated Neo4j knowledge graph to MCP-capable agents (Claude
Desktop, IDE agents, etc.) as read-only tools. Wraps the SAME in-process
``GraphService`` as the REST router — no duplicated Neo4j logic, no second
security surface.

Graph tools (read the PRE-EXTRACTED federated Neo4j KG):
  - describe_schema()               -> labels, relationship types + counts, endpoint prefixes
  - search_nodes(...)               -> nodes by prefix/label/type/name
  - get_node(node_id)               -> single node w/ degree + rel summary
  - get_neighbors(node_id, hops...) -> n-hop induced subgraph
  - find_paths(source_id, ...)      -> ranked node-by-node cross-endpoint paths
  - run_cypher_readonly(cypher,...) -> validated read-only Cypher

Live source tools (Session 15 — invoke the LIVE source systems on demand via
SourceGateway; read-only probes + targeted fetch; source credentials stay
server-side; a caller never sees them):
  - list_sources()                  -> the 3 endpoints + configured/live status
  - synapse_get_entity(syn_id)      -> live Synapse entity metadata (A_MSK)
  - synapse_query_table(syn_id,...) -> live Synapse table rows (A_MSK)
  - sas_list_caslibs()              -> live SAS Viya CAS caslibs (B_SAS)
  - sas_query_adam(caslib,table,..) -> live SAS ADaM table slice (B_SAS)
  - ega_list_files(dataset)         -> live EGA dataset file listing (C_EGA)
  - ega_file_metadata(file_id)      -> live EGA file metadata (C_EGA)
Every live tool returns the uniform gateway envelope. When a source is
unreachable/unconfigured, ``data`` is null and ``error`` carries a typed reason
— rows are NEVER fabricated.

Server-side env required for graph tools: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD.
Server-side env for live tools (optional; unset -> honest 'unconfigured'):
  SYNAPSE_AUTH_TOKEN; SAS_CAS_HOST + (SAS_CAS_TOKEN or SAS_CAS_USER/PASSWORD);
  EGA_USERNAME/EGA_PASSWORD (EGA *metadata* is public and works with no creds).
Run:  python3 -m mcp_server.zeta_graph_mcp   (stdio transport)
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Optional

# make `federation` importable whether launched as module or script
_backend_dir = Path(__file__).resolve().parents[1]
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from mcp.server.fastmcp import FastMCP

from federation.cypher_guard import CypherWriteAttempt
from federation.graph_service import GraphService
from federation.source_gateway import SourceGateway

mcp = FastMCP("zeta-graph")

_service: Optional[GraphService] = None
_gateway: Optional[SourceGateway] = None


def _svc() -> GraphService:
    global _service
    if _service is None:
        _service = GraphService.from_env()
    return _service


def _gw() -> SourceGateway:
    global _gateway
    if _gateway is None:
        _gateway = SourceGateway.from_env()
    return _gateway


@mcp.tool()
def describe_schema() -> dict:
    """Return the graph schema: node labels with counts, relationship types with
    counts, and the id-prefix map that distinguishes the three federated
    endpoints (A_MSK / B_SAS / C_EGA)."""
    return _svc().schema()


@mcp.tool()
def search_nodes(prefix: str = "", label: str = "", type: str = "",
                 name_contains: str = "", limit: int = 50) -> dict:
    """Find nodes. Filter by id `prefix` (e.g. 'trial:sas:'), Neo4j `label`,
    semantic `type`, and/or `name_contains`. limit is capped at 200."""
    nodes = _svc().search(
        prefix=prefix or None, label=label or None, type_=type or None,
        name_contains=name_contains or None, limit=limit,
    )
    return {"count": len(nodes), "nodes": nodes}


@mcp.tool()
def get_node(node_id: str) -> dict:
    """Return a single node by its `id`: all properties, labels, endpoint,
    degree, and an immediate relationship-type summary."""
    node = _svc().get_node(node_id)
    if node is None:
        return {"error": f"Node not found: {node_id}"}
    return node


@mcp.tool()
def get_neighbors(node_id: str, hops: int = 1, rel_types: Optional[list[str]] = None,
                  direction: str = "both", cap: int = 500) -> dict:
    """Return the n-hop induced subgraph around `node_id` (nodes + edges).
    `hops` capped at 3; optional `rel_types` filter; `direction` in/out/both."""
    return _svc().neighbors(node_id, hops=hops, rel_types=rel_types,
                            direction=direction, cap=cap)


@mcp.tool()
def find_paths(source_id: str, target_id: str = "", target_prefix: str = "",
               max_hops: int = 5, k: int = 10) -> dict:
    """Find up to `k` shortest node-by-node paths from `source_id` to either a
    specific `target_id` or any node whose id starts with `target_prefix`
    (e.g. 'trial:sas:' to reach the SAS trial endpoint). `max_hops` capped at 5.
    Each path returns the full node-id chain, relationship-type sequence, and
    the source/target endpoint codes."""
    return _svc().find_paths(source_id, target_id=target_id or None,
                             target_prefix=target_prefix or None,
                             max_hops=max_hops, k=k)


@mcp.tool()
def run_cypher_readonly(cypher: str, params: Optional[dict[str, Any]] = None) -> dict:
    """Run a READ-ONLY Cypher query. Any write/DDL/security construct
    (CREATE/MERGE/DELETE/SET/REMOVE/DROP/LOAD CSV/admin) is rejected before it
    reaches the database. A LIMIT is auto-applied when absent."""
    try:
        return _svc().run_cypher(cypher, params=params)
    except CypherWriteAttempt as exc:
        return {"error": f"Read-only violation: {exc}"}


# --------------------------------------------------------------------------
# LIVE source tools (Session 15) — invoke the three source systems on demand.
# All read-only. All return the uniform SourceGateway envelope:
#   {endpoint, source, status: live|unreachable|unconfigured, latency_ms,
#    data: <real payload | null>, error: <typed reason | null>, grounding}
# data is null on every non-live path — rows are NEVER fabricated.
# --------------------------------------------------------------------------
@mcp.tool()
def list_sources() -> dict:
    """List the three LIVE source endpoints behind Zeta Bridge and their current
    status: A_MSK (Synapse), B_SAS (SAS Viya CAS), C_EGA (EGA). Performs a real
    connect handshake per endpoint and reports `configured` (are server-side
    credentials present) and `status` (live / unreachable / unconfigured) plus
    connection latency. No data is extracted — this is the discovery probe."""
    return _gw().health()


@mcp.tool()
def synapse_get_entity(syn_id: str) -> dict:
    """LIVE: fetch metadata for a Synapse entity by its synID (e.g. 'syn25569736')
    from the A_MSK endpoint. Metadata only (no file bytes). Returns the uniform
    envelope; unreachable/unconfigured yields a typed error with data=null."""
    return _gw().synapse.get_entity(syn_id).to_dict()


@mcp.tool()
def synapse_query_table(syn_id: str, limit: int = 50) -> dict:
    """LIVE: read up to `limit` rows (capped at 500) from a Synapse table entity
    on the A_MSK endpoint via a read-only ``SELECT * ... LIMIT n`` table query.
    Returns the uniform envelope; data is real rows or null on any failure."""
    return _gw().synapse.query_table(syn_id, limit).to_dict()


@mcp.tool()
def sas_list_caslibs() -> dict:
    """LIVE: list the available caslibs on the SAS Viya CAS server (B_SAS
    endpoint) via a read-only ``caslibinfo`` handshake. Returns the uniform
    envelope; unreachable (e.g. bad token / TLS) yields a typed error, data=null."""
    return _gw().sas.list_caslibs().to_dict()


@mcp.tool()
def sas_query_adam(caslib: str, table: str, limit: int = 50) -> dict:
    """LIVE: read up to `limit` rows (capped at 500) from a CDISC ADaM table
    (e.g. caslib='CASUSER', table='ADAE' for adverse events) on the SAS Viya CAS
    server (B_SAS endpoint) via a read-only ``head(n)``. Returns the uniform
    envelope with real clinical rows, or a typed error with data=null."""
    return _gw().sas.query_adam(caslib, table, limit).to_dict()


@mcp.tool()
def ega_list_files(dataset: str = "", limit: int = 50) -> dict:
    """LIVE: list files in an EGA dataset (C_EGA endpoint; default dataset
    EGAD00001011049 / BriTROC HGSOC sWGS) via the public EGA metadata API.
    Returns up to `limit` file accessions with size/checksum/locations. Listing
    + metadata only — controlled-access patient sequence bytes are NOT fetched.
    Returns the uniform envelope; data is real file metadata or null on failure."""
    return _gw().ega.list_files(dataset or None, limit).to_dict()


@mcp.tool()
def ega_file_metadata(file_id: str) -> dict:
    """LIVE: fetch metadata for a single EGA file by its accession (e.g.
    'EGAF00008095047') from the C_EGA endpoint via the public EGA metadata API.
    Metadata only (size, checksum, extension, locations); no byte download.
    Returns the uniform envelope; data is real metadata or null on failure."""
    return _gw().ega.file_metadata(file_id).to_dict()


def main() -> None:
    # confirm creds present early with a clear message
    if not (os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_USER")
            and os.environ.get("NEO4J_PASSWORD")):
        sys.stderr.write("[zeta-graph-mcp] NEO4J_URI/USER/PASSWORD not set in env\n")
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
