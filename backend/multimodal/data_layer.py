"""
ZetaBridge Multi-Modal Data Layer
==================================
Unified abstraction over structured, unstructured, and semi-structured data:
  - StructuredStore: SQL tables via DuckDB/Postgres
  - UnstructuredStore: Documents, text blobs with in-memory vector search
  - BlobStore: Binary objects (S3-compatible interface)
  - DataRouter: Routes queries to the right store by modality

OSS alignment:
  - DuckDB for structured analytics
  - In-process vector similarity for unstructured (no external vector DB needed)
  - S3-compatible blob interface (MinIO/AWS S3 ready)

License: Apache-2.0
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

log = logging.getLogger("zetabridge.multimodal")


@dataclass
class DataObject:
    """Universal data object across modalities."""
    object_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    modality: str = "structured"  # structured | unstructured | semi_structured | binary
    content: Any = None
    metadata: dict = field(default_factory=dict)
    embedding: list[float] | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    content_hash: str = ""

    def __post_init__(self):
        if self.content and not self.content_hash:
            raw = json.dumps(self.content, default=str) if not isinstance(self.content, str) else self.content
            self.content_hash = hashlib.sha256(raw.encode()).hexdigest()[:16]


# ── Structured Store (DuckDB/SQL) ────────────────────────────────────────────

class StructuredStore:
    """SQL-based structured data via DuckDB.

    Wraps DuckDB in-memory for analytics queries against registered tables.
    """

    def __init__(self, duckdb_conn):
        self._conn = duckdb_conn

    def query(self, sql: str) -> list[dict]:
        """Execute a SQL query and return results as dicts."""
        result = self._conn.execute(sql)
        if result.description:
            columns = [desc[0] for desc in result.description]
            rows = result.fetchall()
            return [dict(zip(columns, row)) for row in rows]
        return []

    def insert(self, schema: str, table: str, rows: list[dict]) -> int:
        """Bulk insert rows into a DuckDB table."""
        if not rows:
            return 0
        columns = list(rows[0].keys())
        placeholders = ", ".join(["?"] * len(columns))
        col_str = ", ".join(f'"{c}"' for c in columns)
        sql = f'INSERT INTO "{schema}"."{table}" ({col_str}) VALUES ({placeholders})'
        data = [tuple(row.get(c) for c in columns) for row in rows]
        self._conn.executemany(sql, data)
        return len(data)

    def list_tables(self) -> list[dict]:
        """List all tables in DuckDB."""
        result = self._conn.execute(
            "SELECT table_schema, table_name FROM information_schema.tables WHERE table_schema != 'information_schema'"
        )
        return [{"schema": r[0], "table": r[1]} for r in result.fetchall()]

    def describe_table(self, schema: str, table: str) -> list[dict]:
        """Get column info for a table."""
        result = self._conn.execute(f"""
            SELECT column_name, data_type, is_nullable
            FROM information_schema.columns
            WHERE table_schema = '{schema}' AND table_name = '{table}'
            ORDER BY ordinal_position
        """)
        return [
            {"name": r[0], "type": r[1], "nullable": r[2] == "YES"}
            for r in result.fetchall()
        ]

    def to_data_objects(self, sql: str) -> list[DataObject]:
        """Execute query and wrap results as DataObjects."""
        rows = self.query(sql)
        return [
            DataObject(modality="structured", content=row, metadata={"source": "duckdb"})
            for row in rows
        ]


# ── Unstructured Store (Documents + Vector Search) ───────────────────────────

class UnstructuredStore:
    """In-memory document store with lightweight vector similarity search.

    No external vector DB required — uses pure Python cosine similarity
    for demo-scale workloads. Production would swap in pgvector/Qdrant.
    """

    def __init__(self):
        self._documents: dict[str, DataObject] = {}

    def add_document(
        self,
        content: str,
        metadata: dict = None,
        embedding: list[float] | None = None,
        doc_id: str | None = None,
    ) -> str:
        """Store a document with optional embedding."""
        doc = DataObject(
            object_id=doc_id or str(uuid.uuid4()),
            modality="unstructured",
            content=content,
            metadata=metadata or {},
            embedding=embedding or self._simple_embed(content),
        )
        self._documents[doc.object_id] = doc
        return doc.object_id

    def search(
        self,
        query: str,
        top_k: int = 5,
        query_embedding: list[float] | None = None,
    ) -> list[dict]:
        """Search documents by similarity."""
        if not self._documents:
            return []

        q_emb = query_embedding or self._simple_embed(query)
        scored = []
        for doc in self._documents.values():
            if doc.embedding:
                sim = self._cosine_similarity(q_emb, doc.embedding)
                scored.append((sim, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "id": doc.object_id,
                "content": doc.content[:500] if isinstance(doc.content, str) else str(doc.content)[:500],
                "metadata": doc.metadata,
                "score": round(sim, 4),
            }
            for sim, doc in scored[:top_k]
        ]

    def get_document(self, doc_id: str) -> DataObject | None:
        return self._documents.get(doc_id)

    def list_documents(self, limit: int = 100) -> list[dict]:
        docs = list(self._documents.values())[:limit]
        return [
            {
                "id": d.object_id,
                "content_preview": str(d.content)[:200],
                "metadata": d.metadata,
                "has_embedding": d.embedding is not None,
                "created_at": d.created_at,
            }
            for d in docs
        ]

    def count(self) -> int:
        return len(self._documents)

    def _simple_embed(self, text: str) -> list[float]:
        """Lightweight bag-of-characters embedding for demo.
        In production, swap with sentence-transformers or OpenAI embeddings.
        """
        # Use character frequency as a simple 128-dim embedding
        vec = [0.0] * 128
        text_lower = text.lower()
        for char in text_lower:
            idx = ord(char) % 128
            vec[idx] += 1
        # Normalize
        magnitude = math.sqrt(sum(v * v for v in vec)) or 1
        return [v / magnitude for v in vec]

    @staticmethod
    def _cosine_similarity(a: list[float], b: list[float]) -> float:
        dot = sum(x * y for x, y in zip(a, b))
        mag_a = math.sqrt(sum(x * x for x in a)) or 1
        mag_b = math.sqrt(sum(x * x for x in b)) or 1
        return dot / (mag_a * mag_b)


# ── Blob Store (S3-compatible) ───────────────────────────────────────────────

class BlobStore:
    """In-memory blob store with S3-compatible interface.
    Stores binary objects (images, PDFs, models) by key.
    """

    def __init__(self):
        self._blobs: dict[str, DataObject] = {}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream", metadata: dict = None) -> str:
        obj = DataObject(
            object_id=key,
            modality="binary",
            content=data,
            metadata={"content_type": content_type, "size_bytes": len(data), **(metadata or {})},
        )
        self._blobs[key] = obj
        return key

    def get(self, key: str) -> bytes | None:
        obj = self._blobs.get(key)
        return obj.content if obj else None

    def list_keys(self, prefix: str = "") -> list[dict]:
        return [
            {
                "key": k,
                "size_bytes": obj.metadata.get("size_bytes", 0),
                "content_type": obj.metadata.get("content_type", "unknown"),
                "created_at": obj.created_at,
            }
            for k, obj in self._blobs.items()
            if k.startswith(prefix)
        ]

    def delete(self, key: str) -> bool:
        return self._blobs.pop(key, None) is not None

    def count(self) -> int:
        return len(self._blobs)


# ── Data Router ──────────────────────────────────────────────────────────────

class DataRouter:
    """Routes operations to the correct data store by modality.

    Provides a single interface for:
      - Structured queries (SQL → DuckDB)
      - Unstructured search (similarity → vector store)
      - Binary operations (CRUD → blob store)
    """

    def __init__(self, structured: StructuredStore, unstructured: UnstructuredStore, blob: BlobStore):
        self.structured = structured
        self.unstructured = unstructured
        self.blob = blob

    def query(self, modality: str, **kwargs) -> Any:
        if modality == "structured":
            return self.structured.query(kwargs.get("sql", "SELECT 1"))
        elif modality == "unstructured":
            return self.unstructured.search(kwargs.get("query", ""), kwargs.get("top_k", 5))
        elif modality == "binary":
            key = kwargs.get("key", "")
            return self.blob.get(key)
        else:
            raise ValueError(f"Unknown modality: {modality}")

    def ingest(self, modality: str, **kwargs) -> str:
        if modality == "structured":
            count = self.structured.insert(
                kwargs.get("schema", "public"),
                kwargs.get("table", "data"),
                kwargs.get("rows", []),
            )
            return f"Inserted {count} rows"
        elif modality == "unstructured":
            return self.unstructured.add_document(
                kwargs.get("content", ""),
                kwargs.get("metadata", {}),
            )
        elif modality == "binary":
            return self.blob.put(
                kwargs.get("key", str(uuid.uuid4())),
                kwargs.get("data", b""),
                kwargs.get("content_type", "application/octet-stream"),
            )
        else:
            raise ValueError(f"Unknown modality: {modality}")

    def stats(self) -> dict:
        return {
            "structured": {
                "tables": self.structured.list_tables(),
                "table_count": len(self.structured.list_tables()),
            },
            "unstructured": {
                "documents": self.unstructured.count(),
            },
            "binary": {
                "objects": self.blob.count(),
            },
        }
