"""Java tree-sitter extractor.

Handles:
- class_declaration with fields, methods, constructors
- interface_declaration
- method_declaration, constructor_declaration
- field_declaration (static final → CONSTANT, else VARIABLE)
- enum_declaration
- import_declaration (scoped_identifier like java.util.List)
- package_declaration
- method_invocation (call edges)
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path  # noqa: TC003
from typing import Any

from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind

logger = logging.getLogger(__name__)


class JavaExtractor:
    """Extracts Java code symbols from tree-sitter AST."""

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

    # ── Top-level dispatch ──────────────────────────────────────────────

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

        if kind == "package_declaration":
            self._extract_package(source, node, file_path, parent_id, nodes, edges)
        elif kind == "import_declaration":
            self._extract_import(source, node, file_path, parent_id, nodes, edges)
        elif kind == "class_declaration":
            self._extract_class(source, node, file_path, parent_id, nodes, edges)
        elif kind == "interface_declaration":
            self._extract_interface(source, node, file_path, parent_id, nodes, edges)
        elif kind == "method_declaration":
            self._extract_method(source, node, file_path, parent_id, nodes, edges)
        elif kind == "constructor_declaration":
            self._extract_constructor(source, node, file_path, parent_id, nodes, edges)
        elif kind == "field_declaration":
            self._extract_field(source, node, file_path, parent_id, nodes, edges)
        elif kind == "enum_declaration":
            self._extract_enum(source, node, file_path, parent_id, nodes, edges)
        else:
            for child in node.children:
                self._walk(source, child, file_path, parent_id, nodes, edges)

    # ── Package ──────────────────────────────────────────────────────────

    def _extract_package(
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

        # Package name may be a scoped_identifier or identifier
        name = self._get_node_text(source, node)
        # Strip "package " prefix and ";" suffix for a clean name
        pkg_name = name
        for child in node.children:
            if child.type in ("scoped_identifier", "identifier"):
                pkg_name = source[child.start_byte : child.end_byte].decode()
                break

        pkg_node = Node(
            id=node_id,
            kind=NodeKind.MODULE,
            name=pkg_name,
            qualified_name=pkg_name,
            file_path=file_path,
            language=Language.JAVA,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(pkg_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )
        return node_id

    # ── Import ───────────────────────────────────────────────────────────

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
            language=Language.JAVA,
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

    # ── Class ────────────────────────────────────────────────────────────

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

        # Extract superclass (extends)
        super_node = node.child_by_field_name("superclass")
        bases: list[str] = []
        if super_node:
            bases.append(self._get_node_text(source, super_node).strip())

        # Extract interfaces (implements)
        interfaces_node = node.child_by_field_name("interfaces")
        implements: list[str] = []
        if interfaces_node:
            for child in interfaces_node.children:
                if child.type in ("type_identifier", "scoped_identifier", "generic_type"):
                    implements.append(source[child.start_byte : child.end_byte].decode())

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        class_node = Node(
            id=node_id,
            kind=NodeKind.CLASS,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.JAVA,
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

        # Extends edges
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

        # Implements edges
        for impl_name in implements:
            edges.append(
                Edge(
                    id=self._edge_id(node_id, f"unresolved:{impl_name}", EdgeKind.IMPLEMENTS),
                    source_id=node_id,
                    target_id=f"unresolved:{impl_name}",
                    kind=EdgeKind.IMPLEMENTS,
                    confidence=0.5,
                )
            )

        # Walk class body
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(source, child, file_path, node_id, nodes, edges)
        return node_id

    # ── Interface ────────────────────────────────────────────────────────

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
            language=Language.JAVA,
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
        # Walk interface body for method signatures etc.
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(source, child, file_path, node_id, nodes, edges)
        return node_id

    # ── Method ───────────────────────────────────────────────────────────

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

        # Build signature from parameters and return type
        params_node = node.child_by_field_name("parameters")
        return_node = node.child_by_field_name("type")
        signature = self._build_java_signature(source, name, params_node, return_node)

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        method_node = Node(
            id=node_id,
            kind=NodeKind.METHOD,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.JAVA,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
            signature=signature,
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

    # ── Constructor ──────────────────────────────────────────────────────

    def _extract_constructor(
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

        params_node = node.child_by_field_name("parameters")
        if params_node:
            source[params_node.start_byte : params_node.end_byte].decode().strip()

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        ctor_node = Node(
            id=node_id,
            kind=NodeKind.METHOD,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.JAVA,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(ctor_node)
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

    # ── Field / Constant ─────────────────────────────────────────────────

    def _extract_field(
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

        # Check modifiers for static final → CONSTANT
        modifiers_node = node.child_by_field_name("modifiers")
        is_constant = False
        if modifiers_node:
            mod_text = source[modifiers_node.start_byte : modifiers_node.end_byte].decode()
            is_constant = "static" in mod_text and "final" in mod_text
        kind = NodeKind.CONSTANT if is_constant else NodeKind.VARIABLE

        # Extract the declarator name
        name_node = node.child_by_field_name("declarator")
        if name_node is None:
            # fallback: search for identifier child
            for child in node.children:
                if child.type == "identifier":
                    name_node = child
                    break
        if name_node is None:
            return
        name = source[name_node.start_byte : name_node.end_byte].decode()

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        field_node = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.JAVA,
            start_line=start_line,
            end_line=end_line,
            parent_id=parent_id,
        )
        nodes.append(field_node)
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )

    # ── Enum ─────────────────────────────────────────────────────────────

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
            language=Language.JAVA,
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
        # Walk enum body
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(source, child, file_path, node_id, nodes, edges)
        return node_id

    # ── Call extraction ──────────────────────────────────────────────────

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
        if node.type == "method_invocation":
            name_node = node.child_by_field_name("name")
            if name_node:
                call_name = source[name_node.start_byte : name_node.end_byte].decode()
                # Also check for object (e.g., obj.method())
                obj_node = node.child_by_field_name("object")
                if obj_node:
                    obj_name = source[obj_node.start_byte : obj_node.end_byte].decode()
                    call_name = f"{obj_name}.{call_name}"
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

    # ── Helpers ──────────────────────────────────────────────────────────

    def _build_java_signature(
        self,
        source: bytes,
        name: str,
        params_node: Any | None,
        return_node: Any | None,
    ) -> str:
        params_str = ""
        if params_node:
            params_str = source[params_node.start_byte : params_node.end_byte].decode().strip()
        return_str = ""
        if return_node:
            return_str = source[return_node.start_byte : return_node.end_byte].decode().strip()
        if return_str:
            return f"{name}{params_str} -> {return_str}"
        return f"{name}{params_str}"

    def _build_qualified_name(
        self,
        file_path: Path,
        name: str,
        parent_id: str,
        nodes: list[Node],
    ) -> str:
        for n in nodes:
            if n.id == parent_id:
                if n.kind in (NodeKind.CLASS, NodeKind.INTERFACE, NodeKind.ENUM, NodeKind.MODULE):
                    return f"{n.qualified_name}.{name}"
                elif n.kind == NodeKind.FILE:
                    return f"{file_path.stem}.{name}"
                break
        return name

    @staticmethod
    def _get_node_text(source: bytes, node: Any) -> str:
        return source[node.start_byte : node.end_byte].decode()

    @staticmethod
    def _node_id(file: Path, start: int, end: int) -> str:
        raw = f"{file}:{start}:{end}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _edge_id(source: str, target: str, kind: EdgeKind) -> str:
        raw = f"{source}:{target}:{kind.value}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
