"""Main reference resolver: resolves unresolved edges to concrete node IDs."""

from __future__ import annotations

import logging
import sqlite3

from codemesh.types import EdgeKind

logger = logging.getLogger(__name__)


class ReferenceResolver:
    """Resolves unresolved references in the knowledge graph."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._build_lookup()

    def resolve_all(self) -> int:
        """Resolve all unresolved edges. Returns number of edges resolved."""
        from codemesh.db.queries import get_all_edges

        edges = get_all_edges(self.conn)
        unresolved = [e for e in edges if e.target_id.startswith("unresolved:")]
        resolved_count = 0

        for edge in unresolved:
            target_name = edge.target_id[len("unresolved:") :]
            resolved_id = self._resolve_reference(target_name, edge.source_id, edge.kind)
            if resolved_id:
                self.conn.execute(
                    "UPDATE edges SET target_id=?, confidence=1.0 WHERE id=?",
                    (resolved_id, edge.id),
                )
                resolved_count += 1

        logger.info("Resolved %d/%d unresolved edges", resolved_count, len(unresolved))
        return resolved_count

    def _build_lookup(self) -> None:
        """Build in-memory lookup maps for fast resolution."""
        from codemesh.db.queries import get_all_nodes

        self._by_name: dict[str, list[str]] = {}
        self._by_qualified: dict[str, str] = {}

        for node in get_all_nodes(self.conn):
            self._by_name.setdefault(node.name, []).append(node.id)
            self._by_qualified[node.qualified_name] = node.id

    def _resolve_reference(self, name: str, source_id: str, kind: EdgeKind) -> str | None:
        """Resolve a reference by name. Returns node ID or None."""
        if name in self._by_qualified:
            return self._by_qualified[name]

        candidates = self._by_name.get(name, [])
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return self._disambiguate(candidates, source_id)

        # Fuzzy match
        from codemesh.resolution.name_matcher import fuzzy_match

        match = fuzzy_match(name, list(self._by_name.keys()))
        if match and match in self._by_name:
            cands = self._by_name[match]
            return cands[0] if len(cands) == 1 else self._disambiguate(cands, source_id)

        return None

    def _disambiguate(self, candidates: list[str], source_id: str) -> str | None:
        """Disambiguate between multiple candidate nodes."""
        if not candidates:
            return None

        from codemesh.db.queries import get_node

        source = get_node(self.conn, source_id)
        if source is None:
            return candidates[0]

        # Prefer same file
        for cid in candidates:
            node = get_node(self.conn, cid)
            if node and node.file_path == source.file_path:
                return cid

        # Prefer same language
        for cid in candidates:
            node = get_node(self.conn, cid)
            if node and node.language == source.language:
                return cid

        return candidates[0]
