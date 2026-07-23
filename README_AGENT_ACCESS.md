# Zeta Bridge — Agent Access Guide

This document shows how an **external agent** connects to the Zeta Bridge
federated knowledge graph and pulls data. Two interfaces are provided over the
**same** read-only query core:

1. **REST API** — `/api/graph/*` on the FastAPI backend (any HTTP agent).
2. **MCP server** — `zeta-graph` stdio server (MCP/LLM agents, e.g. Claude Desktop).

Both expose the real federated Neo4j graph (**30,904 nodes / 102,760 edges** as
of Session 12; the graph grows additively) connecting three endpoints:

| Code | Endpoint | Example id-prefixes |
|------|----------|---------------------|
| `A_MSK` | MSK SPECTRUM / Synapse (HGSOC single-cell + genomic) | `genomicfeature:msk:`, `biospecimen:msk:`, `cohort:msk`, `vault:synapse` |
| `B_SAS` | PDS / SAS solid-tumor clinical trials | `patient:sas:`, `trial:sas:`, `arm:sas:`, `clinical_table:sas:`, `vault:sas` |
| `C_EGA` | EGA EGAD00001011049 / BriTROC HGSOC sWGS | `ega:file:`, `ega:sample:`, `specimen:britroc1:`, `cohort:britroc`, `vault:ega` |

## Security model (read this first)

- **The graph is read-only over these interfaces.** Every request runs in a
  Neo4j `READ` transaction, and the free-form `/cypher` endpoint is gated by a
  validator that rejects *any* write/DDL/security construct
  (`CREATE / MERGE / DELETE / DETACH / SET / REMOVE / DROP / FOREACH / LOAD CSV`,
  all admin/security commands, and write procedures) **before** the query
  reaches the database. Rejected writes return HTTP `403` (REST) or an `error`
  field (MCP) and never mutate the graph.
- **Neo4j credentials are never given to the consumer.** They live server-side
  only. The consumer authenticates with a scoped API key.
- **Auth header:** every REST call must send `X-Zeta-Api-Key: <key>`. A missing
  or wrong key returns `401`. If the server has no key configured, endpoints
  return `503` (closed by default — never open unauthenticated).
- Results are capped (default 1000 rows) and a `LIMIT` is auto-applied to any
  `/cypher` query that lacks one.

> Note: this Neo4j Aura instance does not permit creating DB-level users/roles,
> so read-only is enforced at the application layer (operation allow-list +
> Cypher validator). This is the correct pattern for third-party exposure
> anyway — DB credentials are never handed out.

---

## Option A — REST API

Base URL: `http://<host>:8000` (dev) — all paths below are under `/api/graph`.

### Endpoints

| Method | Path | Body / params | Returns |
|--------|------|---------------|---------|
| `GET`  | `/api/graph/health` | — | driver status + node/edge counts |
| `GET`  | `/api/graph/schema` | — | labels+counts, relationship types+counts, endpoint prefixes |
| `GET`  | `/api/graph/node/{id}` | id in path | one node: props, labels, endpoint, degree, rel summary |
| `POST` | `/api/graph/search` | `{prefix?, label?, type?, name_contains?, limit<=200}` | matching nodes |
| `POST` | `/api/graph/neighbors` | `{id, hops(1-3), rel_types?, direction(in/out/both), cap<=2000}` | n-hop induced subgraph |
| `POST` | `/api/graph/paths` | `{source_id, target_id \| target_prefix, max_hops(1-5), k<=25}` | ranked node-by-node paths |
| `POST` | `/api/graph/cypher` | `{cypher, params?, cap?}` | validated read-only Cypher result |

### curl examples

