# mypy: ignore-errors
"""Neural code embedding layer: model, batch embedder, vector store."""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import struct
from pathlib import Path

from codemesh.types import Node

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "jinaai/jina-embeddings-v2-base-code"
DEFAULT_DIMENSIONS = 768
DEFAULT_RERANKER = "BAAI/bge-reranker-v2-m3"
DEFAULT_MODEL_TRUST_REMOTE = True  # jina models require trust_remote_code


class EmbeddingModel:
    """Loads and runs the sentence-transformers embedding model.

    Uses a module-level cache to avoid reloading the model across multiple
    instantiations within the same process.
    """

    _cache: dict[str, tuple[object, int]] = {}

    def __init__(self, model_name: str = DEFAULT_MODEL, device: str | None = None) -> None:
        self.model_name = model_name
        self.device = device
        self._model: object | None = None
        self._dimensions: int | None = None

    @property
    def dimensions(self) -> int:
        if self._dimensions is None:
            self._load_model()
        return self._dimensions or DEFAULT_DIMENSIONS

    def _load_model(self) -> None:
        if self._model is not None:
            return
        # Check module-level cache first
        cache_key = f"{self.model_name}:{self.device}"
        if cache_key in EmbeddingModel._cache:
            self._model, self._dimensions = EmbeddingModel._cache[cache_key]
            logger.info("Embedding model loaded from cache: %s", self.model_name)
            return
        logger.info("Loading embedding model: %s", self.model_name)
        from sentence_transformers import SentenceTransformer

        kwargs: dict = {"device": self.device}
        if DEFAULT_MODEL_TRUST_REMOTE:
            kwargs["trust_remote_code"] = True
        self._model = SentenceTransformer(self.model_name, **kwargs)
        test = self._model.encode(["test"], show_progress_bar=False)
        self._dimensions = test.shape[1]
        # Store in cache
        EmbeddingModel._cache[cache_key] = (self._model, self._dimensions)
        logger.info("Model loaded: %d dimensions", self._dimensions)

    def encode(self, texts: list[str], batch_size: int = 32) -> list[list[float]]:
        self._load_model()
        assert self._model is not None
        all_embeddings: list[list[float]] = []
        for i in range(0, len(texts), batch_size):
            batch = texts[i : i + batch_size]
            embeddings = self._model.encode(
                batch, show_progress_bar=False, normalize_embeddings=True
            )
            all_embeddings.extend(embeddings.tolist())
        return all_embeddings

    def encode_single(self, text: str) -> list[float]:
        return self.encode([text])[0]


EMBEDDABLE_KINDS = frozenset(
    {
        "function",
        "method",
        "class",
        "interface",
        "struct",
        "trait",
        "enum",
        "constant",
        "variable",
        "type_alias",
    }
)


def _make_content(node: Node, source: str | None = None) -> str:
    parts: list[str] = []
    if node.signature:
        parts.append(node.signature)
    if node.docstring:
        parts.append(node.docstring)
    if source:
        lines = source.splitlines()
        start = max(0, node.start_line - 1)
        end = min(len(lines), node.end_line)
        parts.append("\n".join(lines[start:end]))
    return "\n\n".join(parts) if parts else node.qualified_name


class BatchEmbedder:
    """Embeds code symbols in batches."""

    def __init__(self, model: EmbeddingModel | None = None, batch_size: int = 32) -> None:
        self.model = model or EmbeddingModel()
        self.batch_size = batch_size

    def embed_nodes(
        self, conn: sqlite3.Connection, nodes: list[Node], root: Path, force: bool = False
    ) -> int:
        store = VectorStore(conn, self.model.dimensions)
        store.create_table()
        to_embed = [n for n in nodes if n.kind.value in EMBEDDABLE_KINDS]
        if not force:
            to_embed = [n for n in to_embed if not store.is_embedded(n.id, self.model.model_name)]
        if not to_embed:
            return 0
        contents: list[str] = []
        for node in to_embed:
            src = None
            try:
                fp = root / node.file_path if not node.file_path.is_absolute() else node.file_path
                if fp.exists():
                    src = fp.read_text(encoding="utf-8", errors="replace")
            except Exception:
                pass
            contents.append(_make_content(node, src))
        embeddings = self.model.encode(contents, batch_size=self.batch_size)
        store.upsert_embeddings(to_embed, embeddings, self.model.model_name)
        store.update_meta(self.model.model_name, "1.0", len(to_embed))
        return len(to_embed)


