"""Fuzzy name matching for symbol resolution."""

from __future__ import annotations


def fuzzy_match(query: str, candidates: list[str], threshold: float = 0.6) -> str | None:
    """Find the best fuzzy match for a query among candidates."""
    if not candidates:
        return None

    best_score = 0.0
    best_match: str | None = None

    for candidate in candidates:
        score = _similarity(query.lower(), candidate.lower())
        if score > best_score:
            best_score = score
            best_match = candidate

    return best_match if best_score >= threshold else None


def _similarity(a: str, b: str) -> float:
    """Compute similarity between two strings (0.0 to 1.0)."""
    if a == b:
        return 1.0
    if b.startswith(a) or a.startswith(b):
        return 0.8
    if a in b or b in a:
        return 0.7
    dist = _levenshtein(a, b)
    max_len = max(len(a), len(b))
    if max_len == 0:
        return 1.0
    return 1.0 - (dist / max_len)


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance."""
    n, m = len(a), len(b)
    if n == 0:
        return m
    if m == 0:
        return n
    prev = list(range(m + 1))
    curr = [0] * (m + 1)
    for i in range(1, n + 1):
        curr[0] = i
        for j in range(1, m + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            curr[j] = min(curr[j - 1] + 1, prev[j] + 1, prev[j - 1] + cost)
        prev, curr = curr, prev
    return prev[m]