```bash
KEY="your-api-key"
BASE="http://localhost:8000/api/graph"

# 1. health
curl -s -H "X-Zeta-Api-Key: $KEY" "$BASE/health"

# 2. schema (labels, relationship types, endpoint prefix map)
curl -s -H "X-Zeta-Api-Key: $KEY" "$BASE/schema"

# 3. a single node
curl -s -H "X-Zeta-Api-Key: $KEY" \
  "$BASE/node/genomicfeature:msk:mut:NF1"

# 4. search the SAS trial endpoint
curl -s -H "X-Zeta-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"prefix":"trial:sas:","limit":5}' "$BASE/search"

# 5. 2-hop neighborhood of a bridge gene (reaches adverse events via BRIDGE_EDGE)
curl -s -H "X-Zeta-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"id":"genomicfeature:msk:mut:NF1","hops":2,"cap":500}' "$BASE/neighbors"

# 6. cross-endpoint path: an EGA file -> any SAS trial
curl -s -H "X-Zeta-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"source_id":"ega:file:EGAF00008095080","target_prefix":"trial:sas:","max_hops":5,"k":5}' \
  "$BASE/paths"

# 7. read-only Cypher
curl -s -H "X-Zeta-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"cypher":"MATCH (n:ZetaTrial) RETURN n.id AS id LIMIT 5"}' "$BASE/cypher"
```

### Python example

```python
import requests

BASE = "http://localhost:8000/api/graph"
HDR = {"X-Zeta-Api-Key": "your-api-key"}

# find how an EGA sequencing file connects to the SAS trial endpoint
r = requests.post(f"{BASE}/paths", headers=HDR, json={
    "source_id": "ega:file:EGAF00008095080",
    "target_prefix": "trial:sas:",
    "max_hops": 5, "k": 5,
})
for p in r.json()["paths"]:
    chain = " -> ".join(p["node_ids"])
    print(f'[{p["hops"]} hops] {p["source_endpoint"]}->{p["target_endpoint"]}: {chain}')
    print("   via:", " / ".join(p["rel_types"]))
```

Expected (real) output includes:

```
[3 hops] C_EGA->B_SAS: ega:file:EGAF00008095080 -> specimen:britroc1:IM_135 -> community:louvain:s11:4 -> trial:sas:Pancrea_Multipl_2020_430
   via: SAME_FILE_AS / MEMBER_OF_COMMUNITY / MEMBER_OF_COMMUNITY
```

---

## Option B — MCP server

The `zeta-graph` MCP server exposes the same capabilities as MCP **tools** for
LLM/agent frameworks. It talks stdio and calls the graph service in-process.

### Tools

| Tool | Purpose |
|------|---------|
| `describe_schema()` | labels + counts, relationship types + counts, endpoint prefixes |
| `search_nodes(prefix, label, type, name_contains, limit)` | find nodes |
| `get_node(node_id)` | one node with degree + relationship summary |
| `get_neighbors(node_id, hops, rel_types, direction, cap)` | n-hop subgraph |
| `find_paths(source_id, target_id, target_prefix, max_hops, k)` | node-by-node paths |
| `run_cypher_readonly(cypher, params)` | validated read-only Cypher |

### Register the server (`mcp.json`)

```json
{
  "mcpServers": {
    "zeta-graph": {
      "command": "python3",
      "args": ["-m", "mcp_server.zeta_graph_mcp"],
      "cwd": "/absolute/path/to/zetabridge/backend",
      "env": {
        "NEO4J_URI": "neo4j+s://<host>.databases.neo4j.io",
        "NEO4J_USER": "<neo4j-user>",
        "NEO4J_PASSWORD": "<neo4j-password>"
      }
    }
  }
}
```

The agent then calls, e.g., `find_paths(source_id="ega:file:EGAF00008095080",
target_prefix="trial:sas:")` and receives the same path chains as the REST API.

### Run the MCP server directly

```bash
cd backend
NEO4J_URI=... NEO4J_USER=... NEO4J_PASSWORD=... \
  python3 -m mcp_server.zeta_graph_mcp
```

---

## Traversal tips for the consuming agent

