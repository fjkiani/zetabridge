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
    yield
    if cfg.USE_LEGACY_STORE:
        from legacy_app import legacy_shutdown

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
    return {"status": "ok"}


if cfg.USE_LEGACY_STORE:
    from legacy_app import legacy_router

    app.include_router(legacy_router)
    from routers import query as query_router

    app.include_router(query_router.router)
else:
    from routers import catalog, connectors, copilot, lineage, query

    app.include_router(query.router)
    app.include_router(catalog.router)
    app.include_router(lineage.router)
    app.include_router(connectors.router)
    app.include_router(copilot.router)
