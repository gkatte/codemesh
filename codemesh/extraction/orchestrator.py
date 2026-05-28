"""Extraction orchestrator: coordinates parallel parsing of source files."""

from __future__ import annotations

import hashlib
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import tree_sitter

from codemesh.types import Edge, Language, Node

logger = logging.getLogger(__name__)

# Language detection by file extension
EXTENSION_MAP: dict[str, Language] = {
    ".py": Language.PYTHON,
    ".pyw": Language.PYTHON,
    ".ts": Language.TYPESCRIPT,
    ".tsx": Language.TYPESCRIPT,
    ".js": Language.JAVASCRIPT,
    ".jsx": Language.JAVASCRIPT,
    ".rs": Language.RUST,
    ".go": Language.GO,
    ".java": Language.JAVA,
    ".kt": Language.KOTLIN,
    ".kts": Language.KOTLIN,
    ".cpp": Language.CPP,
    ".c": Language.C,
    ".h": Language.C,
    ".hpp": Language.CPP,
    ".rb": Language.RUBY,
    ".swift": Language.SWIFT,
}

# Directories to skip
SKIP_DIRS = frozenset(
    {
        ".git",
        ".svn",
        ".hg",
        "node_modules",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".codemesh",
        "target",
        ".idea",
        ".vscode",
        "coverage",
        "htmlcov",
    }
)


def detect_language(file_path: Path) -> Language:
    """Detect programming language from file extension."""
    return EXTENSION_MAP.get(file_path.suffix, Language.UNKNOWN)


def is_source_file(file_path: Path) -> bool:
    """Check if a file is a source file we should parse."""
    return file_path.suffix in EXTENSION_MAP


def should_skip_dir(dir_name: str) -> bool:
    """Check if a directory should be skipped during traversal."""
    return dir_name in SKIP_DIRS or dir_name.startswith(".")


def discover_files(root: Path) -> list[Path]:
    """Discover all source files under a root directory."""
    files: list[Path] = []
    for dirpath, dirnames, filenames in root.walk():
        # Skip unwanted directories in-place
        dirnames[:] = [d for d in dirnames if not should_skip_dir(d)]
        for fname in filenames:
            fpath = dirpath / fname
            if is_source_file(fpath):
                files.append(fpath)
    return files


def compute_node_id(file: Path, start_line: int, end_line: int) -> str:
    """Compute deterministic node ID."""
    raw = f"{file}:{start_line}:{end_line}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def compute_edge_id(source: str, target: str, kind: str) -> str:
    """Compute deterministic edge ID."""
    raw = f"{source}:{target}:{kind}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class ExtractionOrchestrator:
    """Orchestrates parallel extraction of code symbols from source files."""

    def __init__(self, root: Path, max_workers: int | None = None) -> None:
        self.root = root
        self.max_workers = max_workers

    def extract_all(self) -> tuple[list[Node], list[Edge]]:
        """Extract all nodes and edges from the project.

        Returns (nodes, edges) tuples.
        """
        files = discover_files(self.root)
        logger.info("Discovered %d source files in %s", len(files), self.root)

        all_nodes: list[Node] = []
        all_edges: list[Edge] = []

        # Use ThreadPoolExecutor instead of ProcessPoolExecutor.
        # ProcessPoolExecutor on macOS can deadlock/fork-bomb when the main
        # process has large memory-mapped libraries (tree-sitter, etc.).
        # Tree-sitter parsing is fast enough that GIL contention is not a bottleneck.
        workers = self.max_workers or min(8, len(files)) or 1
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_parse_file, f): f for f in files}
            for future in as_completed(futures):
                file_path = futures[future]
                try:
                    nodes, edges = future.result()
                    all_nodes.extend(nodes)
                    all_edges.extend(edges)
                except Exception as e:
                    logger.warning("Failed to parse %s: %s", file_path, e)

        logger.info("Extracted %d nodes, %d edges", len(all_nodes), len(all_edges))
        return all_nodes, all_edges


def _make_parser(language: Language) -> tree_sitter.Parser | None:
    """Create a tree-sitter parser for the given language."""
    import tree_sitter

    try:
        if language == Language.PYTHON:
            import tree_sitter_python

            lang = tree_sitter.Language(tree_sitter_python.language())
        elif language == Language.TYPESCRIPT:
            import tree_sitter_typescript

            lang = tree_sitter.Language(tree_sitter_typescript.language_typescript())
        elif language == Language.RUST:
            import tree_sitter_rust

            lang = tree_sitter.Language(tree_sitter_rust.language())
        elif language == Language.JAVASCRIPT:
            import tree_sitter_typescript

            lang = tree_sitter.Language(tree_sitter_typescript.language_tsx())
        elif language == Language.GO:
            import tree_sitter_go

            lang = tree_sitter.Language(tree_sitter_go.language())
        elif language == Language.JAVA:
            import tree_sitter_java

            lang = tree_sitter.Language(tree_sitter_java.language())
        elif language == Language.KOTLIN:
            import tree_sitter_kotlin

            lang = tree_sitter.Language(tree_sitter_kotlin.language())
        elif language == Language.SWIFT:
            import tree_sitter_swift

            lang = tree_sitter.Language(tree_sitter_swift.language())
        elif language == Language.C:
            import tree_sitter_c

            lang = tree_sitter.Language(tree_sitter_c.language())
        elif language == Language.CPP:
            import tree_sitter_cpp

            lang = tree_sitter.Language(tree_sitter_cpp.language())
        else:
            return None
        return tree_sitter.Parser(lang)
    except Exception:
        return None


def _parse_file(file_path: Path) -> tuple[list[Node], list[Edge]]:
    """Parse a single file. Entry point for ProcessPoolExecutor."""
    lang = detect_language(file_path)
    if lang == Language.UNKNOWN:
        return [], []

    parser = _make_parser(lang)
    if parser is None:
        return [], []

    source = file_path.read_bytes()
    tree = parser.parse(source)
    if tree is None:
        return [], []
    root_node = tree.root_node

    # Dispatch to language-specific extractor
    from codemesh.extraction.languages import get_extractor

    extractor = get_extractor(lang)
    if extractor is None:
        return [], []

    return extractor.extract(file_path, source, root_node, lang)
