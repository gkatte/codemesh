"""High-level query operations: BM25 keyword search + graph walk expansion."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from codemesh.context.builder import ContextBuilder, ContextFormat, ContextOptions
from codemesh.db.connection import get_connection, get_db_path
from codemesh.db.queries import get_node, search_nodes_fts
from codemesh.db.schema import init_db
from codemesh.graph.traverser import GraphTraverser
from codemesh.types import Node

logger = logging.getLogger(__name__)


# ── Stop words for query term extraction ────────────────────────────────────

_STOP_WORDS = {
    "the",
    "a",
    "an",
    "and",
    "or",
    "but",
    "in",
    "on",
    "at",
    "to",
    "for",
    "of",
    "with",
    "by",
    "from",
    "is",
    "it",
    "that",
    "this",
    "are",
    "was",
    "be",
    "has",
    "had",
    "have",
    "do",
    "does",
    "did",
    "will",
    "would",
    "could",
    "should",
    "may",
    "might",
    "can",
    "shall",
    "not",
    "no",
    "how",
    "what",
    "where",
    "when",
    "who",
    "which",
    "why",
    "i",
    "me",
    "my",
    "we",
    "our",
    "you",
    "your",
    "he",
    "she",
    "they",
    "show",
    "give",
    "tell",
    "also",
    "into",
    "then",
    "than",
    "code",
    "file",
    "files",
}


def _extract_compound_terms(query: str) -> list[str]:
    """Extract compound identifiers (camelCase, snake_case, dot.notation) from query."""
    terms = []
    # CamelCase / PascalCase
    for m in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9]*(?:[A-Z][a-z]+)+)\b", query):
        if len(m.group(1)) >= 3:
            terms.append(m.group(1))
    # snake_case
    for m in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9]*(?:_[a-zA-Z0-9]+)+)\b", query):
        if len(m.group(1)) >= 3:
            terms.append(m.group(1))
    return terms


def query_codebase(
    root: Path,
    query: str,
    limit: int = 10,
    fmt: str = "xml",
) -> str:
    """Query the indexed codebase using BM25 keyword search + graph walk.

    Uses a 3-tier search strategy:
    1. FTS5 with prefix matching and BM25 column weights (name=20, qualified_name=5)
    2. LIKE-based substring fallback for camelCase matching
    3. Fuzzy edit-distance fallback for typos

    Post-hoc scoring: kind bonus + name match bonus + exported bonus.
    Graph walk expansion (BFS depth=1) finds related symbols.
    """
    db_path = get_db_path(root)
    init_db(db_path)

    with get_connection(db_path) as conn:
        results = _bm25_search(conn, query, top_k=limit)

        if not results:
            return f"No results for: {query}"

        # Separate BM25 seeds from graph-walk nodes
        # _bm25_search returns (Node, score) tuples
        # We need to track which are seeds vs graph-expanded
        format_enum = (
            ContextFormat.XML
            if fmt == "xml"
            else (
                ContextFormat.STRUCTURED
                if fmt == "structured"
                else (ContextFormat.GRAPH if fmt == "graph" else ContextFormat.MARKDOWN)
            )
        )
        builder = ContextBuilder(conn, root)

        if format_enum in (ContextFormat.STRUCTURED, ContextFormat.GRAPH):
            entry_points = results[:5]
            related = results[5:] if len(results) > 5 else []
            return builder.build(
                results,
                query,
                ContextOptions(
                    max_snippets=5,
                    max_tokens=2000,
                    max_lines_per_snippet=12,
                    format=format_enum,
                ),
                entry_points=entry_points,
                related=related,
            )

        return builder.build(
            results,
            query,
            ContextOptions(
                max_snippets=3,
                max_tokens=1200,
                max_lines_per_snippet=10,
                max_snippet_chars=600,
                max_per_file=1,
                format=format_enum,
            ),
        )


def get_context(root: Path, query: str, max_tokens: int = 8000) -> str:
    """Get context for a query.

    If the query matches a known symbol name, returns the symbol's
    dependency subgraph. Otherwise, falls back to BM25 search.
    """
    db_path = get_db_path(root)
    init_db(db_path)

    with get_connection(db_path) as conn:
        # Try exact symbol lookup first
        symbol_result = _try_symbol_lookup(conn, query)
        if symbol_result is not None:
            nodes_with_scores = symbol_result
        else:
            nodes_with_scores = _bm25_search(conn, query, top_k=20)

        if not nodes_with_scores:
            return f'<code_context query="{query}">\n  No results found.\n</code_context>'

        builder = ContextBuilder(conn, root)
        return builder.build(
            nodes_with_scores,
            f"Context for {query}",
            ContextOptions(max_tokens=max_tokens),
        )


# ── Internal helpers ──────────────────────────────────────────────────────


def _bm25_search(conn, query: str, top_k: int = 30) -> list[tuple[Node, float]]:
    """BM25 search via FTS5 + graph walk expansion.

    1. FTS5 BM25 keyword search (with prefix matching, LIKE fallback, fuzzy fallback)
    2. Graph walk expansion (BFS depth=2) to find related symbols
    3. Score = BM25 rank for seeds, decayed score for graph neighbors
    """
    # Tier 1-3: FTS5 → LIKE → Fuzzy (all handled in search_nodes_fts)
    candidates = search_nodes_fts(conn, query, limit=top_k)
    if not candidates:
        return []

    traverser = GraphTraverser()
    results: list[tuple[Node, float]] = []
    seen: set[str] = set()

    for node, score in candidates:
        if node.id in seen:
            continue
        seen.add(node.id)
        results.append((node, score))

        # Expand via graph walk (BFS depth=1)
        subgraph = traverser.traverse(
            conn,
            [node.id],
            max_depth=1,
            max_nodes=10,
        )
        for nid, tr in subgraph.nodes.items():
            if nid not in seen:
                seen.add(nid)
                n = get_node(conn, nid)
                if n is not None:
                    results.append((n, tr.score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def _try_symbol_lookup(conn, query: str) -> list[tuple[Node, float]] | None:
    """Try to find an exact symbol match. Returns None if not found."""
    # Try exact qualified name match
    row = conn.execute("SELECT * FROM nodes WHERE qualified_name = ?", (query,)).fetchone()
    if row:
        from codemesh.db.queries import row_to_node

        return [(row_to_node(row), 1.0)]

    # Try exact name match
    row = conn.execute("SELECT * FROM nodes WHERE name = ? LIMIT 1", (query,)).fetchone()
    if row:
        from codemesh.db.queries import row_to_node

        return [(row_to_node(row), 0.9)]

    # Try case-insensitive name match
    row = conn.execute(
        "SELECT * FROM nodes WHERE lower(name) = lower(?) LIMIT 1", (query,)
    ).fetchone()
    if row:
        from codemesh.db.queries import row_to_node

        return [(row_to_node(row), 0.8)]

    return None
