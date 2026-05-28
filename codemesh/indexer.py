"""High-level indexing operations."""

from __future__ import annotations

import logging
import time
from pathlib import Path

from codemesh.db.connection import get_connection, get_db_path
from codemesh.db.queries import count_edges, count_nodes
from codemesh.db.schema import init_db
from codemesh.extraction.orchestrator import ExtractionOrchestrator, discover_files
from codemesh.resolution.resolver import ReferenceResolver

logger = logging.getLogger(__name__)


def index_project(
    root: Path,
    max_workers: int | None = None,
    quiet: bool = False,
) -> dict[str, int | float]:
    """Index an entire project. Returns dict with counts.

    Steps:
    1. Discover source files
    2. Extract AST nodes/edges via tree-sitter (with progress bar)
    3. Batch insert into SQLite
    4. Rebuild FTS5 index
    5. Resolve references
    6. Type inference for call edges
    """
    db_path = get_db_path(root)
    init_db(db_path)

    t0 = time.time()
    node_count = 0
    edge_count = 0

    # Count files upfront for progress bar total
    all_files = discover_files(root)
    total_files = len(all_files)
    logger.info("Discovered %d source files in %s", total_files, root)

    if quiet:
        # No progress bar — run silently
        orchestrator = ExtractionOrchestrator(root, max_workers=max_workers)
        nodes, edges = orchestrator.extract_all()
        t1 = time.time()
    else:
        from rich.progress import (
            BarColumn,
            Progress,
            SpinnerColumn,
            TaskProgressColumn,
            TextColumn,
            TimeElapsedColumn,
            TimeRemainingColumn,
        )

        with Progress(
            SpinnerColumn(),
            TextColumn("[bold cyan]{task.description}"),
            BarColumn(bar_width=40),
            TaskProgressColumn(),
            TextColumn("\u2022"),
            TimeElapsedColumn(),
            TextColumn("\u2022"),
            TimeRemainingColumn(),
            transient=True,
        ) as progress:
            parse_task = progress.add_task("Parsing code  ", total=total_files)

            orchestrator = ExtractionOrchestrator(root, max_workers=max_workers)

            def on_file_done(completed: int, total: int) -> None:
                progress.update(parse_task, completed=completed, total=total)

            nodes, edges = orchestrator.extract_all(progress_cb=on_file_done)
            t1 = time.time()

            progress.update(
                parse_task,
                description="Parsing code  ... done",
                completed=total_files,
                total=total_files,
            )
        logger.info("Extraction: %d nodes, %d edges in %.2fs", len(nodes), len(edges), t1 - t0)

    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM nodes_fts")
        conn.execute("DELETE FROM edges")
        conn.execute("DELETE FROM nodes")
        conn.commit()

        conn.execute("PRAGMA synchronous=OFF")
        conn.execute("PRAGMA locking_mode=EXCLUSIVE")

        conn.execute("DROP TRIGGER IF EXISTS nodes_ai")
        conn.execute("DROP TRIGGER IF EXISTS nodes_ad")
        conn.execute("DROP TRIGGER IF EXISTS nodes_au")

        # Batch insert nodes
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

        # Batch insert edges
        file_map: dict[str, str] = {n.id: str(n.file_path) for n in nodes}
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

        # Rebuild FTS5
        conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild')")

        # Restore FTS5 triggers
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

        conn.commit()
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA locking_mode=NORMAL")

        # Resolve references
        resolver = ReferenceResolver(conn)
        resolved = resolver.resolve_all()
        t4 = time.time()
        logger.info("Resolution: %d/%d resolved in %.2fs", resolved, len(edges), t4 - t3)

        typed = resolver.resolve_call_types()
        t5 = time.time()
        logger.info(
            "Type inference: %d/%d call edges enriched in %.2fs", typed, len(edges), t5 - t4
        )

        node_count = count_nodes(conn)
        edge_count = count_edges(conn)

    total_time = time.time() - t0
    logger.info("Indexed %d nodes, %d edges in %.2fs", node_count, edge_count, total_time)
    return {
        "nodes": node_count,
        "edges": edge_count,
        "time_seconds": round(total_time, 2),
    }
