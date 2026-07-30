"""Vault (Qdrant) access layer for ZetaBridge federated RAG.

Read-only semantic + structured lookup over the ``zeta_vault`` Qdrant collection.
Mirrors the graph layer's anti-blind-guess contract: an external agent calls
``manifest()`` ONCE and learns — computed LIVE from Qdrant — the collection
config, the filterable payload fields WITH their real value vocabularies (via
Qdrant ``facet``), the currently-available search modes, and worked examples. It
never has to guess what to send.

Credentials (``QDRANT_URL``, ``QDRANT_API_KEY``) stay server-side; a caller
authenticates to the REST router with ``X-Zeta-Api-Key``, never with the Qdrant
key.

Search modes:
  - ``filter`` : exact payload-filter lookup over the 6 indexed keyword fields.
                 Zero external deps — always available when Qdrant is reachable.
  - ``dense``  : semantic Cosine search. Embeds the query with ``VAULT_EMBED_MODEL``
                 via OpenRouter (OpenAI-compatible ``/embeddings``). Available ONLY
                 when ``OPENROUTER_API_KEY`` + ``VAULT_EMBED_MODEL`` are set.
  - ``bm25``   : lexical sparse search over the collection's ``bm25`` vector.
                 Available ONLY when ``fastembed`` is importable (matches the
                 loader's ``Qdrant/bm25`` model).

Every unavailable mode degrades HONESTLY: ``search()`` raises
``VaultModeUnavailable`` and ``manifest()`` advertises exactly which modes are
live. Results are NEVER fabricated.
"""

from __future__ import annotations

import os
from typing import Any, Optional

# Filterable payload fields with cardinality tags. Low/medium-cardinality fields
# are safe to enumerate in the manifest via facet; high-cardinality identifier
# fields are filterable but NOT enumerated (would be huge / meaningless).
FILTERABLE_FIELDS = [
    {"key": "entity_type", "cardinality": "low", "enumerate": True,
     "description": "Node/entity class, e.g. TrialPatient, SourceNode, Biospecimen, Trial."},
    {"key": "fingerprint.source_vault", "cardinality": "low", "enumerate": True,
     "description": "Originating federated vault, e.g. EGA, Synapse, PDS_ProjectDataSphere."},
    {"key": "fingerprint.biological_state", "cardinality": "low", "enumerate": True,
     "description": "Biological-state annotation of the record."},
    {"key": "fingerprint.genomic_signature", "cardinality": "medium", "enumerate": True,
     "description": "Genomic signature / feature label."},
    {"key": "fingerprint.primary_key", "cardinality": "high", "enumerate": False,
     "description": "Stable per-record primary key. Filterable for exact lookup; not enumerated."},
    {"key": "source_file_md5", "cardinality": "high", "enumerate": False,
     "description": "MD5 of the source file the record was derived from. Filterable; not enumerated."},
]

_INDEXED_KEYS = {f["key"] for f in FILTERABLE_FIELDS}

_DEFAULT_EMBED_MODEL = "nvidia/llama-nemotron-embed-vl-1b-v2"


class VaultModeUnavailable(RuntimeError):
    """Raised when a search mode is requested but its deps/keys are absent."""