- **Cross endpoints by id-prefix.** To go from any node to the SAS trial layer,
  use `find_paths(..., target_prefix="trial:sas:")`. For the EGA file layer use
  `ega:file:`, for MSK genomics use `genomicfeature:msk:`.
- **Known structural bridges** (short cross-endpoint hops):
  - `SAME_FILE_AS` links `specimen:britroc1:*` ↔ `ega:file:*` (1 hop).
  - `MEMBER_OF_COMMUNITY` links specimens/genes into shared `community:louvain:*`
    nodes that also touch trials.
  - `BRIDGE_EDGE` links `genomicfeature:msk:*` mutations to adverse-event terms.
  - `SPECIMEN_OF` / cohort nodes link MSK biospecimens to the HGSOC cohort.
- **High-degree hubs** (e.g. `genomicfeature:msk:mut:NF1`, biospecimen HARBORS
  hubs) fan out fast — keep `hops<=2` and use `cap` to bound the subgraph.
- Use `run_cypher_readonly` for anything the structured endpoints don't cover;
  it accepts read patterns (`MATCH/OPTIONAL MATCH/WITH/WHERE/RETURN/UNWIND/
  ORDER/LIMIT/SKIP`, `shortestPath`, allow-listed read procedures) and rejects
  everything else.

## Tests

```bash
cd backend
# Cypher read-only validator (attack suite)
python3 -m pytest federation/test_cypher_guard.py -q
# REST integration (needs live Neo4j creds + ZETA_GRAPH_API_KEY in env)
python3 -m pytest routers/test_graph_api.py -q
# MCP end-to-end (needs live Neo4j creds in env)
python3 -m pytest mcp_server/test_mcp_client.py -q -s --asyncio-mode=auto
```

## Front-end (Session 13)

The React/Vite app (`artifacts/zetabridge/`) now ships four **read-only** surfaces
built directly on top of the `/api/graph/*` layer above. The graph is never
mutated from the browser.

### Surfaces

- **Graph Explorer** (`/#/graph`) — search nodes by endpoint / label / name,
  click a result to render its n-hop neighborhood in a D3 force graph, and open
  a detail panel per node. Nodes are colored by endpoint (A_MSK = cyan,
  B_SAS = gold, C_EGA = purple).
- **Path Finder** (Explore/Path tabs inside `/#/graph`) — pick a source node and
  a target endpoint; the app calls `POST /paths` (`target_prefix` routing) and
  renders ranked, node-by-node cross-endpoint paths with the path highlighted in
  the graph. This is the "search for opportunities" surface.
- **Insights** (`/#/insights`) — the Session-12 deep-traversal findings
  (reachability, structural bridges, cross-endpoint path summaries, deep chains)
  as browsable cards. Each card deep-links into Graph Explorer
  (`#/graph?focus=<id>`).
- **Connectors** (`/#/connectors`) — the endpoint registry plus an
  **Add a connection** flow. It builds a *draft-only* mint proposal
  (`mint_proposal_<source>_s13.json`, tagged `_session:13, _draft:true`) that you
  download and review; it is **never** pushed to the live graph.

### Running it

```bash
cd artifacts/zetabridge
pnpm install

# point the app at a running backend + supply the graph API key
export VITE_API_BASE="http://localhost:8000"     # your /api/graph host
export VITE_ZETA_API_KEY="<ZETA_GRAPH_API_KEY>"  # sent as X-Zeta-Api-Key

pnpm run dev                    # dev server
# or
pnpm run build && npx serve dist/public   # production build
```

**Live vs. snapshot.** When `VITE_API_BASE` + `VITE_ZETA_API_KEY` are set and the
backend is reachable, the app queries live Neo4j. If they are unset or the API is
unreachable, it transparently falls back to a bundled real export at
`public/graph-snapshot/graph-snapshot.json` (regenerate with
`python scripts/s13/export_snapshot.py`) so the UI is fully browsable offline.
