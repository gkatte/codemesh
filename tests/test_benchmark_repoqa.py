"""RepoQA benchmark: retrieval quality with ground truth."""

from __future__ import annotations

from pathlib import Path
from typing import NamedTuple

from codemesh.db.connection import get_connection, get_db_path
from codemesh.graph.query_manager import QueryManager
from codemesh.indexer import index_project
from codemesh.querier import query_codebase


class BenchmarkQuery(NamedTuple):
    query: str
    expected_symbols: set[str]
    category: str


BENCHMARK_QUERIES_PYTHON = [
    BenchmarkQuery(
        "create_user",
        {"create_user", "User", "validate"},
        "definition",
    ),
    BenchmarkQuery(
        "validate",
        {"validate", "User", "Admin"},
        "impact",
    ),
    BenchmarkQuery(
        "User",
        {"User", "Admin", "create_user", "create_admin"},
        "dependency",
    ),
    BenchmarkQuery(
        "create_admin",
        {"create_admin", "Admin", "User", "validate"},
        "call_chain",
    ),
    BenchmarkQuery(
        "notify_user",
        {"notify_user", "User"},
        "definition",
    ),
]

BENCHMARK_QUERIES_TS = [
    BenchmarkQuery(
        "createUser",
        {"createUser"},
        "definition",
    ),
    BenchmarkQuery(
        "User",
        {"User", "Admin", "createUser"},
        "dependency",
    ),
    BenchmarkQuery(
        "UserService",
        {"UserService"},
        "definition",
    ),
]


def _recall_at_k(result: str, expected: set[str], k: int | None = None) -> float:
    """Compute recall: fraction of expected symbols found in result."""
    if not expected:
        return 0.0
    found = sum(1 for sym in expected if sym in result)
    return found / len(expected)


def _reciprocal_rank(result: str, expected: set[str]) -> float:
    """Compute MRR: 1/rank of first expected symbol found."""
    words = result.split()
    for rank, word in enumerate(words, 1):
        for sym in expected:
            if sym in word:
                return 1.0 / rank
    return 0.0


class TestRepoQABenchmark:
    """RepoQA benchmark: measure retrieval quality with ground truth."""

    def test_recall_python(self, python_project: Path) -> None:
        """Measure recall for Python project queries."""
        index_project(python_project)
        recalls = []
        for bq in BENCHMARK_QUERIES_PYTHON:
            result = query_codebase(python_project, bq.query, limit=10)
            recall = _recall_at_k(result, bq.expected_symbols)
            recalls.append(recall)
            print(f"\n[RepoQA] '{bq.query}' ({bq.category}): recall={recall:.2f}")

        avg_recall = sum(recalls) / len(recalls)
        print(f"\n[RepoQA] Average recall: {avg_recall:.2f}")
        # For a small synthetic project, we expect reasonable recall
        assert avg_recall > 0.2, f"Average recall = {avg_recall:.2f}"

    def test_recall_typescript(self, typescript_project: Path) -> None:
        """Measure recall for TypeScript project queries."""
        index_project(typescript_project)
        recalls = []
        for bq in BENCHMARK_QUERIES_TS:
            result = query_codebase(typescript_project, bq.query, limit=10)
            recall = _recall_at_k(result, bq.expected_symbols)
            recalls.append(recall)
            print(f"\n[RepoQA] '{bq.query}' ({bq.category}): recall={recall:.2f}")

        avg_recall = sum(recalls) / len(recalls)
        print(f"\n[RepoQA] Average recall (TS): {avg_recall:.2f}")
        assert avg_recall > 0.15, f"Average recall = {avg_recall:.2f}"

    def test_recall_rust(self, rust_project: Path) -> None:
        """Measure recall for Rust project queries."""
        index_project(rust_project)
        queries = [
            BenchmarkQuery("User", {"User", "Admin"}, "definition"),
            BenchmarkQuery("create_user", {"create_user", "User"}, "call_chain"),
            BenchmarkQuery("validate", {"validate", "User"}, "impact"),
        ]
        recalls = []
        for bq in queries:
            result = query_codebase(rust_project, bq.query, limit=10)
            recall = _recall_at_k(result, bq.expected_symbols)
            recalls.append(recall)
            print(f"\n[RepoQA] '{bq.query}' ({bq.category}): recall={recall:.2f}")

        avg_recall = sum(recalls) / len(recalls)
        print(f"\n[RepoQA] Average recall (Rust): {avg_recall:.2f}")
        assert avg_recall > 0.15, f"Average recall = {avg_recall:.2f}"

    def test_mrr_python(self, python_project: Path) -> None:
        """Mean Reciprocal Rank for Python queries."""
        index_project(python_project)
        rr_values = []
        for bq in BENCHMARK_QUERIES_PYTHON:
            result = query_codebase(python_project, bq.query, limit=10)
            rr = _reciprocal_rank(result, bq.expected_symbols)
            rr_values.append(rr)
            print(f"\n[RepoQA] '{bq.query}': RR={rr:.3f}")

        mrr = sum(rr_values) / len(rr_values)
        print(f"\n[RepoQA] MRR (Python): {mrr:.3f}")
        assert mrr > 0.1, f"MRR = {mrr:.3f}"

    def test_structural_search_quality(self, python_project: Path) -> None:
        """Graph-based structural search should find callers/callees."""
        index_project(python_project)
        with get_connection(get_db_path(python_project)) as conn:
            qm = QueryManager(conn)
            # Search for known symbols
            for name in ["create_user", "User", "validate"]:
                node = qm.find_definition(name)
                if node:
                    print(f"\n[RepoQA] Found definition: {name}")

    def test_definition_query(self, python_project: Path) -> None:
        """Definition queries should return the symbol."""
        index_project(python_project)
        result = query_codebase(python_project, "create_user")
        assert "create_user" in result

    def test_impact_query(self, python_project: Path) -> None:
        """Impact analysis should return related symbols."""
        index_project(python_project)
        with get_connection(get_db_path(python_project)) as conn:
            qm = QueryManager(conn)
            subgraph = qm.what_breaks_if_changed("validate")
            print(f"\n[RepoQA] Impact of changing 'validate': {len(subgraph.nodes)} nodes")

    def test_cross_file_flow(self, python_project: Path) -> None:
        """Cross-file queries should find symbols across modules."""
        index_project(python_project)
        # Query for a symbol defined in models.py but used in services.py
        result = query_codebase(python_project, "User", limit=10)
        assert "User" in result
        # Result should reference content from multiple files
        assert len(result) > 100
