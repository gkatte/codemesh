"""Python-specific tree-sitter extractor."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path  # noqa: TC003
from typing import Any

from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind

logger = logging.getLogger(__name__)


class PythonExtractor:
    """Extracts Python code symbols from tree-sitter AST."""

    def extract(
        self,
        file_path: Path,
        source: bytes,
        root_node: Any,
        language: Language,
    ) -> tuple[list[Node], list[Edge]]:
        nodes: list[Node] = []
        edges: list[Edge] = []

        # Create file-level node
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

        # Walk the AST
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
        """Recursively walk the AST and extract nodes/edges."""
        kind = node.type

        if kind == "function_definition":
            self._extract_function(source, node, file_path, parent_id, nodes, edges)
        elif kind == "class_definition":
            self._extract_class(source, node, file_path, parent_id, nodes, edges)
        elif kind == "import_statement" or kind == "import_from_statement":
            self._extract_import(source, node, file_path, parent_id, nodes, edges)
        elif kind == "assignment":
            self._extract_assignment(source, node, file_path, parent_id, nodes, edges)
        else:
            # Recurse into children
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
        """Extract a function/method node. Returns the node ID."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ""

        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)

        # Extract docstring
        docstring = self._extract_docstring(source, node)

        # Extract signature
        params = node.child_by_field_name("parameters")
        return_type = node.child_by_field_name("return_type")
        signature = self._build_signature(source, name, params, return_type)

        # Determine if method or function
        kind = NodeKind.METHOD if self._is_method(node) else NodeKind.FUNCTION

        # Qualified name
        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)

        func_node = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.PYTHON,
            start_line=start_line,
            end_line=end_line,
            start_column=node.start_point[1],
            end_column=node.end_point[1],
            docstring=docstring,
            signature=signature,
            parent_id=parent_id,
        )
        nodes.append(func_node)

        # Edge: parent contains function
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )

        # Extract calls within the function body
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
        """Extract a class node. Returns the node ID."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return ""

        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)

        # Extract superclasses
        superclasses = node.child_by_field_name("superclasses")
        bases: list[str] = []
        if superclasses:
            for child in superclasses.children:
                if child.type == "identifier":
                    bases.append(source[child.start_byte : child.end_byte].decode())

        docstring = self._extract_docstring(source, node)
        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)

        class_node = Node(
            id=node_id,
            kind=NodeKind.CLASS,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.PYTHON,
            start_line=start_line,
            end_line=end_line,
            start_column=node.start_point[1],
            end_column=node.end_point[1],
            docstring=docstring,
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

        # Extract extends edges
        for base_name in bases:
            # Target will be resolved later by ReferenceResolver
            edges.append(
                Edge(
                    id=self._edge_id(node_id, f"unresolved:{base_name}", EdgeKind.EXTENDS),
                    source_id=node_id,
                    target_id=f"unresolved:{base_name}",
                    kind=EdgeKind.EXTENDS,
                    confidence=0.5,
                )
            )

        # Walk class body for methods
        body = node.child_by_field_name("body")
        if body:
            for child in body.children:
                self._walk(source, child, file_path, node_id, nodes, edges)

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
        """Extract import statements."""
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
            language=Language.PYTHON,
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

    def _extract_assignment(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract top-level assignments as constants or variables.

        Heuristic: UPPER_CASE names → CONSTANT, others → VARIABLE.
        Only extracts module-level assignments (parent is module).
        """
        # Only extract module-level assignments
        if node.parent and node.parent.type != "module":
            return

        lhs = node.child_by_field_name("left")
        if lhs is None:
            return

        # Handle simple name assignments (not tuple unpacking, not attributes)
        if lhs.type != "identifier":
            return

        name = source[lhs.start_byte : lhs.end_byte].decode()
        if name.startswith("_"):
            return  # Skip private/placeholder names

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)

        # Heuristic: ALL_CAPS → constant, otherwise → variable
        is_constant = name.isupper() and len(name) > 1
        kind = NodeKind.CONSTANT if is_constant else NodeKind.VARIABLE

        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        var_node = Node(
            id=node_id,
            kind=kind,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.PYTHON,
            start_line=start_line,
            end_line=end_line,
            start_column=node.start_point[1],
            end_column=node.end_point[1],
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
        """Extract function calls within a function body."""
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
        """Recursively find call expressions."""
        if node.type == "call":
            func = node.child_by_field_name("function")
            if func:
                call_name = source[func.start_byte : func.end_byte].decode()
                target_id = f"unresolved:{call_name}"
                edges.append(
                    Edge(
                        id=self._edge_id(caller_id, target_id, EdgeKind.CALLS),
                        source_id=caller_id,
                        target_id=target_id,
                        kind=EdgeKind.CALLS,
                        confidence=0.5,
                        line=node.start_point[0] + 1,
                    )
                )

        for child in node.children:
            self._find_calls_recursive(source, child, caller_id, file_path, edges)

    def _extract_docstring(self, source: bytes, node: Any) -> str:
        """Extract docstring from a function/class body."""
        body = node.child_by_field_name("body")
        if body and body.children:
            first = body.children[0]
            if (
                first.type == "expression_statement"
                and first.children
                and first.children[0].type == "string"
            ):
                raw = source[first.children[0].start_byte : first.children[0].end_byte].decode()
                # Strip quotes
                return raw.strip("'\"").strip()
        return ""

    def _build_signature(self, source: bytes, name: str, params: Any, return_type: Any) -> str:
        """Build a function signature string."""
        params_str = "..."
        if params:
            params_str = source[params.start_byte : params.end_byte].decode()
        ret = ""
        if return_type:
            ret = f" -> {source[return_type.start_byte : return_type.end_byte].decode()}"
        return f"def {name}{params_str}{ret}"

    def _is_method(self, node: Any) -> bool:
        """Check if a function definition is a method (inside a class)."""
        parent = node.parent
        while parent:
            if parent.type == "class_definition":
                return True
            parent = parent.parent
        return False

    def _build_qualified_name(
        self, file_path: Path, name: str, parent_id: str, nodes: list[Node]
    ) -> str:
        """Build qualified name from parent chain."""
        # Find parent node
        for n in nodes:
            if n.id == parent_id:
                if n.kind == NodeKind.CLASS:
                    return f"{n.qualified_name}.{name}"
                elif n.kind == NodeKind.FILE:
                    stem = file_path.stem
                    return f"{stem}.{name}"
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
