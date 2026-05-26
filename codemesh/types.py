"""Core type definitions for CodeMesh.

All shared types (Node, Edge, enums) are defined here as dataclasses.
Every module in codemesh imports from this file.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal


class NodeKind(enum.Enum):
    """Types of code symbols that can appear in the knowledge graph."""

    FUNCTION = "function"
    METHOD = "method"
    CLASS = "class"
    INTERFACE = "interface"
    VARIABLE = "variable"
    CONSTANT = "constant"
    IMPORT = "import"
    MODULE = "module"
    FILE = "file"
    TYPE_ALIAS = "type_alias"
    ENUM = "enum"
    STRUCT = "struct"
    TRAIT = "trait"
    DECORATOR = "decorator"
    PARAMETER = "parameter"
    PROPERTY = "property"
    UNKNOWN = "unknown"


class EdgeKind(enum.Enum):
    """Types of relationships between code symbols."""

    CONTAINS = "contains"  # Parent-child (file contains function)
    CALLS = "calls"  # Function call relationship
    IMPORTS = "imports"  # Import/dependency
    EXTENDS = "extends"  # Inheritance
    IMPLEMENTS = "implements"  # Interface implementation
    TYPE_OF = "type_of"  # Type annotation
    RETURNS = "returns"  # Return type
    REFERENCES = "references"  # Generic reference
    INSTANTIATES = "instantiates"  # Creates an instance
    OVERRIDES = "overrides"  # Method override
    EXPORTS = "exports"  # Module export
    DECORATES = "decorates"  # Decorator application


class Language(enum.Enum):
    """Supported programming languages."""

    PYTHON = "python"
    TYPESCRIPT = "typescript"
    JAVASCRIPT = "javascript"
    RUST = "rust"
    GO = "go"
    JAVA = "java"
    CPP = "cpp"
    C = "c"
    RUBY = "ruby"
    UNKNOWN = "unknown"


class QueryType(enum.Enum):
    """Query classification types for retrieval routing."""

    STRUCTURAL = "structural"  # "What calls this function?" → Graph walk priority
    SEMANTIC = "semantic"  # "authentication middleware" → Vector search priority
    HYBRID = "hybrid"  # Both signals equally
    DEFINITION = "definition"  # Direct KG lookup for symbol definition


@dataclass(frozen=True, slots=True)
class Node:
    """A code symbol in the knowledge graph."""

    id: str  # SHA256(file_path:start_line:end_line)
    kind: NodeKind
    name: str
    qualified_name: str  # e.g., "module.Class.method"
    file_path: Path
    language: Language
    start_line: int
    end_line: int
    start_column: int = 0
    end_column: int = 0
    docstring: str = ""
    signature: str = ""
    visibility: str = "public"  # public, private, protected
    parent_id: str | None = None  # ID of parent node
    metadata: dict[str, str] = field(default_factory=dict)
    is_exported: bool = False
    is_async: bool = False
    is_static: bool = False
    is_abstract: bool = False


@dataclass(frozen=True, slots=True)
class Edge:
    """A relationship between two code symbols."""

    id: str  # SHA256(source_id:target_id:kind)
    source_id: str  # Node ID
    target_id: str  # Node ID
    kind: EdgeKind
    confidence: float = 1.0  # 0.0 to 1.0
    weight_source: Literal["ast", "learned", "hybrid"] = "ast"
    line: int = 0
    column: int = 0
    metadata: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ScoredNode:
    """A node with a relevance score from retrieval."""

    node: Node
    score: float
    source: Literal["graph_walk", "semantic", "fusion", "reranker"] = "fusion"


@dataclass
class SearchFilters:
    """Filters for retrieval queries."""

    kinds: list[NodeKind] = field(default_factory=list)
    languages: list[Language] = field(default_factory=list)
    file_patterns: list[str] = field(default_factory=list)  # glob patterns
    exclude_patterns: list[str] = field(default_factory=list)


# Type aliases
type NodeId = str
type EdgeId = str
type FilePath = Path
