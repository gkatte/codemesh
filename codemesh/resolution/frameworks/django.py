"""Django-specific resolution patterns."""

from __future__ import annotations

from pathlib import Path

from codemesh.resolution.frameworks import register_pattern


class DjangoPattern:
    """Resolves Django-specific patterns like model references, URL patterns."""

    @property
    def name(self) -> str:
        return "django"

    def resolve(self, symbol: str, context: dict[str, object]) -> str | None:
        # e.g., "app.Model" -> find model class
        if "." in symbol:
            app, model = symbol.split(".", 1)
            # Look for model class in app's models.py
            root = context.get("root")
            if isinstance(root, Path):
                models_file = root / app / "models.py"
                if models_file.exists():
                    return f"file:{models_file}"
        return None


register_pattern(DjangoPattern())
