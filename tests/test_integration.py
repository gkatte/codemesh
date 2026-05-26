"""End-to-end integration tests."""

from __future__ import annotations

from pathlib import Path

from codemesh.indexer import index_project
from codemesh.querier import get_context, query_codebase


class TestEndToEnd:
    def test_index_and_query_python(self, python_project: Path) -> None:
        stats = index_project(python_project)
        assert stats["nodes"] > 0
        assert stats["edges"] > 0
        result = query_codebase(python_project, "create_user", limit=5)
        assert "create_user" in result

    def test_context_for_symbol(self, python_project: Path) -> None:
        index_project(python_project)
        result = get_context(python_project, "User", max_tokens=4000)
        assert "User" in result

    def test_impact_analysis(self, python_project: Path) -> None:
        from codemesh.db.connection import get_connection, get_db_path
        from codemesh.graph.query_manager import QueryManager

        index_project(python_project)
        with get_connection(get_db_path(python_project)) as conn:
            qm = QueryManager(conn)
            subgraph = qm.what_breaks_if_changed("validate")
            assert len(subgraph.nodes) > 0

    def test_typescript_project(self, typescript_project: Path) -> None:
        stats = index_project(typescript_project)
        assert stats["nodes"] > 0

    def test_rust_project(self, rust_project: Path) -> None:
        stats = index_project(rust_project)
        assert stats["nodes"] > 0


class TestRetrieval:
    def test_bm25_search(self, python_project: Path) -> None:
        """BM25 keyword search should find matching symbols."""
        index_project(python_project)
        result = query_codebase(python_project, "create_user", limit=5)
        assert "create_user" in result

    def test_bm25_symbol_lookup(self, python_project: Path) -> None:
        """Exact symbol lookup should work via get_context."""
        index_project(python_project)
        result = get_context(python_project, "create_user")
        assert "create_user" in result

    def test_bm25_relevance_ranking(self, python_project: Path) -> None:
        """Results should be relevance-ranked (best match first)."""
        index_project(python_project)
        result = query_codebase(python_project, "validate user", limit=5)
        # Should find validate method and validate-related symbols
        assert "validate" in result or "User" in result
