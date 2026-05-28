"""Main reference resolver: resolves unresolved edges to concrete node IDs."""

from __future__ import annotations

import json
import logging
import re
import sqlite3

from codemesh.types import EdgeKind, Node

logger = logging.getLogger(__name__)


class ReferenceResolver:
    """Resolves unresolved references in the knowledge graph."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        self._build_lookup()

    def resolve_all(self) -> int:
        """Resolve all unresolved edges. Returns number of edges resolved.

        Fully in-memory: no per-edge DB queries, single batch UPDATE.
        """
        unresolved = [e for e in self._all_edges if e.target_id.startswith("unresolved:")]
        if not unresolved:
            return 0

        updates: list[tuple[str, str]] = []
        unresolved_prefix_len = len("unresolved:")

        for edge in unresolved:
            target_name = edge.target_id[unresolved_prefix_len:]

            # O(1) qualified name lookup
            resolved_id = self._by_qualified.get(target_name)
            if resolved_id:
                updates.append((resolved_id, edge.id))
                continue

            # O(1) name lookup
            candidates = self._by_name.get(target_name, [])
            if len(candidates) == 1:
                updates.append((candidates[0], edge.id))
            elif len(candidates) > 1:
                # Disambiguate using in-memory lookups
                src_node = self._by_id.get(edge.source_id)
                if src_node:
                    src_file = str(src_node.file_path)
                    src_lang = src_node.language.value
                    best = None
                    for cid in candidates:
                        cnode = self._by_id.get(cid)
                        if cnode and str(cnode.file_path) == src_file:
                            best = cid
                            break
                    if best is None:
                        for cid in candidates:
                            cnode = self._by_id.get(cid)
                            if cnode and cnode.language.value == src_lang:
                                best = cid
                                break
                    resolved_id = best if best else candidates[0]
                else:
                    resolved_id = candidates[0]
                updates.append((resolved_id, edge.id))
            # else: fuzzy match — skip for speed (rare case)

        # Batch update all resolved edges
        if updates:
            self.conn.executemany(
                "UPDATE edges SET target_id=?, confidence=1.0 WHERE id=?",
                updates,
            )

        logger.info("Resolved %d/%d unresolved edges", len(updates), len(unresolved))
        return len(updates)

    def _build_lookup(self) -> None:
        """Build in-memory lookup maps for fast resolution."""
        from codemesh.db.queries import get_all_edges, get_all_nodes

        self._by_name: dict[str, list[str]] = {}
        self._by_qualified: dict[str, str] = {}
        self._by_id: dict[str, Node] = {}

        for node in get_all_nodes(self.conn):
            self._by_name.setdefault(node.name, []).append(node.id)
            self._by_qualified[node.qualified_name] = node.id
            self._by_id[node.id] = node

        # Also pre-compute all edges for fast lookups in resolve_all
        self._all_edges = get_all_edges(self.conn)

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
        """Disambiguate between multiple candidate nodes using in-memory lookup."""
        if not candidates:
            return None

        source = self._by_id.get(source_id)
        if source is None:
            return candidates[0]

        src_file = str(source.file_path)
        src_lang = source.language.value

        # Prefer same file
        for cid in candidates:
            cnode = self._by_id.get(cid)
            if cnode and str(cnode.file_path) == src_file:
                return cid

        # Prefer same language
        for cid in candidates:
            cnode = self._by_id.get(cid)
            if cnode and cnode.language.value == src_lang:
                return cid

        return candidates[0]

    def resolve_call_types(self) -> int:
        """Cross-file type inference pass for CALLS edges.

        Populates resolved_target and type_context on CALLS edges using 3 strategies.
        Only sets resolved_target when resolution is unambiguous.
        Optimized: batch updates via executemany, zero DB queries in hot loop.
        """
        from codemesh.db.queries import get_all_edges, get_all_nodes

        # Pre-load all nodes into memory for fast lookup
        all_nodes = get_all_nodes(self.conn)
        by_id = {n.id: n for n in all_nodes}
        by_name: dict[str, list] = {}
        for n in all_nodes:
            by_name.setdefault(n.name, []).append(n)

        edges = get_all_edges(self.conn)
        call_edges = [
            e
            for e in edges
            if e.kind == EdgeKind.CALLS and not e.target_id.startswith("unresolved:")
        ]

        # Pre-compute edges-by-target and imports-by-source for O(1) lookups
        self._edges_by_target: dict[str, list] = {}
        imports_by_source: dict[str, dict[str, str]] = {}
        for e in edges:
            self._edges_by_target.setdefault(e.target_id, []).append(e)
            if e.kind == "imports":
                src = by_id.get(e.source_id)
                tgt = by_id.get(e.target_id)
                if src and tgt:
                    imports_by_source.setdefault(src.id, {})[tgt.name] = tgt.qualified_name

        updates: list[tuple[str, str, str]] = []
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

            # Strategy 3: import-based disambiguation (O(1) lookup)
            if not receiver_type:
                imported = imports_by_source.get(source.id, {}).get(target.name)
                if imported:
                    resolved_target = imported
                    type_context = {"source": "import"}

            # If we have a receiver type, look for matching method
            if receiver_type and not resolved_target:
                candidates = [
                    n
                    for n in by_name.get(target.name, [])
                    if n.kind.value in ("method", "function")
                    and n.qualified_name.startswith(receiver_type + ".")
                ]
                if len(candidates) == 1:
                    resolved_target = candidates[0].qualified_name
                    type_context = {"receiver": receiver_type, "source": "type_annotation"}
                elif len(candidates) > 1:
                    type_context = {
                        "receiver": receiver_type,
                        "ambiguous": [c.qualified_name for c in candidates],
                        "source": "type_annotation",
                    }

            if resolved_target or type_context:
                updates.append(
                    (
                        resolved_target or "",
                        json.dumps(type_context) if type_context else "",
                        edge.id,
                    )
                )
                updated += 1

        # Batch update all call edges
        if updates:
            self.conn.executemany(
                "UPDATE edges SET resolved_target=?, type_context=? WHERE id=?",
                updates,
            )

        self.conn.commit()
        logger.info("Type resolution: updated %d/%d call edges", updated, len(call_edges))
        return updated

    def _infer_receiver_type_fast(self, node_id: str, by_id: dict) -> str | None:
        """Strategy 1: infer receiver type from type annotations.
        Uses pre-loaded by_id map — no DB queries.
        """
        # Check for 'reads' edges targeting this node
        for edge in self._edges_by_target.get(node_id, []):
            if edge.kind == "reads":
                src = by_id.get(edge.source_id)
                if src and src.signature:
                    match = re.search(r":\s*([A-Z]\w+)", src.signature)
                    if match:
                        return match.group(1)
        return None

    def _infer_type_from_dataflow_fast(self, node_id: str, by_id: dict) -> str | None:
        """Strategy 2: infer receiver type from data_flow edges.
        Uses pre-loaded by_id map — no DB queries.
        """
        for edge in self._edges_by_target.get(node_id, []):
            if edge.kind in ("data_flow", "reads"):
                src = by_id.get(edge.source_id)
                if src and src.kind.value in ("variable", "parameter") and src.signature:
                    match = re.search(r":\s*([A-Z]\w+)", src.signature)
                    if match:
                        return match.group(1)
        return None
