"""Database schema definitions and migration support."""

from __future__ import annotations

from pathlib import Path

from codemesh.db.connection import get_connection

SCHEMA_SQL = """
-- Nodes table: code symbols
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    language TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    end_line INTEGER NOT NULL,
    start_column INTEGER DEFAULT 0,
    end_column INTEGER DEFAULT 0,
    docstring TEXT DEFAULT '',
    signature TEXT DEFAULT '',
    visibility TEXT DEFAULT 'public',
    parent_id TEXT,
    metadata TEXT DEFAULT '{}',
    is_exported INTEGER DEFAULT 0,
    is_async INTEGER DEFAULT 0,
    is_static INTEGER DEFAULT 0,
    is_abstract INTEGER DEFAULT 0,
    created_at INTEGER DEFAULT (unixepoch())
);

-- Edges table: relationships between symbols
CREATE TABLE IF NOT EXISTS edges (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    confidence REAL DEFAULT 1.0,
    weight_source TEXT DEFAULT 'ast',
    line INTEGER DEFAULT 0,
    column INTEGER DEFAULT 0,
    metadata TEXT DEFAULT '{}',
    created_at INTEGER DEFAULT (unixepoch())
);

-- Full-text search virtual table (BM25 via FTS5)
-- Column weights: id=0, name=20, qualified_name=5, docstring=1, signature=2
-- Heavy name weight ensures exact/prefix name matches rank above incidental
-- mentions in long docstrings or qualified names of nested symbols.
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id,
    name,
    qualified_name,
    docstring,
    signature,
    content='nodes',
    content_rowid='rowid'
);

-- Query log for analytics
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text TEXT NOT NULL,
    retrieved_nodes TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_lower_name ON nodes(lower(name));
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_confidence ON edges(confidence);

-- FTS5 triggers to keep index in sync
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, name, qualified_name, docstring, signature)
    VALUES ('delete', OLD.rowid, OLD.id, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);
    INSERT INTO nodes_fts(rowid, id, name, qualified_name, docstring, signature)
    VALUES (NEW.rowid, NEW.id, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);
END;
"""


def init_db(db_path: Path | None = None) -> None:
    """Initialize the database schema."""
    if db_path is None:
        db_path = Path.cwd() / ".codemesh" / "index.db"
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA_SQL)


def migrate_db(db_path: Path | None = None) -> None:
    """Run any pending migrations."""
    init_db(db_path)
