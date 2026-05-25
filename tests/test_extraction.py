"""Tests for the tree-sitter extraction layer."""

from __future__ import annotations

from pathlib import Path

from codemesh.extraction.orchestrator import ExtractionOrchestrator, detect_language
from codemesh.types import EdgeKind, Language, NodeKind


class TestFileDiscovery:
    def test_discover_python_files(self, python_project: Path) -> None:
        ExtractionOrchestrator(python_project)  # just check it doesn't crash

    def test_detect_language(self) -> None:
        assert detect_language(Path("test.py")) == Language.PYTHON
        assert detect_language(Path("test.ts")) == Language.TYPESCRIPT
        assert detect_language(Path("test.rs")) == Language.RUST
        assert detect_language(Path("test.txt")) == Language.UNKNOWN


class TestPythonExtraction:
    def test_extract_functions(self, python_project: Path) -> None:
        orch = ExtractionOrchestrator(python_project)
        nodes, edges = orch.extract_all()
        names = [n.name for n in nodes]
        assert "create_user" in names
        assert "create_admin" in names

    def test_extract_classes(self, python_project: Path) -> None:
        orch = ExtractionOrchestrator(python_project)
        nodes, edges = orch.extract_all()
        classes = [n for n in nodes if n.kind == NodeKind.CLASS]
        names = [c.name for c in classes]
        assert "User" in names
        assert "Admin" in names

    def test_extract_methods(self, python_project: Path) -> None:
        orch = ExtractionOrchestrator(python_project)
        nodes, edges = orch.extract_all()
        methods = [n for n in nodes if n.kind == NodeKind.METHOD]
        names = [m.name for m in methods]
        assert "validate" in names

    def test_extract_calls(self, python_project: Path) -> None:
        orch = ExtractionOrchestrator(python_project)
        nodes, edges = orch.extract_all()
        call_edges = [e for e in edges if e.kind == EdgeKind.CALLS]
        assert len(call_edges) > 0


class TestTypeScriptExtraction:
    def test_extract_functions(self, typescript_project: Path) -> None:
        orch = ExtractionOrchestrator(typescript_project)
        nodes, edges = orch.extract_all()
        names = [n.name for n in nodes]
        assert "createUser" in names


class TestRustExtraction:
    def test_extract_structs(self, rust_project: Path) -> None:
        orch = ExtractionOrchestrator(rust_project)
        nodes, edges = orch.extract_all()
        structs = [n for n in nodes if n.kind == NodeKind.STRUCT]
        names = [s.name for s in structs]
        assert "User" in names