class VectorStore:
    """Stores and queries embeddings using sqlite-vec with brute-force fallback.

    Tries to use the sqlite-vec virtual table for ANN search. If the extension
    is not available, falls back to brute-force cosine similarity over the
    nodes.embedding BLOB column.
    """

    def __init__(self, conn: sqlite3.Connection, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self.conn = conn
        self.dimensions = dimensions
        self._use_vec0 = self._check_vec0()

    def _check_vec0(self) -> bool:
        """Check if sqlite-vec virtual table is available."""
        try:
            self.conn.execute("SELECT 1 FROM sqlite_master WHERE name='nodes_embedding'").fetchone()
            # Table exists, vec0 is available
            return True
        except Exception:
            pass
        # Try creating the table to see if vec0 is available
        try:
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS nodes_embedding USING vec0("
                f"id TEXT PRIMARY KEY, embedding FLOAT[{self.dimensions}], metadata TEXT)"
            )
            return True
        except Exception:
            return False

    def create_table(self) -> None:
        """Create the vector table if using sqlite-vec."""
        if self._use_vec0:
            self.conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS nodes_embedding USING vec0("
                f"id TEXT PRIMARY KEY, embedding FLOAT[{self.dimensions}], metadata TEXT)"
            )

    def upsert_embeddings(
        self, nodes: list[Node], embeddings: list[list[float]], model_name: str
    ) -> None:
        import json

        for node, embedding in zip(nodes, embeddings, strict=True):
            blob = struct.pack(f"{len(embedding)}f", *embedding)
            metadata = json.dumps(
                {
                    "file_path": str(node.file_path),
                    "start_line": node.start_line,
                    "end_line": node.end_line,
                    "language": node.language.value,
                }
            )
            # Always store in nodes table (for brute-force fallback)
            self.conn.execute(
                "UPDATE nodes SET embedding=?, embedding_model=?, last_embedded_at=unixepoch() WHERE id=?",
                (blob, model_name, node.id),
            )
            # Also store in vec0 virtual table if available
            if self._use_vec0:
                with contextlib.suppress(Exception):
                    self.conn.execute(
                        "INSERT OR REPLACE INTO nodes_embedding (id, embedding, metadata) VALUES (?, ?, ?)",
                        (node.id, blob, metadata),
                    )

    def search(self, query_embedding: list[float], top_k: int = 20) -> list[tuple[str, float]]:
        if self._use_vec0:
            return self._search_vec0(query_embedding, top_k)
        return self._search_brute_force(query_embedding, top_k)

    def _search_vec0(self, query_embedding: list[float], top_k: int) -> list[tuple[str, float]]:
        """ANN search via sqlite-vec."""
        import struct as _struct

        blob = _struct.pack(f"{len(query_embedding)}f", *query_embedding)
        try:
            rows = self.conn.execute(
                "SELECT id, distance FROM nodes_embedding WHERE embedding MATCH ? AND k = ?",
                (blob, top_k),
            ).fetchall()
            return [(row[0], row[1]) for row in rows]
        except Exception:
            return self._search_brute_force(query_embedding, top_k)

    def _search_brute_force(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[str, float]]:
        """Brute-force cosine similarity over nodes.embedding BLOB column."""
        import math

        rows = self.conn.execute(
            "SELECT id, embedding FROM nodes WHERE embedding IS NOT NULL"
        ).fetchall()

        query_norm = math.sqrt(sum(x * x for x in query_embedding))
        if query_norm == 0:
            return []

        scored = []
        for node_id, blob in rows:
            if blob is None:
                continue
            vec = list(struct.unpack(f"{len(query_embedding)}f", blob))
            dot = sum(a * b for a, b in zip(query_embedding, vec, strict=False))
            vec_norm = math.sqrt(sum(x * x for x in vec))
            if vec_norm == 0:
                continue
            similarity = dot / (query_norm * vec_norm)
            scored.append((node_id, 1.0 - similarity))

        scored.sort(key=lambda x: x[1])
        return scored[:top_k]

    def is_embedded(self, node_id: str, model_name: str) -> bool:
        row = self.conn.execute(
            "SELECT last_embedded_at FROM nodes WHERE id=? AND embedding_model=?",
            (node_id, model_name),
        ).fetchone()
        return row is not None and row[0] is not None

    def delete_all(self) -> None:
        if self._use_vec0:
            with contextlib.suppress(Exception):
                self.conn.execute("DELETE FROM nodes_embedding")
        self.conn.execute(
            "UPDATE nodes SET embedding=NULL, embedding_model='none', last_embedded_at=NULL"
        )

    def update_meta(self, model_name: str, model_version: str, total_vectors: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO embedding_index_meta (model_name, model_version, dimensions, indexed_at, total_vectors) VALUES (?, ?, ?, unixepoch(), ?)",
            (model_name, model_version, self.dimensions, total_vectors),
        )


class CrossEncoderReranker:
    """Cross-encoder re-ranker for filtering noise from embedding search results.

    Uses ONNX Runtime for 14x faster inference on CPU. Falls back to PyTorch
    if ONNX model is not available.

    Uses a module-level cache to avoid reloading across instantiations.
    """

    _cache: dict[str, tuple[object, object]] = {}

    def __init__(self, model_name: str = DEFAULT_RERANKER, device: str | None = None) -> None:
        self.model_name = model_name
        self.device = device
        self._model: object | None = None
        self._tokenizer: object | None = None

    def _load_model(self) -> None:
        if self._model is not None:
            return
        cache_key = f"{self.model_name}:{self.device}"
        if cache_key in CrossEncoderReranker._cache:
            self._model, self._tokenizer = CrossEncoderReranker._cache[cache_key]
            logger.info("Re-ranker loaded from cache: %s", self.model_name)
            return
        logger.info("Loading cross-encoder re-ranker: %s", self.model_name)

        import os
        onnx_path = os.path.expanduser("~/.cache/codemesh/onnx-reranker")

        try:
            if os.path.exists(onnx_path):
                from optimum.onnxruntime import ORTModelForSequenceClassification
                from transformers import AutoTokenizer
                self._tokenizer = AutoTokenizer.from_pretrained(onnx_path)
                self._model = ORTModelForSequenceClassification.from_pretrained(onnx_path)
                logger.info("Re-ranker loaded (ONNX): %s", self.model_name)
            else:
                raise FileNotFoundError("ONNX model not found, falling back to PyTorch")
        except Exception as e:
            logger.info("ONNX load failed (%s), using PyTorch: %s", e, self.model_name)
            from transformers import AutoModelForSequenceClassification, AutoTokenizer
            self._tokenizer = AutoTokenizer.from_pretrained(self.model_name)
            self._model = AutoModelForSequenceClassification.from_pretrained(self.model_name)
            if self.device:
                self._model = self._model.to(self.device)
            self._model.eval()
            logger.info("Re-ranker loaded (PyTorch): %s", self.model_name)

        CrossEncoderReranker._cache[cache_key] = (self._model, self._tokenizer)

    def rerank(
        self,
        query: str,
        documents: list[tuple[str, str]],
        top_k: int | None = None,
        threshold: float = 0.3,
    ) -> list[tuple[str, float]]:
        """Re-rank documents by query-document relevance.

        Args:
            query: The search query string.
            documents: List of (id, text) tuples to re-rank.
            top_k: Maximum number of results to return (None = all above threshold).
            threshold: Minimum relevance score to include a document.

        Returns:
            List of (id, score) tuples sorted by score descending.
        """
        self._load_model()
        if self._model is None or self._tokenizer is None or not documents:
            return [(doc_id, 0.5) for doc_id, _ in documents]

        try:
            import torch

            pairs = [(query, doc_text) for _, doc_text in documents]
            inputs = self._tokenizer(
                [p[0] for p in pairs],
                [p[1] for p in pairs],
                padding=True,
                truncation=True,
                max_length=128,
                return_tensors="pt",
            )
            input_ids = inputs["input_ids"]
            attention_mask = inputs["attention_mask"]

            with torch.no_grad():
                try:
                    # ONNX Runtime model needs explicit input_ids/attention_mask
                    logits = self._model(
                        input_ids=input_ids, attention_mask=attention_mask
                    ).logits.squeeze(-1)
                except TypeError:
                    # PyTorch model accepts **inputs dict
                    if self.device:
                        inputs = {k: v.to(self.device) for k, v in inputs.items()}
                    logits = self._model(**inputs).logits.squeeze(-1)
                scores = torch.sigmoid(logits).cpu().tolist()
            results = [
                (doc_id, float(score))
                for (doc_id, _), score in zip(documents, scores, strict=False)
                if score >= threshold
            ]
            results.sort(key=lambda x: x[1], reverse=True)
            if top_k:
                results = results[:top_k]
            return results
        except Exception as e:
            logger.warning("Re-ranking failed: %s, returning unranked", e)
            return [(doc_id, 0.5) for doc_id, _ in documents]