class VaultService:
    """Thin read-only wrapper over a Qdrant collection."""

    def __init__(self, client, collection: str, embed_model: str = "",
                 openrouter_key: str = "",
                 openrouter_base: str = "https://openrouter.ai/api/v1"):
        self._client = client
        self.collection = collection
        self.embed_model = embed_model
        self._openrouter_key = openrouter_key
        self._openrouter_base = openrouter_base.rstrip("/")
        self._bm25_model = None  # lazy fastembed sparse model

    # ---- construction -----------------------------------------------------
    @classmethod
    def from_env(cls) -> "VaultService":
        url = os.environ.get("QDRANT_URL", "")
        api_key = os.environ.get("QDRANT_API_KEY", "")
        if not url or not api_key:
            raise RuntimeError(
                "Vault not configured: set QDRANT_URL and QDRANT_API_KEY server-side."
            )
        from qdrant_client import QdrantClient

        client = QdrantClient(url=url, api_key=api_key, timeout=30)
        return cls(
            client=client,
            collection=os.environ.get("QDRANT_COLLECTION", "zeta_vault"),
            embed_model=os.environ.get("VAULT_EMBED_MODEL", _DEFAULT_EMBED_MODEL),
            openrouter_key=os.environ.get("OPENROUTER_API_KEY", ""),
        )

    # ---- capability flags -------------------------------------------------
    def dense_enabled(self) -> bool:
        return bool(self._openrouter_key and self.embed_model)

    def bm25_enabled(self) -> bool:
        try:
            import fastembed  # noqa: F401

            return True
        except Exception:
            return False

    def modes(self) -> list[str]:
        m = ["filter"]
        if self.dense_enabled():
            m.append("dense")
        if self.bm25_enabled():
            m.append("bm25")
        return m

    # ---- health -----------------------------------------------------------
    def health(self) -> dict:
        info = self._client.get_collection(self.collection)
        return {
            "status": "ok",
            "collection": self.collection,
            "points": info.points_count,
            "modes_available": self.modes(),
            "dense_enabled": self.dense_enabled(),
            "bm25_enabled": self.bm25_enabled(),
        }

    # ---- manifest (the anti-blind-guess discovery verb) -------------------
    def manifest(self, facet_limit: int = 50) -> dict:
        info = self._client.get_collection(self.collection)

        vectors: dict[str, Any] = {}
        try:
            for name, params in (info.config.params.vectors or {}).items():
                vectors[name] = {"size": params.size, "distance": str(params.distance)}
        except Exception:
            pass
        sparse: list[str] = []
        try:
            sparse = list((info.config.params.sparse_vectors or {}).keys())
        except Exception:
            pass

        fields = []
        for f in FILTERABLE_FIELDS:
            entry = {
                "key": f["key"],
                "type": "keyword",
                "cardinality": f["cardinality"],
                "description": f["description"],
                "filterable": True,
                "enumerated": bool(f["enumerate"]),
            }
            if f["enumerate"]:
                try:
                    fr = self._client.facet(
                        collection_name=self.collection, key=f["key"], limit=facet_limit
                    )
                    entry["values"] = [
                        {"value": h.value, "count": h.count} for h in fr.hits
                    ]
                except Exception as exc:  # facet unsupported / field unindexed
                    entry["values_error"] = str(exc)
            fields.append(entry)

        modes = self.modes()
        dense_size = vectors.get("dense", {}).get("size")
        return {
            "collection": self.collection,
            "points": info.points_count,
            "vectors": vectors,
            "sparse_vectors": sparse,
            "embedding": {
                "model": self.embed_model or None,
                "provider": "openrouter" if self.embed_model else None,
                "dense_query_enabled": self.dense_enabled(),
                "dense_vector_size": dense_size,
                "note": (
                    "Dense semantic search is LIVE. The query is embedded with the "
                    "model above; verify it emits a %s-dim vector." % dense_size
                    if self.dense_enabled() else
                    "Dense semantic search is WIRED but OFF: set OPENROUTER_API_KEY "
                    "(+ optionally VAULT_EMBED_MODEL) server-side to enable. Filter "
                    "mode fully answers structured lookups without it."
                ),
            },
            "search_modes": modes,
            "filterable_fields": fields,
            "search_endpoint": {
                "path": "/api/vault/search",
                "method": "POST",
                "auth_header": "X-Zeta-Api-Key",
                "body_schema": {
                    "query": "string (text; used by dense/bm25; ignored by filter)",
                    "mode": "one of %s" % modes,
                    "filters": "object mapping any filterable_fields key -> exact value",
                    "limit": "int 1..100 (default 10)",
                },
            },
            "examples": [
                {"description": "List EGA-sourced records (structured lookup, no guessing).",
                 "body": {"mode": "filter",
                          "filters": {"fingerprint.source_vault": "EGA"}, "limit": 10}},
                {"description": "Fetch a specific record by its primary key.",
                 "body": {"mode": "filter",
                          "filters": {"fingerprint.primary_key": "<value from manifest/results>"},
                          "limit": 1}},
            ] + ([{"description": "Semantic search (dense) — currently LIVE.",
                   "body": {"mode": "dense",
                            "query": "platinum-resistant ovarian cancer copy-number signature",
                            "limit": 10}}] if self.dense_enabled() else []),
        }

    # ---- search -----------------------------------------------------------
    def search(self, query: str = "", mode: str = "filter",
               filters: Optional[dict] = None, limit: int = 10) -> dict:
        limit = max(1, min(int(limit), 100))
        mode = (mode or "filter").lower()
        qfilter = self._build_filter(filters)

        if mode == "filter":
            points, _ = self._client.scroll(
                collection_name=self.collection, scroll_filter=qfilter,
                limit=limit, with_payload=True, with_vectors=False,
            )
            return {"mode": "filter", "count": len(points),
                    "results": [self._format(p) for p in points]}

        if mode == "dense":
            if not self.dense_enabled():
                raise VaultModeUnavailable(
                    "dense mode unavailable: OPENROUTER_API_KEY + VAULT_EMBED_MODEL "
                    "not configured. Use mode='filter', or configure keys server-side.")
            if not query:
                raise ValueError("dense mode requires a non-empty 'query'.")
            vec = self._embed(query)
            resp = self._client.query_points(
                collection_name=self.collection, query=vec, using="dense",
                query_filter=qfilter, limit=limit, with_payload=True,
            )
            return {"mode": "dense", "count": len(resp.points),
                    "results": [self._format(p) for p in resp.points]}

        if mode == "bm25":
            if not self.bm25_enabled():
                raise VaultModeUnavailable(
                    "bm25 mode unavailable: `fastembed` not installed server-side. "
                    "Use mode='filter'.")
            if not query:
                raise ValueError("bm25 mode requires a non-empty 'query'.")
            sv = self._embed_bm25(query)
            resp = self._client.query_points(
                collection_name=self.collection, query=sv, using="bm25",
                query_filter=qfilter, limit=limit, with_payload=True,
            )
            return {"mode": "bm25", "count": len(resp.points),
                    "results": [self._format(p) for p in resp.points]}

        raise ValueError("unknown mode '%s'. Valid modes: %s" % (mode, self.modes()))

    # ---- helpers ----------------------------------------------------------
    def _build_filter(self, filters: Optional[dict]):
        if not filters:
            return None
        from qdrant_client import models

        conds = []
        for k, v in filters.items():
            if k not in _INDEXED_KEYS:
                raise ValueError(
                    "field '%s' is not filterable. Filterable fields: %s"
                    % (k, sorted(_INDEXED_KEYS)))
            conds.append(models.FieldCondition(key=k, match=models.MatchValue(value=v)))
        return models.Filter(must=conds)

    def _embed(self, text: str) -> list:
        import httpx

        headers = {"Authorization": "Bearer %s" % self._openrouter_key,
                   "Content-Type": "application/json"}
        payload = {"model": self.embed_model, "input": text}
        with httpx.Client(timeout=30) as h:
            r = h.post("%s/embeddings" % self._openrouter_base, headers=headers, json=payload)
            r.raise_for_status()
            data = r.json()
        return data["data"][0]["embedding"]

    def _embed_bm25(self, text: str):
        from qdrant_client import models

        if self._bm25_model is None:
            from fastembed import SparseTextEmbedding

            os.environ.setdefault("FASTEMBED_CACHE_PATH", "/tmp/.fastembed_cache")
            self._bm25_model = SparseTextEmbedding(model_name="Qdrant/bm25")
        emb = next(iter(self._bm25_model.query_embed(text)))
        return models.SparseVector(indices=emb.indices.tolist(), values=emb.values.tolist())

    @staticmethod
    def _format(point) -> dict:
        payload = dict(point.payload or {})
        for tk in ("text", "content", "document", "chunk", "body"):
            if isinstance(payload.get(tk), str) and len(payload[tk]) > 400:
                payload[tk] = payload[tk][:400] + "\u2026"
        out = {"id": str(point.id), "payload": payload}
        score = getattr(point, "score", None)
        if score is not None:
            out["score"] = score
        return out


# module singleton -----------------------------------------------------------
_vault: Optional[VaultService] = None


def get_vault_service() -> VaultService:
    global _vault
    if _vault is None:
        _vault = VaultService.from_env()
    return _vault
