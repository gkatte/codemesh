"""TypeScript/JavaScript tree-sitter extractor."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path  # noqa: TC003
from typing import Any

from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind

logger = logging.getLogger(__name__)


class TypeScriptExtractor:
    """Extracts TypeScript/JavaScript code symbols from tree-sitter AST."""

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

        if kind in ("function_declaration", "arrow_function"):
            self._extract_function(source, node, file_path, parent_id, nodes, edges)
        elif kind == "class_declaration":
            self._extract_class(source, node, file_path, parent_id, nodes, edges)
        elif kind in ("import_statement", "import_clause"):
            self._extract_import(source, node, file_path, parent_id, nodes, edges)
        elif kind == "method_definition":
            self._extract_method(source, node, file_path, parent_id, nodes, edges)
        elif kind == "interface_declaration":
            self._extract_interface(source, node, file_path, parent_id, nodes, edges)
        elif kind == "type_alias_declaration":
            self._extract_type_alias(source, node, file_path, parent_id, nodes, edges)
        elif kind == "enum_declaration":
            self._extract_enum(source, node, file_path, parent_id, nodes, edges)
        elif kind == "variable_declaration":
            self._extract_variable(source, node, file_path, parent_id, nodes, edges)
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
            name_node = self._find_function_name(node)
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
            language=Language.TYPESCRIPT,
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
            language=Language.TYPESCRIPT,
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

    def _extract_class(
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

        heritage = node.child_by_field_name("heritage_clauses")
        bases: list[str] = []
        if heritage:
            for child in heritage.children:
                if child.type == "extends_clause":
                    for ident in child.children:
                        if ident.type == "type_identifier":
                            bases.append(source[ident.start_byte : ident.end_byte].decode())

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        class_node = Node(
            id=node_id,
            kind=NodeKind.CLASS,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.TYPESCRIPT,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            metadata={"bases": ",".join(bases)},
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

        for base_name in bases:
            edges.append(
                Edge(
                    id=self._edge_id(node_id, f"unresolved:{base_name}", EdgeKind.EXTENDS),
                    source_id=node_id,
                    target_id=f"unresolved:{base_name}",
                    kind=EdgeKind.EXTENDS,
                    confidence=0.5,
                )
            )

        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(source, child, file_path, node_id, nodes, edges)
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
            language=Language.TYPESCRIPT,
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
            language=Language.TYPESCRIPT,
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

    def _extract_type_alias(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        type_node = Node(
            id=node_id,
            kind=NodeKind.TYPE_ALIAS,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.TYPESCRIPT,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(type_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )

    def _extract_enum(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
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
            language=Language.TYPESCRIPT,
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

    def _extract_variable(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract variable declarations (let/const/var)."""
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                if name_node is None:
                    continue
                name = source[name_node.start_byte : name_node.end_byte].decode()
                if name.startswith("_") and len(name) <= 2:
                    continue  # Skip placeholder names
                start_line = child.start_point[0] + 1
                end_line = child.end_point[0] + 1
                node_id = self._node_id(file_path, start_line, end_line)
                # Determine if constant (const) or variable
                parent_kind = node.type
                kind = NodeKind.CONSTANT if "const" in parent_kind else NodeKind.VARIABLE
                qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
                var_node = Node(
                    id=node_id,
                    kind=kind,
                    name=name,
                    qualified_name=qualified,
                    file_path=file_path,
                    language=Language.TYPESCRIPT,
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

    def _find_function_name(self, node: Any) -> Any:
        """Try to find function name from parent variable declarator."""
        parent = node.parent
        if parent and parent.type == "variable_declarator":
            return parent.child_by_field_name("name")
        return None

    def _build_qualified_name(
        self,
        file_path: Path,
        name: str,
        parent_id: str,
        nodes: list[Node],
    ) -> str:
        for n in nodes:
            if n.id == parent_id:
                if n.kind in (NodeKind.CLASS, NodeKind.INTERFACE):
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
