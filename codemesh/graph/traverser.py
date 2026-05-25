"""Weighted BFS/DFS graph traversal with configurable edge weights."""

from __future__ import annotations

import heapq
import sqlite3
from dataclasses import dataclass, field
from typing import Literal

from codemesh.db.queries import get_edges_by_source, get_edges_by_target, get_node
from codemesh.types import Edge, EdgeKind

DEFAULT_EDGE_WEIGHTS: dict[EdgeKind, float] = {
    EdgeKind.CONTAINS: 1.0,
    EdgeKind.CALLS: 0.9,
    EdgeKind.EXTENDS: 0.95,
    EdgeKind.IMPLEMENTS: 0.9,
    EdgeKind.INSTANTIATES: 0.8,
    EdgeKind.IMPORTS: 0.8,
    EdgeKind.OVERRIDES: 0.85,
    EdgeKind.TYPE_OF: 0.7,
    EdgeKind.RETURNS: 0.7,
    EdgeKind.REFERENCES: 0.5,
    EdgeKind.EXPORTS: 0.6,
    EdgeKind.DECORATES: 0.6,
}


@dataclass
class TraversalResult:
    """Result of a single traversal step."""

    node_id: str
    depth: int
    score: float
    path: list[str] = field(default_factory=list)


@dataclass
class Subgraph:
    """A subgraph discovered by traversal."""

    nodes: dict[str, TraversalResult]
    edges: list[Edge]
    root_ids: list[str]


class GraphTraverser:
    """Weighted graph traversal engine."""

    def traverse(
        self,
        conn: sqlite3.Connection,
        start_ids: list[str],
        max_depth: int = 3,
        max_nodes: int = 50,
        min_score: float = 0.01,
        direction: Literal["forward", "backward", "both"] = "forward",
        edge_kinds: list[EdgeKind] | None = None,
        edge_weights: dict[EdgeKind, float] | None = None,
    ) -> Subgraph:
        """Weighted BFS traversal from starting nodes."""
        weights = edge_weights or DEFAULT_EDGE_WEIGHTS
        nodes: dict[str, TraversalResult] = {}
        edges: list[Edge] = []
        heap: list[tuple[float, str, int, list[str]]] = []

        for sid in start_ids:
            if get_node(conn, sid) is not None:
                heapq.heappush(heap, (-1.0, sid, 0, []))
                nodes[sid] = TraversalResult(node_id=sid, depth=0, score=1.0)

        while heap and len(nodes) < max_nodes:
            neg_score, node_id, depth, path = heapq.heappop(heap)
            score = -neg_score

            if depth >= max_depth or score < min_score:
                continue

            adjacent = self._get_adjacent(conn, node_id, direction)
            for edge in adjacent:
                if edge_kinds and edge.kind not in edge_kinds:
                    continue
                weight = weights.get(edge.kind, 0.5)

                neighbor_id = edge.target_id if edge.source_id == node_id else edge.source_id
                neighbor = get_node(conn, neighbor_id)
                if neighbor is None or neighbor_id in nodes:
                    continue

                new_score = score * weight
                new_path = path + [edge.id]
                nodes[neighbor_id] = TraversalResult(
                    node_id=neighbor_id, depth=depth + 1, score=new_score, path=new_path
                )
                edges.append(edge)
                heapq.heappush(heap, (-new_score, neighbor_id, depth + 1, new_path))

        return Subgraph(nodes=nodes, edges=edges, root_ids=list(start_ids))

    def _get_adjacent(self, conn: sqlite3.Connection, node_id: str, direction: str) -> list[Edge]:
        edges: list[Edge] = []
        if direction in ("forward", "both"):
            edges.extend(get_edges_by_source(conn, node_id))
        if direction in ("backward", "both"):
            edges.extend(get_edges_by_target(conn, node_id))
        return edges
