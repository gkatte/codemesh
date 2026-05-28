"""C/C++ tree-sitter extractor.

Handles both C and C++ since they share a similar tree-sitter grammar
structure. Accepts a Language parameter (Language.C or Language.CPP) and
adjusts qualified-name construction accordingly.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path  # noqa: TC003
from typing import Any

from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind

logger = logging.getLogger(__name__)


class CFamilyExtractor:
    """Extracts C/C++ code symbols from tree-sitter AST.

    A single extractor handles both C and C++ since the tree-sitter grammars
    share the same node types for the constructs we care about. The
    ``language`` parameter controls qualified-name formatting:

    * C++ → ``namespace::name`` (or ``file_stem::name`` at file scope)
    * C   → ``file_stem::name`` (C has no namespaces)
    """

    def extract(
        self,
        file_path: Path,
        source: bytes,
        root_node: Any,
        language: Language,
    ) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []

        file_id = self._node_id(file_path, 1, root_node.end_point[0] + 1)
        file_node = Node(
            id=file_id,
            kind=NodeKind.FILE,
            name=file_path.name,
            qualified_name=str(file_path),
            file_path=file_path,
            language=language,
            start_line=1,
            end_line=root_node.end_point[0] + 1,
        )
        nodes.append(file_node)

        self._walk(source, root_node, file_path, language, file_id, nodes, edges)
        return nodes, edges

    # ------------------------------------------------------------------
    # Walk / dispatch
    # ------------------------------------------------------------------

    def _walk(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        language: Language,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        kind = node.type

        if kind == "function_definition":
            self._extract_function(source, node, file_path, language, parent_id, nodes, edges)
        elif kind == "class_specifier":
            self._extract_class(source, node, file_path, language, parent_id, nodes, edges)
        elif kind == "struct_specifier":
            self._extract_struct(source, node, file_path, language, parent_id, nodes, edges)
        elif kind == "enum_specifier":
            self._extract_enum(source, node, file_path, language, parent_id, nodes, edges)
        elif kind == "type_definition":
            self._extract_type_alias(source, node, file_path, language, parent_id, nodes, edges)
        elif kind == "preproc_include":
            self._extract_import(source, node, file_path, language, parent_id, nodes, edges)
        elif kind == "declaration":
            if self._is_file_level(node):
                self._extract_variable(source, node, file_path, language, parent_id, nodes, edges)
            else:
                for child in node.children:
                    self._walk(source, child, file_path, language, parent_id, nodes, edges)
        elif kind == "namespace_definition":
            self._extract_namespace(source, node, file_path, language, parent_id, nodes, edges)
        else:
            for child in node.children:
                self._walk(source, child, file_path, language, parent_id, nodes, edges)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _is_file_level(node: Any) -> bool:
        """Return True if *node* is a direct child of the translation_unit."""
        parent = node.parent
        return parent is not None and parent.type == "translation_unit"

    # ------------------------------------------------------------------
    # Extractors — each returns the created node's id (or "")
    # ------------------------------------------------------------------

    def _extract_function(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        language: Language,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        """Extract a function_definition node."""
        declarator = node.child_by_field_name("declarator")
        name = ""
        if declarator:
            func_decl = declarator.child_by_field_name("declarator") or declarator
            # Unwrap function_declarator to find the identifier
            name_node = None
            if func_decl.type == "function_declarator":
                for child in func_decl.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            elif isinstance(func_decl, type(node)):  # tree-sitter node
                name_node = func_decl.child_by_field_name("declarator")
            if name_node is None and func_decl.type == "identifier":
                name_node = func_decl
            if name_node is None:
                # Last resort: scan all children for an identifier
                for child in func_decl.children:
                    if child.type == "identifier":
                        name_node = child
                        break
            if name_node is None:
                name_node = func_decl
            name = source[name_node.start_byte : name_node.end_byte].decode()

        if not name:
            return ""

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(file_path, language, name, parent_id, nodes)

        func_node = Node(
            id=node_id,
            kind=NodeKind.FUNCTION,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(func_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        self._extract_calls(source, node, node_id, file_path, edges)
        return node_id

    def _extract_class(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        language: Language,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        """Extract a C++ class_specifier node."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ""
        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(file_path, language, name, parent_id, nodes)

        class_node = Node(
            id=node_id,
            kind=NodeKind.CLASS,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(class_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(source, child, file_path, language, node_id, nodes, edges)
        return node_id

    def _extract_struct(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        language: Language,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        """Extract a struct_specifier node (C or C++)."""
        name_node = node.child_by_field_name("name")
        name = ""
        if name_node:
            name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(
            file_path, language, name or "<anonymous_struct>", parent_id, nodes
        )

        struct_node = Node(
            id=node_id,
            kind=NodeKind.STRUCT,
            name=name or "<anonymous_struct>",
            qualified_name=qualified,
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(struct_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(source, child, file_path, language, node_id, nodes, edges)
        return node_id

    def _extract_enum(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        language: Language,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        """Extract an enum_specifier node."""
        name_node = node.child_by_field_name("name")
        name = ""
        if name_node:
            name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(
            file_path, language, name or "<anonymous_enum>", parent_id, nodes
        )

        enum_node = Node(
            id=node_id,
            kind=NodeKind.ENUM,
            name=name or "<anonymous_enum>",
            qualified_name=qualified,
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(enum_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        return node_id

    def _extract_type_alias(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        language: Language,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        """Extract a ``typedef`` declaration as a TYPE_ALIAS node."""
        declarator = node.child_by_field_name("declarator")
        name = ""
        if declarator:
            if declarator.type == "identifier":
                name = source[declarator.start_byte : declarator.end_byte].decode()
            else:
                # Walk to the rightmost identifier for complex typedefs
                stack = list(declarator.children)
                while stack:
                    current = stack.pop(0)
                    if current.type == "identifier":
                        name = source[current.start_byte : current.end_byte].decode()
                        break
                    stack.extend(current.children)

        if not name:
            return ""

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(file_path, language, name, parent_id, nodes)

        alias_node = Node(
            id=node_id,
            kind=NodeKind.TYPE_ALIAS,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(alias_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        return node_id

    def _extract_variable(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        language: Language,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        """Extract a file-level declaration as a VARIABLE node."""
        declarator = node.child_by_field_name("declarator")
        name = ""
        if declarator:
            if declarator.type == "identifier":
                name = source[declarator.start_byte : declarator.end_byte].decode()
            elif declarator.type == "init_declarator":
                inner = declarator.child_by_field_name("declarator")
                if inner and inner.type == "identifier":
                    name = source[inner.start_byte : inner.end_byte].decode()
            elif declarator.type == "pointer_declarator":
                # Recursively find the identifier inside pointer_declarator
                stack = [declarator]
                while stack:
                    current = stack.pop()
                    for child in current.children:
                        if child.type == "identifier":
                            name = source[child.start_byte : child.end_byte].decode()
                            break
                        stack.append(child)
                    if name:
                        break

        if not name:
            return ""

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(file_path, language, name, parent_id, nodes)

        var_node = Node(
            id=node_id,
            kind=NodeKind.VARIABLE,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(var_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        return node_id

    def _extract_import(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        language: Language,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract a ``#include`` directive as an IMPORT node."""
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        text = source[node.start_byte : node.end_byte].decode().strip()

        import_node = Node(
            id=node_id,
            kind=NodeKind.IMPORT,
            name=text[:80],
            qualified_name=f"include:{text[:80]}",
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(import_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        edges.append(
            Edge(
                id=self._edge_id(parent_id, f"unresolved:{text}", EdgeKind.IMPORTS),
                source_id=parent_id,
                target_id=f"unresolved:{text}",
                kind=EdgeKind.IMPORTS,
                confidence=0.5,
            )
        )

    def _extract_namespace(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        language: Language,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        """Extract a C++ namespace_definition as a MODULE node."""
        name_node = node.child_by_field_name("name")
        name = ""
        if name_node:
            name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(file_path, language, name, parent_id, nodes)

        ns_node = Node(
            id=node_id,
            kind=NodeKind.MODULE,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=language,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(ns_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(source, child, file_path, language, node_id, nodes, edges)
        return node_id

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    def _extract_calls(
        self,
        source: bytes,
        func_node: Any,
        func_id: str,
        file_path: Path,
        edges: list[Edge],
    ) -> None:
        """Extract call_expressions from a function body."""
        body = func_node.child_by_field_name("body")
        if body is None:
            return
        self._find_calls_recursive(source, body, func_id, file_path, edges)

    def _find_calls_recursive(
        self,
        source: bytes,
        node: Any,
        caller_id: str,
        file_path: Path,
        edges: list[Edge],
    ) -> None:
        """Recursively find call_expression nodes and emit CALLS edges."""
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func:
                call_name = source[func.start_byte : func.end_byte].decode()
                edges.append(
                    Edge(
                        id=self._edge_id(caller_id, f"unresolved:{call_name}", EdgeKind.CALLS),
                        source_id=caller_id,
                        target_id=f"unresolved:{call_name}",
                        kind=EdgeKind.CALLS,
                        confidence=0.5,
                        line=node.start_point[0] + 1,
                    )
                )
        for child in node.children:
            self._find_calls_recursive(source, child, caller_id, file_path, edges)

    # ------------------------------------------------------------------
    # Qualified name construction
    # ------------------------------------------------------------------

    def _build_qualified_name(
        self,
        file_path: Path,
        language: Language,
        name: str,
        parent_id: str,
        nodes: list[Node],
    ) -> str:
        """Build a qualified name for a symbol.

        For C++: ``namespace::name`` (or ``file_stem::name`` at file scope).
        For C:   ``file_stem::name`` (C has no namespaces).
        """
        for n in nodes:
            if n.id == parent_id:
                if n.kind in (
                    NodeKind.CLASS,
                    NodeKind.STRUCT,
                    NodeKind.ENUM,
                    NodeKind.MODULE,
                ):
                    return f"{n.qualified_name}::{name}"
                elif n.kind == NodeKind.FILE:
                    return f"{file_path.stem}::{name}"
                break
        return name

    # ------------------------------------------------------------------
    # ID helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _node_id(file: Path, start: int, end: int) -> str:
        raw = f"{file}:{start}:{end}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _edge_id(source: str, target: str, kind: EdgeKind) -> str:
        raw = f"{source}:{target}:{kind.value}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
