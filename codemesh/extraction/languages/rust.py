"""Rust tree-sitter extractor."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path  # noqa: TC003
from typing import Any

from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind

logger = logging.getLogger(__name__)


class RustExtractor:
    """Extracts Rust code symbols from tree-sitter AST."""

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

        if kind == "function_item":
            self._extract_function(source, node, file_path, parent_id, nodes, edges)
        elif kind == "struct_item":
            self._extract_struct(source, node, file_path, parent_id, nodes, edges)
        elif kind == "trait_item":
            self._extract_trait(source, node, file_path, parent_id, nodes, edges)
        elif kind == "impl_item":
            self._extract_impl(source, node, file_path, parent_id, nodes, edges)
        elif kind == "use_declaration":
            self._extract_import(source, node, file_path, parent_id, nodes, edges)
        elif kind == "mod_item":
            self._extract_module(source, node, file_path, parent_id, nodes, edges)
        elif kind == "enum_item":
            self._extract_enum(source, node, file_path, parent_id, nodes, edges)
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

        is_method = self._is_in_impl(node)
        kind = NodeKind.METHOD if is_method else NodeKind.FUNCTION

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        func_node = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.RUST,
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
            language=Language.RUST,
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

    def _extract_trait(
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
        trait_node = Node(
            id=node_id,
            kind=NodeKind.TRAIT,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.RUST,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(trait_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        return node_id

    def _extract_impl(
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

        type_node = node.child_by_field_name("type")
        type_name = ""
        if type_node:
            type_name = source[type_node.start_byte : type_node.end_byte].decode()

        impl_node = Node(
            id=node_id,
            kind=NodeKind.MODULE,
            name=f"impl {type_name}",
            qualified_name=f"impl_{type_name}",
            file_path=file_path,
            language=Language.RUST,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(impl_node)
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
                self._walk(source, child, file_path, node_id, nodes, edges)
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
            language=Language.RUST,
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
            qualified_name=f"use:{text[:80]}",
            file_path=file_path,
            language=Language.RUST,
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

    def _extract_module(
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
        mod_node = Node(
            id=node_id,
            kind=NodeKind.MODULE,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.RUST,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(mod_node)
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
                self._walk(source, child, file_path, node_id, nodes, edges)
        return node_id

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

    def _is_in_impl(self, node: Any) -> bool:
        parent = node.parent
        while parent:
            if parent.type == "impl_item":
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
                if n.kind in (NodeKind.STRUCT, NodeKind.TRAIT, NodeKind.ENUM, NodeKind.MODULE):
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
