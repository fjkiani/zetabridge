"""End-to-end MCP smoke test: spawn the stdio server and call its tools.

Run:
  cd backend && env NEO4J_URI=... NEO4J_USER=... NEO4J_PASSWORD=... \
      python3 -m pytest mcp_server/test_mcp_client.py -q -s
"""

import json
import os
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.asyncio

_BACKEND = str(Path(__file__).resolve().parents[1])


async def _run_tools():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    env = dict(os.environ)
    env.setdefault("PYTHONPATH", _BACKEND)
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "mcp_server.zeta_graph_mcp"],
        env=env,
        cwd=_BACKEND,
    )
    out = {}
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            out["tools"] = [t.name for t in tools.tools]

            # describe_schema
            r = await session.call_tool("describe_schema", {})
            schema = json.loads(r.content[0].text)
            out["has_endpoints"] = set(schema.get("endpoint_prefixes", {}).keys()) == {"A_MSK", "B_SAS", "C_EGA"}

            # find_paths cross-endpoint (must match REST result)
            r = await session.call_tool("find_paths", {
                "source_id": "ega:file:EGAF00008095080",
                "target_prefix": "trial:sas:", "max_hops": 5, "k": 5,
            })
            fp = json.loads(r.content[0].text)
            out["path_count"] = fp.get("count", 0)
            out["first_path"] = fp["paths"][0] if fp.get("paths") else None

            # read-only guard via MCP
            r = await session.call_tool("run_cypher_readonly", {
                "cypher": "CREATE (n:HackViaMCP {id:'evil-mcp'}) RETURN n",
            })
            guard = json.loads(r.content[0].text)
            out["write_blocked"] = "error" in guard
    return out


async def test_mcp_e2e():
    out = await _run_tools()
    print("MCP tools:", out["tools"])
    print("first cross-endpoint path:", out["first_path"])
    assert set(out["tools"]) >= {
        "describe_schema", "search_nodes", "get_node",
        "get_neighbors", "find_paths", "run_cypher_readonly",
    }
    assert out["has_endpoints"] is True
    assert out["path_count"] >= 1
    assert out["first_path"]["source_endpoint"] == "C_EGA"
    assert out["first_path"]["target_endpoint"] == "B_SAS"
    assert out["write_blocked"] is True
