"""TypeScript/JavaScript tree-sitter extractor.

Handles:
- function_declaration, arrow_function, function_expression
- class_declaration with method_definition, property_definition
- import_statement, import_clause
- interface_declaration, type_alias_declaration, enum_declaration
- variable_declaration, lexical_declaration (const/let/var)
- export_statement (all inner declaration types)
- object_method_definition (shorthand methods in object literals)
- public_field_definition (class fields)
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path  # noqa: TC003
from typing import Any

from codemesh.types import Edge, EdgeKind, Language, Node, NodeKind

logger = logging.getLogger(__name__)


class TypeScriptExtractor:
    """Extracts TypeScript/JavaScript code symbols from tree-sitter AST.

    Handles both .ts/.tsx (TypeScript) and .js/.jsx (JavaScript) files.
    JavaScript files use the same extractor — the tree-sitter grammar is
    shared (tsx grammar covers both).
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
        elif kind == "lexical_declaration":
            self._extract_lexical(source, node, file_path, parent_id, nodes, edges)
        elif kind == "export_statement":
            self._handle_export(source, node, file_path, parent_id, nodes, edges)
        elif kind == "function_signature":
            # TypeScript function overload signatures
            self._extract_function_signature(source, node, file_path, parent_id, nodes, edges)
        elif kind == "method_signature":
            # TypeScript method signatures in interfaces
            self._extract_method_signature(source, node, file_path, parent_id, nodes, edges)
        elif kind == "public_field_definition":
            # Class fields (TypeScript 3.8+): class Foo { bar = 1; }
            self._extract_public_field(source, node, file_path, parent_id, nodes, edges)
        elif kind == "assignment":
            # JS: function assigned to variable → treat as function
            # e.g., module.exports.foo = function() {}
            self._extract_assignment_pattern(source, node, file_path, parent_id, nodes, edges)
        else:
            for child in node.children:
                self._walk(source, child, file_path, parent_id, nodes, edges)

    # ── Function / Method ───────────────────────────────────────────────

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

        signature = self._build_ts_signature(source, name, node)
        docstring = self._extract_ts_docstring(source, node)
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
            signature=signature,
            docstring=docstring,
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
        signature = self._build_ts_signature(source, name, node)
        docstring = self._extract_ts_docstring(source, node)
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
            signature=signature,
            docstring=docstring,
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

    _extract_function_signature = _extract_function  # same logic, TS overload
    _extract_method_signature = _extract_method      # same logic, interface method sig

    # ── Class ───────────────────────────────────────────────────────────

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

    # ── Interface / Type Alias / Enum ───────────────────────────────────

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
        docstring = self._extract_ts_docstring(source, node)
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
            docstring=docstring,
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

    # ── Variables / Constants ───────────────────────────────────────────

    def _extract_variable(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract variable declarations (let/const/var).

        Handles:
        - const FOO = value  → CONSTANT
        - let foo = value    → VARIABLE
        - const fn = () =>   → FUNCTION (arrow function)
        - module.exports =   → captures the object literal methods
        """
        node_text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        is_const = node_text.strip().startswith("const")

        for child in node.children:
            if child.type == "variable_declarator":
                self._extract_variable_declarator(
                    source, child, file_path, parent_id, nodes, edges, is_const
                )

    def _extract_lexical(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract lexical declarations (let/const in TS/JS block scope)."""
        node_text = source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")
        is_const = node_text.strip().startswith("const")
        for child in node.children:
            if child.type == "variable_declarator":
                self._extract_variable_declarator(
                    source, child, file_path, parent_id, nodes, edges, is_const
                )

    def _extract_variable_declarator(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
        is_const: bool,
    ) -> None:
        """Extract a single variable declarator, handling arrow functions."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = source[name_node.start_byte : name_node.end_byte].decode()
        if name.startswith("_") and len(name) <= 2:
            return  # Skip placeholder names

        value_node = node.child_by_field_name("value")
        # Check if the value is an arrow function — extract as function, not variable
        if value_node is not None and value_node.type == "arrow_function":
            self._extract_function(source, value_node, file_path, parent_id, nodes, edges)
            return

        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)

        # Better constant heuristic: UPPER_CASE or const with primitive value
        has_upper = any(c.isupper() for c in name)
        has_lower = any(c.islower() for c in name)
        is_constant = is_const and has_upper and not has_lower and len(name) > 1
        kind = NodeKind.CONSTANT if is_constant else NodeKind.VARIABLE
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

    def _extract_public_field(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract class field declarations (public_field_definition).

        TypeScript class fields: class Foo { bar = 1; }
        """
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        name = source[name_node.start_byte : name_node.end_byte].decode()
        start_line = node.start_point[0] + 1
        end_line = node.end_point[0] + 1
        node_id = self._node_id(file_path, start_line, end_line)
        qualified = self._build_qualified_name(file_path, name, parent_id, nodes)
        field_node = Node(
            id=node_id,
            kind=NodeKind.VARIABLE,
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            language=Language.TYPESCRIPT,
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

    def _extract_assignment_pattern(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Extract JS assignment patterns like module.exports.foo = function() {}.

        Only extracts module-level assignments (parent is program/statement_block)
        that assign arrow functions or function expressions.
        """
        if node.parent and node.parent.type not in ("program", "statement_block", "module"):
            return

        left = node.child_by_field_name("left")
        right = node.child_by_field_name("right")
        if left is None or right is None:
            return

        # Only extract if right side is a function
        if right.type not in ("arrow_function", "function"):
            return

        # Get name from left side
        if left.type == "identifier":
            name = source[left.start_byte : left.end_byte].decode()
        elif left.type == "member_expression":
            prop = left.child_by_field_name("property")
            if prop is None:
                return
            name = source[prop.start_byte : prop.end_byte].decode()
        else:
            return

        if name.startswith("_") and len(name) <= 2:
            return

        self._extract_function(source, right, file_path, parent_id, nodes, edges)

    # ── Export handling ─────────────────────────────────────────────────

    def _handle_export(
        self,
        source: bytes,
        node: Any,
        file_path: Path,
        parent_id: str,
        nodes: list[Node],
        edges: list[Edge],
    ) -> None:
        """Walk into export statements to extract the inner declaration.

        Handles:
        - export function_declaration
        - export class_declaration
        - export lexical_declaration (const/let)
        - export variable_declaration
        - export default function_declaration
        - export type_alias_declaration
        - export enum_declaration
        - export interface_declaration
        - export { ... } (re-exports, skipped)
        """
        for child in node.children:
            kind = child.type
            if kind == "function_declaration":
                self._extract_function(source, child, file_path, parent_id, nodes, edges)
            elif kind == "class_declaration":
                self._extract_class(source, child, file_path, parent_id, nodes, edges)
            elif kind == "lexical_declaration":
                self._extract_lexical(source, child, file_path, parent_id, nodes, edges)
            elif kind == "variable_declaration":
                self._extract_variable(source, child, file_path, parent_id, nodes, edges)
            elif kind == "type_alias_declaration":
                self._extract_type_alias(source, child, file_path, parent_id, nodes, edges)
            elif kind == "enum_declaration":
                self._extract_enum(source, child, file_path, parent_id, nodes, edges)
            elif kind == "interface_declaration":
                self._extract_interface(source, child, file_path, parent_id, nodes, edges)
            else:
                # Recurse deeper for nested export patterns
                # e.g., export default () => {} or export { named }
                self._walk(source, child, file_path, parent_id, nodes, edges)

    # ── Call extraction ─────────────────────────────────────────────────

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

    # ── Helpers ─────────────────────────────────────────────────────────

    def _find_function_name(self, node: Any) -> Any:
        """Try to find function name from parent variable declarator."""
        parent = node.parent
        if parent and parent.type == "variable_declarator":
            return parent.child_by_field_name("name")
        return None

    def _build_ts_signature(self, source: bytes, name: str, node: Any) -> str:
        """Build a TypeScript function/method signature string.

        Extracts parameters and return type from the AST node.
        """
        params_node = node.child_by_field_name("parameters")
        params_str = "(...)"
        if params_node:
            params_str = source[params_node.start_byte : params_node.end_byte].decode(
                "utf-8", errors="replace"
            )
        return_node = node.child_by_field_name("return_type")
        if return_node:
            ret = source[return_node.start_byte : return_node.end_byte].decode(
                "utf-8", errors="replace"
            )
            return f"function {name}{params_str}{ret}"
        return f"function {name}{params_str}"

    def _extract_ts_docstring(self, source: bytes, node: Any) -> str:
        """Extract the JSDoc/TSdoc comment preceding a function/method.

        Looks for comment nodes that immediately precede the function node,
        or the first string literal in the function body.
        """
        # Check for leading comment nodes
        prev = node.prev_sibling
        if prev and prev.type == "comment":
            text = source[prev.start_byte : prev.end_byte].decode("utf-8", errors="replace")
            # Strip // or /* */ markers
            text = text.strip()
            if text.startswith("//"):
                return text[2:].strip()
            if text.startswith("/*"):
                text = text[2:]
                if text.endswith("*/"):
                    text = text[:-2]
                # Remove leading * from each line (JSDoc style)
                lines = text.split("\n")
                cleaned = []
                for line in lines:
                    line = line.strip()
                    if line.startswith("*"):
                        line = line[1:].strip()
                    if line and not line.startswith("@"):  # Skip @param/@returns tags
                        cleaned.append(line)
                return " ".join(cleaned[:5])  # First 5 non-tag lines
            return text

        # Fall back to first statement in body (string literal / expression)
        body = node.child_by_field_name("body")
        if body and body.children:
            first = body.children[0]
            if (
                first.type == "expression_statement"
                and first.children
                and first.children[0].type == "string"
            ):
                raw = source[first.children[0].start_byte : first.children[0].end_byte].decode(
                    "utf-8", errors="replace"
                )
                return raw.strip("'\"").strip()
        return ""

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
