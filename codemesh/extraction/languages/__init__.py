"""Language-specific tree-sitter extractors."""

from __future__ import annotations

from pathlib import Path  # noqa: TC003
from typing import Protocol

from codemesh.types import Edge, Language, Node


class LanguageExtractor(Protocol):
    """Protocol for language-specific extractors."""

    def extract(
        self,
        file_path: Path,
        source: bytes,
        root_node: object,
        language: Language,
    ) -> tuple[list[Node], list[Edge]]: ...


_EXTRACTORS: dict[Language, LanguageExtractor] = {}


def register_extractor(language: Language, extractor: LanguageExtractor) -> None:
    """Register a language extractor."""
    _EXTRACTORS[language] = extractor


def get_extractor(language: Language) -> LanguageExtractor | None:
    """Get the extractor for a language."""
    return _EXTRACTORS.get(language)


# Import and register all extractors
def _register_all() -> None:
    """Register all available extractors."""
    try:
        from codemesh.extraction.languages.python import PythonExtractor

        register_extractor(Language.PYTHON, PythonExtractor())
    except ImportError:
        pass

    try:
        from codemesh.extraction.languages.typescript import TypeScriptExtractor

        ts_extractor = TypeScriptExtractor()
        register_extractor(Language.TYPESCRIPT, ts_extractor)
        register_extractor(Language.JAVASCRIPT, ts_extractor)
    except ImportError:
        pass

    try:
        from codemesh.extraction.languages.rust import RustExtractor

        register_extractor(Language.RUST, RustExtractor())
    except ImportError:
        pass

    try:
        from codemesh.extraction.languages.swift import SwiftExtractor

        register_extractor(Language.SWIFT, SwiftExtractor())
    except ImportError:
        pass

    try:
        from codemesh.extraction.languages.go import GoExtractor

        register_extractor(Language.GO, GoExtractor())
    except ImportError:
        pass

    try:
        from codemesh.extraction.languages.java import JavaExtractor

        java_extractor = JavaExtractor()
        register_extractor(Language.JAVA, java_extractor)
        register_extractor(Language.KOTLIN, java_extractor)
    except ImportError:
        pass

    try:
        from codemesh.extraction.languages.c_family import CFamilyExtractor

        c_family_extractor = CFamilyExtractor()
        register_extractor(Language.C, c_family_extractor)
        register_extractor(Language.CPP, c_family_extractor)
    except ImportError:
        pass


_register_all()
