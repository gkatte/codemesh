"""Vector store: ANN search via sqlite-vec."""

from __future__ import annotations

import logging
import sqlite3
import struct
from pathlib import Path

logger = logging.getLogger(__name__)


def _encode_vector(vec: list[float]) -> bytes:
    """Encode a float32 vector as bytes for sqlite-vec."""
    return struct.pack(f"{len(vec)}f", *vec)


def _decode_vector(data: bytes, dimensions: int) -> list[float]:
    """Decode bytes back to a float32 vector."""
    return list(struct.unpack(f"{dimensions}f", data))


class VectorStore:
    """SQLite-vec based vector storage and ANN search.

    Creates a virtual table `vec_nodes` using sqlite-vec extension
    for approximate nearest neighbor search.
    """

    def __init__(self, conn: sqlite3.Connection, dimensions: int) -> None:
        self.conn = conn
        self.dimensions = dimensions
        self._ensure_table()

    def _ensure_table(self) -> None:
        """Create the vector virtual table if it doesn't exist."""
        try:
            self.conn.execute(
                f"""
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_nodes USING vec0(
                    node_id TEXT PRIMARY KEY,
                    embedding FLOAT[{self.dimensions}]
                )
                """
            )
        except sqlite3.OperationalError as e:
            # sqlite-vec extension may not be available; fall back to brute force
            logger.warning("sqlite-vec not available (%s), using brute-force search", e)
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS vec_nodes_bf (node_id TEXT PRIMARY KEY, embedding BLOB)"
            )

    def upsert(self, node_id: str, embedding: list[float]) -> None:
        """Insert or update a vector for a node."""
        blob = _encode_vector(embedding)
        try:
            self.conn.execute(
                "INSERT OR REPLACE INTO vec_nodes (node_id, embedding) VALUES (?, ?)",
                (node_id, blob),
            )
        except sqlite3.OperationalError:
            self.conn.execute(
                "INSERT OR REPLACE INTO vec_nodes_bf (node_id, embedding) VALUES (?, ?)",
                (node_id, blob),
            )

    def upsert_batch(self, items: list[tuple[str, list[float]]]) -> None:
        """Batch insert/update vectors."""
        try:
            self.conn.executemany(
                "INSERT OR REPLACE INTO vec_nodes (node_id, embedding) VALUES (?, ?)",
                [(nid, _encode_vector(emb)) for nid, emb in items],
            )
        except sqlite3.OperationalError:
            self.conn.executemany(
                "INSERT OR REPLACE INTO vec_nodes_bf (node_id, embedding) VALUES (?, ?)",
                [(nid, _encode_vector(emb)) for nid, emb in items],
            )

    def search(self, query_embedding: list[float], top_k: int = 20) -> list[tuple[str, float]]:
        """ANN search. Returns (node_id, distance) pairs sorted by distance."""
        blob = _encode_vector(query_embedding)
        try:
            rows = self.conn.execute(
                """
                SELECT node_id, distance
                FROM vec_nodes
                WHERE embedding MATCH ?
                ORDER BY distance
                LIMIT ?
                """,
                (blob, top_k),
            ).fetchall()
            return [(row[0], row[1]) for row in rows]
        except sqlite3.OperationalError:
            return self._brute_force_search(query_embedding, top_k)

    def _brute_force_search(
        self, query_embedding: list[float], top_k: int
    ) -> list[tuple[str, float]]:
        """Fallback brute-force cosine similarity search."""
        import math

        def cosine_sim(a: list[float], b_bytes: bytes) -> float:
            b = _decode_vector(b_bytes, len(a))
            dot = sum(x * y for x, y in zip(a, b))
            norm_a = math.sqrt(sum(x * x for x in a))
            norm_b = math.sqrt(sum(x * x for x in b))
            if norm_a == 0 or norm_b == 0:
                return 0.0
            return dot / (norm_a * norm_b)

        rows = self.conn.execute("SELECT node_id, embedding FROM vec_nodes_bf").fetchall()
        scored = [(nid, 1.0 - cosine_sim(query_embedding, emb_blob)) for nid, emb_blob in rows]
        scored.sort(key=lambda x: x[1])
        return scored[:top_k]

    def count(self) -> int:
        """Count indexed vectors."""
        try:
            row = self.conn.execute("SELECT COUNT(*) FROM vec_nodes").fetchone()
            return row[0] if row else 0
        except sqlite3.OperationalError:
            row = self.conn.execute("SELECT COUNT(*) FROM vec_nodes_bf").fetchone()
            return row[0] if row else 0
