"""Database query helpers: node CRUD, FTS5 search with BM25 scoring."""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

from codemesh.types import Language, Node, NodeKind


def row_to_node(row: sqlite3.Row) -> Node:
    """Convert a database row to a Node."""
    return Node(
        id=row["id"],
        kind=NodeKind(row["kind"]),
        name=row["name"],
        qualified_name=row["qualified_name"],
        file_path=Path(row["file_path"]),
        language=Language(row["language"]),
        start_line=row["start_line"],
        end_line=row["end_line"],
        start_column=row["start_column"],
        end_column=row["end_column"],
        docstring=row["docstring"] or "",
        signature=row["signature"] or "",
        visibility=row["visibility"] or "public",
        parent_id=row["parent_id"],
    )


def get_node(conn: sqlite3.Connection, node_id: str) -> Node | None:
    """Fetch a node by ID."""
    row = conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    return row_to_node(row) if row else None


def get_all_nodes(conn: sqlite3.Connection) -> list[Node]:
    """Fetch all nodes."""
    rows = conn.execute("SELECT * FROM nodes").fetchall()
    return [row_to_node(r) for r in rows]


def get_all_node_names(conn: sqlite3.Connection) -> list[str]:
    """Fetch all distinct node names (for fuzzy fallback)."""
    rows = conn.execute("SELECT DISTINCT name FROM nodes").fetchall()
    return [r[0] for r in rows]


def get_all_edges(conn: sqlite3.Connection) -> list:
    """Fetch all edges."""
    from codemesh.types import Edge, EdgeKind
    rows = conn.execute("SELECT * FROM edges").fetchall()
    edges = []
    for r in rows:
        try:
            edges.append(Edge(
                id=r["id"],
                source_id=r["source_id"],
                target_id=r["target_id"],
                kind=EdgeKind(r["kind"]),
                confidence=r["confidence"],
                weight_source=r["weight_source"],
                line=r["line"],
                column=r["column"],
            ))
        except Exception:
            pass
    return edges


def get_edges_by_source(conn: sqlite3.Connection, source_id: str) -> list:
    """Fetch edges by source node ID."""
    rows = conn.execute(
        "SELECT * FROM edges WHERE source_id = ?", (source_id,)
    ).fetchall()
    from codemesh.types import Edge, EdgeKind
    edges = []
    for r in rows:
        try:
            edges.append(Edge(
                id=r["id"], source_id=r["source_id"], target_id=r["target_id"],
                kind=EdgeKind(r["kind"]), confidence=r["confidence"],
                weight_source=r["weight_source"], line=r["line"], column=r["column"],
            ))
        except Exception:
            pass
    return edges


def get_edges_by_target(conn: sqlite3.Connection, target_id: str) -> list:
    """Fetch edges by target node ID."""
    rows = conn.execute(
        "SELECT * FROM edges WHERE target_id = ?", (target_id,)
    ).fetchall()
    from codemesh.types import Edge, EdgeKind
    edges = []
    for r in rows:
        try:
            edges.append(Edge(
                id=r["id"], source_id=r["source_id"], target_id=r["target_id"],
                kind=EdgeKind(r["kind"]), confidence=r["confidence"],
                weight_source=r["weight_source"], line=r["line"], column=r["column"],
            ))
        except Exception:
            pass
    return edges


def count_nodes(conn: sqlite3.Connection) -> int:
    """Count total nodes."""
    row = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()
    return row[0] if row else 0


def count_edges(conn: sqlite3.Connection) -> int:
    """Count total edges."""
    row = conn.execute("SELECT COUNT(*) FROM edges").fetchone()
    return row[0] if row else 0


def insert_node(conn: sqlite3.Connection, node: Node) -> None:
    """Insert or replace a node."""
    conn.execute(
        """
        INSERT OR REPLACE INTO nodes
            (id, kind, name, qualified_name, file_path, language,
             start_line, end_line, start_column, end_column,
             docstring, signature, visibility, parent_id, metadata)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            node.id, node.kind.value, node.name, node.qualified_name,
            str(node.file_path), node.language.value,
            node.start_line, node.end_line,
            node.start_column, node.end_column,
            node.docstring, node.signature, node.visibility,
            node.parent_id, "{}",
        ),
    )


def insert_edge(conn: sqlite3.Connection, edge) -> None:
    """Insert or replace an edge. Accepts an Edge object or individual params."""
    from codemesh.types import Edge
    if isinstance(edge, Edge):
        conn.execute(
            """
            INSERT OR REPLACE INTO edges
                (id, source_id, target_id, kind, confidence,
                 weight_source, line, column, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                edge.id, edge.source_id, edge.target_id, edge.kind.value,
                edge.confidence, edge.weight_source, edge.line,
                edge.column, "{}",
            ),
        )
    else:
        # Backward-compatible: treat as edge_id (legacy callers)
        pass


# ── FTS5 BM25 Search ─────────────────────────────────────────────────────────

