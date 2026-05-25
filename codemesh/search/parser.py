"""FTS5 query parser and search interface."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from codemesh.db.queries import search_nodes_fts
from codemesh.types import Node


@dataclass
class SearchQuery:
    """Parsed search query."""

    raw: str
    fts5_query: str
    symbol: str | None = None
    description: str | None = None


class SearchQueryParser:
    """Parses user queries into FTS5-compatible search queries."""

    SYMBOL_PATTERN = re.compile(
        r"\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b"
        r"|(\b[a-z]+(?:_[a-z]+)+\b)"
        r"|(\b[A-Z]{2,}\b)"
    )

    def parse(self, raw_query: str) -> SearchQuery:
        symbol = self._extract_symbol(raw_query)
        fts5 = self._build_fts5_query(raw_query)
        description = raw_query
        if symbol:
            description = description.replace(symbol, "").strip()
        return SearchQuery(raw=raw_query, fts5_query=fts5, symbol=symbol, description=description)

    def _extract_symbol(self, query: str) -> str | None:
        matches = self.SYMBOL_PATTERN.findall(query)
        for match_group in matches:
            for m in match_group:
                if m and isinstance(m, str):
                    return m
        return None

    def _build_fts5_query(self, raw: str) -> str:
        cleaned = re.sub(r'["^~*(){}\\<>?.,;:!@#$%&]', "", raw)
        stop_words = frozenset(
            {
                "what",
                "which",
                "how",
                "where",
                "when",
                "who",
                "the",
                "a",
                "an",
                "is",
                "are",
                "was",
                "were",
                "do",
                "does",
                "did",
                "in",
                "on",
                "at",
                "to",
                "of",
                "for",
                "with",
                "by",
                "from",
                "as",
                "and",
                "or",
            }
        )
        tokens = [t for t in cleaned.split() if t.lower() not in stop_words and len(t) > 1]
        return " AND ".join(tokens) if tokens else raw


def search(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[tuple[Node, float]]:
    """Convenience function: parse + search in one call."""
    parser = SearchQueryParser()
    parsed = parser.parse(query)
    return search_nodes_fts(conn, parsed.fts5_query, limit)
