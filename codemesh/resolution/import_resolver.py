"""Import/path alias resolution for various module systems."""

from __future__ import annotations

import sqlite3
from pathlib import Path


class ImportResolver:
    """Resolves import statements to concrete nodes."""

    def __init__(self, conn: sqlite3.Connection, root: Path) -> None:
        self.conn = conn
        self.root = root

    def resolve_python_import(self, import_text: str, source_file: Path) -> str | None:
        """Resolve a Python import statement to a node ID."""
        parts = import_text.replace("from ", "").replace("import ", "").strip().split()
        if not parts:
            return None

        module_path = parts[0]
        imported_name = parts[-1] if len(parts) > 1 else module_path.split(".")[-1]

        relative = module_path.replace(".", "/")
        candidates = [
            self.root / f"{relative}.py",
            self.root / relative / "__init__.py",
        ]

        for candidate in candidates:
            if candidate.exists():
                from codemesh.db.queries import get_all_nodes

                nodes = get_all_nodes(self.conn)
                for n in nodes:
                    if n.file_path == candidate:
                        if imported_name == "*" or n.name == imported_name:
                            return n.id
                        if n.kind.value == "file":
                            return n.id
        return None

    def resolve_typescript_import(self, import_text: str, source_file: Path) -> str | None:
        """Resolve a TypeScript import statement to a node ID."""
        import re

        match = re.search(r"from\s+['\"]([^'\"]+)['\"]", import_text)
        if not match:
            return None

        module_path = match.group(1)
        source_dir = source_file.parent

        if module_path.startswith("."):
            resolved = (source_dir / module_path).resolve()
        else:
            resolved = self.root / module_path

        for ext in ["", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.tsx"]:
            candidate = Path(str(resolved) + ext)
            if candidate.exists():
                from codemesh.db.queries import get_all_nodes

                nodes = get_all_nodes(self.conn)
                for n in nodes:
                    if n.file_path == candidate and n.kind.value == "file":
                        return n.id
        return None
