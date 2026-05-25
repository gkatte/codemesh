"""Database schema definitions and migration support."""

from __future__ import annotations

from pathlib import Path

from codemesh.db.connection import get_connection

SCHEMA_SQL = """
-- Nodes table: code symbols
-- Note: No FK on parent_id because parent nodes may not exist yet
-- during initial extraction (extraction order is not guaranteed).
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
    embedding BLOB,
    embedding_model TEXT DEFAULT 'none',
    last_embedded_at INTEGER,
    created_at INTEGER DEFAULT (unixepoch())
);

-- Edges table: relationships between symbols
-- Note: No FK constraints on source_id/target_id because unresolved
-- references (e.g., "unresolved:helper") are a normal intermediate state
-- that gets resolved by the ReferenceResolver after initial extraction.
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

-- Full-text search virtual table
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    name,
    qualified_name,
    docstring,
    signature,
    content='nodes',
    content_rowid='rowid'
);

-- Embedding index metadata
CREATE TABLE IF NOT EXISTS embedding_index_meta (
    model_name TEXT PRIMARY KEY,
    model_version TEXT NOT NULL,
    dimensions INTEGER NOT NULL,
    indexed_at INTEGER NOT NULL,
    total_vectors INTEGER NOT NULL
);

-- Query log for training data collection
CREATE TABLE IF NOT EXISTS query_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text TEXT NOT NULL,
    query_type TEXT,
    retrieved_nodes TEXT NOT NULL,
    agent_action_tokens INTEGER,
    agent_tool_calls INTEGER,
    resolution_success INTEGER,
    created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

-- Re-ranker training pairs
CREATE TABLE IF NOT EXISTS training_pairs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    query_text TEXT NOT NULL,
    code_snippet TEXT NOT NULL,
    node_id TEXT NOT NULL,
    label REAL NOT NULL,
    source TEXT NOT NULL,
    created_at INTEGER NOT NULL DEFAULT (unixepoch())
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_nodes_kind ON nodes(kind);
CREATE INDEX IF NOT EXISTS idx_nodes_file ON nodes(file_path);
CREATE INDEX IF NOT EXISTS idx_nodes_language ON nodes(language);
CREATE INDEX IF NOT EXISTS idx_nodes_qualified ON nodes(qualified_name);
CREATE INDEX IF NOT EXISTS idx_nodes_embedding ON nodes(embedding_model, last_embedded_at);
CREATE INDEX IF NOT EXISTS idx_edges_source ON edges(source_id);
CREATE INDEX IF NOT EXISTS idx_edges_target ON edges(target_id);
CREATE INDEX IF NOT EXISTS idx_edges_kind ON edges(kind);
CREATE INDEX IF NOT EXISTS idx_edges_confidence ON edges(confidence);
CREATE INDEX IF NOT EXISTS idx_query_log_type ON query_log(query_type);
CREATE INDEX IF NOT EXISTS idx_training_label ON training_pairs(label);

-- FTS5 triggers to keep index in sync
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, name, qualified_name, docstring, signature)
    VALUES (NEW.rowid, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, name, qualified_name, docstring, signature)
    VALUES ('delete', OLD.rowid, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, name, qualified_name, docstring, signature)
    VALUES ('delete', OLD.rowid, OLD.name, OLD.qualified_name, OLD.docstring, OLD.signature);
    INSERT INTO nodes_fts(rowid, name, qualified_name, docstring, signature)
    VALUES (NEW.rowid, NEW.name, NEW.qualified_name, NEW.docstring, NEW.signature);
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
