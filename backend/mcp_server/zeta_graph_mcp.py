"""Zeta Bridge Graph — MCP stdio server.

Exposes the federated Neo4j knowledge graph to MCP-capable agents (Claude
Desktop, IDE agents, etc.) as read-only tools. Wraps the SAME in-process
``GraphService`` as the REST router — no duplicated Neo4j logic, no second
security surface.

Tools:
  - describe_schema()               -> labels, relationship types + counts, endpoint prefixes
  - search_nodes(...)               -> nodes by prefix/label/type/name
  - get_node(node_id)               -> single node w/ degree + rel summary
  - get_neighbors(node_id, hops...) -> n-hop induced subgraph
  - find_paths(source_id, ...)      -> ranked node-by-node cross-endpoint paths
  - run_cypher_readonly(cypher,...) -> validated read-only Cypher

Server-side env required: NEO4J_URI, NEO4J_USER, NEO4J_PASSWORD.
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

mcp = FastMCP("zeta-graph")

_service: Optional[GraphService] = None


def _svc() -> GraphService:
    global _service
    if _service is None:
        _service = GraphService.from_env()
    return _service


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


def main() -> None:
    # confirm creds present early with a clear message
    if not (os.environ.get("NEO4J_URI") and os.environ.get("NEO4J_USER")
            and os.environ.get("NEO4J_PASSWORD")):
        sys.stderr.write("[zeta-graph-mcp] NEO4J_URI/USER/PASSWORD not set in env\n")
    mcp.run()  # stdio transport


if __name__ == "__main__":
    main()
