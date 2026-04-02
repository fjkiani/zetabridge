"""
ZetaBridge Cloud — FastAPI Backend
===================================
Single-process cloud-native data platform. No Docker. No local installs.

Embedded services:
    - Catalog Manager: Postgres-backed (Neon free tier) federated catalog
      replacing Gravitino/Lakekeeper/Unity with a unified REST API
    - Lineage Engine: OpenLineage-compatible event store in Postgres,
      replacing Marquez with a lightweight implementation
    - Query Engine: DuckDB embedded, replacing Doris for analytics
    - AI Agent: HuggingFace Inference API for Arctic Text2SQL,
      no GPU or vLLM needed

License: Apache-2.0
"""

from __future__ import annotations

import os
import json
import uuid
import logging
from datetime import datetime, timezone
from contextlib import asynccontextmanager
from typing import Any

import duckdb
import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column, DateTime, Integer, String, Text, Boolean,
    create_engine, text as sa_text,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://zetabridge:zetabridge@localhost:5432/zetabridge",
)
HF_API_TOKEN = os.environ.get("HF_API_TOKEN", "")
HF_MODEL_ID = os.environ.get(
    "HF_MODEL_ID",
    "Snowflake/Arctic-Text2SQL-R1-7B",
)
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")

logging.basicConfig(level=LOG_LEVEL, format="%(asctime)s %(levelname)-8s %(message)s")
log = logging.getLogger("zetabridge")

# ── Database Models ───────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    pass


class CatalogEntry(Base):
    """Federated catalog — replaces Gravitino metalake/catalog/schema/table hierarchy."""
    __tablename__ = "catalog_entries"

    id = Column(Integer, primary_key=True, autoincrement=True)
    metalake = Column(String(255), nullable=False, default="zetabridge")
    catalog_name = Column(String(255), nullable=False)
    catalog_type = Column(String(50), nullable=False)  # iceberg, delta, duckdb
    schema_name = Column(String(255), nullable=False)
    table_name = Column(String(255), nullable=False)
    columns_json = Column(Text, nullable=False, default="[]")
    properties_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class LineageEvent(Base):
    """OpenLineage event store — replaces Marquez."""
    __tablename__ = "lineage_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    run_id = Column(String(64), nullable=False)
    job_namespace = Column(String(255), nullable=False, default="zetabridge")
    job_name = Column(String(255), nullable=False)
    event_type = Column(String(20), nullable=False)  # START, COMPLETE, FAIL
    inputs_json = Column(Text, nullable=False, default="[]")
    outputs_json = Column(Text, nullable=False, default="[]")
    facets_json = Column(Text, nullable=False, default="{}")
    event_time = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class QueryLog(Base):
    """NL query audit log."""
    __tablename__ = "query_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    question = Column(Text, nullable=False)
    generated_sql = Column(Text, nullable=False)
    engine = Column(String(50), nullable=False)
    row_count = Column(Integer, default=0)
    duration_ms = Column(Integer, default=0)
    error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


# ── DB Engine ─────────────────────────────────────────────────────────────────

engine = create_engine(DATABASE_URL, pool_pre_ping=True, pool_size=5)
SessionLocal = sessionmaker(bind=engine)


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ── DuckDB (Embedded Analytics Engine) ────────────────────────────────────────

_duckdb_conn: duckdb.DuckDBPyConnection | None = None


