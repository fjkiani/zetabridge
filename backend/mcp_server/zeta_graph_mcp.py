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
    """List the four LIVE source endpoints behind Zeta Bridge and their current
    status: A_MSK (Synapse), B_SAS (SAS Viya CAS), C_EGA (EGA), D_ARGO (ICGC
    ARGO). Performs a real connect handshake per endpoint and reports
    `configured` (are server-side credentials present) and `status` (live /
    unreachable / unconfigured) plus connection latency. No data is extracted —
    this is the discovery probe."""
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


# --------------------------------------------------------------------------
# ICGC ARGO (D_ARGO) live tools — Overture SONG/SCORE, DACO controlled genomics.
# Download is a TOKEN-HANDOFF: argo_download_url mints a short-lived pre-signed
# URL; the caller streams bytes DIRECTLY from object storage. Bytes are never
# proxied through Zeta Bridge (that would reintroduce a bandwidth bottleneck).
# --------------------------------------------------------------------------
@mcp.tool()
def argo_list_entities(project: str = "", access: str = "", file_type: str = "", size: int = 50) -> dict:
    """LIVE: search the ICGC ARGO SCORE object registry (D_ARGO endpoint). Filter
    by `project` (e.g. 'POG-CA'), `access` ('controlled'/'open'), and `file_type`
    (extension, e.g. 'cram','bam','vcf'). Returns up to `size` (capped 500) real
    objects, each with object_id, gnos_id, and donor/sample/experiment derived
    from the ARGO filename convention. Registry listing works without the token;
    controlled *content* download requires the DACO token at resolve time.
    Returns the uniform envelope; data is real objects or null on failure."""
    return _gw().argo.list_entities(project or None, access or None, file_type or None, size).to_dict()


@mcp.tool()
def argo_entity_metadata(object_id: str) -> dict:
    """LIVE: fetch one ICGC ARGO object's registry metadata + derived
    relationships (project -> donor -> sample -> file, experiment, date) by its
    object_id (a UUID). Returns the uniform envelope; data is real metadata or
    null (typed error) on failure."""
    return _gw().argo.entity_metadata(object_id).to_dict()


@mcp.tool()
def argo_download_url(object_id: str, offset: int = 0, length: int = -1) -> dict:
    """LIVE: mint a short-lived pre-signed SCORE download URL for a controlled
    ICGC ARGO object (D_ARGO endpoint). Returns the uniform envelope whose
    `data.parts[].url` is a direct object-storage URL — the CALLER streams bytes
    directly from `data.object_host`; Zeta Bridge never proxies the bytes. Also
    returns object_md5 and object_size for verification. Requires the DACO token;
    an invalid/non-entitled token yields a typed auth error with data=null. No
    bytes are transferred by this call."""
    return _gw().argo.resolve_download(object_id, offset, length).to_dict()


@mcp.tool()
def argo_graph_neighbors(node_id: str, hops: int = 1, cap: int = 200) -> dict:
    """READ-ONLY graph traversal of the ICGC ARGO subgraph loaded in Neo4j.
    Given an ARGO node id (e.g. 'argo:donor:DO256421', 'argo:file:<object_id>',
    'argo:sample:SA621283'), return the n-hop induced subgraph (nodes + edges)
    over Argo* nodes: Program-[:HAS_DONOR]->Donor-[:HAS_SAMPLE]->Sample-
    [:HAS_FILE]->File, plus Sample-[:SERIAL_WITH]->Sample for same-donor serial
    pairs. `hops` capped at 3. Requires the ARGO subgraph to have been loaded."""
    return _svc().neighbors(node_id, hops=hops, direction="both", cap=cap)


# --------------------------------------------------------------------------
# Vault (Qdrant) tools (Session 16) — read-only RAG over the `zeta_vault`
# vector collection. Same anti-blind-guess contract as describe_schema():
# call vault_manifest() ONCE to learn the collection config, the filterable
# fields with their LIVE value vocabularies, the available search modes, and
# worked examples — then call vault_search(). Qdrant credentials stay
# server-side. Requires QDRANT_URL + QDRANT_API_KEY; absent -> a typed error,
# never a fabricated result.
# --------------------------------------------------------------------------
_vault_svc = None


def _vault():
    global _vault_svc
    if _vault_svc is None:
        from federation.vault_store import get_vault_service

        _vault_svc = get_vault_service()
    return _vault_svc


@mcp.tool()
def vault_manifest() -> dict:
    """Discovery verb for the federated vector store (Qdrant `zeta_vault`). Call
    this FIRST. Returns — computed LIVE from Qdrant — the collection point count,
    vector config (dense 2048/Cosine + bm25 sparse), the embedding model and
    whether dense semantic search is currently enabled, every filterable payload
    field WITH its real value vocabulary + counts (via facet), the available
    search modes, and worked example queries. An agent that reads this cannot
    guess wrong about what to send to vault_search()."""
    try:
        return _vault().manifest()
    except Exception as exc:
        return {"error": f"vault unavailable: {exc}"}


@mcp.tool()
def vault_search(query: str = "", mode: str = "filter",
                 filters: Optional[dict] = None, limit: int = 10) -> dict:
    """Read-only search over the vault. `mode`: 'filter' (exact payload-filter
    lookup over indexed fields — always available), 'dense' (semantic Cosine;
    only if OPENROUTER_API_KEY configured), or 'bm25' (lexical sparse; only if
    fastembed installed). `filters` maps a filterable field (see vault_manifest)
    to an exact value. `limit` capped at 100. Unavailable modes return a typed
    error — results are NEVER fabricated."""
    try:
        return _vault().search(query=query, mode=mode, filters=filters, limit=limit)
    except Exception as exc:
        return {"error": f"vault search failed: {exc}"}


def main() -> None:
    # confirm creds present early with a clear message
    if not (os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_USER")
            and os.environ.get("NEO4J_PASSWORD")):
        sys.stderr.write("[zeta-graph-mcp] NEO4J_URI/USER/PASSWORD not set in env\n")
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
