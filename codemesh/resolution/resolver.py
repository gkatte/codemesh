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

    def resolve_call_types(self) -> int:
        """Cross-file type inference pass for CALLS edges.

        Populates resolved_target and type_context on CALLS edges using 3 strategies.
        Only sets resolved_target when resolution is unambiguous.
        """
        from codemesh.db.queries import get_all_edges, get_all_nodes, get_node

        # Pre-load all nodes into memory for fast lookup
        all_nodes = get_all_nodes(self.conn)
        by_id = {n.id: n for n in all_nodes}
        by_name: dict[str, list] = {}
        for n in all_nodes:
            by_name.setdefault(n.name, []).append(n)

        edges = get_all_edges(self.conn)
        call_edges = [e for e in edges if e.kind == EdgeKind.CALLS
                      and not e.target_id.startswith("unresolved:")]
        updated = 0

        for edge in call_edges:
            source = by_id.get(edge.source_id)
            target = by_id.get(edge.target_id)
            if source is None or target is None:
                continue

            resolved_target = None
            type_context = {}

            # Strategy 1: receiver type from explicit annotation
            receiver_type = self._infer_receiver_type_fast(edge.source_id, by_id)

            # Strategy 2: receiver type from data_flow edges
            if not receiver_type:
                receiver_type = self._infer_type_from_dataflow_fast(edge.source_id, by_id)

            # Strategy 3: import-based disambiguation
            if not receiver_type:
                imported = self._find_imported_symbol_fast(source, target.name, by_id)
                if imported:
                    resolved_target = imported
                    type_context = {"source": "import"}

            # If we have a receiver type, look for matching method
            if receiver_type and not resolved_target:
                candidates = [n for n in by_name.get(target.name, [])
                              if n.kind.value in ("method", "function")
                              and n.qualified_name.startswith(receiver_type + ".")]
                if len(candidates) == 1:
                    resolved_target = candidates[0].qualified_name
                    type_context = {"receiver": receiver_type, "source": "type_annotation"}
                elif len(candidates) > 1:
                    type_context = {"receiver": receiver_type,
                                    "ambiguous": [c.qualified_name for c in candidates],
                                    "source": "type_annotation"}

            if resolved_target or type_context:
                import json
                self.conn.execute(
                    "UPDATE edges SET resolved_target=?, type_context=? WHERE id=?",
                    (resolved_target or "", json.dumps(type_context) if type_context else "",
                     edge.id),
                )
                updated += 1

        self.conn.commit()
        logger.info("Type resolution: updated %d/%d call edges", updated, len(call_edges))
        return updated

    def _infer_receiver_type_fast(self, node_id: str, by_id: dict) -> str | None:
        """Strategy 1: infer receiver type from type annotations."""
        import re
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE target_id = ? AND kind = 'reads'",
            (node_id,)
        ).fetchall()
        for row in rows:
            src = by_id.get(row["source_id"])
            if src and src.signature:
                match = re.search(r":\s*([A-Z]\w+)", src.signature)
                if match:
                    return match.group(1)
        return None

    def _infer_type_from_dataflow_fast(self, node_id: str, by_id: dict) -> str | None:
        """Strategy 2: infer receiver type from data_flow edges."""
        import re
        rows = self.conn.execute(
            "SELECT * FROM edges WHERE target_id = ? AND kind IN ('data_flow', 'reads')",
            (node_id,)
        ).fetchall()
        for row in rows:
            src = by_id.get(row["source_id"])
            if src and src.kind.value in ("variable", "parameter"):
                if src.signature:
                    match = re.search(r":\s*([A-Z]\w+)", src.signature)
                    if match:
                        return match.group(1)
        return None

    def _find_imported_symbol_fast(self, source_node, name: str, by_id: dict) -> str | None:
        """Strategy 3: find symbol imported from exactly one module."""
        rows = self.conn.execute(
            """
            SELECT n.qualified_name
            FROM edges e
            JOIN nodes n ON e.target_id = n.id
            WHERE e.source_id = ? AND e.kind = 'imports' AND n.name = ?
            """,
            (source_node.id, name),
        ).fetchall()
        if len(rows) == 1:
            return rows[0][0]
        return None
