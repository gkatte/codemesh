"""High-level graph query manager."""

from __future__ import annotations

import sqlite3

from codemesh.db.queries import get_all_nodes, get_node, search_nodes_fts
from codemesh.graph.traverser import GraphTraverser, Subgraph
from codemesh.types import EdgeKind, Node, SearchFilters


class QueryManager:
    """High-level queries over the knowledge graph."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self.traverser = GraphTraverser()

    def find_definition(self, symbol_name: str) -> Node | None:
        """Find the definition of a symbol by name."""
        nodes = get_all_nodes(self.conn)
        for n in nodes:
            if n.qualified_name == symbol_name:
                return n
        for n in nodes:
            if n.name == symbol_name:
                return n
        return None

    def find_callers(self, function_name: str) -> list[Node]:
        """Find all functions that call the given function."""
        target = self.find_definition(function_name)
        if target is None:
            return []
        results: list[Node] = []
        for edge in self.traverser._get_adjacent(self.conn, target.id, "backward"):
            if edge.kind == EdgeKind.CALLS:
                source = get_node(self.conn, edge.source_id)
                if source:
                    results.append(source)
        return results

    def find_callees(self, function_name: str) -> list[Node]:
        """Find all functions called by the given function."""
        target = self.find_definition(function_name)
        if target is None:
            return []
        results: list[Node] = []
        for edge in self.traverser._get_adjacent(self.conn, target.id, "forward"):
            if edge.kind == EdgeKind.CALLS:
                callee = get_node(self.conn, edge.target_id)
                if callee:
                    results.append(callee)
        return results

    def find_dependents(self, symbol_name: str, max_depth: int = 3) -> Subgraph:
        """Find all code that depends on a symbol."""
        target = self.find_definition(symbol_name)
        if target is None:
            return Subgraph(nodes={}, edges=[], root_ids=[])
        return self.traverser.traverse(
            self.conn,
            [target.id],
            max_depth=max_depth,
            direction="backward",
            edge_kinds=[EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.EXTENDS, EdgeKind.REFERENCES],
        )

    def find_dependencies(self, symbol_name: str, max_depth: int = 3) -> Subgraph:
        """Find all dependencies of a symbol."""
        target = self.find_definition(symbol_name)
        if target is None:
            return Subgraph(nodes={}, edges=[], root_ids=[])
        return self.traverser.traverse(
            self.conn,
            [target.id],
            max_depth=max_depth,
            direction="forward",
            edge_kinds=[EdgeKind.CALLS, EdgeKind.IMPORTS, EdgeKind.EXTENDS, EdgeKind.TYPE_OF],
        )

    def structural_search(
        self, query: str, filters: SearchFilters | None = None, max_depth: int = 3
    ) -> list[tuple[Node, float]]:
        """Search by symbol name and expand via graph.

        BM25 hits use their FTS score directly (higher = more relevant).
        Graph-walk neighbors are scaled to [0, 0.5) so they never outrank
        a BM25 hit. This prevents the traversal noise from burying the
        actual search result.
        """
        candidates = search_nodes_fts(self.conn, query, limit=10)
        if not candidates:
            return []

        results: list[tuple[Node, float]] = []
        seen: set[str] = set()

        for node, bm25_score in candidates:
            if node.id in seen:
                continue
            seen.add(node.id)
            results.append((node, bm25_score))

            subgraph = self.traverser.traverse(
                self.conn,
                [node.id],
                max_depth=max_depth,
                max_nodes=30,
            )
            for nid, tr in subgraph.nodes.items():
                if nid not in seen:
                    seen.add(nid)
                    n = get_node(self.conn, nid)
                    if n:
                        # Scale graph scores to [0, 0.5) so they never
                        # outrank a direct BM25 hit
                        results.append((n, tr.score * 0.5))

        results.sort(key=lambda x: x[1], reverse=True)
        return results

    def what_breaks_if_changed(self, symbol_name: str) -> Subgraph:
        """Impact analysis: what breaks if I change this symbol?"""
        return self.find_dependents(symbol_name, max_depth=5)
