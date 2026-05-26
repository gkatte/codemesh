"""Token-budget-aware context builder with deduplication."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from codemesh.types import Node

# Node kinds that provide high information value
_HIGH_VALUE_KINDS = {
    "function", "method", "class", "interface", "type_alias", "struct", "trait",
    "component", "route", "variable", "constant", "enum", "module", "namespace",
}


class ContextFormat(Enum):
    XML = "xml"
    MARKDOWN = "markdown"
    STRUCTURED = "structured"  # Entry Points + Related Symbols + Code


@dataclass
class ContextOptions:
    max_tokens: int = 1200
    max_snippets: int = 3   # Max 3 snippets
    max_lines_per_snippet: int = 10  # Short snippets
    context_margin: int = 0
    max_per_file: int = 1
    max_snippet_chars: int = 600  # Per-snippet cap
    include_graph_summary: bool = False
    format: ContextFormat = ContextFormat.XML
    filter_low_value: bool = True
    max_snippet_chars: int = 800  # Per-snippet cap


@dataclass
class Snippet:
    file_path: Path
    start_line: int
    end_line: int
    code: str
    relevance_score: float
    node_name: str = ""
    source: str = "bm25"  # "bm25" or "graph:calls" or "graph:contains" or "graph:references"
    edge_kind: str = ""  # the edge kind that connected this node (for graph nodes)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _line_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    """Compute fractional overlap between two line ranges."""
    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    if overlap_start >= overlap_end:
        return 0.0
    overlap_len = overlap_end - overlap_start
    min_len = min(a_end - a_start, b_end - b_start)
    return overlap_len / min_len if min_len > 0 else 0.0


class ContextBuilder:
    """Builds token-budget-aware context for LLM agents.

    Deduplicates overlapping snippets. Filters low-value node kinds (imports/exports).
    """

    OVERLAP_THRESHOLD = 0.6

    def __init__(self, conn: sqlite3.Connection, root: Path) -> None:
        self.conn = conn
        self.root = root

    def build(
        self,
        nodes: list[tuple[Node, float]],
        query: str,
        options: ContextOptions | None = None,
        entry_points: list[tuple[Node, float]] | None = None,
        related: list[tuple[Node, float]] | None = None,
    ) -> str:
        if options is None:
            options = ContextOptions()

        snippets = self._extract_snippets(nodes, options)
        deduped = self._deduplicate(snippets)
        total_tokens = 0
        selected: list[Snippet] = []
        file_counts: dict[Path, int] = {}

        for snippet in deduped:
            tokens = estimate_tokens(snippet.code)
            if total_tokens + tokens > options.max_tokens or len(selected) >= options.max_snippets:
                break
            fc = file_counts.get(snippet.file_path, 0)
            if fc >= options.max_per_file:
                continue
            selected.append(snippet)
            total_tokens += tokens
            file_counts[snippet.file_path] = fc + 1

        if options.format == ContextFormat.XML:
            return self._format_xml(selected, query)
        if options.format == ContextFormat.STRUCTURED:
            return self._format_structured(selected, query, entry_points, related)
        return self._format_markdown(selected, query)

    def _extract_snippets(
        self, nodes: list[tuple[Node, float]], options: ContextOptions
    ) -> list[Snippet]:
        snippets: list[Snippet] = []
        for node, score in nodes:
            # Filter low-value node kinds
            if options.filter_low_value and node.kind.value not in _HIGH_VALUE_KINDS:
                continue
            try:
                file_path = (
                    self.root / node.file_path
                    if not node.file_path.is_absolute()
                    else node.file_path
                )
                if not file_path.exists():
                    continue
                lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
                start = max(0, node.start_line - 1 - options.context_margin)
                end = min(len(lines), node.end_line + options.context_margin)
                # Cap snippet size tightly
                if end - start > options.max_lines_per_snippet:
                    node_len = node.end_line - node.start_line + 1
                    extra = options.max_lines_per_snippet - node_len
                    start = max(0, node.start_line - 1 - extra // 2)
                    end = min(len(lines), start + options.max_lines_per_snippet)
                code_lines = lines[start:end]
                code = "\n".join(code_lines)
                # Cap individual snippet char size
                if len(code) > options.max_snippet_chars:
                    truncated_lines = []
                    char_count = 0
                    for line in code_lines:
                        if char_count + len(line) + 1 > options.max_snippet_chars:
                            break
                        truncated_lines.append(line)
                        char_count += len(line) + 1
                    code = "\n".join(truncated_lines)
                snippets.append(
                    Snippet(
                        file_path=node.file_path,
                        start_line=start + 1,
                        end_line=start + len(code_lines),
                        code=code,
                        relevance_score=score,
                        node_name=node.name,
                    )
                )
            except Exception:
                continue
        return snippets

    def _deduplicate(self, snippets: list[Snippet]) -> list[Snippet]:
        """Remove overlapping snippets, keeping the higher-scored one."""
        kept: list[Snippet] = []
        for snippet in snippets:
            is_dup = False
            for existing in kept:
                if existing.file_path != snippet.file_path:
                    continue
                overlap = _line_overlap(
                    existing.start_line,
                    existing.end_line,
                    snippet.start_line,
                    snippet.end_line,
                )
                if overlap >= self.OVERLAP_THRESHOLD:
                    is_dup = True
                    break
            if not is_dup:
                kept.append(snippet)
        return kept

    def _format_xml(self, snippets: list[Snippet], query: str) -> str:
        lines = [f'<code_context query="{xml_escape(query)}">']
        for s in snippets:
            rel = xml_escape(f"{s.relevance_score:.2f}")
            source_attr = xml_escape(s.source)
            lines.append(
                f'  <snippet file="{xml_escape(str(s.file_path))}" '
                f'lines="{s.start_line}-{s.end_line}" relevance="{rel}" source="{source_attr}">'
            )
            if s.node_name:
                lines.append(f"    <!-- {xml_escape(s.node_name)} -->")
            lines.append(f"    {xml_escape(s.code)}")
            lines.append("  </snippet>")
        lines.append("</code_context>")
        return "\n".join(lines)

    def _format_markdown(self, snippets: list[Snippet], query: str) -> str:
        lines = [f"## Code Context: {query}", ""]
        for s in snippets:
            header = f"### {s.file_path}:{s.start_line}-{s.end_line}"
            if s.node_name:
                header += f" ({s.node_name})"
            lines.append(header)
            lines.append("```")
            lines.append(s.code)
            lines.append("```")
            lines.append("")
        return "\n".join(lines)

    def _format_structured(self, snippets: list[Snippet], query: str,
                           entry_points: list[tuple[Node, float]] | None = None,
                           related: list[tuple[Node, float]] | None = None) -> str:
        """Structured output with entry points, related symbols, and code.

        Three sections:
        - Entry Points: BM25-matched symbols with signatures
        - Related Symbols: Graph-walk-discovered symbols
        - Code: Deduplicated code snippets with file:line references
        """
        lines = ["## Code Context", "", f"**Query:** {query}", ""]

        # Entry Points section
        if entry_points:
            lines.append("### Entry Points")
            lines.append("")
            for node, score in entry_points[:5]:
                sig = node.signature or ""
                vis = f" ({node.visibility})" if node.visibility != "public" else ""
                async_tag = " (async)" if node.is_async else ""
                exported_tag = " (exported)" if node.is_exported else ""
                lines.append(f"- **{node.name}** ({node.kind.value}){vis}{async_tag}{exported_tag} - {node.file_path}:{node.start_line}")
                if sig:
                    lines.append(f"  `{sig[:120]}`")
                if node.docstring:
                    lines.append(f"  {node.docstring[:100]}")
            lines.append("")

        # Related Symbols section
        if related:
            lines.append("### Related Symbols")
            lines.append("")
            seen_files: set[str] = set()
            for node, score in related[:15]:
                file_key = f"{node.file_path}:{node.name}"
                if file_key in seen_files:
                    continue
                seen_files.add(file_key)
                lines.append(f"- {node.file_path}: {node.name} ({node.kind.value})")
            lines.append("")

        # Code section
        if snippets:
            lines.append("### Code")
            lines.append("")
            for s in snippets:
                header = f"#### {s.file_path}:{s.start_line}-{s.end_line}"
                if s.node_name:
                    header += f" ({s.node_name})"
                if s.source and s.source != "bm25":
                    header += f" [{s.source}]"
                lines.append(header)
                lines.append("```")
                lines.append(s.code)
                lines.append("```")
                lines.append("")

        return "\n".join(lines)
