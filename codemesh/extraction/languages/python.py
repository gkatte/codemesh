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
        elif kind in ("import_statement", "import_from_statement"):
            self._extract_import(source, node, file_path, parent_id, nodes, edges)
        elif kind == "assignment":
            self._extract_assignment(source, node, file_path, parent_id, nodes, edges)
        elif kind == "annotated_assignment":
            self._extract_annotated_assignment(source, node, file_path, parent_id, nodes, edges)
        elif kind == "decorated_definition":
            # Handle decorated functions/classes: @decorator def foo(): ...
            # Extract the inner definition
            for child in node.children:
                if child.type in ("function_definition", "class_definition"):
                    self._walk(source, child, file_path, parent_id, nodes, edges)
                    break
            else:
                for child in node.children:
                    self._walk(source, child, file_path, parent_id, nodes, edges)
        elif kind == "expression_statement":
            # Check for module-level assignments wrapped in expression_statement
            # This handles some edge cases in tree-sitter output
            for child in node.children:
                if child.type == "assignment":
                    self._extract_assignment(source, child, file_path, parent_id, nodes, edges)
                elif child.type == "annotated_assignment":
                    self._extract_annotated_assignment(source, child, file_path, parent_id, nodes, edges)
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

        docstring = self._extract_docstring(source, node)
        params = node.child_by_field_name("parameters")
        return_type = node.child_by_field_name("return_type")
        signature = self._build_signature(source, name, params, return_type)

        kind = NodeKind.METHOD if self._is_method(node) else NodeKind.FUNCTION
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
        edges.append(
            Edge(
                id=self._edge_id(parent_id, node_id, EdgeKind.CONTAINS),
                source_id=parent_id,
                target_id=node_id,
                kind=EdgeKind.CONTAINS,
            )
        )

        self._extract_calls(source, node, node_id, file_path, edges)
        self._extract_type_references(source, node, node_id, file_path, edges)
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

        Handles:
        - Simple: FOO = 1, bar = 2
        - Tuple unpacking: A, B = 1, 2 (extracts each name)
        - Multiple targets: a = b = 1
        """
        # Only extract module-level assignments
        if node.parent and node.parent.type != "module":
            return

        lhs = node.child_by_field_name("left")
        if lhs is None:
            return

        # Handle different LHS patterns
        names = self._extract_assignment_names(source, lhs)
        if not names:
            return

        for name, name_node in names:
            if name.startswith("_"):
                continue

            start_line = node.start_point[0] + 1
            end_line = node.end_point[0] + 1
            node_id = self._node_id(file_path, start_line, end_line)

            # All module-level assignments treated as constants
            kind = NodeKind.CONSTANT

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
                start_column=name_node.start_point[1],
                end_column=name_node.end_point[1],
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

    def _extract_annotated_assignment(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract type-annotated assignments like `MAX_SIZE: int = 100`.

        Only extracts module-level annotated assignments.
        """
        # Only extract module-level
        if node.parent and node.parent.type != "module":
            return

        lhs = node.child_by_field_name("left")
        if lhs is None or lhs.type != "identifier":
            return

        name = source[lhs.start_byte : lhs.end_byte].decode()
        if name.startswith("_"):
            return

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)

        # All module-level annotated assignments treated as constants
        kind = NodeKind.CONSTANT

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

    def _extract_assignment_names(
        self, source: bytes, lhs: Any
    ) -> list[tuple[str, Any]]:
        """Extract variable names from assignment LHS.
        Handles simple identifiers, tuple unpacking, and attribute assignments.
        """
        if lhs.type == "identifier":
            name = source[lhs.start_byte : lhs.end_byte].decode()
            return [(name, lhs)]
        elif lhs.type == "tuple_pattern" or lhs.type == "tuple":
            results = []
            for child in lhs.children:
                if child.type == "identifier":
                    name = source[child.start_byte : child.end_byte].decode()
                    results.append((name, child))
                elif child.type == "tuple_pattern" or child.type == "tuple":
                    results.extend(self._extract_assignment_names(source, child))
            return results
        elif lhs.type == "list_pattern":
            results = []
            for child in lhs.children:
                if child.type == "identifier":
                    name = source[child.start_byte : child.end_byte].decode()
                    results.append((name, child))
            return results
        return []

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
        for n in nodes:
            if n.id == parent_id:
                if n.kind == NodeKind.CLASS:
                    return f"{n.qualified_name}.{name}"
                elif n.kind == NodeKind.FILE:
                    stem = file_path.stem
                    return f"{stem}.{name}"
                break
        return name

    def _extract_type_references(
        self,
        source: bytes,
        node: Any,
        parent_id: str,
        file_path: Path,
        edges: list[Edge],
    ) -> None:
        """Extract type references from Python type annotations."""
        self._scan_type_refs(source, node, parent_id, file_path, edges)

    def _scan_type_refs(
        self,
        source: bytes,
        node: Any,
        parent_id: str,
        file_path: Path,
        edges: list[Edge],
    ) -> None:
        """Recursively scan for type identifier references in annotations."""
        # In Python tree-sitter, type annotations use "type" nodes containing identifiers
        if node.type in ("identifier", "attribute"):
            # Only create references for type annotations, not all identifiers
            # Check if parent is a type-related node
            parent = node.parent
            if parent and parent.type in ("type", "subscript", "generic_type", "tuple_type"):
                type_name = source[node.start_byte : node.end_byte].decode()
                if len(type_name) >= 2 and type_name[0].isupper():
                    # Heuristic: capitalized identifiers in types are type references
                    edges.append(
                        Edge(
                            id=self._edge_id(parent_id, f"unresolved:{type_name}", EdgeKind.REFERENCES),
                            source_id=parent_id,
                            target_id=f"unresolved:{type_name}",
                            kind=EdgeKind.REFERENCES,
                            confidence=0.4,
                            line=node.start_point[0] + 1,
                        )
                    )
        for child in node.children:
            self._scan_type_refs(source, child, parent_id, file_path, edges)

    @staticmethod
    def _node_id(file: Path, start: int, end: int) -> str:
        raw = f"{file}:{start}:{end}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]

    @staticmethod
    def _edge_id(source: str, target: str, kind: EdgeKind) -> str:
        raw = f"{source}:{target}:{kind.value}"
        return hashlib.sha256(raw.encode()).hexdigest()[:16]
