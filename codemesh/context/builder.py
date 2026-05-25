"""Token-budget-aware context builder."""

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


class ContextBuilder:
    """Builds token-budget-aware context for LLM agents."""

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
        total_tokens = 0
        selected: list[Snippet] = []

        for snippet in snippets:
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

    def _format_xml(self, snippets: list[Snippet], query: str) -> str:
        lines = [f'<code_context query="{xml_escape(query)}">']
        for s in snippets:
            lines.append(
                f'  <snippet file="{xml_escape(str(s.file_path))}" '
                f'lines="{s.start_line}-{s.end_line}" relevance="{s.relevance_score:.2f}">'
            )
            lines.append(f"    {xml_escape(s.code)}")
            lines.append("  </snippet>")
        lines.append("</code_context>")
        return "\n".join(lines)

    def _format_markdown(self, snippets: list[Snippet], query: str) -> str:
        lines = [f"## Code Context: {query}", ""]
        for s in snippets:
            lines.append(f"### {s.file_path}:{s.start_line}-{s.end_line}")
            lines.append("```")
            lines.append(s.code)
            lines.append("```")
            lines.append("")
        return "\n".join(lines)
