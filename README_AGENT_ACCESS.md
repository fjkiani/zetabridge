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

---

## Signal intelligence API (Session 14)

A second read-only router, `/api/signals`, turns the graph into ranked,
**grounded** intelligence. Every value is precomputed on a node/edge and traced
back in the response — nothing is re-derived or fabricated. Auth is the same
`X-Zeta-Api-Key` header as `/api/graph`, and it loads in both store modes.

### Endpoints

| Method | Path | Returns |
|---|---|---|
| GET | `/api/signals/health` | liveness |
| GET | `/api/signals/overview` | totals, endpoints, reachability headline, top broker, signal counts, blind-spot count |
| GET | `/api/signals/top?family=<f>&limit=<n>` | ranked signals for a family (`all` interleaves families round-robin) |
| GET | `/api/signals/bridges` | genomic→AE cross-endpoint bridges |
| GET | `/api/signals/gaps` | quantified blind spots (KBGaps) |
| POST | `/api/signals/agent` | run a grounded agent (see below) |
| GET | `/api/signals/{slug}` | full attributes + connecting subgraph for one signal |

Families: `all`, `drug_ae`, `pharmacovig`, `genomic_bridge`, `cross_trial`,
`outlier`. Each is ranked by its **native** metric (`rate_ratio`, `ror`,
`bridge_score`, `consistency_score`, `rate_ratio`) plus a clearly-labeled derived
`strength_derived ∈ [0,1]` for cross-family ordering only.

### curl examples

```bash
export API=http://localhost:8000 KEY=<ZETA_GRAPH_API_KEY>

# overview (the value numbers)
curl -s -H "X-Zeta-Api-Key: $KEY" "$API/api/signals/overview"

# strongest genomic→AE bridges
curl -s -H "X-Zeta-Api-Key: $KEY" "$API/api/signals/bridges"

# top 5 pharmacovigilance (ROR) signals
curl -s -H "X-Zeta-Api-Key: $KEY" "$API/api/signals/top?family=pharmacovig&limit=5"

# run the gap auditor (grounded; returns grounding[] node ids)
curl -s -H "X-Zeta-Api-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"agent":"gap_auditor","action":"list_gaps"}' \
  "$API/api/signals/agent"
```

### The 3 backend agents (`POST /api/signals/agent`)

Body: `{ "agent": <name>, "action"?: str, "family"?: str, "slug"?: str, "limit"?: int }`.
Response envelope: `{ agent, status, benchmark, summary, findings, grounding[], used_llm, ... }`.

- **`signal_miner`** — rank/explain signals, drug-toxicity profiles.
- **`bridge_hunter`** — find/explain genomic→AE bridges.
- **`gap_auditor`** — surface blind spots and what closing each unlocks.

Narration via Groq is optional (`GROQ_API_KEY`, `GROQ_MODEL` default
`llama-3.3-70b-versatile`) and **never invents numbers**; with no key the
deterministic summary is returned and `used_llm=false`. A fabrication-guard test
fails the build if a narrative contains a number not present in the grounded facts.

### Tests

```bash
# signals service values + agent fabrication guard (needs live Neo4j + ZETA_GRAPH_API_KEY)
pytest backend/routers/test_signals_api.py -q
```

---

## Front-end (Session 14 additions)

Session 14 adds the value/intelligence surfaces and two read-only copilots on top
of the Session 13 app.

### New surfaces

- **Signal Intel** (`/#/signals`) — ranked hub across all five families with a
  value strip (nodes, edges, signals, blind spots, reachability). Each card shows
  the native metric and the labeled derived strength, and links to a per-slug page.
- **Signal detail** (`/#/signals/<slug>`) — full attributes, a focused subgraph of
  the connecting nodes/edges, and an "Open in Graph Explorer" deep-link.
- **Bridges** (`/#/bridges`) — genomic→AE cross-endpoint bridges (MSK gene ↔ SAS
  adverse event) with a network view and the strongest-bridge spotlight.
- **Blind Spots** (`/#/gaps`) — the quantified KBGaps (what pharma got wrong),
  each with impact, fix path, and a "draft a fix proposal" link into Connectors.
