"""Prepared database queries for common operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind

if TYPE_CHECKING:
    import sqlite3


def insert_node(conn: sqlite3.Connection, node: Node) -> None:
    """Insert or replace a node."""
    conn.execute(
        """
        INSERT OR REPLACE INTO nodes
        (id, kind, name, qualified_name, file_path, language,
         start_line, end_line, start_column, end_column,
         docstring, signature, visibility, parent_id, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node.id,
            node.kind.value,
            node.name,
            node.qualified_name,
            str(node.file_path),
            node.language.value,
            node.start_line,
            node.end_line,
            node.start_column,
            node.end_column,
            node.docstring,
            node.signature,
            node.visibility,
            node.parent_id,
            json.dumps(node.metadata),
        ),
    )


def insert_edge(conn: sqlite3.Connection, edge: Edge) -> None:
    """Insert or replace an edge."""
    conn.execute(
        """
        INSERT OR REPLACE INTO edges
        (id, source_id, target_id, kind, confidence, weight_source,
         line, column, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            edge.id,
            edge.source_id,
            edge.target_id,
            edge.kind.value,
            edge.confidence,
            edge.weight_source,
            edge.line,
            edge.column,
            json.dumps(edge.metadata),
        ),
    )


def get_node(conn: sqlite3.Connection, node_id: str) -> Node | None:
    """Get a node by ID."""
    row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        return None
    return _row_to_node(row)


def get_nodes_by_file(conn: sqlite3.Connection, file_path: str) -> list[Node]:
    """Get all nodes for a file."""
    rows = conn.execute("SELECT * FROM nodes WHERE file_path = ?", (file_path,)).fetchall()
    return [_row_to_node(r) for r in rows]


def get_edges_by_source(conn: sqlite3.Connection, source_id: str) -> list[Edge]:
    """Get all edges from a source node."""
    rows = conn.execute("SELECT * FROM edges WHERE source_id = ?", (source_id,)).fetchall()
    return [_row_to_edge(r) for r in rows]


def get_edges_by_target(conn: sqlite3.Connection, target_id: str) -> list[Edge]:
    """Get all edges to a target node."""
    rows = conn.execute("SELECT * FROM edges WHERE target_id = ?", (target_id,)).fetchall()
    return [_row_to_edge(r) for r in rows]


def get_all_nodes(conn: sqlite3.Connection) -> list[Node]:
    """Get all nodes."""
    rows = conn.execute("SELECT * FROM nodes").fetchall()
    return [_row_to_node(r) for r in rows]


def get_all_edges(conn: sqlite3.Connection) -> list[Edge]:
    """Get all edges."""
    rows = conn.execute("SELECT * FROM edges").fetchall()
    return [_row_to_edge(r) for r in rows]


def delete_nodes_by_file(conn: sqlite3.Connection, file_path: str) -> int:
    """Delete all nodes for a file. Returns count of deleted nodes."""
    cursor = conn.execute("DELETE FROM nodes WHERE file_path = ?", (file_path,))
    return cursor.rowcount


def delete_edges_by_file(conn: sqlite3.Connection, file_path: str) -> int:
    """Delete all edges where source or target is in a file."""
    cursor = conn.execute(
        """
        DELETE FROM edges WHERE source_id IN (
            SELECT id FROM nodes WHERE file_path = ?
        ) OR target_id IN (
            SELECT id FROM nodes WHERE file_path = ?
        )
        """,
        (file_path, file_path),
    )
    return cursor.rowcount


def count_nodes(conn: sqlite3.Connection) -> int:
    """Count total nodes."""
    row = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
    return row[0] if row else 0


def count_edges(conn: sqlite3.Connection) -> int:
    """Count total edges."""
    row = conn.execute("SELECT COUNT(*) FROM edges").fetchone()
    return row[0] if row else 0


_FTS5_RESERVED = frozenset({"AND", "OR", "NOT", "NEAR"})


def _sanitize_fts5_query(query: str) -> str:
    """Sanitize a user query for FTS5 MATCH.

    - Strips leading/trailing whitespace
    - Removes empty queries
    - Escapes FTS5 special characters: ( ) " * ^
    - Falls back to prefix search on identifier-like tokens
    """
    import re

    query = query.strip()
    if not query:
        return ""

    # Extract identifier-like tokens (snake_case, CamelCase, dotted.paths)
    tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_.]*", query)
    if not tokens:
        return ""

    # Build safe FTS5 query: each token as a prefix match
    safe_parts = []
    for tok in tokens:
        # Remove any internal FTS5-special chars
        tok = tok.replace('"', "").replace("(", "").replace(")", "")
        if tok and tok.upper() not in _FTS5_RESERVED:
            safe_parts.append(f"{tok}*")

    return " OR ".join(safe_parts) if safe_parts else ""


def search_nodes_fts(
    conn: sqlite3.Connection, query: str, limit: int = 20
) -> list[tuple[Node, float]]:
    """Full-text search on nodes. Returns (Node, rank) pairs."""
    safe_query = _sanitize_fts5_query(query)
    if not safe_query:
        return []
    rows = conn.execute(
        """
        SELECT n.*, rank
        FROM nodes_fts f
        JOIN nodes n ON n.rowid = f.rowid
        WHERE nodes_fts MATCH ?
        ORDER BY rank
        LIMIT ?
        """,
        (safe_query, limit),
    ).fetchall()
    return [(_row_to_node(r), r["rank"]) for r in rows]


def _row_to_node(row: sqlite3.Row) -> Node:
    """Convert a database row to a Node."""
    return Node(
        id=row["id"],
        kind=NodeKind(row["kind"]),
        name=row["name"],
        qualified_name=row["qualified_name"],
        file_path=Path(row["file_path"]),
        language=Language(row["language"]),
        start_line=row["start_line"],
        end_line=row["end_line"],
        start_column=row["start_column"],
        end_column=row["end_column"],
        docstring=row["docstring"],
        signature=row["signature"],
        visibility=row["visibility"],
        parent_id=row["parent_id"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )


def _row_to_edge(row: sqlite3.Row) -> Edge:
    """Convert a database row to an Edge."""
    return Edge(
        id=row["id"],
        source_id=row["source_id"],
        target_id=row["target_id"],
        kind=EdgeKind(row["kind"]),
        confidence=row["confidence"],
        weight_source=row["weight_source"],
        line=row["line"],
        column=row["column"],
        metadata=json.loads(row["metadata"]) if row["metadata"] else {},
    )
