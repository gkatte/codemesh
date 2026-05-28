"""Swift tree-sitter extractor."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path  # noqa: TC003
from typing import Any

from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind

logger = logging.getLogger(__name__)


class SwiftExtractor:
    """Extracts Swift code symbols from tree-sitter AST."""

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

        self._walk(source, root_node, file_path, file_id, nodes, edges)
        return nodes, edges

    def _walk(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        kind = node.type

        if kind == "class_declaration":
            self._extract_class_declaration(source, node, file_path, parent_id, nodes, edges)
        elif kind == "protocol_declaration":
            self._extract_protocol(source, node, file_path, parent_id, nodes, edges)
        elif kind == "function_declaration":
            self._extract_function(source, node, file_path, parent_id, nodes, edges)
        elif kind == "init_declaration":
            self._extract_init(source, node, file_path, parent_id, nodes, edges)
        elif kind == "property_declaration":
            self._extract_property(source, node, file_path, parent_id, nodes, edges)
        elif kind == "enum_declaration":
            self._extract_enum(source, node, file_path, parent_id, nodes, edges)
        elif kind == "import_declaration":
            self._extract_import(source, node, file_path, parent_id, nodes, edges)
        else:
            for child in node.children:
                self._walk(source, child, file_path, parent_id, nodes, edges)

    def _extract_class_declaration(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ""
        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)

        # Determine if this is a class or struct by checking preceding sibling keyword
        kind = NodeKind.CLASS
        for child in node.children:
            child_text = source[child.start_byte : child.end_byte].decode().strip()
            if child_text in ("class", "struct"):
                if child_text == "struct":
                    kind = NodeKind.STRUCT
                break

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        class_node = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.SWIFT,
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
        # Walk children for nested declarations and calls
        self._walk_children(source, node, file_path, node_id, nodes, edges)
        return node_id

    def _extract_protocol(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ""
        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        protocol_node = Node(
            id=node_id,
            kind=NodeKind.INTERFACE,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.SWIFT,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(protocol_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        self._walk_children(source, node, file_path, node_id, nodes, edges)
        return node_id

    def _extract_function(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ""
        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)

        is_method = self._is_in_class_context(node)
        kind = NodeKind.METHOD if is_method else NodeKind.FUNCTION

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        func_node = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.SWIFT,
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

    def _extract_init(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)

        qualified = self._build_qualified_name(file_path, "init", parent_id, nodes)
        init_node = Node(
            id=node_id,
            kind=NodeKind.METHOD,
            name="init",
            qualified_name=qualified,
            file_path=file_path,
            language=Language.SWIFT,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(init_node)
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

    def _extract_property(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ""
        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)

        # Determine if constant (let) or variable (var)
        kind = NodeKind.VARIABLE
        for child in node.children:
            child_text = source[child.start_byte : child.end_byte].decode().strip()
            if child_text == "let":
                kind = NodeKind.CONSTANT
                break
            elif child_text == "var":
                break

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        prop_node = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.SWIFT,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(prop_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        return node_id

    def _extract_enum(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ""
        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        enum_node = Node(
            id=node_id,
            kind=NodeKind.ENUM,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.SWIFT,
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
        self._walk_children(source, node, file_path, node_id, nodes, edges)
        return node_id

    def _extract_import(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        text = source[node.start_byte : node.end_byte].decode().strip()
        import_node = Node(
            id=node_id,
            kind=NodeKind.IMPORT,
            name=text[:80],
            qualified_name=f"import:{text[:80]}",
            file_path=file_path,
            language=Language.SWIFT,
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

    def _walk_children(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        for child in node.children:
            self._walk(source, child, file_path, parent_id, nodes, edges)

    def _extract_calls(
        self,
        source: bytes,
        func_node: Any,
        func_id: str,
        file_path: Path,
        edges: list[Edge],
    ) -> None:
        self._find_calls(source, func_node, func_id, file_path, edges)

    def _find_calls(
        self,
        source: bytes,
        node: Any,
        caller_id: str,
        file_path: Path,
        edges: list[Edge],
    ) -> None:
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
            self._find_calls(source, child, caller_id, file_path, edges)

    def _is_in_class_context(self, node: Any) -> bool:
        """Check if a function_declaration is inside a class_declaration or enum_declaration."""
        parent = node.parent
        while parent:
            if parent.type in ("class_declaration", "struct_declaration", "enum_declaration"):
                return True
            parent = parent.parent
        return False

    def _build_qualified_name(
        self,
        file_path: Path,
        name: str,
        parent_id: str,
        nodes: list[Node],
    ) -> str:
        for n in nodes:
            if n.id == parent_id:
                if n.kind in (
                    NodeKind.CLASS,
                    NodeKind.STRUCT,
                    NodeKind.ENUM,
                    NodeKind.INTERFACE,
                    NodeKind.MODULE,
                ):
                    return f"{n.qualified_name}.{name}"
                elif n.kind == NodeKind.FILE:
                    return f"{file_path.stem}.{name}"
                break
        return name

    @staticmethod
    def _node_id(file: Path, start: int, end: int) -> str:
        raw = f"{file}:{start}:{end}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _edge_id(source: str, target: str, kind: EdgeKind) -> str:
        raw = f"{source}:{target}:{kind.value}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
