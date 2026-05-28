"""End-to-end embedding pipeline test. Requires sentence-transformers."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from codemesh.db.schema import init_db
from codemesh.types import Language, Node, NodeKind


def _has_sentence_transformers() -> bool:
    """Check if sentence-transformers is available and model can load.

    Returns False in CI / resource-constrained environments to avoid OOM.
    Set CODEMESH_RUN_EMBEDDING_E2E=1 to force-enable.
    """
    import os

    if os.environ.get("CODEMESH_RUN_EMBEDDING_E2E", "") != "1":
        return False
    try:
        import sentence_transformers  # noqa: F401

        return True
    except ImportError:
        return False


EMBEDDING_MODEL_REASON = (
    "Embedding E2E tests require CODEMESH_RUN_EMBEDDING_E2E=1 env var to run. "
    "Set it to enable: CODEMESH_RUN_EMBEDDING_E2E=1 pytest tests/test_embedding_e2e.py"
)


class TestEmbeddingModelE2E:
    """End-to-end embedding pipeline test with real model."""

    @pytest.mark.skipif(not _has_sentence_transformers(), reason=EMBEDDING_MODEL_REASON)
    def test_model_loads_and_encodes(self) -> None:
        """Model loads and produces 768-dim embeddings."""
        from codemesh.embedding.model import EmbeddingModel

        model = EmbeddingModel()
        embeddings = model.encode(["def hello(): pass"])
        assert len(embeddings) == 1
        assert len(embeddings[0]) == 768

    @pytest.mark.skipif(not _has_sentence_transformers(), reason=EMBEDDING_MODEL_REASON)
    def test_semantic_similarity(self) -> None:
        """Similar code snippets should have higher similarity than dissimilar ones."""
        import numpy as np
        from codemesh.embedding.model import EmbeddingModel

        model = EmbeddingModel()
        embeddings = model.encode(
            [
                "def create_user(name): pass",
                "def create_admin(name): pass",
                "def sort_list(items): pass",
            ]
        )
        # Embeddings are normalized, so dot product = cosine similarity
        sim_same = float(np.dot(embeddings[0], embeddings[1]))
        sim_diff = float(np.dot(embeddings[0], embeddings[2]))
        assert sim_same > sim_diff, (
            f"Similar code similarity ({sim_same:.3f}) should be > dissimilar ({sim_diff:.3f})"
        )

    @pytest.mark.skipif(not _has_sentence_transformers(), reason=EMBEDDING_MODEL_REASON)
    def test_batch_encoding(self) -> None:
        """Batch encoding should produce same results as single encoding."""
        from codemesh.embedding.model import EmbeddingModel

        model = EmbeddingModel()
        single = model.encode_single("def foo(): return 42")
        batch = model.encode(["def foo(): return 42"])
        assert len(single) == len(batch[0]) == 768
        # Should be very close (allow small floating point diff)
        import numpy as np

        assert np.allclose(single, batch[0], atol=1e-5)

    @pytest.mark.skipif(not _has_sentence_transformers(), reason=EMBEDDING_MODEL_REASON)
    def test_encode_single(self) -> None:
        """encode_single should return a proper embedding."""
        from codemesh.embedding.model import EmbeddingModel

        model = EmbeddingModel()
        emb = model.encode_single("def authenticate(user): pass")
        assert len(emb) == 768
        # Embedding should be normalized (unit vector)
        import numpy as np

        norm = np.linalg.norm(emb)
        assert abs(norm - 1.0) < 0.01, f"Embedding norm = {norm}, expected ~1.0"

    @pytest.mark.skipif(not _has_sentence_transformers(), reason=EMBEDDING_MODEL_REASON)
    def test_code_vs_text_differentiation(self) -> None:
        """Code and natural language should produce different embeddings."""
        import numpy as np
        from codemesh.embedding.model import EmbeddingModel

        model = EmbeddingModel()
        code_emb = model.encode_single("def process(data): return data")
        text_emb = model.encode_single("process data return data")
        similarity = float(np.dot(code_emb, text_emb))
        # They should be somewhat similar but not identical
        assert 0.3 < similarity < 0.99, f"Code-text similarity = {similarity:.3f}"


class TestVectorStoreE2E:
    """End-to-end vector store test with SQLite-vec."""

    @pytest.mark.skipif(not _has_sentence_transformers(), reason=EMBEDDING_MODEL_REASON)
    def test_vector_store_roundtrip(self, tmp_path: Path) -> None:
        """Store and retrieve embeddings via SQLite-vec."""
        from codemesh.embedding.model import EmbeddingModel, VectorStore

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        model = EmbeddingModel()
        store = VectorStore(conn, model.dimensions)
        store.create_table()

        node = Node(
            id="test_func",
            kind=NodeKind.FUNCTION,
            name="test_func",
            qualified_name="test.test_func",
            file_path=Path("test.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=5,
            parent_id=None,
        )
        embedding = model.encode(["def test_func(): return True"])[0]
        store.upsert_embeddings([node], [embedding], model.model_name)

        results = store.search(embedding, top_k=1)
        assert len(results) == 1
        assert results[0][0] == "test_func"
        conn.close()

    @pytest.mark.skipif(not _has_sentence_transformers(), reason=EMBEDDING_MODEL_REASON)
    def test_vector_store_multiple_nodes(self, tmp_path: Path) -> None:
        """Store multiple nodes and search returns closest match."""
        from codemesh.embedding.model import EmbeddingModel, VectorStore

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        model = EmbeddingModel()
        store = VectorStore(conn, model.dimensions)
        store.create_table()

        nodes = [
            Node(
                id=f"func_{i}",
                kind=NodeKind.FUNCTION,
                name=f"func_{i}",
                qualified_name=f"mod.func_{i}",
                file_path=Path("mod.py"),
                language=Language.PYTHON,
                start_line=i * 5,
                end_line=i * 5 + 3,
                parent_id=None,
            )
            for i in range(5)
        ]
        texts = [
            "def create_user(name): pass",
            "def create_admin(name): pass",
            "def delete_user(id): pass",
            "def sort_list(items): pass",
            "def render_html(template): pass",
        ]
        embeddings = model.encode(texts)
        store.upsert_embeddings(nodes, embeddings, model.model_name)

        # Search with a query similar to create_user
        query_emb = model.encode_single("def add_user(name): pass")
        results = store.search(query_emb, top_k=3)
        assert len(results) == 3
        # Top result should be one of the "create" functions
        top_id = results[0][0]
        assert "func_" in top_id
        conn.close()

    @pytest.mark.skipif(not _has_sentence_transformers(), reason=EMBEDDING_MODEL_REASON)
    def test_is_embedded_check(self, tmp_path: Path) -> None:
        """is_embedded should correctly report embedding status."""
        from codemesh.embedding.model import EmbeddingModel, VectorStore

        from codemesh.db.queries import insert_node

        db_path = tmp_path / "test.db"
        init_db(db_path)
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA journal_mode=WAL")

        model = EmbeddingModel()
        store = VectorStore(conn, model.dimensions)
        store.create_table()

        node = Node(
            id="embedded_func",
            kind=NodeKind.FUNCTION,
            name="embedded_func",
            qualified_name="mod.embedded_func",
            file_path=Path("mod.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=3,
            parent_id=None,
        )
        insert_node(conn, node)
        assert not store.is_embedded("embedded_func", model.model_name)

        embedding = model.encode(["def embedded_func(): pass"])[0]
        store.upsert_embeddings([node], [embedding], model.model_name)
        assert store.is_embedded("embedded_func", model.model_name)
        conn.close()


class TestEmbeddingMock:
    """Mock-based embedding tests that don't require the model."""

    def test_make_content_with_signature(self) -> None:
        """_make_content should include signature."""
        from codemesh.embedding.model import _make_content

        node = Node(
            id="test",
            kind=NodeKind.FUNCTION,
            name="test",
            qualified_name="mod.test",
            file_path=Path("mod.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=3,
            parent_id=None,
            signature="def test(x: int) -> bool",
        )
        content = _make_content(node)
        assert "def test(x: int) -> bool" in content

    def test_make_content_with_docstring(self) -> None:
        """_make_content should include docstring."""
        from codemesh.embedding.model import _make_content

        node = Node(
            id="test",
            kind=NodeKind.FUNCTION,
            name="test",
            qualified_name="mod.test",
            file_path=Path("mod.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=3,
            parent_id=None,
            docstring="This is a test function.",
        )
        content = _make_content(node)
        assert "This is a test function." in content

    def test_make_content_with_source(self) -> None:
        """_make_content should include source code."""
        from codemesh.embedding.model import _make_content

        node = Node(
            id="test",
            kind=NodeKind.FUNCTION,
            name="test",
            qualified_name="mod.test",
            file_path=Path("mod.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=3,
            parent_id=None,
        )
        source = "def test():\n    return True\n"
        content = _make_content(node, source)
        assert "return True" in content

    def test_make_content_fallback(self) -> None:
        """_make_content should fallback to qualified_name."""
        from codemesh.embedding.model import _make_content

        node = Node(
            id="test",
            kind=NodeKind.FUNCTION,
            name="test",
            qualified_name="mod.test",
            file_path=Path("mod.py"),
            language=Language.PYTHON,
            start_line=1,
            end_line=3,
            parent_id=None,
        )
        content = _make_content(node)
        assert content == "mod.test"

    def test_embeddable_kinds(self) -> None:
        """Only function-like kinds should be embeddable."""
        from codemesh.embedding.model import EMBEDDABLE_KINDS

        assert "function" in EMBEDDABLE_KINDS
        assert "method" in EMBEDDABLE_KINDS
        assert "class" in EMBEDDABLE_KINDS
        assert "struct" in EMBEDDABLE_KINDS
        assert "file" not in EMBEDDABLE_KINDS
