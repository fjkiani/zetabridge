"""
ZetaBridge substrate API — FastAPI app factory.

Active stack: FastAPI + DuckDB (analytics) + SQLite (lineage).
Removed: Gravitino, Marquez, Postgres/legacy stack, USE_LEGACY_STORE dual-mode.

Lifespan does exactly one thing: seed DuckDB biotech tables on startup.
CORS is an explicit allowlist from CORS_ORIGINS env var (no wildcard).
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

_backend_dir = Path(__file__).resolve().parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import cfg

logging.basicConfig(level=cfg.LOG_LEVEL)
log = logging.getLogger("zetabridge")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed DuckDB biotech tables (idempotent — INSERT OR IGNORE)
    try:
        from data.biotech.seed_biotech import seed_biotech_tables
        seed_biotech_tables()
        log.info("DuckDB biotech tables seeded.")
    except Exception as exc:
        log.warning("Biotech seed skipped: %s", exc)

    # Initialize local lineage store (creates SQLite schema if not exists)
    try:
        from lineage.local_store import init_lineage_store
        init_lineage_store()
        log.info("Local lineage store initialized.")
    except Exception as exc:
        log.warning("Lineage store init skipped: %s", exc)

    yield
    log.info("ZetaBridge substrate shutting down.")


app = FastAPI(
    title="ZetaBridge Substrate API",
    version="0.2.0",
    description=(
        "Lean service substrate: NL→SQL query engine, DuckDB catalog, "
        "local lineage store, honest connector registry. "
        "Brenus trial-domain extension points are stubbed at /api/trials, "
        "/api/claims, /api/blockers, /api/consumers."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=cfg.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── Active routers ────────────────────────────────────────────────────────────
from routers.health import router as health_router
from routers.query import router as query_router
from routers.federation import router as federation_router
from routers.connectors import router as connectors_router
from routers.lineage import router as lineage_router
from routers.catalog import router as catalog_router

# Brenus extension-point stubs (return 501 until integration sprint)
from routers.brenus_stubs import router as brenus_router

app.include_router(health_router)
app.include_router(query_router)
app.include_router(federation_router)
app.include_router(connectors_router)
app.include_router(lineage_router)
app.include_router(catalog_router)
app.include_router(brenus_router)

# ── Disabled routers (quarantined) ────────────────────────────────────────────
# routers/copilot.py  — depends on legacy_app._copilot (removed)
# routers/benchmarks  — benchmarks module deleted
# These are NOT imported. Revive from quarantine/ when needed.
