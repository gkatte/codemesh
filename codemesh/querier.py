"""High-level query operations: hybrid retrieval with RRF fusion."""

from __future__ import annotations

import json
import logging
import socket
from pathlib import Path

from codemesh.context.builder import ContextBuilder, ContextFormat, ContextOptions
from codemesh.db.connection import get_connection, get_db_path
from codemesh.db.queries import get_all_nodes, search_nodes_fts
from codemesh.db.schema import init_db
from codemesh.embedding.model import CrossEncoderReranker, EmbeddingModel, VectorStore
from codemesh.graph.traverser import GraphTraverser
from codemesh.retrieval import reciprocal_rank_fusion
from codemesh.types import Node

logger = logging.getLogger(__name__)

_DAEMON_SOCKET = Path.home() / ".cache" / "codemesh" / "embed.sock"


def _daemon_request(endpoint: str, data: dict, timeout: float = 30.0) -> dict | None:
    """Send a request to the embedding daemon. Returns None if daemon unavailable."""
    if not _DAEMON_SOCKET.exists():
        return None
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        sock.connect(str(_DAEMON_SOCKET))
        body = json.dumps(data).encode()
        request = (
            f"POST {endpoint} HTTP/1.1\r\n"
            f"Host: localhost\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            f"Connection: close\r\n"
            f"\r\n"
        ).encode() + body
        sock.sendall(request)
        # Read response (daemon closes connection after response)
        response = b""
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            response += chunk
        sock.close()
        # Parse JSON from body
        header_end = response.index(b"\r\n\r\n")
        content_length = 0
        headers = response[:header_end].decode()
        for line in headers.split("\r\n"):
            if line.lower().startswith("content-length:"):
                content_length = int(line.split(":")[1].strip())
        body = response[header_end + 4 : header_end + 4 + content_length]
        return json.loads(body.decode())
    except Exception as e:
        logger.debug("Daemon request failed: %s", e)
        return None


def _daemon_encode(text: str) -> list[float] | None:
    """Encode a single text via the daemon. Returns None if daemon unavailable."""
    result = _daemon_request("/embed", {"texts": [text]})
    if result and "embeddings" in result and len(result["embeddings"]) > 0:
        return result["embeddings"][0]
    return None


def _daemon_rerank(
    query: str,
    documents: list[tuple[str, str]],
    threshold: float = 0.3,
    top_k: int | None = None,
) -> list[tuple[str, float]] | None:
    """Re-rank documents via the daemon. Returns None if daemon unavailable."""
    docs = [{"id": doc_id, "text": text} for doc_id, text in documents]
    result = _daemon_request(
        "/rerank",
        {"query": query, "documents": docs, "threshold": threshold, "top_k": top_k},
    )
    if result and "results" in result:
        return [(r[0], r[1]) for r in result["results"]]
    return None


def query_codebase(
    root: Path,
    query: str,
    limit: int = 10,
    fmt: str = "xml",
    alpha: float = 0.5,
    rerank: bool = True,
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
        rerank: Whether to apply cross-encoder re-ranking.
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

        # ── Cross-encoder re-ranking ─────────────────────────────────
        # Only re-rank if we have enough results and embeddings exist
        if rerank and semantic_results:
            fused = _rerank_results(conn, root, query, fused, limit=limit * 2)

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
            return f'<code_context query="{query}">\n  No results found.\n</code_context>'

        builder = ContextBuilder(conn, root)
        return builder.build(
            nodes_with_scores,
            f"Context for {query}",
            ContextOptions(max_tokens=max_tokens),
        )


# ── Internal helpers ──────────────────────────────────────────────────────


def _structural_search(conn, query: str, top_k: int = 30) -> list[tuple[Node, float]]:
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


def _semantic_search(conn, query: str, top_k: int = 30) -> list[tuple[Node, float]]:
    """Embedding-based semantic similarity search.

    Tries daemon first (fast, models already loaded), falls back to in-process.
    Returns empty list if no embeddings are indexed or if the embedding
    model fails to load.
    """
    # Check if any embeddings exist before trying to load the model
    row = conn.execute("SELECT total_vectors FROM embedding_index_meta LIMIT 1").fetchone()
    if row is None or row[0] == 0:
        return []

    try:
        # Try daemon first (fast path — models already loaded)
        query_emb = _daemon_encode(query)
        if query_emb is None:
            # Daemon not available, fall back to in-process
            model = EmbeddingModel()
            query_emb = model.encode_single(query)

        store = VectorStore(conn, len(query_emb))
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


def _try_symbol_lookup(conn, query: str) -> list[tuple[Node, float]] | None:
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


def _rerank_results(
    conn,
    root: Path,
    query: str,
    fused: list[tuple[Node, float]],
    limit: int = 20,
) -> list[tuple[Node, float]]:
    """Re-rank fused results using a cross-encoder.

    Takes the top candidates from RRF fusion, extracts their source text,
    and re-scores them with a cross-encoder model. Returns results sorted
    by re-ranker score (descending), limited to top N per file.
    """
    if not fused:
        return fused

    # Try daemon first (fast path — models already loaded)
    documents: list[tuple[str, str]] = []
    node_map: dict[str, Node] = {}
    # Only re-rank top N docs (more = exponentially slower on CPU)
    rerank_k = min(len(fused), 5)
    for node, _score in fused[:rerank_k]:
        text = _get_node_text(conn, root, node)
        documents.append((node.id, text))
        node_map[node.id] = node

    # Use daemon if available, otherwise fall back to in-process
    # Note: threshold=0.0 because ONNX model produces scores in [0, 0.1] range
    reranked = _daemon_rerank(query, documents, threshold=0.0, top_k=limit)
    if reranked is None:
        reranker = CrossEncoderReranker()
        reranked = reranker.rerank(query, documents, top_k=limit, threshold=0.0)

    results: list[tuple[Node, float]] = []
    for node_id, score in reranked:
        if node_id in node_map:
            results.append((node_map[node_id], score))

    # If re-ranking filtered everything, fall back to original
    if not results:
        return fused

    return results


def _get_node_text(conn, root: Path, node: Node) -> str:
    """Extract the source text for a node (truncated for re-ranking)."""
    parts: list[str] = []
    if node.signature:
        parts.append(node.signature)
    if node.docstring:
        parts.append(node.docstring)
    try:
        fp = root / node.file_path if not node.file_path.is_absolute() else node.file_path
        if fp.exists():
            source = fp.read_text(encoding="utf-8", errors="replace")
            lines = source.splitlines()
            start = max(0, node.start_line - 1)
            end = min(len(lines), node.end_line)
            # Cap at 30 lines for re-ranking (enough for key signal)
            code_lines = lines[start : min(end, start + 30)]
            parts.append("\n".join(code_lines))
    except Exception:
        pass
    text = "\n\n".join(parts) if parts else node.qualified_name
    # Truncate to 200 chars for re-ranking speed (longer = much slower on CPU)
    return text[:200]
