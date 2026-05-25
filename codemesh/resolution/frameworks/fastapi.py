"""FastAPI-specific resolution patterns."""

from __future__ import annotations

from codemesh.resolution.frameworks import register_pattern


class FastAPIPattern:
    """Resolves FastAPI-specific patterns like dependency injection."""

    @property
    def name(self) -> str:
        return "fastapi"

    def resolve(self, symbol: str, context: dict[str, object]) -> str | None:
        # e.g., "Depends(func)" -> resolve func
        if symbol.startswith("Depends("):
            inner = symbol[8:-1]  # Extract inner function name
            return inner
        return None


register_pattern(FastAPIPattern())
