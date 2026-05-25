# mypy: ignore-errors
"""Embedding projection for CodeMesh visualization."""

from __future__ import annotations

import hashlib
import struct
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

from codemesh.db.connection import create_connection, get_db_path

if TYPE_CHECKING:
    pass


def _get_cache_path(root: Path) -> Path:
    """Get the UMAP cache file path."""
    return root / ".codemesh" / "umap_cache.msgpack"


def _compute_index_hash(conn) -> str:
    """Compute a hash of the embedding index to invalidate cache."""

    rows = conn.execute(
        "SELECT id, embedding, last_embedded_at FROM nodes WHERE embedding IS NOT NULL ORDER BY id"
    ).fetchall()
    h = hashlib.md5()
    for row in rows:
        h.update(row["id"].encode())
        h.update(struct.pack("d", row["last_embedded_at"] or 0))
    return h.hexdigest()


def _load_cache(cache_path: Path, index_hash: str) -> list[dict] | None:
    """Load cached UMAP projection if valid."""
    import msgpack

    if not cache_path.exists():
        return None
    try:
        data = msgpack.unpackb(cache_path.read_bytes(), raw=False)
        if data.get("hash") == index_hash:
            return data["points"]
    except Exception:
        pass
    return None


def _save_cache(cache_path: Path, index_hash: str, points: list[dict]) -> None:
    """Save UMAP projection to cache."""
    import msgpack

    cache_path.parent.mkdir(parents=True, exist_ok=True)
    data = {"hash": index_hash, "points": points}
    cache_path.write_bytes(msgpack.packb(data))


def project_embeddings(
    root: Path,
    n_components: int = 2,
    n_neighbors: int = 15,
    min_dist: float = 0.1,
) -> list[dict]:
    """Project code symbol embeddings to 2D/3D using UMAP.

    Args:
        root: Project root path.
        n_components: 2 or 3 for 2D/3D projection.
        n_neighbors: UMAP n_neighbors parameter.
        min_dist: UMAP min_dist parameter.

    Returns:
        List of dicts with id, name, x, y, z, kind, file_path, degree.
    """
    import umap

    db_path = get_db_path(root)
    conn = create_connection(db_path)

    try:
        # Load embeddings
        rows = conn.execute(
            """
            SELECT n.id, n.name, n.kind, n.file_path,
                   n.embedding, n.qualified_name,
                   (SELECT COUNT(*) FROM edges WHERE source_id = n.id OR target_id = n.id) as degree
            FROM nodes n
            WHERE n.embedding IS NOT NULL
            """
        ).fetchall()

        if not rows:
            return []

        index_hash = _compute_index_hash(conn)
        cache_path = _get_cache_path(root)

        # Try cache first
        cached = _load_cache(cache_path, index_hash)
        if cached is not None:
            return cached

        # Build embedding matrix
        ids = []
        names = []
        kinds = []
        files = []
        qualified_names = []
        degrees = []
        vectors = []

        for row in rows:
            vec = np.frombuffer(row["embedding"], dtype=np.float32)
            if vec.shape[0] == 0:
                continue
            ids.append(row["id"])
            names.append(row["name"])
            kinds.append(row["kind"])
            files.append(row["file_path"])
            qualified_names.append(row["qualified_name"])
            degrees.append(row["degree"])
            vectors.append(vec)

        if not vectors:
            return []

        matrix = np.stack(vectors)

        # For very small datasets, skip UMAP and use random projection
        if len(vectors) <= 4:
            rng = np.random.RandomState(42)
            projected = rng.randn(len(vectors), n_components).astype(np.float32)
        else:
            # UMAP projection — adjust n_neighbors for small datasets
            n_neighbors_umap = min(n_neighbors, max(2, len(vectors) - 1))
            reducer = umap.UMAP(
                n_components=n_components,
                n_neighbors=n_neighbors_umap,
                min_dist=min_dist,
                metric="cosine",
                random_state=42,
            )
            projected = reducer.fit_transform(matrix)

        # Build result
        points = []
        for i in range(len(ids)):
            point = {
                "id": ids[i],
                "name": names[i],
                "kind": kinds[i],
                "file_path": files[i],
                "qualified_name": qualified_names[i],
                "degree": degrees[i],
                "x": float(projected[i][0]),
                "y": float(projected[i][1]),
            }
            if n_components == 3 and projected.shape[1] > 2:
                point["z"] = float(projected[i][2])
            points.append(point)

        # Cache result
        _save_cache(cache_path, index_hash, points)

        return points
    finally:
        conn.close()


def get_embedding_stats(root: Path) -> dict:
    """Get statistics about the embedding index."""
    db_path = get_db_path(root)
    conn = create_connection(db_path)
    try:
        total = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        embedded = conn.execute(
            "SELECT COUNT(*) FROM nodes WHERE embedding IS NOT NULL"
        ).fetchone()[0]
        model = conn.execute(
            "SELECT embedding_model FROM nodes WHERE embedding_model != 'none' LIMIT 1"
        ).fetchone()
        return {
            "total_nodes": total,
            "embedded_nodes": embedded,
            "model": model[0] if model else "none",
        }
    finally:
        conn.close()
