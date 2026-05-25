# mypy: ignore-errors
"""Watchdog-based file watcher with debounced synchronization."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from watchdog.events import (  # type: ignore[import-untyped]
    FileSystemEvent,
    FileSystemEventHandler,
)
from watchdog.observers import Observer

from codemesh.db.connection import get_connection, get_db_path
from codemesh.db.queries import delete_edges_by_file, delete_nodes_by_file, insert_edge, insert_node
from codemesh.extraction.orchestrator import _parse_file, is_source_file
from codemesh.resolution.resolver import ReferenceResolver

logger = logging.getLogger(__name__)


class Debouncer:
    """Debounces rapid file change events."""

    def __init__(self, delay: float = 1.0) -> None:
        self.delay = delay
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def debounce(self, key: str, callback: Callable[[], None]) -> None:
        with self._lock:
            if key in self._timers:
                self._timers[key].cancel()
            timer = threading.Timer(self.delay, callback)
            timer.daemon = True
            self._timers[key] = timer
            timer.start()


class CodeMeshEventHandler(FileSystemEventHandler):
    """Handles filesystem events for CodeMesh."""

    def __init__(self, root: Path, db_path: Path, debounce_delay: float = 1.0) -> None:
        self.root = root
        self.db_path = db_path
        self.debouncer = Debouncer(delay=debounce_delay)
        super().__init__()

    def on_created(self, event: FileSystemEvent) -> None:
        path = str(event.src_path)
        if not event.is_directory and is_source_file(Path(path)):
            self._schedule_sync(Path(path))

    def on_modified(self, event: FileSystemEvent) -> None:
        path = str(event.src_path)
        if not event.is_directory and is_source_file(Path(path)):
            self._schedule_sync(Path(path))

    def on_deleted(self, event: FileSystemEvent) -> None:
        path = str(event.src_path)
        if not event.is_directory and is_source_file(Path(path)):
            self._remove_file(Path(path))

    def on_moved(self, event: FileSystemEvent) -> None:
        src = str(event.src_path)
        dest = str(event.dest_path) if hasattr(event, "dest_path") else None
        if is_source_file(Path(src)):
            self._remove_file(Path(src))
        if dest and is_source_file(Path(dest)):
            self._schedule_sync(Path(dest))

    def _schedule_sync(self, file_path: Path) -> None:
        key = str(file_path)
        logger.info("File changed: %s (debouncing)", file_path)
        self.debouncer.debounce(key, lambda: self._sync_file(file_path))

    def _sync_file(self, file_path: Path) -> None:
        logger.info("Syncing: %s", file_path)
        try:
            with get_connection(self.db_path) as conn:
                delete_edges_by_file(conn, str(file_path))
                delete_nodes_by_file(conn, str(file_path))
                nodes, edges = _parse_file(file_path)
                for node in nodes:
                    insert_node(conn, node)
                for edge in edges:
                    insert_edge(conn, edge)
                resolver = ReferenceResolver(conn)
                resolver.resolve_all()
            logger.info("Synced: %s (%d nodes, %d edges)", file_path, len(nodes), len(edges))
        except Exception as e:
            logger.error("Failed to sync %s: %s", file_path, e)

    def _remove_file(self, file_path: Path) -> None:
        with get_connection(self.db_path) as conn:
            delete_edges_by_file(conn, str(file_path))
            delete_nodes_by_file(conn, str(file_path))
        logger.info("Removed: %s", file_path)


class FileWatcher:
    """Watches a directory for file changes and syncs the index."""

    def __init__(
        self, root: Path, db_path: Path | None = None, debounce_delay: float = 1.0
    ) -> None:
        self.root = root
        self.db_path = db_path or get_db_path(root)
        self.debounce_delay = debounce_delay
        self._observer: Observer | None = None

    def start(self) -> None:
        handler = CodeMeshEventHandler(self.root, self.db_path, self.debounce_delay)
        self._observer = Observer()
        self._observer.schedule(handler, str(self.root), recursive=True)
        self._observer.daemon = True
        self._observer.start()
        logger.info("File watcher started for %s", self.root)

    def stop(self) -> None:
        if self._observer:
            self._observer.stop()
            self._observer.join(timeout=5)
            self._observer = None
            logger.info("File watcher stopped")

    def __enter__(self) -> FileWatcher:
        self.start()
        return self

    def __exit__(self, *args: object) -> None:
        self.stop()
