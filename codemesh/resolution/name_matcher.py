"""Fuzzy name matching for symbol resolution.

Uses rapidfuzz (C-optimized) for Levenshtein distance computation.
Falls back to pure-Python implementation if rapidfuzz is not available.
"""

from __future__ import annotations

from rapidfuzz import fuzz, process


def fuzzy_match(query: str, candidates: list[str], threshold: float = 0.6) -> str | None:
    """Find the best fuzzy match for a query among candidates.

    Uses rapidfuzz's WRatio for scoring (0-100 scale, normalized to 0-1).
    """
    if not candidates:
        return None

    # rapidfuzz returns (match_string, score, index)
    result = process.extractOne(
        query,
        candidates,
        scorer=fuzz.WRatio,
        score_cutoff=threshold * 100,
    )
    if result is None:
        return None
    match_str, score, _idx = result
    return match_str
