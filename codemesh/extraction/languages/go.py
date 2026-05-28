"""Go tree-sitter extractor."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path  # noqa: TC003
from typing import Any

from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind

logger = logging.getLogger(__name__)


class GoExtractor:
    """Extracts Go code symbols from tree-sitter AST."""

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

        if kind == "function_declaration":
            self._extract_function(source, node, file_path, parent_id, nodes, edges)
        elif kind == "method_declaration":
            self._extract_method(source, node, file_path, parent_id, nodes, edges)
        elif kind == "type_declaration":
            self._extract_type_declaration(source, node, file_path, parent_id, nodes, edges)
        elif kind == "const_declaration":
            self._extract_constant(source, node, file_path, parent_id, nodes, edges)
        elif kind == "import_declaration":
            self._extract_import(source, node, file_path, parent_id, nodes, edges)
        else:
            for child in node.children:
                self._walk(source, child, file_path, parent_id, nodes, edges)

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

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        func_node = Node(
            id=node_id,
            kind=NodeKind.FUNCTION,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.GO,
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

    def _extract_method(
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
        method_node = Node(
            id=node_id,
            kind=NodeKind.METHOD,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.GO,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(method_node)
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

    def _extract_type_declaration(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        # type_declaration contains type_spec children
        for child in node.children:
            if child.type == "type_spec":
                type_spec_child = child.child_by_field_name("type")
                if type_spec_child is None:
                    continue
                if type_spec_child.type == "struct_type":
                    self._extract_struct(source, child, file_path, parent_id, nodes, edges)
                elif type_spec_child.type == "interface_type":
                    self._extract_interface(source, child, file_path, parent_id, nodes, edges)
        return ""

    def _extract_struct(
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
        struct_node = Node(
            id=node_id,
            kind=NodeKind.STRUCT,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.GO,
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
        return node_id

    def _extract_interface(
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
        iface_node = Node(
            id=node_id,
            kind=NodeKind.INTERFACE,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.GO,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(iface_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        return node_id

    def _extract_constant(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> str:
        # const_declaration may contain multiple const_spec children
        for child in node.children:
            if child.type == "const_spec":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                name = source[name_node.start_byte : name_node.end_byte].decode()
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                node_id = self._node_id(file_path, start_line, end_line)
                qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
                const_node = Node(
                    id=node_id,
                    kind=NodeKind.CONSTANT,
                    name=name,
                    qualified_name=qualified,
                    file_path=file_path,
                    language=Language.GO,
                    start_line=start_line,
                    end_line=end_line,
                    parent_id=parent_id,
                )
                nodes.append(const_node)
                edges.append(
                    Edge(
                        id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                        source_id=parent_id,
                        target_id=node_id,
                        kind=EdgeKind.CONTAINS,
                    )
                )
        return ""

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
            language=Language.GO,
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

    def _extract_calls(
        self,
        source: bytes,
        func_node: Any,
        func_id: str,
        file_path: Path,
        edges: list[Edge],
    ) -> None:
        body = func_node.child_by_field_name("body")
        if body is None:
            return
        self._find_calls(source, body, func_id, file_path, edges)

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

    def _build_qualified_name(
        self,
        file_path: Path,
        name: str,
        parent_id: str,
        nodes: list[Node],
    ) -> str:
        for n in nodes:
            if n.id == parent_id:
                if n.kind in (NodeKind.STRUCT, NodeKind.INTERFACE, NodeKind.ENUM, NodeKind.MODULE):
                    return f"{n.qualified_name}::{name}"
                elif n.kind == NodeKind.FILE:
                    return f"{file_path.stem}::{name}"
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
