"""High-level query operations."""

from __future__ import annotations

from pathlib import Path

from codemesh.context.builder import ContextBuilder, ContextFormat, ContextOptions
from codemesh.db.connection import get_connection, get_db_path
from codemesh.db.schema import init_db
from codemesh.graph.query_manager import QueryManager


def query_codebase(root: Path, query: str, limit: int = 10, fmt: str = "xml") -> str:
    """Query the indexed codebase."""
    db_path = get_db_path(root)
    init_db(db_path)

    with get_connection(db_path) as conn:
        qm = QueryManager(conn)
        results = qm.structural_search(query, max_depth=3)

        if not results:
            return f"No results for: {query}"

        format_enum = ContextFormat.XML if fmt == "xml" else ContextFormat.MARKDOWN
        builder = ContextBuilder(conn, root)
        return builder.build(results, query, ContextOptions(max_snippets=limit, format=format_enum))


def get_context(root: Path, symbol: str, max_tokens: int = 8000) -> str:
    """Get context for a specific symbol."""
    db_path = get_db_path(root)
    init_db(db_path)

    with get_connection(db_path) as conn:
        qm = QueryManager(conn)
        subgraph = qm.find_dependents(symbol, max_depth=3)

        from codemesh.db.queries import get_node

        nodes_with_scores = []
        for nid, tr in subgraph.nodes.items():
            n = get_node(conn, nid)
            if n:
                nodes_with_scores.append((n, tr.score))

        builder = ContextBuilder(conn, root)
        return builder.build(
            nodes_with_scores, f"Context for {symbol}", ContextOptions(max_tokens=max_tokens)
        )
