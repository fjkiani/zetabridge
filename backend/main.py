"""
ZetaBridge API entry — app factory with optional legacy Postgres stack
and Track 2 routers (Gravitino / Marquez / InferenceClient paths).
"""

from __future__ import annotations

import sys
from contextlib import asynccontextmanager
from pathlib import Path

# Run as: uvicorn main:app from backend/, or PYTHONPATH=backend uvicorn main:app
_backend_dir = Path(__file__).resolve().parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import cfg


@asynccontextmanager
async def lifespan(app: FastAPI):
    import logging

    _log = logging.getLogger("zetabridge")

    if cfg.USE_LEGACY_STORE:
        from legacy_app import legacy_startup, legacy_shutdown

        await legacy_startup()
    else:
        try:
            from catalog.gravitino_client import GravitinoClient

            g = GravitinoClient()
            g.setup_metalake()
            g.register_snowflake_catalog()
            g.register_databricks_catalog()
        except Exception:
            pass

    # Clinical backbone: CRC IPD → Postgres (env + content-hash gated)
    try:
        from services.crc_ipd_seed import seed_crc_ipd_if_configured

        seed_result = seed_crc_ipd_if_configured()
        _log.info("CRC IPD lifespan seed: %s", seed_result)
        app.state.crc_ipd_seed = seed_result
    except Exception as exc:
        _log.warning("CRC IPD lifespan seed failed: %s", exc)
        app.state.crc_ipd_seed = {"crc_ipd_seed": "error", "error": str(exc)}

    yield
    if cfg.USE_LEGACY_STORE:
        from legacy_app import legacy_shutdown

        # Seed biotech research data into DuckDB
        try:
            from data.biotech.seed_biotech import seed_biotech_tables

            seed_biotech_tables()
        except Exception as exc:
            _log.warning("Biotech seed skipped: %s", exc)
        await legacy_shutdown()


app = FastAPI(title="ZetaBridge API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health():
    out = {"status": "ok"}
    seed = getattr(app.state, "crc_ipd_seed", None)
    if seed is not None:
        out["crc_ipd_seed"] = seed
    return out


if cfg.USE_LEGACY_STORE:
    from legacy_app import legacy_router

    app.include_router(legacy_router)
    from routers import query as query_router

    app.include_router(query_router.router)
else:
    from routers import catalog, connectors, copilot, lineage, query, federation

    app.include_router(query.router)
    app.include_router(catalog.router)
    app.include_router(lineage.router)
    app.include_router(connectors.router)
    app.include_router(copilot.router)
    app.include_router(federation.router)

# Graph access layer (Session 12) — read-only /api/graph over federated Neo4j KG.
# Registered in BOTH store modes so external agents can reach it regardless of
# USE_LEGACY_STORE.
from routers import graph as graph_router

app.include_router(graph_router.router)

# Signal-Intelligence layer (Session 14) — read-only /api/signals over the
# federated Neo4j KG + the 3 signal agents. Registered in BOTH store modes so
# the value surfaces work regardless of USE_LEGACY_STORE.
from routers import signals as signals_router

app.include_router(signals_router.router)

# Live source extraction layer (Session 15) — authenticated /api/sources that
# invokes the three LIVE source systems (Synapse / SAS Viya CAS / EGA) on demand
# via SourceGateway. Read-only probes + targeted fetch; source credentials stay
# server-side. Registered in BOTH store modes so external agents can extract
# live regardless of USE_LEGACY_STORE.
from routers import sources as sources_router

app.include_router(sources_router.router)

# Vault access layer (Session 16) — read-only /api/vault over the federated
# Qdrant `zeta_vault` collection. Structured filter lookup is always on; dense
# semantic search is gated on OPENROUTER_*. GET /api/vault/manifest lets an
# external agent discover the collection LIVE (fields + value vocabularies +
# modes) and never blind-guess. Registered in BOTH store modes.
from routers import vault as vault_router

app.include_router(vault_router.router)

# Capability layer — outcome-anchor index + Efficacy Predictor. Surfaces the
# byte-verified outcome anchors (SPECTRUM / BriTROC-1 / ARGO-POG570 / PDS) and a
# Cox/logistic Efficacy Predictor with user-configured inputs. Read-only.
from routers import capability as capability_router

app.include_router(capability_router.router)

# Agent framework surface — /api/agents (roster, stats, tools, execution
# history) for the Agents page + Overview dashboard. Registered in BOTH modes.
from routers import agents as agents_router

app.include_router(agents_router.router)

# Platform status rollup — /api/platform/status for the Overview dashboard.
from routers import platform as platform_router

app.include_router(platform_router.router)

# Agent GPS navigation layer — /api/gps (task registry, graph coordinates,
# provenance ledger). Registered in BOTH modes.
from routers import gps as gps_router

app.include_router(gps_router.router)

# GraphRAG — /api/rag multi-hop traversal over the live KG (no cache).
from routers import rag as rag_router

app.include_router(rag_router.router)
