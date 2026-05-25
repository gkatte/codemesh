"""High-level indexing operations."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from codemesh.db.connection import get_connection, get_db_path
from codemesh.db.queries import count_edges, count_nodes, insert_edge, insert_node
from codemesh.db.schema import init_db
from codemesh.embedding.model import BatchEmbedder, EmbeddingModel
from codemesh.extraction.orchestrator import ExtractionOrchestrator
from codemesh.resolution.resolver import ReferenceResolver

logger = logging.getLogger(__name__)


def index_project(
    root: Path,
    max_workers: int | None = None,
    embed: bool = False,
    embedding_model: str | None = None,
) -> dict[str, int | float]:
    """Index an entire project. Returns dict with counts.

    Steps:
    1. Extract AST nodes/edges via tree-sitter
    2. Insert into SQLite
    3. Resolve references
    4. Compute neural embeddings (optional, for semantic search)
    """
    db_path = get_db_path(root)
    init_db(db_path)

    t0 = time.time()

    # Step 1: Extract
    orchestrator = ExtractionOrchestrator(root, max_workers=max_workers)
    nodes, edges = orchestrator.extract_all()
    t1 = time.time()
    logger.info("Extraction: %d nodes, %d edges in %.2fs", len(nodes), len(edges), t1 - t0)

    with get_connection(db_path) as conn:
        # Step 2: Insert nodes
        for node in nodes:
            insert_node(conn, node)
        t2 = time.time()
        logger.info("Node insert: %d in %.2fs", len(nodes), t2 - t1)

        # Step 3: Insert edges
        for edge in edges:
            insert_edge(conn, edge)
        t3 = time.time()
        logger.info("Edge insert: %d in %.2fs", len(edges), t3 - t2)

        # Step 4: Resolve references
        resolver = ReferenceResolver(conn)
        resolved = resolver.resolve_all()
        t4 = time.time()
        logger.info("Resolution: %d/%d resolved in %.2fs", resolved, len(edges), t4 - t3)

        node_count = count_nodes(conn)
        edge_count = count_edges(conn)

        # Step 5: Compute embeddings
        embedding_count = 0
        if embed:
            t5 = time.time()
            model = EmbeddingModel(model_name=embedding_model) if embedding_model else EmbeddingModel()
            embedder = BatchEmbedder(model=model, batch_size=64)
            embedding_count = embedder.embed_nodes(conn, nodes, root, force=True)
            t6 = time.time()
            logger.info("Embeddings: %d vectors in %.2fs", embedding_count, t6 - t5)

    total_time = time.time() - t0
    logger.info(
        "Indexed %d nodes, %d edges (%d embeddings) in %.2fs",
        node_count, edge_count, embedding_count, total_time,
    )
    return {
        "nodes": node_count,
        "edges": edge_count,
        "embeddings": embedding_count,
        "time_seconds": round(total_time, 2),
    }


def sync_project(root: Path) -> None:
    """Start file watcher for a project."""
    db_path = get_db_path(root)
    init_db(db_path)

    import signal

    from codemesh.sync.watcher import FileWatcher

    with FileWatcher(root, db_path):
        signal.pause()
