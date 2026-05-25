"""Token-budget-aware context builder with deduplication."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from codemesh.types import Node


class ContextFormat(Enum):
    XML = "xml"
    MARKDOWN = "markdown"


@dataclass
class ContextOptions:
    max_tokens: int = 8000
    max_snippets: int = 20
    context_margin: int = 3
    include_graph_summary: bool = True
    format: ContextFormat = ContextFormat.XML


@dataclass
class Snippet:
    file_path: Path
    start_line: int
    end_line: int
    code: str
    relevance_score: float
    node_name: str = ""


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _line_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> float:
    """Compute fractional overlap between two line ranges.

    Returns the fraction of the smaller range that overlaps with the larger.
    0.0 = no overlap, 1.0 = complete overlap.
    """
    overlap_start = max(a_start, b_start)
    overlap_end = min(a_end, b_end)
    if overlap_start >= overlap_end:
        return 0.0
    overlap_len = overlap_end - overlap_start
    min_len = min(a_end - a_start, b_end - b_start)
    return overlap_len / min_len if min_len > 0 else 0.0


class ContextBuilder:
    """Builds token-budget-aware context for LLM agents.

    Deduplicates overlapping snippets: when two snippets cover substantially
    the same lines in the same file, only the higher-scored one is kept.
    """

    OVERLAP_THRESHOLD = 0.6  # If >60% of lines overlap, treat as duplicate

    def __init__(self, conn: sqlite3.Connection, root: Path) -> None:
        self.conn = conn
        self.root = root

    def build(
        self,
        nodes: list[tuple[Node, float]],
        query: str,
        options: ContextOptions | None = None,
    ) -> str:
        if options is None:
            options = ContextOptions()

        snippets = self._extract_snippets(nodes, options)
        deduped = self._deduplicate(snippets)
        total_tokens = 0
        selected: list[Snippet] = []

        for snippet in deduped:
            tokens = estimate_tokens(snippet.code)
            if total_tokens + tokens > options.max_tokens or len(selected) >= options.max_snippets:
                break
            selected.append(snippet)
            total_tokens += tokens

        if options.format == ContextFormat.XML:
            return self._format_xml(selected, query)
        return self._format_markdown(selected, query)

    def _extract_snippets(
        self, nodes: list[tuple[Node, float]], options: ContextOptions
    ) -> list[Snippet]:
        snippets: list[Snippet] = []
        for node, score in nodes:
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
                code_lines = lines[start:end]
                code = "\n".join(
                    f"{start + i + 1:4d} | {line}" for i, line in enumerate(code_lines)
                )
                snippets.append(
                    Snippet(
                        file_path=node.file_path,
                        start_line=start + 1,
                        end_line=end,
                        code=code,
                        relevance_score=score,
                        node_name=node.name,
                    )
                )
            except Exception:
                continue
        return snippets

    def _deduplicate(self, snippets: list[Snippet]) -> list[Snippet]:
        """Remove overlapping snippets, keeping the higher-scored one.

        Snippets are processed in order of relevance_score (already sorted
        by the caller). For each pair of snippets from the same file, if
        the line overlap exceeds the threshold, the lower-scored snippet
        is dropped.
        """
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
            lines.append(
                f'  <snippet file="{xml_escape(str(s.file_path))}" '
                f'lines="{s.start_line}-{s.end_line}" relevance="{rel}">'
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
