"""Hybrid retrieval: classifier, graph walk, semantic, RRF fusion, re-ranker."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from codemesh.db.queries import get_node
from codemesh.graph.traverser import GraphTraverser
from codemesh.types import EdgeKind, Node, QueryType, SearchFilters

STRUCTURAL_VERBS = frozenset(
    {
        "call", "calls", "called", "calling",
        "depend", "depends", "depending",
        "import", "imports", "imported",
        "extend", "extends", "extending",
        "inherit", "inherits", "inheriting",
        "override", "overrides", "overriding",
        "reference", "references", "referencing",
        "use", "uses", "using",
        "implement", "implements", "implementing",
    }
)
DEFINITION_VERBS = frozenset(
    {"define", "defines", "definition", "where", "what is", "declare", "signature"}
)


@dataclass
class QueryClassification:
    query_type: QueryType
    structural_confidence: float
    semantic_confidence: float
    symbol: str | None = None
    description: str | None = None


class QueryClassifier:
    """Classifies queries to determine retrieval strategy."""

    SYMBOL_PATTERN = re.compile(
        r"\b([A-Z][a-zA-Z0-9]*(?:\.[A-Z][a-zA-Z0-9]*)+)\b"
        r"|(\b[a-z][a-z0-9]*(?:_[a-z][a-z0-9]*)+\b)"
        r"|(\b[a-z]+_[a-z]+\b)"
    )

    def classify(self, query: str) -> QueryClassification:
        query_lower = query.lower()
        words = set(query_lower.split())

        if words & DEFINITION_VERBS:
            symbol = self._extract_symbol(query)
            return QueryClassification(
                QueryType.DEFINITION, 0.9 if symbol else 0.6, 0.3, symbol, None
            )

        has_structural = bool(words & STRUCTURAL_VERBS)
        symbol = self._extract_symbol(query)

        if has_structural and symbol:
            return QueryClassification(QueryType.STRUCTURAL, 0.9, 0.2, symbol, None)
        if has_structural:
            return QueryClassification(QueryType.STRUCTURAL, 0.6, 0.4, None, query)
        if symbol:
            return QueryClassification(QueryType.HYBRID, 0.6, 0.6, symbol, query)
        return QueryClassification(QueryType.SEMANTIC, 0.2, 0.9, None, query)

    def _extract_symbol(self, query: str) -> str | None:
        matches = self.SYMBOL_PATTERN.findall(query)
        for match_group in matches:
            for m in match_group:
                if m and isinstance(m, str) and len(m) > 1:
                    return m
        return None


QUERY_TYPE_WEIGHTS: dict[QueryType, dict[EdgeKind, float]] = {
    QueryType.STRUCTURAL: {
        EdgeKind.CALLS: 1.17,
        EdgeKind.CONTAINS: 1.3,
        EdgeKind.EXTENDS: 1.235,
        EdgeKind.IMPORTS: 0.8,
        EdgeKind.REFERENCES: 0.5,
    },
    QueryType.SEMANTIC: {
        EdgeKind.CALLS: 0.9,
        EdgeKind.CONTAINS: 1.0,
        EdgeKind.EXTENDS: 0.95,
        EdgeKind.IMPORTS: 0.8,
        EdgeKind.REFERENCES: 0.6,
    },
}


class GraphWalkRetriever:
    """Retrieves relevant code by walking the knowledge graph."""

    def retrieve(
        self,
        conn: sqlite3.Connection,
        entry_points: list[str],
        query_type: QueryType = QueryType.HYBRID,
        max_depth: int = 3,
        max_nodes: int = 50,
    ) -> list[tuple[Node, float]]:
        weights = QUERY_TYPE_WEIGHTS.get(query_type)
        traverser = GraphTraverser()
        subgraph = traverser.traverse(
            conn, entry_points, max_depth=max_depth, max_nodes=max_nodes, edge_weights=weights
        )
        results = [
            (n, tr.score)
            for nid, tr in subgraph.nodes.items()
            if (n := get_node(conn, nid)) is not None
        ]
        results.sort(key=lambda x: x[1], reverse=True)
        return results


class SemanticRetriever:
    """Retrieves relevant code via vector similarity search."""

    def __init__(self, conn: sqlite3.Connection, model: Any | None = None) -> None:
        self.conn = conn
        from codemesh.embedding.model import EmbeddingModel, VectorStore

        self.model = model or EmbeddingModel()
        self.store = VectorStore(conn, self.model.dimensions)

    def retrieve(
        self, query: str, top_k: int = 20, filters: SearchFilters | None = None
    ) -> list[tuple[Node, float]]:
        query_embedding = self.model.encode_single(query)
        results = self.store.search(query_embedding, top_k=top_k * 2)
        nodes = []
        seen = set()
        for node_id, distance in results:
            node = get_node(self.conn, node_id)
            if node is None or node_id in seen:
                continue
            seen.add(node_id)
            if filters and filters.kinds and node.kind not in filters.kinds:
                continue
            if filters and filters.languages and node.language not in filters.languages:
                continue
            nodes.append((node, 1.0 - distance))
            if len(nodes) >= top_k:
                break
        return nodes


def reciprocal_rank_fusion(
    structural: list[tuple[Node, float]],
    semantic: list[tuple[Node, float]],
    alpha: float = 0.5,
    k: int = 60,
) -> list[tuple[Node, float]]:
    """Combine structural and semantic results via RRF.

    Args:
        structural: Results from graph walk, as (Node, score) pairs.
        semantic: Results from embedding similarity, as (Node, score) pairs.
        alpha: Weight for structural results (1-alpha for semantic).
        k: RRF constant (higher = less rank sensitivity).
    """
    scores: dict[str, tuple[Node, float]] = {}
    for rank, (node, _) in enumerate(structural):
        rrf = alpha * (1.0 / (k + rank + 1))
        scores[node.id] = (node, rrf)
    for rank, (node, _) in enumerate(semantic):
        rrf = (1.0 - alpha) * (1.0 / (k + rank + 1))
        if node.id in scores:
            existing_node, existing_score = scores[node.id]
            scores[node.id] = (existing_node, existing_score + rrf)
        else:
            scores[node.id] = (node, rrf)
    fused = sorted(scores.values(), key=lambda x: x[1], reverse=True)
    # Normalize scores to 0-1 range for display
    if fused:
        max_score = fused[0][1]
        if max_score > 0:
            fused = [(node, score / max_score) for node, score in fused]
    return fused


class CrossEncoderReranker:
    """Re-ranks candidates using cross-encoder. Falls back to RRF scores."""

    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3") -> None:
        self.model_name = model_name
        self._model: Any | None = None
        self._available = False

    def _load_model(self) -> None:
        if self._model is not None:
            return
        try:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(self.model_name)
            self._available = True
        except Exception:
            self._available = False

    def rerank(
        self, query: str, candidates: list[tuple[Node, float]], top_k: int = 10
    ) -> list[tuple[Node, float]]:
        if not candidates:
            return []
        self._load_model()
        if not self._available or self._model is None:
            return candidates[:top_k]
        pairs = [(query, self._get_snippet(node)) for node, _ in candidates]
        rerank_scores = self._model.predict(pairs)
        reranked = [
            (node, 0.6 * float(rerank_scores[i]) + 0.4 * rrf)
            for i, (node, rrf) in enumerate(candidates)
        ]
        reranked.sort(key=lambda x: x[1], reverse=True)
        return reranked[:top_k]

    def _get_snippet(self, node: Node, max_length: int = 512) -> str:
        parts = [
            p
            for p in [node.signature, node.docstring, f"({node.kind.value} {node.qualified_name})"]
            if p
        ]
        return " ".join(parts)[:max_length]
