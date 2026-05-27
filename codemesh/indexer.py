"""High-level indexing operations."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from codemesh.db.connection import get_connection, get_db_path
from codemesh.db.queries import count_edges, count_nodes, insert_edge, insert_node
from codemesh.db.schema import init_db
from codemesh.extraction.orchestrator import ExtractionOrchestrator
from codemesh.resolution.resolver import ReferenceResolver

logger = logging.getLogger(__name__)


def index_project(
    root: Path,
    max_workers: int | None = None,
) -> dict[str, int | float]:
    """Index an entire project. Returns dict with counts.

    Steps:
    1. Extract AST nodes/edges via tree-sitter
    2. Insert into SQLite
    3. Resolve references

    No embeddings — pure BM25 keyword search with graph walk expansion.
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
        # Clear existing data before re-indexing
        conn.execute("DELETE FROM nodes_fts")
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM nodes")
        conn.commit()

        # Rebuild FTS5 from empty (triggers will repopulate)
        conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")

        # Step 2: Insert nodes
        for node in nodes:
            insert_node(conn, node)
        t2 = time.time()
        logger.info("Node insert: %d in %.2fs", len(nodes), t2 - t1)

        # Step 3: Insert edges
        from codemesh.db.queries import insert_file_node_dep
        for edge in edges:
            insert_edge(conn, edge)
            # Track cross-file dependencies for delta indexing
            src_node = next((n for n in nodes if n.id == edge.source_id), None)
            tgt_node = next((n for n in nodes if n.id == edge.target_id), None)
            if src_node and tgt_node and str(src_node.file_path) != str(tgt_node.file_path):
                insert_file_node_dep(conn, str(src_node.file_path), edge.target_id)
        t3 = time.time()
        logger.info("Edge insert: %d in %.2fs", len(edges), t3 - t2)

        # Step 4: Resolve references
        resolver = ReferenceResolver(conn)
        resolved = resolver.resolve_all()
        t4 = time.time()
        logger.info("Resolution: %d/%d resolved in %.2fs", resolved, len(edges), t4 - t3)

        # Step 5: Type inference for call edges
        typed = resolver.resolve_call_types()
        t5 = time.time()
        logger.info("Type inference: %d/%d call edges enriched in %.2fs",
                     typed, len(edges), t5 - t4)

        node_count = count_nodes(conn)
        edge_count = count_edges(conn)

    total_time = time.time() - t0
    logger.info(
        "Indexed %d nodes, %d edges in %.2fs",
        node_count, edge_count, total_time,
    )
    return {
        "nodes": node_count,
        "edges": edge_count,
        "time_seconds": round(total_time, 2),
    }


def delta_index_file(root: Path, file_path: Path) -> dict[str, int]:
    """Incrementally re-index a single file using symbol-level delta.

    Compares old nodes (from DB) vs new extraction (from tree-sitter),
    computes deleted/added/modified symbols, and applies minimal changes.
    Also invalidates cross-file edges pointing to deleted nodes.
    """
    from codemesh.db.queries import (
        count_ghost_edges,
        delete_edges_by_source,
        delete_node_and_edges,
        get_files_referencing_node,
        get_incoming_edges_to_node,
        get_nodes_by_file,
        insert_edge,
        insert_file_node_dep,
        insert_node,
    )
    from codemesh.extraction.orchestrator import _parse_file

    db_path = get_db_path(root)
    init_db(db_path)

    result = {"deleted": 0, "added": 0, "modified": 0, "ghost_edges": 0}

    with get_connection(db_path) as conn:
        # Get old nodes for this file
        old_nodes = get_nodes_by_file(conn, str(file_path))
        old_by_name = {n.qualified_name: n for n in old_nodes}

        # Extract new nodes/edges from file
        new_nodes, new_edges = _parse_file(file_path)
        new_by_name = {n.qualified_name: n for n in new_nodes}

        old_names = set(old_by_name.keys())
        new_names = set(new_by_name.keys())

        deleted = old_names - new_names
        added = new_names - old_names
        common = old_names & new_names
        modified = {k for k in common if old_by_name[k].content_hash != new_by_name[k].content_hash}

        # 1. Delete removed nodes and invalidate cross-file edges
        for name in deleted:
            node = old_by_name[name]
            delete_node_and_edges(conn, node.id)
            conn.execute(
                "DELETE FROM file_node_deps WHERE node_id = ?", (node.id,)
            )
            result["deleted"] += 1

        # 2. Insert new nodes
        for name in added:
            insert_node(conn, new_by_name[name])
            result["added"] += 1

        # 3. Update modified nodes (preserve IDs, update hash/signature)
        for name in modified:
            new_node = new_by_name[name]
            delete_edges_by_source(conn, new_node.id)
            insert_node(conn, new_node)
            result["modified"] += 1

        # 4. Re-extract edges for affected nodes
        affected = added | modified
        for name in affected:
            node = new_by_name[name]
            delete_edges_by_source(conn, node.id)
            for edge in new_edges:
                if edge.source_id == node.id:
                    insert_edge(conn, edge)
                    # Track cross-file deps
                    if edge.source_id != edge.target_id:
                        insert_file_node_dep(conn, str(file_path), edge.target_id)

        # 5. Re-extract edges for unmodified nodes that reference changed nodes
        #    (handles case where target of an edge was deleted and re-added)
        for name in (old_names - affected):
            node = old_by_name[name]
            for edge in new_edges:
                if edge.source_id == node.id:
                    insert_file_node_dep(conn, str(file_path), edge.target_id)

        # 6. Integrity check
        result["ghost_edges"] = count_ghost_edges(conn)
        conn.commit()

    logger.info(
        "Delta index %s: -%d +%d ~%d nodes, %d ghost edges",
        file_path, result["deleted"], result["added"],
        result["modified"], result["ghost_edges"],
    )
    return result


def sync_project(root: Path) -> None:
    """Start file watcher for a project."""
    db_path = get_db_path(root)
    init_db(db_path)

    import signal

    from codemesh.sync.watcher import FileWatcher

    with FileWatcher(root, db_path):
        signal.pause()
