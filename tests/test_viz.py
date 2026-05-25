"""Tests for CodeMesh visualization (V1-V4)."""

from __future__ import annotations

from pathlib import Path

import pytest

from codemesh.db.connection import create_connection
from codemesh.db.schema import init_db
from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind


@pytest.fixture
def test_db(tmp_path):
    """Create a test database with sample nodes and edges."""
    db_path = tmp_path / ".codemesh" / "index.db"
    db_path.parent.mkdir(parents=True)
    init_db(db_path)

    conn = create_connection(db_path)

    # Create test nodes
    nodes = [
        Node(
            id="n1",
            kind=NodeKind.FUNCTION,
            name="authenticate",
            qualified_name="auth.authenticate",
            file_path=Path("auth/handler.py"),
            language=Language.PYTHON,
            start_line=10,
            end_line=25,
            signature="def authenticate(user: str) -> bool",
            docstring="Authenticate a user",
        ),
        Node(
            id="n2",
            kind=NodeKind.FUNCTION,
            name="get_user",
            qualified_name="auth.get_user",
            file_path=Path("auth/handler.py"),
            language=Language.PYTHON,
            start_line=30,
            end_line=45,
            signature="def get_user(user_id: str) -> User",
        ),
        Node(
            id="n3",
            kind=NodeKind.CLASS,
            name="User",
            qualified_name="models.User",
            file_path=Path("models/user.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=50,
            docstring="User model class",
        ),
        Node(
            id="n4",
            kind=NodeKind.METHOD,
            name="save",
            qualified_name="models.User.save",
            file_path=Path("models/user.py"),
            language=Language.PYTHON,
            start_line=20,
            end_line=30,
        ),
        Node(
            id="n5",
            kind=NodeKind.MODULE,
            name="handler",
            qualified_name="auth.handler",
            file_path=Path("auth/handler.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=100,
        ),
    ]

    for node in nodes:
        # Insert with embedding BLOB for some nodes
        emb = None
        if node.kind in (NodeKind.FUNCTION, NodeKind.CLASS):
            import numpy as np

            emb = np.random.randn(768).astype(np.float32).tobytes()

        conn.execute(
            """
            INSERT INTO nodes (id, kind, name, qualified_name, file_path, language,
                start_line, end_line, docstring, signature, embedding, embedding_model)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                node.id,
                node.kind.value,
                node.name,
                node.qualified_name,
                str(node.file_path),
                node.language.value,
                node.start_line,
                node.end_line,
                node.docstring,
                node.signature,
                emb,
                "test" if emb else "none",
            ),
        )

    # Create test edges
    edges = [
        Edge(id="e1", source_id="n5", target_id="n1", kind=EdgeKind.CONTAINS),
        Edge(id="e2", source_id="n5", target_id="n2", kind=EdgeKind.CONTAINS),
        Edge(id="e3", source_id="n1", target_id="n2", kind=EdgeKind.CALLS),
        Edge(id="e4", source_id="n3", target_id="n4", kind=EdgeKind.CONTAINS),
    ]

    for edge in edges:
        conn.execute(
            "INSERT INTO edges (id, source_id, target_id, kind) VALUES (?, ?, ?, ?)",
            (edge.id, edge.source_id, edge.target_id, edge.kind.value),
        )

    conn.commit()
    conn.close()

    return tmp_path


class TestGraphBuilder:
    """Tests for V1: graph_builder.py"""

    def test_build_graph_all(self, test_db):
        from codemesh.viz.graph_builder import build_graph

        result = build_graph(test_db)
        assert "nodes" in result
        assert "edges" in result
        assert len(result["nodes"]) == 5
        assert len(result["edges"]) == 4

    def test_build_graph_kind_filter(self, test_db):
        from codemesh.viz.graph_builder import build_graph

        result = build_graph(test_db, kind_filter=["function"])
        assert len(result["nodes"]) == 2
        names = [n["data"]["name"] for n in result["nodes"]]
        assert "authenticate" in names
        assert "get_user" in names

    def test_build_graph_language_filter(self, test_db):
        from codemesh.viz.graph_builder import build_graph

        result = build_graph(test_db, language_filter=["python"])
        assert len(result["nodes"]) == 5  # All are python

    def test_build_graph_symbol_focus(self, test_db):
        from codemesh.viz.graph_builder import build_graph

        result = build_graph(test_db, symbol_focus="authenticate", depth=2)
        node_ids = [n["data"]["id"] for n in result["nodes"]]
        assert "n1" in node_ids  # authenticate itself
        assert "n2" in node_ids  # get_user (called by authenticate)
        assert "n5" in node_ids  # handler (contains authenticate)

    def test_build_graph_nested_focus(self, test_db):
        from codemesh.viz.graph_builder import build_graph

        result = build_graph(test_db, symbol_focus="User", depth=1)
        node_ids = [n["data"]["id"] for n in result["nodes"]]
        assert "n3" in node_ids  # User class

    def test_node_format(self, test_db):
        from codemesh.viz.graph_builder import build_graph

        result = build_graph(test_db)
        node = result["nodes"][0]
        assert "data" in node
        assert "id" in node["data"]
        assert "name" in node["data"]
        assert "kind" in node["data"]
        assert "file_path" in node["data"]
        assert "qualified_name" in node["data"]

    def test_edge_format(self, test_db):
        from codemesh.viz.graph_builder import build_graph

        result = build_graph(test_db)
        edge = result["edges"][0]
        assert "data" in edge
        assert "source" in edge["data"]
        assert "target" in edge["data"]
        assert "kind" in edge["data"]

    def test_empty_filter_returns_all(self, test_db):
        from codemesh.viz.graph_builder import build_graph

        result = build_graph(test_db, kind_filter=[])
        assert len(result["nodes"]) == 5

    def test_no_matching_filter(self, test_db):
        from codemesh.viz.graph_builder import build_graph

        result = build_graph(test_db, kind_filter=["decorator"])
        assert len(result["nodes"]) == 0
        assert len(result["edges"]) == 0


class TestServer:
    """Tests for V1: server.py API endpoints"""

    def test_create_app(self, test_db):
        from codemesh.viz.server import create_app

        app = create_app(test_db)
        assert app is not None

    def test_stats_endpoint(self, test_db):
        from starlette.testclient import TestClient

        from codemesh.viz.server import create_app

        app = create_app(test_db)
        client = TestClient(app)
        response = client.get("/api/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_nodes"] == 5
        assert data["total_edges"] == 4

    def test_graph_endpoint(self, test_db):
        from starlette.testclient import TestClient

        from codemesh.viz.server import create_app

        app = create_app(test_db)
        client = TestClient(app)
        response = client.get("/api/graph")
        assert response.status_code == 200
        data = response.json()
        assert len(data["nodes"]) == 5
        assert len(data["edges"]) == 4

    def test_graph_filter_endpoint(self, test_db):
        from starlette.testclient import TestClient

        from codemesh.viz.server import create_app

        app = create_app(test_db)
        client = TestClient(app)
        response = client.get("/api/graph?kind=function")
        data = response.json()
        assert len(data["nodes"]) == 2

    def test_node_detail_endpoint(self, test_db):
        from starlette.testclient import TestClient

        from codemesh.viz.server import create_app

        app = create_app(test_db)
        client = TestClient(app)
        response = client.get("/api/node/n1")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "authenticate"
        assert data["kind"] == "function"

    def test_node_not_found(self, test_db):
        from starlette.testclient import TestClient

        from codemesh.viz.server import create_app

        app = create_app(test_db)
        client = TestClient(app)
        response = client.get("/api/node/nonexistent")
        assert response.status_code == 404

    def test_search_endpoint(self, test_db):
        from starlette.testclient import TestClient

        from codemesh.viz.server import create_app

        app = create_app(test_db)
        client = TestClient(app)
        response = client.get("/api/search?q=authenticate")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert data[0]["name"] == "authenticate"

    def test_embedding_stats_endpoint(self, test_db):
        from starlette.testclient import TestClient

        from codemesh.viz.server import create_app

        app = create_app(test_db)
        client = TestClient(app)
        response = client.get("/api/embedding-stats")
        assert response.status_code == 200
        data = response.json()
        assert data["total_nodes"] == 5
        assert data["embedded_nodes"] == 3  # 2 functions + 1 class

    def test_index_html(self, test_db):
        from starlette.testclient import TestClient

        from codemesh.viz.server import create_app

        app = create_app(test_db)
        client = TestClient(app)
        response = client.get("/")
        assert response.status_code == 200
        assert "cytoscape" in response.text.lower()
        assert "codemesh" in response.text.lower()


class TestEmbeddingProjector:
    """Tests for V2: embedding_projector.py"""

    def test_project_embeddings_2d(self, test_db):
        pytest.importorskip("umap")
        from codemesh.viz.embedding_projector import project_embeddings

        points = project_embeddings(test_db, n_components=2)
        assert len(points) == 3  # 3 nodes have embeddings
        for p in points:
            assert "id" in p
            assert "name" in p
            assert "x" in p
            assert "y" in p
            assert "kind" in p
            assert "file_path" in p
            assert "degree" in p
            assert isinstance(p["x"], float)
            assert isinstance(p["y"], float)

    def test_project_embeddings_3d(self, test_db):
        pytest.importorskip("umap")
        from codemesh.viz.embedding_projector import project_embeddings

        points = project_embeddings(test_db, n_components=3)
        assert len(points) == 3  # 3 nodes have embeddings
        for p in points:
            assert "z" in p
            assert isinstance(p["z"], float)

    def test_embedding_stats(self, test_db):
        from codemesh.viz.embedding_projector import get_embedding_stats

        stats = get_embedding_stats(test_db)
        assert stats["total_nodes"] == 5
        assert stats["embedded_nodes"] == 3  # 2 functions + 1 class have embeddings
        assert stats["model"] == "test"

    def test_no_embeddings(self, tmp_path):
        db_path = tmp_path / ".codemesh" / "index.db"
        db_path.parent.mkdir(parents=True)
        init_db(db_path)

        conn = create_connection(db_path)
        conn.execute(
            """INSERT INTO nodes (id, kind, name, qualified_name, file_path, language, start_line, end_line, embedding_model)
               VALUES ('x1', 'function', 'foo', 'foo', 'foo.py', 'python', 1, 10, 'none')"""
        )
        conn.commit()
        conn.close()

        from codemesh.viz.embedding_projector import get_embedding_stats, project_embeddings

        points = project_embeddings(tmp_path)
        assert len(points) == 0

        stats = get_embedding_stats(tmp_path)
        assert stats["embedded_nodes"] == 0