# Stop words to filter from search queries
_STOP_WORDS = {
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "it", "that", "this", "are", "was",
    "be", "has", "had", "have", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "not", "no", "all", "each",
    "every", "how", "what", "where", "when", "who", "which", "why",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "they",
    "show", "give", "tell",
    "been", "done", "made", "used", "using", "work", "works", "found",
    "also", "into", "then", "than", "just", "more", "some", "such",
    "over", "only", "out", "its", "so", "up", "as", "if",
    "look", "need", "needs", "want", "happen", "happens",
    "affect", "affected", "break", "breaks", "failing",
    "implemented", "implement",
    "code", "file", "files", "function", "method", "class", "type",
    "fix", "bug", "called",
}


def _split_camel_case(text: str) -> str:
    """Split camelCase/PascalCase into space-separated words.
    E.g., 'getUserName' → 'get User Name'
    """
    # Insert space between lowercase and uppercase
    text = re.sub(r"([a-z])([A-Z])", r"\1 \2", text)
    # Insert space between consecutive uppercase and uppercase+lowercase
    text = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", text)
    return text


def _get_stem_variants(term: str) -> list[str]:
    """Generate stem variants for FTS prefix matching.
    E.g., 'caching' → ['cach', 'cache'], 'eviction' → ['evict']
    """
    variants = []
    t = term.lower()
    if len(t) <= 3:
        return variants

    # -ing: caching→cach/cache, handling→handl/handle
    if t.endswith("ing") and len(t) > 5:
        base = t[:-3]
        variants.append(base)
        variants.append(base + "e")
        if len(base) >= 2 and base[-1] == base[-2]:
            variants.append(base[:-1])

    # -tion/-sion: eviction→evict
    if (t.endswith("tion") or t.endswith("sion")) and len(t) > 5:
        variants.append(t[:-3])

    # -ment: management→manage
    if t.endswith("ment") and len(t) > 6:
        variants.append(t[:-4])

    # -ies: entries→entry
    if t.endswith("ies") and len(t) > 4:
        variants.append(t[:-3] + "y")
    # -es: processes→process
    elif t.endswith("es") and len(t) > 4:
        variants.append(t[:-2])
    # -s: errors→error (skip -ss endings)
    elif t.endswith("s") and not t.endswith("ss") and len(t) > 4:
        variants.append(t[:-1])

    # -ed: handled→handle
    if t.endswith("ed") and not t.endswith("eed") and len(t) > 4:
        variants.append(t[:-1])
        variants.append(t[:-2])
        if t.endswith("ied") and len(t) > 5:
            variants.append(t[:-3] + "y")

    # -er: builder→build
    if t.endswith("er") and len(t) > 4:
        base = t[:-2]
        variants.append(base)
        variants.append(base + "e")
        if len(base) >= 2 and base[-1] == base[-2]:
            variants.append(base[:-1])

    return [v for v in variants if len(v) >= 3 and v != t]


def _extract_search_terms(query: str) -> list[str]:
    """Extract meaningful search terms from a natural language query.
    Splits camelCase, PascalCase, snake_case into individual tokens.
    Filters stop words. Generates stem variants for FTS prefix matching.
    """
    tokens: set[str] = set()

    # Preserve compound identifiers before splitting
    for m in re.finditer(r"\b([a-zA-Z][a-zA-Z0-9]*(?:[A-Z][a-z]+)+)\b", query):
        if m.group(1) and len(m.group(1)) >= 3:
            tokens.add(m.group(1).lower())

    # Split camelCase/PascalCase
    camel_split = _split_camel_case(query)
    # Replace underscores and dots with spaces
    normalised = re.sub(r"[_.]+", " ", camel_split)
    # Split on non-alphanumeric
    words = re.split(r"[^a-zA-Z0-9]+", normalised)

    for word in words:
        lower = word.lower()
        if len(lower) < 3:
            continue
        if lower in _STOP_WORDS:
            continue
        tokens.add(lower)

    # Generate stem variants
    stems: set[str] = set()
    for token in list(tokens):
        for variant in _get_stem_variants(token):
            if variant not in tokens and variant not in _STOP_WORDS:
                stems.add(variant)
    tokens |= stems

    return list(tokens)


def _build_fts_query(terms: list[str]) -> str:
    """Build FTS5 query string with prefix matching.
    E.g., ['auth', 'service'] → '"auth"* OR "service"*'
    """
    parts = []
    for term in terms:
        # Escape special FTS5 characters
        cleaned = re.sub(r"['\"*():^]", "", term)
        if cleaned:
            parts.append(f'"{cleaned}"*')
    return " OR ".join(parts) if parts else ""


