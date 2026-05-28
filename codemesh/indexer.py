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

        # Optimize for bulk load: disable fsync, use exclusive lock
        # Must be done after commit (can't change inside transaction)
        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")

        # Drop FTS5 triggers during bulk load to avoid per-row index maintenance
        # Rebuild FTS5 in one shot after all nodes are inserted (~100x faster)
        conn.execute("DROP TRIGGER IF EXISTS nodes_ai")
        conn.execute("DROP TRIGGER IF EXISTS nodes_ad")
        conn.execute("DROP TRIGGER IF EXISTS nodes_au")

        # Step 2: Batch insert nodes via executemany
        node_rows = [
            (
                n.id,
                n.kind.value,
                n.name,
                n.qualified_name,
                str(n.file_path),
                n.language.value,
                n.start_line,
                n.end_line,
                n.start_column,
                n.end_column,
                n.docstring,
                n.signature,
                n.visibility,
                n.parent_id,
                "{}",
                int(n.is_exported),
                int(n.is_async),
                int(n.is_static),
                int(n.is_abstract),
                n.metadata.get("content_hash", "") if n.metadata else "",
            )
            for n in nodes
        ]
        conn.executemany(
            """
            INSERT OR REPLACE INTO nodes
                (id, kind, name, qualified_name, file_path, language,
                 start_line, end_line, start_column, end_column,
                 docstring, signature, visibility, parent_id, metadata,
                 is_exported, is_async, is_static, is_abstract, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            node_rows,
        )
        t2 = time.time()
        logger.info("Node insert: %d in %.2fs", len(nodes), t2 - t1)

        # Step 3: Batch insert edges via executemany
        # Pre-compute cross-file deps in Python (O(n) with set lookup)
        file_map: dict[str, str] = {}
        for n in nodes:
            file_map[n.id] = str(n.file_path)

        edge_rows = []
        dep_rows: list[tuple[str, str]] = []
        seen_deps: set[tuple[str, str]] = set()

        for edge in edges:
            edge_rows.append(
                (
                    edge.id,
                    edge.source_id,
                    edge.target_id,
                    edge.kind.value,
                    edge.confidence,
                    edge.weight_source,
                    edge.line,
                    edge.column,
                    "{}",
                    getattr(edge, "resolved_target", None) or "",
                    getattr(edge, "type_context", None) or "",
                )
            )
            src_file = file_map.get(edge.source_id)
            tgt_file = file_map.get(edge.target_id)
            if src_file and tgt_file and src_file != tgt_file:
                dep_key = (src_file, edge.target_id)
                if dep_key not in seen_deps:
                    dep_rows.append(dep_key)
                    seen_deps.add(dep_key)

        conn.executemany(
            """
            INSERT OR REPLACE INTO edges
                (id, source_id, target_id, kind, confidence,
                 weight_source, line, column, metadata,
                 resolved_target, type_context)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            edge_rows,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO file_node_deps (file_path, node_id) VALUES (?, ?)",
            dep_rows,
        )
        t3 = time.time()
        logger.info("Edge insert: %d in %.2fs", len(edges), t3 - t2)

        # Step 4: Rebuild FTS5 index in one shot (vs thousands of trigger-fired inserts)
        conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")

        # Step 5: Restore FTS5 triggers for future incremental inserts
        conn.execute(
            "CREATE TRIGGER nodes_ai AFTER INSERT ON nodes BEGIN "
            "INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature) "
            "VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature); "
            "END"
        )
        conn.execute(
            "CREATE TRIGGER nodes_ad AFTER DELETE ON nodes BEGIN "
            "INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature) "
            "VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature); "
            "END"
        )
        conn.execute(
            "CREATE TRIGGER nodes_au AFTER UPDATE ON nodes BEGIN "
            "INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature) "
            "VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature); "
            "INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature) "
            "VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature); "
            "END"
        )

        # Step 6: Commit bulk inserts, then restore safe PRAGMAs
        conn.commit()
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA locking_mode=NORMAL")

        # Step 7: Resolve references
        resolver = ReferenceResolver(conn)
        resolved = resolver.resolve_all()
        t4 = time.time()
        logger.info("Resolution: %d/%d resolved in %.2fs", resolved, len(edges), t4 - t3)

        # Step 8: Type inference for call edges
        typed = resolver.resolve_call_types()
        t5 = time.time()
        logger.info(
            "Type inference: %d/%d call edges enriched in %.2fs", typed, len(edges), t5 - t4
        )

        node_count = count_nodes(conn)
        edge_count = count_edges(conn)

    total_time = time.time() - t0
    logger.info(
        "Indexed %d nodes, %d edges in %.2fs",
        node_count,
        edge_count,
        total_time,
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
        get_nodes_by_file,
        insert_file_node_dep,
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
            conn.execute("DELETE FROM file_node_deps WHERE node_id = ?", (node.id,))
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
        for name in old_names - affected:
            node = old_by_name[name]
            for edge in new_edges:
                if edge.source_id == node.id:
                    insert_file_node_dep(conn, str(file_path), edge.target_id)

        # 6. Integrity check
        result["ghost_edges"] = count_ghost_edges(conn)
        conn.commit()

    logger.info(
        "Delta index %s: -%d +%d ~%d nodes, %d ghost edges",
        file_path,
        result["deleted"],
        result["added"],
        result["modified"],
        result["ghost_edges"],
    )
    return result


def sync_project(root: Path, debounce_delay: float = 1.0) -> None:
    """Start file watcher for a project."""
    db_path = get_db_path(root)
    init_db(db_path)

    import signal

    from codemesh.sync.watcher import FileWatcher

    with FileWatcher(root, db_path, debounce_delay=debounce_delay):
        signal.pause()