- **Value / Moat** (`/#/value`) — the acquisition thesis wired to live numbers.
- **Insight Analyst** (`/#/analyst`) — chat copilot #1; routes a question to the
  right backend agent and renders grounded findings + graph deep-links.
- **Connection Guide** (in `/#/connectors`) — copilot #2; pick a blind spot
  (deep-linkable via `#/connectors?gap=<slug>`), get a grounded `gap_auditor`
  explanation, then draft an additive mint proposal as the fix.

The two chat copilots require the live backend (grounded server-side Cypher, no
offline fabrication); all data browsing works offline from the snapshot.

**Snapshot v2.** `public/graph-snapshot/graph-snapshot.json` is now
`schema_version: 2` — a superset that adds `overview`, `signals`, `bridges`, and
`gaps` so the value surfaces render offline. Regenerate with
`python scripts/s14/export_snapshot_v2.py`. The Session 13 snapshot is preserved
as `graph-snapshot-v1.json`.

---

## Live source extraction (Session 15) — connect to the 3 source systems directly

`/api/graph` and `/api/signals` read the **pre-extracted** federated graph.
Session 15 adds a third router, **`/api/sources`**, that invokes the **live
source systems on demand** so an agent can pull *fresh* records that were never
ingested into Neo4j:

| Endpoint | Live system | Client library | Auth |
|---|---|---|---|
| `A_MSK` | **Synapse** (Sage Bionetworks) | `synapseclient` | `SYNAPSE_AUTH_TOKEN` (server-side) |
| `B_SAS` | **SAS Viya CAS** (`mpmprodvdmml.ondemand.sas.com`) | `swat` | `SAS_CAS_TOKEN` **or** `SAS_CAS_USER`+`SAS_CAS_PASSWORD` (server-side) |
| `C_EGA` | **EGA** dataset `EGAD00001011049` (BriTROC) | EGA REST (public metadata) | none for metadata (`EGA_USERNAME`/`EGA_PASSWORD` only for controlled ops) |

### One-key onboarding for an external agent

