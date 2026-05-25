# mypy: ignore-errors
"""Neural code embedding layer: model, batch embedder, vector store."""

from __future__ import annotations

import logging
import sqlite3
import struct
from pathlib import Path

from codemesh.types import Node

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "nomic-ai/nomic-embed-code"
DEFAULT_DIMENSIONS = 768


class EmbeddingModel:
    """Loads and runs the sentence-transformers embedding model."""

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
        logger.info("Loading embedding model: %s", self.model_name)
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self.model_name, device=self.device)
        test = self._model.encode(["test"], show_progress_bar=False)
        self._dimensions = test.shape[1]
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
    {"function", "method", "class", "interface", "struct", "trait", "enum"}
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
    """Stores and queries embeddings using SQLite-vec."""

    def __init__(self, conn: sqlite3.Connection, dimensions: int = 768) -> None:
        self.conn = conn
        self.dimensions = dimensions

    def create_table(self) -> None:
        self.conn.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS nodes_embedding USING vec0(id TEXT PRIMARY KEY, embedding FLOAT[{self.dimensions}], metadata TEXT)"
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
            self.conn.execute(
                "INSERT OR REPLACE INTO nodes_embedding (id, embedding, metadata) VALUES (?, ?, ?)",
                (node.id, blob, metadata),
            )
            self.conn.execute(
                "UPDATE nodes SET embedding=?, embedding_model=?, last_embedded_at=unixepoch() WHERE id=?",
                (blob, model_name, node.id),
            )

    def search(self, query_embedding: list[float], top_k: int = 20) -> list[tuple[str, float]]:
        blob = struct.pack(f"{len(query_embedding)}f", *query_embedding)
        rows = self.conn.execute(
            "SELECT id, distance FROM nodes_embedding WHERE embedding MATCH ? AND k = ?",
            (blob, top_k),
        ).fetchall()
        return [(row[0], row[1]) for row in rows]

    def is_embedded(self, node_id: str, model_name: str) -> bool:
        row = self.conn.execute(
            "SELECT last_embedded_at FROM nodes WHERE id=? AND embedding_model=?",
            (node_id, model_name),
        ).fetchone()
        return row is not None and row[0] is not None

    def delete_all(self) -> None:
        self.conn.execute("DELETE FROM nodes_embedding")
        self.conn.execute(
            "UPDATE nodes SET embedding=NULL, embedding_model='none', last_embedded_at=NULL"
        )

    def update_meta(self, model_name: str, model_version: str, total_vectors: int) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO embedding_index_meta (model_name, model_version, dimensions, indexed_at, total_vectors) VALUES (?, ?, ?, unixepoch(), ?)",
            (model_name, model_version, self.dimensions, total_vectors),
        )