def _bounded_edit_distance(a: str, b: str, max_dist: int) -> int:
    """Damerau-Levenshtein bounded edit distance. Returns max_dist+1 if exceeded."""
    if a == b:
        return 0
    al, bl = len(a), len(b)
    if abs(al - bl) > max_dist:
        return max_dist + 1
    if al == 0:
        return bl
    if bl == 0:
        return al

    prev = list(range(bl + 1))
    cur = [0] * (bl + 1)

    for i in range(1, al + 1):
        cur[0] = i
        row_min = cur[0]
        for j in range(1, bl + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(cur[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
            if cur[j] < row_min:
                row_min = cur[j]
        if row_min > max_dist:
            return max_dist + 1
        prev, cur = cur, prev

    return prev[bl]


def _kind_bonus(kind: str) -> int:
    """Kind-based bonus for search ranking."""
    bonuses = {
        "function": 10, "method": 10, "class": 8, "interface": 9,
        "type_alias": 6, "struct": 6, "trait": 9, "enum": 5,
        "property": 3, "field": 3, "variable": 2, "constant": 3,
        "import": 1, "file": 0,
    }
    return bonuses.get(kind, 0)


def _name_match_bonus(node_name: str, query: str) -> int:
    """Bonus when a node's name matches the search query."""
    name_lower = node_name.lower()
    query_lower = query.lower()

    # Exact match
    if name_lower == query_lower:
        return 80

    # Name starts with query
    if name_lower.startswith(query_lower):
        ratio = len(query_lower) / len(name_lower) if name_lower else 0
        return round(10 + 30 * ratio)

    # Name contains query
    if name_lower.find(query_lower) != -1:
        return 10

    return 0


def search_nodes_fts(conn: sqlite3.Connection, query: str,
                    limit: int = 10) -> list[tuple[Node, float]]:
    """Full-text search with BM25 + multi-signal scoring.

    3-tier search strategy:
    1. FTS5 with prefix matching and BM25 column weights
    2. LIKE-based substring fallback (for camelCase matching)
    3. Fuzzy edit-distance fallback (for typos)

    Post-hoc scoring adds: kind bonus, name match bonus.
    """
    if not query or not query.strip():
        return []

    terms = _extract_search_terms(query)
    if not terms:
        return []

    # ── Tier 1: FTS5 with prefix matching ─────────────────────────────────
    fts_query_str = _build_fts_query(terms)
    results: list[tuple[Node, float]] = []
    seen_ids: set[str] = set()

    if fts_query_str:
        fts_limit = max(limit * 5, 100)
        try:
            rows = conn.execute(
                """
                SELECT nodes.*, bm25(nodes_fts, 0, 20, 5, 1, 2) as score
                FROM nodes_fts
                JOIN nodes ON nodes_fts.id = nodes.id
                WHERE nodes_fts MATCH ?
                ORDER BY score LIMIT ?
                """,
                (fts_query_str, fts_limit),
            ).fetchall()
            for row in rows:
                node = row_to_node(row)
                score = abs(row["score"])  # bm25 returns negative scores
                results.append((node, score))
                seen_ids.add(node.id)
        except Exception:
            pass  # FTS query failed, fall through

    # ── Tier 2: LIKE-based substring search ───────────────────────────────
    if len(results) < limit:
        like_query = query.strip()
        try:
            rows = conn.execute(
                """
                SELECT nodes.*,
                    CASE
                        WHEN lower(name) = lower(?) THEN 1.0
                        WHEN lower(name) LIKE lower(?) THEN 0.9
                        WHEN lower(qualified_name) LIKE lower(?) THEN 0.7
                        ELSE 0.5
                    END as score
                FROM nodes
                WHERE (lower(name) LIKE lower(?) OR lower(qualified_name) LIKE lower(?))
                    AND id NOT IN ({})
                ORDER BY score DESC, length(name) ASC LIMIT ?
                """.format(",".join("?" * len(seen_ids)) if seen_ids else "NULL"),
                [
                    like_query,
                    f"{like_query}%",
                    f"%{like_query}%",
                    f"%{like_query}%",
                    f"{like_query}%",
                ] + list(seen_ids) + [limit * 3],
            ).fetchall()
            for row in rows:
                node = row_to_node(row)
                results.append((node, row["score"]))
                seen_ids.add(node.id)
        except Exception:
            pass

    # ── Tier 3: Fuzzy edit-distance fallback ──────────────────────────────
    if len(results) < limit and len(query.strip()) >= 3:
        query_lower = query.strip().lower()
        max_dist = 1 if len(query_lower) <= 4 else 2
        all_names = get_all_node_names(conn)
        candidates = []
        for name in all_names:
            dist = _bounded_edit_distance(name.lower(), query_lower, max_dist)
            if dist <= max_dist:
                candidates.append((name, dist))
        candidates.sort(key=lambda x: x[1])

        fuzzy_limit = max(limit * 2, 50)
        for name, dist in candidates[:fuzzy_limit]:
            if len(results) >= limit:
                break
            try:
                rows = conn.execute(
                    "SELECT * FROM nodes WHERE name = ? LIMIT 5",
                    (name,),
                ).fetchall()
                for row in rows:
                    if row["id"] not in seen_ids:
                        node = row_to_node(row)
                        results.append((node, 1.0 / (1 + dist)))
                        seen_ids.add(node.id)
            except Exception:
                pass

    # ── Post-hoc scoring ──────────────────────────────────────────────────
    if results:
        scored = []
        for node, score in results:
            text = query.strip()
            final_score = (
                score
                + _kind_bonus(node.kind.value)
                + _name_match_bonus(node.name, text)
            )
            scored.append((node, final_score))
        scored.sort(key=lambda x: x[1], reverse=True)
        results = scored[:limit]

    return results