def get_duckdb() -> duckdb.DuckDBPyConnection:
    global _duckdb_conn
    if _duckdb_conn is None:
        _duckdb_conn = duckdb.connect(":memory:")
        log.info("DuckDB in-memory engine initialized")
    return _duckdb_conn


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot: create tables, seed demo data, init DuckDB."""
    log.info("ZetaBridge booting — creating tables")
    Base.metadata.create_all(engine)
    _seed_demo_data()
    _seed_duckdb()
    log.info("ZetaBridge ready")
    yield
    log.info("ZetaBridge shutting down")
    if _duckdb_conn:
        _duckdb_conn.close()


# ── FastAPI App ───────────────────────────────────────────────────────────────

app = FastAPI(
    title="ZetaBridge",
    description="Cloud-native open data platform — catalog, lineage, query engine, AI",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ══════════════════════════════════════════════════════════════════════════════
# CATALOG API — Federated metadata (replaces Gravitino + Lakekeeper + Unity)
# ══════════════════════════════════════════════════════════════════════════════

class ColumnDef(BaseModel):
    name: str
    type: str
    nullable: bool = True
    comment: str = ""


class RegisterTableRequest(BaseModel):
    catalog_name: str = Field(..., description="e.g. lakehouse_iceberg, lakehouse_delta, analytics_duckdb")
    catalog_type: str = Field(..., description="iceberg | delta | duckdb")
    schema_name: str
    table_name: str
    columns: list[ColumnDef]
    properties: dict[str, str] = {}


class CatalogTableResponse(BaseModel):
    id: int
    metalake: str
    catalog_name: str
    catalog_type: str
    schema_name: str
    table_name: str
    columns: list[dict]
    properties: dict
    fqn: str
    created_at: str


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "zetabridge", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.post("/api/catalog/tables", response_model=CatalogTableResponse)
async def register_table(req: RegisterTableRequest):
    """Register a table in the federated catalog.

    Replaces: Gravitino POST /api/metalakes/{m}/catalogs/{c}/schemas/{s}/tables
    """
    db = SessionLocal()
    try:
        existing = db.query(CatalogEntry).filter_by(
            catalog_name=req.catalog_name,
            schema_name=req.schema_name,
            table_name=req.table_name,
        ).first()
        if existing:
            raise HTTPException(409, f"Table {req.catalog_name}.{req.schema_name}.{req.table_name} already exists")

        entry = CatalogEntry(
            catalog_name=req.catalog_name,
            catalog_type=req.catalog_type,
            schema_name=req.schema_name,
            table_name=req.table_name,
            columns_json=json.dumps([c.model_dump() for c in req.columns]),
            properties_json=json.dumps(req.properties),
        )
        db.add(entry)
        db.commit()
        db.refresh(entry)

        # If DuckDB catalog, also create the table in DuckDB
        if req.catalog_type == "duckdb":
            _create_duckdb_table(req)

        return _entry_to_response(entry)
    finally:
        db.close()


@app.get("/api/catalog/tables", response_model=list[CatalogTableResponse])
async def list_tables(
    catalog_name: str | None = Query(None),
    schema_name: str | None = Query(None),
):
    """List all registered tables, optionally filtered."""
    db = SessionLocal()
    try:
        q = db.query(CatalogEntry)
        if catalog_name:
            q = q.filter_by(catalog_name=catalog_name)
        if schema_name:
            q = q.filter_by(schema_name=schema_name)
        return [_entry_to_response(e) for e in q.all()]
    finally:
        db.close()


@app.get("/api/catalog/tables/{table_id}", response_model=CatalogTableResponse)
async def get_table(table_id: int):
    db = SessionLocal()
    try:
        entry = db.query(CatalogEntry).get(table_id)
        if not entry:
            raise HTTPException(404, "Table not found")
        return _entry_to_response(entry)
    finally:
        db.close()


@app.delete("/api/catalog/tables/{table_id}")
async def delete_table(table_id: int):
    db = SessionLocal()
    try:
        entry = db.query(CatalogEntry).get(table_id)
        if not entry:
            raise HTTPException(404, "Table not found")
        db.delete(entry)
        db.commit()
        return {"deleted": True, "id": table_id}
    finally:
        db.close()


@app.get("/api/catalog/stats")
async def catalog_stats():
    """Dashboard stats for the catalog."""
    db = SessionLocal()
    try:
        total = db.query(CatalogEntry).count()
        by_type = {}
        for row in db.execute(sa_text("SELECT catalog_type, COUNT(*) as cnt FROM catalog_entries GROUP BY catalog_type")):
            by_type[row[0]] = row[1]
        catalogs = set()
        schemas = set()
        for e in db.query(CatalogEntry).all():
            catalogs.add(e.catalog_name)
            schemas.add(f"{e.catalog_name}.{e.schema_name}")
        return {
            "total_tables": total,
            "catalogs": len(catalogs),
            "schemas": len(schemas),
            "by_type": by_type,
            "catalog_names": sorted(catalogs),
        }
    finally:
        db.close()


def _entry_to_response(e: CatalogEntry) -> CatalogTableResponse:
    return CatalogTableResponse(
        id=e.id,
        metalake=e.metalake,
        catalog_name=e.catalog_name,
        catalog_type=e.catalog_type,
        schema_name=e.schema_name,
        table_name=e.table_name,
        columns=json.loads(e.columns_json),
        properties=json.loads(e.properties_json),
        fqn=f"{e.catalog_name}.{e.schema_name}.{e.table_name}",
        created_at=e.created_at.isoformat() if e.created_at else "",
    )


def _create_duckdb_table(req: RegisterTableRequest):
    """Create the table in DuckDB for queryable analytics."""
    duck = get_duckdb()
    schema_sql = f'CREATE SCHEMA IF NOT EXISTS "{req.schema_name}"'
    duck.execute(schema_sql)
    col_defs = ", ".join(
        f'"{c.name}" {_to_duckdb_type(c.type)}'
        for c in req.columns
    )
    create_sql = f'CREATE TABLE IF NOT EXISTS "{req.schema_name}"."{req.table_name}" ({col_defs})'
    duck.execute(create_sql)
    log.info("Created DuckDB table %s.%s", req.schema_name, req.table_name)


def _to_duckdb_type(t: str) -> str:
    mapping = {
        "string": "VARCHAR", "integer": "INTEGER", "long": "BIGINT",
        "float": "FLOAT", "double": "DOUBLE", "boolean": "BOOLEAN",
        "date": "DATE", "timestamp": "TIMESTAMP", "timestamp_tz": "TIMESTAMPTZ",
        "binary": "BLOB", "byte": "TINYINT", "short": "SMALLINT",
    }
    return mapping.get(t.lower(), "VARCHAR")


# ══════════════════════════════════════════════════════════════════════════════
# LINEAGE API — OpenLineage-compatible event store (replaces Marquez)
# ══════════════════════════════════════════════════════════════════════════════

class LineageEventRequest(BaseModel):
    job_name: str
    event_type: str = Field(..., description="START | COMPLETE | FAIL")
    run_id: str | None = None
    job_namespace: str = "zetabridge"
    inputs: list[dict] = []
    outputs: list[dict] = []
    facets: dict = {}


class LineageEventResponse(BaseModel):
    id: int
    run_id: str
    job_namespace: str
    job_name: str
    event_type: str
    inputs: list[dict]
    outputs: list[dict]
    facets: dict
    event_time: str


@app.post("/api/lineage/events", response_model=LineageEventResponse)
async def emit_lineage_event(req: LineageEventRequest):
    """Emit an OpenLineage-compatible event. Replaces Marquez POST /api/v1/lineage."""
    db = SessionLocal()
    try:
        event = LineageEvent(
            run_id=req.run_id or str(uuid.uuid4()),
            job_namespace=req.job_namespace,
            job_name=req.job_name,
            event_type=req.event_type.upper(),
            inputs_json=json.dumps(req.inputs),
            outputs_json=json.dumps(req.outputs),
            facets_json=json.dumps(req.facets),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        return _lineage_to_response(event)
    finally:
        db.close()


@app.get("/api/lineage/events", response_model=list[LineageEventResponse])
async def list_lineage_events(
    job_name: str | None = Query(None),
    limit: int = Query(100, le=500),
):
    """List lineage events, newest first."""
    db = SessionLocal()
    try:
        q = db.query(LineageEvent).order_by(LineageEvent.event_time.desc())
        if job_name:
            q = q.filter_by(job_name=job_name)
        return [_lineage_to_response(e) for e in q.limit(limit).all()]
    finally:
        db.close()


@app.get("/api/lineage/graph")
async def lineage_graph():
    """Build a D3-compatible lineage graph from stored events.
    Returns nodes (jobs + datasets) and edges (input/output relationships)."""
    db = SessionLocal()
    try:
        events = db.query(LineageEvent).all()
        nodes: dict[str, dict] = {}
        edges: list[dict] = []

        for e in events:
            job_id = f"job:{e.job_name}"
            if job_id not in nodes:
                nodes[job_id] = {"id": job_id, "label": e.job_name, "type": "job"}

            for inp in json.loads(e.inputs_json):
                ds_name = inp.get("name", inp) if isinstance(inp, dict) else str(inp)
                ds_id = f"dataset:{ds_name}"
                if ds_id not in nodes:
                    nodes[ds_id] = {"id": ds_id, "label": ds_name, "type": "dataset"}
                edge_key = f"{ds_id}->{job_id}"
                edges.append({"source": ds_id, "target": job_id, "type": "input"})

            for out in json.loads(e.outputs_json):
                ds_name = out.get("name", out) if isinstance(out, dict) else str(out)
                ds_id = f"dataset:{ds_name}"
                if ds_id not in nodes:
                    nodes[ds_id] = {"id": ds_id, "label": ds_name, "type": "dataset"}
                edges.append({"source": job_id, "target": ds_id, "type": "output"})

        return {"nodes": list(nodes.values()), "edges": edges}
    finally:
        db.close()


@app.get("/api/lineage/stats")
async def lineage_stats():
    db = SessionLocal()
    try:
        total = db.query(LineageEvent).count()
        jobs = set()
        datasets = set()
        by_type = {}
        for e in db.query(LineageEvent).all():
            jobs.add(e.job_name)
            for inp in json.loads(e.inputs_json):
                ds_name = inp.get("name", inp) if isinstance(inp, dict) else str(inp)
                datasets.add(ds_name)
            for out in json.loads(e.outputs_json):
                ds_name = out.get("name", out) if isinstance(out, dict) else str(out)
                datasets.add(ds_name)
            by_type[e.event_type] = by_type.get(e.event_type, 0) + 1
        return {
            "total_events": total,
            "unique_jobs": len(jobs),
            "unique_datasets": len(datasets),
            "by_type": by_type,
        }
    finally:
        db.close()


def _lineage_to_response(e: LineageEvent) -> LineageEventResponse:
    return LineageEventResponse(
        id=e.id,
        run_id=e.run_id,
        job_namespace=e.job_namespace,
        job_name=e.job_name,
        event_type=e.event_type,
        inputs=json.loads(e.inputs_json),
        outputs=json.loads(e.outputs_json),
        facets=json.loads(e.facets_json),
        event_time=e.event_time.isoformat() if e.event_time else "",
    )


# ══════════════════════════════════════════════════════════════════════════════
# QUERY API — NL→SQL via HuggingFace + DuckDB execution
# ══════════════════════════════════════════════════════════════════════════════

class NLQueryRequest(BaseModel):
    question: str
    execute: bool = True


class NLQueryResponse(BaseModel):
    question: str
    sql: str
    engine: str
    results: list[dict] | None = None
    row_count: int = 0
    duration_ms: int = 0
    error: str | None = None
    schema_context: str = ""


class SQLQueryRequest(BaseModel):
    sql: str


@app.post("/api/query/nl", response_model=NLQueryResponse)
async def nl_query(req: NLQueryRequest):
    """Natural-language to SQL: discovers catalog schema, calls Arctic Text2SQL
    via HuggingFace Inference API, executes on DuckDB.

    Replaces: vLLM + Gravitino discovery + Doris/DuckDB routing."""
    import time
    start = time.monotonic()

    # 1. Build schema context from catalog
    schema_ctx = _build_schema_context()

    # 2. Generate SQL via HuggingFace
    try:
        sql = await _call_hf_text2sql(req.question, schema_ctx)
    except Exception as exc:
        log.error("Text2SQL generation failed: %s", exc)
        return NLQueryResponse(
            question=req.question, sql="", engine="none",
            error=f"SQL generation failed: {exc}", schema_context=schema_ctx,
        )

    # 3. Execute on DuckDB
    results = None
    row_count = 0
    error = None
    if req.execute and sql:
        try:
            duck = get_duckdb()
            result = duck.execute(sql)
            if result.description:
                columns = [desc[0] for desc in result.description]
                rows = result.fetchall()
                results = [dict(zip(columns, row)) for row in rows]
                row_count = len(results)
        except Exception as exc:
            error = str(exc)
            log.error("DuckDB execution failed: %s", exc)

    duration_ms = int((time.monotonic() - start) * 1000)

    # Log the query
    db = SessionLocal()
    try:
        db.add(QueryLog(
            question=req.question, generated_sql=sql, engine="duckdb",
            row_count=row_count, duration_ms=duration_ms, error=error,
        ))
        db.commit()
    finally:
        db.close()

    return NLQueryResponse(
        question=req.question, sql=sql, engine="duckdb",
        results=results, row_count=row_count, duration_ms=duration_ms,
        error=error, schema_context=schema_ctx,
    )


@app.post("/api/query/sql")
async def raw_sql_query(req: SQLQueryRequest):
    """Execute raw SQL on DuckDB."""
    try:
        duck = get_duckdb()
        result = duck.execute(req.sql)
        if result.description:
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return {
                "sql": req.sql,
                "columns": columns,
                "results": [dict(zip(columns, row)) for row in rows],
                "row_count": len(rows),
            }
        return {"sql": req.sql, "columns": [], "results": [], "row_count": 0}
    except Exception as exc:
        raise HTTPException(400, str(exc))


@app.get("/api/query/history")
async def query_history(limit: int = Query(50, le=200)):
    db = SessionLocal()
    try:
        logs = db.query(QueryLog).order_by(QueryLog.created_at.desc()).limit(limit).all()
        return [
            {
                "id": l.id,
                "question": l.question,
                "sql": l.generated_sql,
                "engine": l.engine,
                "row_count": l.row_count,
                "duration_ms": l.duration_ms,
                "error": l.error,
                "created_at": l.created_at.isoformat() if l.created_at else "",
            }
            for l in logs
        ]
    finally:
        db.close()


def _build_schema_context() -> str:
    """Build DDL-style schema context from all catalog entries for Text2SQL prompt."""
    db = SessionLocal()
    try:
        entries = db.query(CatalogEntry).all()
        lines = []
        for e in entries:
            cols = json.loads(e.columns_json)
            col_defs = ", ".join(
                f"{c['name']} {c['type']}" + ("" if c.get("nullable", True) else " NOT NULL")
                for c in cols
            )
            fqn = f"{e.schema_name}.{e.table_name}"
            lines.append(f"CREATE TABLE {fqn} ({col_defs});")
        return "\n".join(lines)
    finally:
        db.close()


async def _call_hf_text2sql(question: str, schema_context: str) -> str:
    """Call HuggingFace Inference API for Arctic Text2SQL.

    Uses: HuggingFace Inference API (free tier) with
    Snowflake/Arctic-Text2SQL-R1-7B (Apache-2.0).
    Falls back to a rule-based SQL generator if HF is unavailable.
    """
    prompt = (
        f"### Task\nGenerate a SQL query to answer: `{question}`\n\n"
        f"### Database Schema\n{schema_context}\n\n"
        f"### Answer\nGiven the database schema, here is the SQL query that answers `{question}`:\n```sql\n"
    )

    if HF_API_TOKEN:
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"https://api-inference.huggingface.co/models/{HF_MODEL_ID}",
                    headers={"Authorization": f"Bearer {HF_API_TOKEN}"},
                    json={
                        "inputs": prompt,
                        "parameters": {
                            "max_new_tokens": 256,
                            "temperature": 0.01,
                            "return_full_text": False,
                        },
                    },
                )
                if resp.is_success:
                    data = resp.json()
                    if isinstance(data, list) and data:
                        raw = data[0].get("generated_text", "")
                        sql = raw.split("```")[0].strip().rstrip(";").strip()
                        if sql:
                            return sql
        except Exception as exc:
            log.warning("HF API call failed, using fallback: %s", exc)

    # Fallback: simple keyword-based SQL generation
    return _fallback_sql_gen(question, schema_context)


def _fallback_sql_gen(question: str, schema_context: str) -> str:
    """Rule-based SQL fallback when HF API is unavailable.
    Generates reasonable DuckDB SQL from question keywords."""
    q = question.lower()
    # Extract table names from schema
    tables = []
    for line in schema_context.split("\n"):
        if line.startswith("CREATE TABLE"):
            tname = line.split("(")[0].replace("CREATE TABLE", "").strip()
            tables.append(tname)

    if not tables:
        return "SELECT 'No tables registered' AS message"

    # Match question to best table
    best_table = tables[0]
    for t in tables:
        if any(word in t.lower() for word in q.split()):
            best_table = t
            break

    if any(w in q for w in ["count", "how many", "total"]):
        return f"SELECT COUNT(*) as total FROM {best_table}"
    elif any(w in q for w in ["average", "avg", "mean"]):
        return f"SELECT * FROM {best_table} LIMIT 10"
    elif any(w in q for w in ["all", "show", "list", "select"]):
        return f"SELECT * FROM {best_table} LIMIT 100"
    else:
        return f"SELECT * FROM {best_table} LIMIT 20"


# ══════════════════════════════════════════════════════════════════════════════
# SEED DATA — Demo-ready from boot
# ══════════════════════════════════════════════════════════════════════════════

def _seed_demo_data():
    """Seed the catalog and lineage with demo data so the platform is usable immediately."""
    db = SessionLocal()
    try:
        if db.query(CatalogEntry).count() > 0:
            log.info("Demo data already exists, skipping seed")
            return

        # ── Catalog: Iceberg tables ──
        iceberg_tables = [
            {
                "catalog_name": "lakehouse_iceberg",
                "catalog_type": "iceberg",
                "schema_name": "raw",
                "table_name": "events",
                "columns": [
                    {"name": "event_id", "type": "string", "nullable": False, "comment": "UUID"},
                    {"name": "event_ts", "type": "timestamp", "nullable": False, "comment": "Event time UTC"},
                    {"name": "user_id", "type": "string", "nullable": True, "comment": "User identifier"},
                    {"name": "event_type", "type": "string", "nullable": False, "comment": "page_view|click|purchase"},
                    {"name": "payload", "type": "string", "nullable": True, "comment": "JSON payload"},
                ],
                "properties": {"format": "iceberg", "location": "s3://zetabridge/raw/events"},
            },
            {
                "catalog_name": "lakehouse_iceberg",
                "catalog_type": "iceberg",
                "schema_name": "raw",
                "table_name": "users",
                "columns": [
                    {"name": "user_id", "type": "string", "nullable": False, "comment": "Primary key"},
                    {"name": "email", "type": "string", "nullable": False, "comment": "User email"},
                    {"name": "name", "type": "string", "nullable": True, "comment": "Display name"},
                    {"name": "signup_date", "type": "date", "nullable": False, "comment": "Registration date"},
                    {"name": "plan", "type": "string", "nullable": False, "comment": "free|pro|enterprise"},
                ],
                "properties": {"format": "iceberg", "location": "s3://zetabridge/raw/users"},
            },
            {
                "catalog_name": "lakehouse_iceberg",
                "catalog_type": "iceberg",
                "schema_name": "analytics",
                "table_name": "daily_active_users",
                "columns": [
                    {"name": "date", "type": "date", "nullable": False, "comment": "Activity date"},
                    {"name": "dau_count", "type": "integer", "nullable": False, "comment": "Distinct users"},
                    {"name": "new_users", "type": "integer", "nullable": False, "comment": "First-time users"},
                    {"name": "returning_users", "type": "integer", "nullable": False, "comment": "Returning users"},
                ],
                "properties": {"format": "iceberg", "location": "s3://zetabridge/analytics/dau"},
            },
        ]

        # ── Catalog: Delta tables ──
        delta_tables = [
            {
                "catalog_name": "lakehouse_delta",
                "catalog_type": "delta",
                "schema_name": "ml",
                "table_name": "feature_store",
                "columns": [
                    {"name": "entity_id", "type": "string", "nullable": False, "comment": "Entity key"},
                    {"name": "feature_name", "type": "string", "nullable": False, "comment": "Feature identifier"},
                    {"name": "feature_value", "type": "double", "nullable": True, "comment": "Numeric value"},
                    {"name": "computed_at", "type": "timestamp", "nullable": False, "comment": "Computation time"},
                ],
                "properties": {"format": "delta", "location": "s3://zetabridge/ml/feature_store"},
            },
            {
                "catalog_name": "lakehouse_delta",
                "catalog_type": "delta",
                "schema_name": "ml",
                "table_name": "predictions",
                "columns": [
                    {"name": "prediction_id", "type": "string", "nullable": False, "comment": "UUID"},
                    {"name": "model_version", "type": "string", "nullable": False, "comment": "Model version tag"},
                    {"name": "input_hash", "type": "string", "nullable": False, "comment": "Hash of input features"},
                    {"name": "score", "type": "double", "nullable": False, "comment": "Prediction score"},
                    {"name": "created_at", "type": "timestamp", "nullable": False, "comment": "Inference time"},
                ],
                "properties": {"format": "delta", "location": "s3://zetabridge/ml/predictions"},
            },
        ]

        # ── Catalog: DuckDB analytics (queryable) ──
        duckdb_tables = [
            {
                "catalog_name": "analytics_duckdb",
                "catalog_type": "duckdb",
                "schema_name": "reporting",
                "table_name": "revenue",
                "columns": [
                    {"name": "date", "type": "date", "nullable": False, "comment": "Revenue date"},
                    {"name": "product", "type": "string", "nullable": False, "comment": "Product name"},
                    {"name": "region", "type": "string", "nullable": False, "comment": "Sales region"},
                    {"name": "amount", "type": "double", "nullable": False, "comment": "Revenue USD"},
                    {"name": "units", "type": "integer", "nullable": False, "comment": "Units sold"},
                ],
                "properties": {"engine": "duckdb"},
            },
            {
                "catalog_name": "analytics_duckdb",
                "catalog_type": "duckdb",
                "schema_name": "reporting",
                "table_name": "customers",
                "columns": [
                    {"name": "customer_id", "type": "string", "nullable": False, "comment": "Primary key"},
                    {"name": "name", "type": "string", "nullable": False, "comment": "Customer name"},
                    {"name": "segment", "type": "string", "nullable": False, "comment": "enterprise|smb|consumer"},
                    {"name": "ltv", "type": "double", "nullable": False, "comment": "Lifetime value USD"},
                    {"name": "signup_date", "type": "date", "nullable": False, "comment": "First purchase"},
                ],
                "properties": {"engine": "duckdb"},
            },
        ]

        all_tables = iceberg_tables + delta_tables + duckdb_tables
        for t in all_tables:
            entry = CatalogEntry(
                catalog_name=t["catalog_name"],
                catalog_type=t["catalog_type"],
                schema_name=t["schema_name"],
                table_name=t["table_name"],
                columns_json=json.dumps(t["columns"]),
                properties_json=json.dumps(t["properties"]),
            )
            db.add(entry)

        # ── Lineage events ──
        runs = [
            ("dbt.stg_events", "raw.events", "analytics.stg_events",
             "SELECT event_id, event_ts, event_type FROM raw.events WHERE event_ts >= CURRENT_DATE - 1"),
            ("dbt.daily_active_users", "analytics.stg_events", "analytics.daily_active_users",
             "SELECT date, COUNT(DISTINCT user_id) as dau FROM analytics.stg_events GROUP BY date"),
            ("dbt.user_features", "raw.users", "ml.feature_store",
             "SELECT user_id, plan, signup_date FROM raw.users"),
            ("ml.predict_churn", "ml.feature_store", "ml.predictions",
             "SELECT entity_id, score FROM ml.churn_model(feature_store)"),
            ("dbt.revenue_report", "raw.events", "reporting.revenue",
             "SELECT date, product, region, SUM(amount) FROM raw.transactions GROUP BY 1,2,3"),
            ("airbyte.ingest_events", "source.kafka_events", "raw.events",
             "COPY INTO raw.events FROM kafka_events"),
            ("dlt.load_customers", "source.crm_api", "reporting.customers",
             "COPY INTO reporting.customers FROM crm_api"),
        ]

        for job_name, input_ds, output_ds, sql in runs:
            run_id = str(uuid.uuid4())
            for evt_type in ["START", "COMPLETE"]:
                event = LineageEvent(
                    run_id=run_id,
                    job_name=job_name,
                    event_type=evt_type,
                    inputs_json=json.dumps([{"name": input_ds, "namespace": "zetabridge"}]),
                    outputs_json=json.dumps([{"name": output_ds, "namespace": "zetabridge"}]),
                    facets_json=json.dumps({"sql": {"query": sql}}),
                )
                db.add(event)

        db.commit()
        log.info("Seeded %d catalog tables and %d lineage runs", len(all_tables), len(runs))
    except Exception as exc:
        log.error("Seed failed: %s", exc)
        db.rollback()
    finally:
        db.close()


def _seed_duckdb():
    """Seed DuckDB with actual queryable data for demo purposes."""
    import random
    duck = get_duckdb()
    try:
        duck.execute('CREATE SCHEMA IF NOT EXISTS "reporting"')

        # Revenue table
        duck.execute('''
            CREATE TABLE IF NOT EXISTS "reporting"."revenue" (
                "date" DATE, "product" VARCHAR, "region" VARCHAR,
                "amount" DOUBLE, "units" INTEGER
            )
        ''')
        products = ["ZetaBridge Pro", "ZetaBridge Enterprise", "ZetaBridge Starter", "API Credits"]
        regions = ["US-East", "US-West", "EU-West", "APAC", "LATAM"]
        revenue_data = []
        for day_offset in range(90):
            d = f"2026-01-{(day_offset % 28) + 1:02d}" if day_offset < 28 else \
                f"2026-02-{((day_offset - 28) % 28) + 1:02d}" if day_offset < 56 else \
                f"2026-03-{((day_offset - 56) % 31) + 1:02d}"
            for _ in range(random.randint(2, 5)):
                p = random.choice(products)
                r = random.choice(regions)
                amt = round(random.uniform(500, 25000), 2)
                units = random.randint(1, 50)
                revenue_data.append((d, p, r, amt, units))

        duck.executemany(
            'INSERT INTO "reporting"."revenue" VALUES (?, ?, ?, ?, ?)',
            revenue_data,
        )

        # Customers table
        duck.execute('''
            CREATE TABLE IF NOT EXISTS "reporting"."customers" (
                "customer_id" VARCHAR, "name" VARCHAR, "segment" VARCHAR,
                "ltv" DOUBLE, "signup_date" DATE
            )
        ''')
        segments = ["enterprise", "smb", "consumer"]
        names = [
            "Acme Corp", "Globex Inc", "Initech", "Umbrella LLC", "Waystar",
            "Pied Piper", "Hooli", "Dunder Mifflin", "Sterling Cooper", "Stark Industries",
            "Wayne Enterprises", "Cyberdyne", "Soylent Corp", "Tyrell Corp", "Massive Dynamic",
            "InGen", "Weyland-Yutani", "Oscorp", "LexCorp", "Capsule Corp",
        ]
        customer_data = []
        for i, name in enumerate(names):
            cid = f"cust_{i+1:03d}"
            seg = random.choice(segments)
            ltv = round(random.uniform(1000, 500000), 2)
            signup = f"2025-{random.randint(1,12):02d}-{random.randint(1,28):02d}"
            customer_data.append((cid, name, seg, ltv, signup))

        duck.executemany(
            'INSERT INTO "reporting"."customers" VALUES (?, ?, ?, ?, ?)',
            customer_data,
        )

        log.info("Seeded DuckDB: %d revenue rows, %d customers", len(revenue_data), len(customer_data))
    except Exception as exc:
        log.error("DuckDB seed failed: %s", exc)


# ── Run ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
