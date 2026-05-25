"""High-level indexing operations."""

from __future__ import annotations

import logging
from pathlib import Path

from codemesh.db.connection import get_connection, get_db_path
from codemesh.db.queries import count_edges, count_nodes, insert_edge, insert_node
from codemesh.db.schema import init_db
from codemesh.extraction.orchestrator import ExtractionOrchestrator
from codemesh.resolution.resolver import ReferenceResolver
from codemesh.sync.watcher import FileWatcher

logger = logging.getLogger(__name__)


def index_project(root: Path, max_workers: int | None = None) -> dict[str, int]:
    """Index an entire project. Returns dict with 'nodes' and 'edges' counts."""
    db_path = get_db_path(root)
    init_db(db_path)

    orchestrator = ExtractionOrchestrator(root, max_workers=max_workers)
    nodes, edges = orchestrator.extract_all()

    with get_connection(db_path) as conn:
        for node in nodes:
            insert_node(conn, node)
        for edge in edges:
            insert_edge(conn, edge)
        resolver = ReferenceResolver(conn)
        resolver.resolve_all()
        node_count = count_nodes(conn)
        edge_count = count_edges(conn)

    logger.info("Indexed %d nodes, %d edges", node_count, edge_count)
    return {"nodes": node_count, "edges": edge_count}


def sync_project(root: Path) -> None:
    """Start file watcher for a project."""
    db_path = get_db_path(root)
    init_db(db_path)

    import signal

    with FileWatcher(root, db_path):
        signal.pause()
