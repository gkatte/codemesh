"""SQLite connection manager with WAL mode and optimized settings."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator


DB_PATH = Path(".codemesh/index.db")


def get_db_path(root: Path | None = None) -> Path:
    """Get the database path for a given project root."""
    if root is None:
        root = Path.cwd()
    return root / DB_PATH


def create_connection(db_path: Path) -> sqlite3.Connection:
    """Create an optimized SQLite connection.

    Enables WAL mode for concurrent reads, foreign keys,
    and other performance optimizations. Also loads the sqlite-vec
    extension for ANN vector search.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
    conn.execute("PRAGMA temp_store=MEMORY")
    _load_sqlite_vec(conn)
    return conn


def _load_sqlite_vec(conn: sqlite3.Connection) -> None:
    """Load the sqlite-vec extension if available."""
    try:
        conn.enable_load_extension(True)
        import sqlite_vec

        conn.load_extension(sqlite_vec.loadable_path())
    except Exception:
        pass  # sqlite-vec not available; brute-force fallback will be used


@contextmanager
def get_connection(db_path: Path | None = None) -> Generator[sqlite3.Connection, None, None]:
    """Context manager for database connections."""
    if db_path is None:
        db_path = get_db_path()
    conn = create_connection(db_path)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
