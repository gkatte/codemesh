# mypy: ignore-errors
"""Graph builder for CodeMesh visualization."""

from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import TYPE_CHECKING

from codemesh.db.connection import create_connection, get_db_path
from codemesh.db.queries import (
    get_all_edges,
    get_all_nodes,
)

if TYPE_CHECKING:
    pass


def build_graph(
    root: Path,
    kind_filter: list[str] | None = None,
    language_filter: list[str] | None = None,
    file_filter: list[str] | None = None,
    symbol_focus: str | None = None,
    depth: int = 3,
) -> dict:
    """Build a Cytoscape.js-compatible graph from the CodeMesh index.

    Args:
        root: Project root path.
        kind_filter: Optional list of NodeKind values to include.
        language_filter: Optional list of Language values to include.
        file_filter: Optional glob patterns for file paths.
        symbol_focus: Optional symbol name for BFS subgraph.
        depth: Max BFS depth when symbol_focus is set.

    Returns:
        Dict with 'nodes' and 'edges' lists in Cytoscape.js format.
    """
    db_path = get_db_path(root)
    conn = create_connection(db_path)

    try:
        all_nodes = get_all_nodes(conn)
        all_edges = get_all_edges(conn)

        # Apply filters
        nodes = _filter_nodes(all_nodes, kind_filter, language_filter, file_filter)

        if symbol_focus:
            nodes = _bfs_subgraph(conn, nodes, all_edges, symbol_focus, depth)

        node_ids = {n.id for n in nodes}
        edges = [e for e in all_edges if e.source_id in node_ids and e.target_id in node_ids]

        return {
            "nodes": [_node_to_cy(n) for n in nodes],
            "edges": [_edge_to_cy(e) for e in edges],
        }
    finally:
        conn.close()


def _filter_nodes(
    nodes: list,
    kind_filter: list[str] | None,
    language_filter: list[str] | None,
    file_filter: list[str] | None,
) -> list:
    """Apply filters to node list."""
    result = nodes
    if kind_filter:
        kf = set(kind_filter)
        result = [n for n in result if n.kind.value in kf]
    if language_filter:
        lf = set(language_filter)
        result = [n for n in result if n.language.value in lf]
    if file_filter:
        import fnmatch

        patterns = file_filter
        result = [n for n in result if any(fnmatch.fnmatch(str(n.file_path), p) for p in patterns)]
    return result


def _bfs_subgraph(
    conn,
    nodes: list,
    all_edges: list,
    symbol_focus: str,
    depth: int,
) -> list:
    """BFS from symbol_focus to build a subgraph."""
    # Find seed node(s) matching the symbol name
    seeds = [n for n in nodes if symbol_focus.lower() in n.name.lower()]
    if not seeds:
        return nodes

    node_map = {n.id: n for n in nodes}
    visited: set[str] = set()
    queue: deque[tuple[str, int]] = deque()

    for seed in seeds:
        visited.add(seed.id)
        queue.append((seed.id, 0))

    # Build adjacency maps
    outgoing: dict[str, list] = {}
    incoming: dict[str, list] = {}
    for e in all_edges:
        outgoing.setdefault(e.source_id, []).append(e)
        incoming.setdefault(e.target_id, []).append(e)

    while queue:
        node_id, d = queue.popleft()
        if d >= depth:
            continue

        for edge in outgoing.get(node_id, []):
            tid = edge.target_id
            if tid not in visited and tid in node_map:
                visited.add(tid)
                queue.append((tid, d + 1))

        for edge in incoming.get(node_id, []):
            sid = edge.source_id
            if sid not in visited and sid in node_map:
                visited.add(sid)
                queue.append((sid, d + 1))

    return [node_map[nid] for nid in visited if nid in node_map]


def _node_to_cy(node) -> dict:
    """Convert a Node to Cytoscape.js element format."""
    return {
        "data": {
            "id": node.id,
            "name": node.name,
            "kind": node.kind.value,
            "file_path": str(node.file_path),
            "start_line": node.start_line,
            "docstring": node.docstring or "",
            "signature": node.signature or "",
            "language": node.language.value,
            "qualified_name": node.qualified_name,
        }
    }


def _edge_to_cy(edge) -> dict:
    """Convert an Edge to Cytoscape.js element format."""
    return {
        "data": {
            "id": edge.id,
            "source": edge.source_id,
            "target": edge.target_id,
            "kind": edge.kind.value,
            "confidence": edge.confidence,
        }
    }
