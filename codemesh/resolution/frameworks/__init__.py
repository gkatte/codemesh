"""Framework-specific resolution patterns."""

from __future__ import annotations

from typing import Any

_PATTERNS: dict[str, Any] = {}


def register_pattern(pattern: Any) -> None:
    _PATTERNS[pattern.name] = pattern


def get_pattern(name: str) -> Any | None:
    return _PATTERNS.get(name)