You configure **one** thing to consume everything: the same
`X-Zeta-Api-Key` you already use for `/api/graph`. **The three source tokens stay
entirely server-side** — the backend holds them and extracts on your behalf; a
caller never sees or sends them. This is a deliberate security choice: EGA is
controlled-access human patient genomics, SAS CAS is a live clinical-trials
warehouse, and Synapse holds MSK data — committing those tokens to source would
put them in git history permanently. You get the same outcome ("connect once,
extract live") with one scoped key instead of three raw credentials.

### Honesty contract (the same no-fabrication guarantee as the graph)

Every `/api/sources` response is a uniform **envelope**:

```json
{
  "endpoint": "C_EGA",
  "source": "ega",
  "status": "live",                 // "live" | "unreachable" | "unconfigured"
  "action": "list_files",
  "latency_ms": 512.3,
  "data": [ ... real rows ... ],    // present IFF status == "live", else null
  "error": null,                    // typed reason when not live, else null
  "grounding": { "dataset": "EGAD00001011049", "n_files": 3 }
}
```

- `status: "live"` → real data returned; `data` is populated; `grounding` proves
  what was touched (dataset id, syn id, caslib/table, row count).
- `status: "unconfigured"` → the server has no token / the client lib is absent.
  `data` is `null`. **Not an error, not fabricated** — just honest "not wired up here."
- `status: "unreachable"` → a real connection was attempted and failed
  (`auth`, `tls_ca_unavailable`, network/timeout). `data` is `null`; `error`
  carries the typed reason.

`data` is `null` on **every** non-live path — there is no snapshot substitution
and no invented rows. A 24-case gateway contract-test suite asserts this
invariant for all three clients (the analogue of the agent fabrication guard).

### REST endpoints (`/api/sources`, `X-Zeta-Api-Key` required)

| Method | Path | Params | Returns |
|---|---|---|---|
| GET | `/api/sources/health` | — | per-endpoint connect handshake + `configured?` flags (no data) |
| GET | `/api/sources/synapse/whoami` | — | Synapse auth handshake (profile) |
| GET | `/api/sources/synapse/entity/{syn_id}` | syn id in path | one Synapse entity's metadata |
| GET | `/api/sources/synapse/table/{syn_id}` | `limit` (default 50) | real rows from a Synapse table |
| GET | `/api/sources/sas/caslibs` | — | CAS caslibs visible to the session |
| GET | `/api/sources/sas/adam` | `caslib`, `table`, `limit` | real ADaM rows (e.g. `ADAE`) |
| GET | `/api/sources/ega/files` | `dataset` (default `EGAD00001011049`), `limit` | real EGA file accessions + sizes + MD5 |
| GET | `/api/sources/ega/file/{file_id}` | file id in path | one EGA file's metadata |

Same auth semantics as the rest of the API: **401** without/with a wrong key,
**503** when the server has no `ZETA_GRAPH_API_KEY` configured (closed by default).

```bash
export API=http://localhost:8000 KEY=<ZETA_GRAPH_API_KEY>

# which sources are wired up here?
curl -s -H "X-Zeta-Api-Key: $KEY" "$API/api/sources/health"

# live EGA extraction — works with NO source token (public metadata)
curl -s -H "X-Zeta-Api-Key: $KEY" \
  "$API/api/sources/ega/files?dataset=EGAD00001011049&limit=3"

# live Synapse entity (returns status:"unconfigured" if SYNAPSE_AUTH_TOKEN unset)
curl -s -H "X-Zeta-Api-Key: $KEY" "$API/api/sources/synapse/entity/syn25569736"

# live SAS ADaM adverse-event slice (needs SAS_CAS creds server-side)
curl -s -H "X-Zeta-Api-Key: $KEY" \
  "$API/api/sources/sas/adam?caslib=CASUSER&table=ADAE&limit=20"
```

### New MCP live tools (alongside the 6 graph tools — 13 total)

The `zeta-graph` MCP server now exposes seven live-extraction tools on the same
stdio server and the same server-side credential surface:

| Tool | Purpose |
|---|---|
| `list_sources()` | the 3 endpoints + their `configured?` / `live?` status |
| `synapse_get_entity(syn_id)` | one Synapse entity's metadata (live) |
| `synapse_query_table(syn_id, limit)` | real rows from a Synapse table (live) |
| `sas_list_caslibs()` | CAS caslibs (live) |
| `sas_query_adam(caslib, table, limit)` | real ADaM rows (live) |
| `ega_list_files(dataset)` | real EGA file accessions/metadata (live; public) |
| `ega_file_metadata(file_id)` | one EGA file's metadata (live) |

Each returns the same envelope and the same typed-error behavior as the REST
routes. Server-side source env vars go in your **local** `mcp.json` copy (see the
`_live_source_secrets_note` block in `backend/mcp_server/mcp.json`) — leave any
unset and that tool honestly reports `unconfigured`. EGA metadata works with no
credentials at all.

### Server-side secret configuration

All source secrets are read from env in `backend/config.py` (all default empty):

```
SYNAPSE_AUTH_TOKEN=            # Synapse personal access token
SAS_CAS_HOST=mpmprodvdmml.ondemand.sas.com
SAS_CAS_PORT=443
SAS_CAS_PROTOCOL=https
SAS_CAS_TOKEN=                 # OAuth token  (or use USER/PASSWORD below)
SAS_CAS_USER=
SAS_CAS_PASSWORD=
SAS_CAS_CADATA=                # optional CA bundle path if TLS cert issue
EGA_USERNAME=                  # only for controlled ops; metadata is public
EGA_PASSWORD=
EGA_DEFAULT_DATASET=EGAD00001011049
```

- **Local:** put them in a git-ignored `.env` (see `.env.example`), never in a
  committed file.
- **Render:** the env var **names** are declared in `render.yaml` with
  `sync: false`; set the **values** as Render dashboard secrets. Nothing sensitive
  lives in the repo.

### Tests

```bash
cd backend
# gateway contract tests (mocked; no live creds needed) — includes no-fabrication guard
python3 -m pytest federation/test_source_gateway.py -q
# REST auth + envelope passthrough
python3 -m pytest routers/test_sources_api.py -q
# live smoke — hits the REAL sources and PRINTS what returned (visible proof).
# EGA prints real accessions/sizes/MD5 with no creds; Synapse/SAS run wherever
# their tokens are configured, otherwise skip honestly as "unconfigured".
python3 federation/live_smoke.py
```

## Live source extraction (Session 16) — ICGC ARGO (`D_ARGO`), controlled BAM/CRAM by direct-from-S3 handoff

Session 16 adds a **fourth** live source — **ICGC ARGO** (the Overture
SONG/SCORE stack at `api.platform.icgc-argo.org`) — for DACO-controlled cancer
genomics (WGS/WXS **CRAM**, RNA-seq **BAM**). It follows the exact same
`/api/sources` + MCP + envelope pattern as the other three, with one crucial
transport difference for the heavy bytes.

| Endpoint | Live system | Auth | Data |
|---|---|---|---|
| `D_ARGO` | **ICGC ARGO** — Overture SONG + SCORE (`/storage-api`) | `ICGC_ARGO_TOKEN` (DACO-approved, server-side) | controlled BAM/CRAM object registry + pre-signed download URLs |

### The transport contract (read this — it is the whole point)

**ZetaBridge is a URL-minting coordinator, not a byte pipe.** For everything
except the actual alignment bytes, the flow is identical to the other sources
(call an endpoint, get an envelope). For the bytes:

1. You call `download-url/{object_id}` (REST) or `argo_download_url(object_id)`
   (MCP) with your `X-Zeta-Api-Key`.
2. The backend uses the **server-side** DACO token to mint a **short-lived
   pre-signed URL** to object storage (`object.genomeinformatics.org`) and
   returns it in `data.parts[].url` (plus `object_md5`, `object_size`).
3. **You stream the bytes DIRECTLY from that pre-signed URL** (S3-style HTTP
   `GET`, `Range` supported → slice a genomic region without pulling the whole
   file). **The bytes never transit ZetaBridge.**

This is deliberate: proxying a ~9 GB CRAM through the app dyno is the exact
bandwidth wall that throttles a proxied path. Direct-from-object-storage
measured **~10× faster** in probing. The DACO token is never exposed to you —
you present only the scoped `X-Zeta-Api-Key`, same as everything else.

### REST endpoints (`/api/sources/argo`, `X-Zeta-Api-Key` required)

| Method | Path | Params | Returns |
|---|---|---|---|
| GET | `/api/sources/argo/health` | — | token-`configured?` + a cheap SCORE liveness ping (no data) |
| GET | `/api/sources/argo/entities` | `project`, `access`, `file_type`, `size` (default 50) | real object registry rows (`object_id`, `file_name`, `gnos_id`, `project_code`, `access`) + donor/sample/experiment derived from the filename convention |
| GET | `/api/sources/argo/entity/{object_id}` | object id in path | one object's registry metadata (`404` if unknown) |
| GET | `/api/sources/argo/download-url/{object_id}` | `offset` (default 0), `length` (default -1 = whole object) | **minted pre-signed URL spec** (`data.parts[].url`, `object_md5`, `object_size`, `object_host`) for direct-from-S3 streaming |

Failure typing on `download-url`: **401** invalid/expired token, **403** token
lacks DACO controlled-data access, **404** unknown object, **503** token unset,
**504** timeout, **502** other upstream. On every failure path `data` is `null`
and **no URL is returned** — a pre-signed URL is minted only on the live path.

```bash
export API=http://localhost:8000 KEY=<ZETA_GRAPH_API_KEY>

# is ARGO wired up + is SCORE alive?
curl -s -H "X-Zeta-Api-Key: $KEY" "$API/api/sources/argo/health"

# find controlled CRAMs in a project (client-side filters on extension/access)
curl -s -H "X-Zeta-Api-Key: $KEY" \
  "$API/api/sources/argo/entities?project=POG-CA&access=controlled&file_type=cram&size=10"

# mint a pre-signed URL, then stream a 1 MiB Range slice DIRECT from object storage:
URL=$(curl -s -H "X-Zeta-Api-Key: $KEY" \
  "$API/api/sources/argo/download-url/<object_id>" | python3 -c 'import sys,json;print(json.load(sys.stdin)["data"]["parts"][0]["url"])')
curl -s -H "Range: bytes=0-1048575" "$URL" -o slice.bin   # ZetaBridge is NOT in this hop
```

### New MCP live tools (ARGO — 4 more, alongside the 13 → 17 total)

| Tool | Purpose |
|---|---|
| `argo_list_entities(project, access, file_type, size)` | search the controlled object registry (live) |
| `argo_entity_metadata(object_id)` | one object's metadata + derived donor/sample fields (live) |
| `argo_download_url(object_id, offset, length)` | mint a pre-signed URL for **direct-from-object-storage** streaming (bytes do not flow through ZetaBridge) |
| `argo_graph_neighbors(node_id, hops, cap)` | read-only Neo4j traversal of the loaded ARGO subgraph (`argo:donor:DO…`, `argo:sample:SA…`, `argo:file:<object_id>`) |

Same envelope + typed-error behavior as the other tools. `argo_download_url`
returns the URL spec only; you do the direct stream.

### The ARGO knowledge subgraph (read-only / additive to Neo4j)

`backend/federation/argo_graph_loader.py` loads a bounded ARGO cohort into Neo4j
as an **additive, `Argo*`-namespaced** subgraph — it never mutates the existing
MSK/SAS/EGA nodes:

- Nodes: `(:ArgoProgram)-[:HAS_DONOR]->(:ArgoDonor)-[:HAS_SAMPLE]->(:ArgoSample)-[:HAS_FILE]->(:ArgoFile)`.
- `(:ArgoSample)-[:SERIAL_WITH]->(:ArgoSample)` links same-donor serial samples
  (diagnosis/relapse timepoints) → enables serial-sample genomic-instability
  analysis.
- Node ids are namespaced (`argo:program:<code>`, `argo:donor:<DO…>`,
  `argo:sample:<SA…>`, `argo:file:<object_id>`); the loader is idempotent
  (`MERGE`), so re-running does not duplicate.

### Server-side secret configuration (ARGO)

```
ICGC_ARGO_TOKEN=              # DACO-approved token (server-side only, never sent to callers)
ICGC_ARGO_API_BASE=https://api.platform.icgc-argo.org
ICGC_ARGO_OBJECT_HOST=object.genomeinformatics.org
```

Local: git-ignored `.env` (see `.env.example`). Render: names declared in
`render.yaml` with `sync: false`; values set as dashboard secrets. Leave
`ICGC_ARGO_TOKEN` unset → ARGO reports `unconfigured` (registry listing may
still work; controlled download resolution returns `unreachable:auth`).

### Tests (ARGO)

```bash
cd backend
# ArgoClient contract tests (mocked httpx; no live creds) — parser + no-fabrication guard
python3 -m pytest federation/test_argo_client.py -q
# ARGO REST route auth + envelope + typed-error mapping
python3 -m pytest routers/test_argo_routes.py -q
```

## Front-end (Session 15 additions)

Two new opportunity surfaces on top of the Session 14 app:

- **Live Extraction** (`/#/live`) — the **Live Extraction Console.** Three
  endpoint cards (Synapse / SAS CAS / EGA), each with a **Connect** handshake and
  a **Fetch** action (Synapse entity/table, SAS ADaM query, EGA file list). Renders
  the real returned rows, a source badge (cyan/gold/purple), **latency**, and an
  honest status chip (`live` / `unreachable: <reason>` / `unconfigured`). On
  `unreachable`, it shows the real reason plus a clearly-labeled "view the
  extracted equivalent in Graph Explorer" deep-link — **never** a silent snapshot swap.
- **Opportunities** (`/#/opportunities`) — the **Opportunity board.** Fuses the
  strongest signals + bridges + gaps into ranked cards, each with the concrete
  finding, the supporting numbers, the blind spot it closes, a plain "why this is
  worth money" line, and a live-provenance chip. Deep-links to `/signals/:slug`,
  `/bridges`, `/gaps`, and `/live`.

Unlike the data-browsing surfaces, `/live` is **live-only** by design (a live
console must be live) — it requires the running backend and the `X-Zeta-Api-Key`;
there is no offline snapshot fallback for live extraction.
