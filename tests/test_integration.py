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
    def test_rrf_fusion(self) -> None:
        from codemesh.retrieval import reciprocal_rank_fusion
        from codemesh.types import Language, Node, NodeKind

        n1 = Node(
            id="a",
            kind=NodeKind.FUNCTION,
            name="a",
            qualified_name="a",
            file_path=Path("a.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=5,
            parent_id=None,
        )
        n2 = Node(
            id="b",
            kind=NodeKind.FUNCTION,
            name="b",
            qualified_name="b",
            file_path=Path("b.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=5,
            parent_id=None,
        )
        n3 = Node(
            id="c",
            kind=NodeKind.FUNCTION,
            name="c",
            qualified_name="c",
            file_path=Path("c.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=5,
            parent_id=None,
        )
        fused = reciprocal_rank_fusion([(n1, 0.9), (n2, 0.5)], [(n2, 0.8), (n3, 0.6)])
        assert len(fused) == 3
        assert fused[0][0].id == "b"

    def test_query_classifier(self) -> None:
        from codemesh.retrieval import QueryClassifier, QueryType

        c = QueryClassifier()
        r = c.classify("which functions call validate_user?")
        assert r.query_type == QueryType.STRUCTURAL
        assert r.symbol == "validate_user"
        r2 = c.classify("authentication middleware")
        assert r2.query_type == QueryType.SEMANTIC
