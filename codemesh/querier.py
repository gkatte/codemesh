"""High-level query operations: hybrid retrieval with RRF fusion."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from codemesh.context.builder import ContextBuilder, ContextFormat, ContextOptions
from codemesh.db.connection import get_connection, get_db_path
from codemesh.db.queries import get_all_nodes, search_nodes_fts
from codemesh.db.schema import init_db
from codemesh.embedding.model import EmbeddingModel
from codemesh.embedding.store import VectorStore
from codemesh.graph.traverser import GraphTraverser
from codemesh.retrieval import reciprocal_rank_fusion
from codemesh.types import Node

logger = logging.getLogger(__name__)


def query_codebase(
    root: Path,
    query: str,
    limit: int = 10,
    fmt: str = "xml",
    alpha: float = 0.5,
) -> str:
    """Query the indexed codebase using hybrid retrieval.

    Always runs both structural (graph walk) and semantic (embedding) search,
    then fuses results via Reciprocal Rank Fusion (RRF).

    Args:
        root: Path to the indexed codebase.
        query: Natural language query string.
        limit: Maximum number of results to return.
        fmt: Output format — "xml" or "markdown".
        alpha: RRF weight for structural results (1-alpha for semantic).
    """
    db_path = get_db_path(root)
    init_db(db_path)

    with get_connection(db_path) as conn:
        # ── Structural retrieval: FTS5 + graph walk ──────────────────
        structural_results = _structural_search(conn, query, top_k=limit * 3)

        # ── Semantic retrieval: embedding similarity ─────────────────
        semantic_results = _semantic_search(conn, query, top_k=limit * 3)

        # ── Fuse via RRF ─────────────────────────────────────────────
        fused = reciprocal_rank_fusion(
            structural_results,
            semantic_results,
            alpha=alpha,
        )

        if not fused:
            return f"No results for: {query}"

        # ── Build context ────────────────────────────────────────────
        format_enum = ContextFormat.XML if fmt == "xml" else ContextFormat.MARKDOWN
        builder = ContextBuilder(conn, root)
        return builder.build(
            fused[:limit],
            query,
            ContextOptions(max_snippets=limit, format=format_enum),
        )


def get_context(root: Path, query: str, max_tokens: int = 8000) -> str:
    """Get context for a query.

    If the query matches a known symbol name, returns the symbol's
    dependency subgraph. Otherwise, falls back to hybrid search.
    """
    db_path = get_db_path(root)
    init_db(db_path)

    with get_connection(db_path) as conn:
        # Try exact symbol lookup first
        symbol_result = _try_symbol_lookup(conn, query)
        if symbol_result is not None:
            nodes_with_scores = symbol_result
        else:
            # Fall back to hybrid search
            structural = _structural_search(conn, query, top_k=20)
            semantic = _semantic_search(conn, query, top_k=20)
            nodes_with_scores = reciprocal_rank_fusion(structural, semantic)

        if not nodes_with_scores:
            return f"<code_context query=\"{query}\">\n  No results found.\n</code_context>"

        builder = ContextBuilder(conn, root)
        return builder.build(
            nodes_with_scores,
            f"Context for {query}",
            ContextOptions(max_tokens=max_tokens),
        )


# ── Internal helpers ──────────────────────────────────────────────────────


def _structural_search(
    conn, query: str, top_k: int = 30
) -> list[tuple[Node, float]]:
    """FTS5 search + graph walk expansion."""
    candidates = search_nodes_fts(conn, query, limit=top_k)
    if not candidates:
        return []

    traverser = GraphTraverser()
    results: list[tuple[Node, float]] = []
    seen: set[str] = set()

    for node, rank in candidates:
        if node.id in seen:
            continue
        seen.add(node.id)
        score = 1.0 / (1.0 + abs(rank))
        results.append((node, score))

        # Expand via graph walk
        subgraph = traverser.traverse(
            conn,
            [node.id],
            max_depth=2,
            max_nodes=20,
        )
        for nid, tr in subgraph.nodes.items():
            if nid not in seen:
                seen.add(nid)
                n = _get_node_safe(conn, nid)
                if n is not None:
                    results.append((n, tr.score))

    results.sort(key=lambda x: x[1], reverse=True)
    return results[:top_k]


def _semantic_search(
    conn, query: str, top_k: int = 30
) -> list[tuple[Node, float]]:
    """Embedding-based semantic similarity search.

    Returns empty list if no embeddings are indexed or if the embedding
    model fails to load.
    """
    # Check if any embeddings exist before trying to load the model
    try:
        row = conn.execute("SELECT COUNT(*) FROM embedding_index_meta").fetchone()
        if row is None or row[0] == 0:
            return []
    except sqlite3.OperationalError:
        return []

    try:
        model = EmbeddingModel()
        store = VectorStore(conn, model.dimensions)
        if store.count() == 0:
            return []
        query_emb = model.encode_single(query)
        hits = store.search(query_emb, top_k=top_k)
    except Exception as e:
        logger.warning("Semantic search failed (%s), returning empty", e)
        return []

    results: list[tuple[Node, float]] = []
    for node_id, distance in hits:
        node = _get_node_safe(conn, node_id)
        if node is not None:
            similarity = max(0.0, 1.0 - distance)
            results.append((node, similarity))

    return results


def _try_symbol_lookup(
    conn, query: str
) -> list[tuple[Node, float]] | None:
    """Try to find an exact symbol match. Returns None if not found."""
    nodes = get_all_nodes(conn)
    # Try qualified name first
    for n in nodes:
        if n.qualified_name == query:
            return [(n, 1.0)]
    # Try simple name
    for n in nodes:
        if n.name == query:
            return [(n, 0.9)]
    return None


def _get_node_safe(conn, node_id: str) -> Node | None:
    """Safely fetch a node by ID."""
    from codemesh.db.queries import get_node

    try:
        return get_node(conn, node_id)
    except Exception:
        return None
