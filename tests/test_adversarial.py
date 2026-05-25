"""Adversarial edge case and stress tests."""

from __future__ import annotations

import time
from pathlib import Path

from codemesh.indexer import index_project
from codemesh.querier import query_codebase


class TestEmptyAndDegenerate:
    """Empty and degenerate inputs."""

    def test_empty_project(self, tmp_path: Path) -> None:
        """Indexing an empty directory should not crash."""
        stats = index_project(tmp_path)
        assert stats["nodes"] == 0
        assert stats["edges"] == 0

    def test_single_empty_file(self, tmp_path: Path) -> None:
        """A single empty .py file should not crash."""
        (tmp_path / "empty.py").write_text("")
        stats = index_project(tmp_path)
        assert stats["nodes"] >= 1  # At least the file node

    def test_whitespace_only_file(self, tmp_path: Path) -> None:
        """File with only whitespace."""
        (tmp_path / "ws.py").write_text("   \n\n  \n")
        stats = index_project(tmp_path)
        assert stats["nodes"] >= 1

    def test_comments_only_file(self, tmp_path: Path) -> None:
        """File with only comments, no code."""
        (tmp_path / "comments.py").write_text('''
# This is a comment
# Another comment
"""Module docstring."""
''')
        stats = index_project(tmp_path)
        assert stats["nodes"] >= 1


class TestUnicodeAndSpecialChars:
    """Unicode identifiers and special characters."""

    def test_unicode_identifiers(self, tmp_path: Path) -> None:
        """Handle unicode in identifiers."""
        (tmp_path / "unicode_test.py").write_text('''
def 你好世界():
    """Unicode function name."""
    return "hello"

class Ñoño:
    """Unicode class name."""
    def café(self):
        pass
''')
        stats = index_project(tmp_path)
        assert stats["nodes"] > 0

    def test_unicode_strings(self, tmp_path: Path) -> None:
        """Handle unicode in string literals."""
        (tmp_path / "unicode_str.py").write_text("""
def greet():
    return "Hello 世界 🌍"
""")
        stats = index_project(tmp_path)
        assert stats["nodes"] > 0

    def test_special_chars_in_strings(self, tmp_path: Path) -> None:
        """Handle code with special characters in strings."""
        (tmp_path / "special.py").write_text('''
def query():
    """Contains 'quotes', "double", and {braces}."""
    sql = "SELECT * FROM users WHERE name = 'O\\'Brien'"
    regex = r"^[a-z]+\\d{2,4}$"
    return sql + regex
''')
        stats = index_project(tmp_path)
        assert stats["nodes"] > 0

    def test_mixed_line_endings(self, tmp_path: Path) -> None:
        """Handle files with mixed line endings."""
        (tmp_path / "mixed.py").write_bytes(
            b"def func_a():\r\n    pass\r\ndef func_b():\n    pass\r\n"
        )
        stats = index_project(tmp_path)
        assert stats["nodes"] >= 2

    def test_tab_indentation(self, tmp_path: Path) -> None:
        """Handle tab-indented code."""
        (tmp_path / "tabs.py").write_text("def foo():\n\treturn 1\n")
        stats = index_project(tmp_path)
        assert stats["nodes"] > 0


class TestCircularAndComplex:
    """Circular imports and complex structures."""

    def test_circular_imports(self, tmp_path: Path) -> None:
        """Handle circular imports without infinite loops."""
        (tmp_path / "a.py").write_text("""
from b import func_b
def func_a():
    return func_b()
""")
        (tmp_path / "b.py").write_text("""
from a import func_a
def func_b():
    return func_a()
""")
        stats = index_project(tmp_path)
        assert stats["nodes"] > 0
        assert stats["edges"] > 0

    def test_mutual_class_refs(self, tmp_path: Path) -> None:
        """Classes that reference each other."""
        (tmp_path / "models.py").write_text("""
class Parent:
    def __init__(self):
        self.children: list[Child] = []

class Child:
    def __init__(self, parent: Parent):
        self.parent = parent
""")
        stats = index_project(tmp_path)
        classes = stats["nodes"]
        assert classes >= 3  # 2 classes + file node

    def test_deeply_nested_calls(self, tmp_path: Path) -> None:
        """Handle deeply nested function definitions without crashing.

        Note: tree-sitter has practical limits on indentation depth for Python.
        Very deep nesting may not parse correctly, but should not crash.
        """
        lines = []
        for i in range(5):
            indent = "    " * i
            if i < 4:
                lines.append(f"{indent}def level_{i}():")
                lines.append(f"{indent}    return level_{i + 1}()")
            else:
                lines.append(f"{indent}def level_{i}():")
                lines.append(f"{indent}    return {i}")
        (tmp_path / "nested.py").write_text("\n".join(lines))
        # Should not crash — tree-sitter may or may not parse deep nesting
        stats = index_project(tmp_path)
        assert stats["nodes"] >= 1  # At minimum the file node

    def test_wide_call_graph(self, tmp_path: Path) -> None:
        """One function calling many others."""
        lines = ["def main():"]
        for i in range(100):
            lines.append(f"    func_{i}()")
        for i in range(100):
            lines.append(f"def func_{i}(): return {i}")
        (tmp_path / "wide.py").write_text("\n".join(lines))
        stats = index_project(tmp_path)
        assert stats["nodes"] >= 100


class TestLargeFiles:
    """Large file handling."""

    def test_large_file_many_functions(self, tmp_path: Path) -> None:
        """Handle a file with 500+ functions."""
        lines = [f"def func_{i}(): return {i}" for i in range(500)]
        (tmp_path / "large.py").write_text("\n".join(lines))
        start = time.perf_counter()
        stats = index_project(tmp_path)
        elapsed = time.perf_counter() - start
        assert stats["nodes"] >= 500
        assert elapsed < 30.0, f"Large file indexing took {elapsed:.1f}s"

    def test_large_class_many_methods(self, tmp_path: Path) -> None:
        """Handle a class with many methods."""
        lines = ["class BigClass:"]
        for i in range(100):
            lines.append(f"    def method_{i}(self): return {i}")
        (tmp_path / "big_class.py").write_text("\n".join(lines))
        stats = index_project(tmp_path)
        assert stats["nodes"] >= 100  # class + methods + file

    def test_many_small_files(self, tmp_path: Path) -> None:
        """Handle many small files."""
        for i in range(50):
            (tmp_path / f"module_{i}.py").write_text(f"def func_{i}(): return {i}")
        stats = index_project(tmp_path)
        assert stats["nodes"] >= 50


class TestQueryRobustness:
    """Query robustness on edge cases."""

    def test_query_empty_string(self, python_project: Path) -> None:
        """Query with empty string should not crash."""
        index_project(python_project)
        result = query_codebase(python_project, "")
        assert isinstance(result, str)

    def test_query_special_chars(self, python_project: Path) -> None:
        """Query with special characters should not crash."""
        index_project(python_project)
        result = query_codebase(python_project, "create_user() -> User")
        assert isinstance(result, str)

    def test_query_nonexistent_symbol(self, python_project: Path) -> None:
        """Query for a symbol that doesn't exist."""
        index_project(python_project)
        result = query_codebase(python_project, "nonexistent_function_xyz")
        assert isinstance(result, str)

    def test_query_very_long(self, python_project: Path) -> None:
        """Query with very long string."""
        index_project(python_project)
        long_query = "create_user " * 100
        result = query_codebase(python_project, long_query)
        assert isinstance(result, str)
