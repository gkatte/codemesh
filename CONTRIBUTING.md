# Contributing to CodeMesh

## Getting Started

```bash
git clone https://github.com/gkatte/codemesh.git
cd codemesh
uv pip install -e ".[dev]"
```

## Running Tests

```bash
# Run all tests (fast)
pytest tests/ -x -q

# Run with coverage
pytest tests/ -v --cov=codemesh --cov-report=html

# Skip slow/integration tests
pytest tests/ -x -q -m "not slow"
```

Tests must pass before every commit.

```bash
ruff check . --fix && ruff format .
```

## Project Structure

```
codemesh/
├── cli/            # Typer CLI commands (init, index, query, etc.)
├── db/             # SQLite schema, connections, queries
├── extraction/     # tree-sitter AST extraction + language-specific extractors
├── resolution/     # Reference resolution + type inference
├── search/         # BM25 keyword search (3-tier: FTS5, LIKE, fuzzy)
├── graph/          # Graph walk (BFS), traversal, impact analysis
├── context/        # Context builder (token-budget-aware XML output)
├── mcp/            # MCP server (stdio/SSE transport)
└── types.py        # Shared types: Node, Edge, EdgeKind, NodeKind
```

## Supported Languages

| Language    | tree-sitter package          | File Extensions          |
|-------------|-----------------------------|--------------------------|
| Python      | `tree-sitter-python`         | `.py`                    |
| TypeScript  | `tree-sitter-typescript`     | `.ts`, `.tsx`           |
| JavaScript  | `tree-sitter-typescript`     | `.js`, `.jsx`           |
| Rust        | `tree-sitter-rust`           | `.rs`                    |
| Go          | `tree-sitter-go`             | `.go`                    |
| Java        | `tree-sitter-java`           | `.java`                  |
| Kotlin      | `tree-sitter-kotlin`         | `.kt`, `.kts`           |
| Swift       | `tree-sitter-swift`          | `.swift`                 |
| C           | `tree-sitter-c`              | `.c`, `.h`              |
| C++         | `tree-sitter-cpp`            | `.cpp`, `.hpp`, `.cc`   |

## Adding a New Language

1. **Add the tree-sitter dependency** to `pyproject.toml`:
   ```toml
   "tree-sitter-mylang>=0.5",
   ```

2. **Create a new extractor** in `codemesh/extraction/extractors/mylang.py`:
   - Use `tree-sitter` to parse source files
   - Define AST query patterns for the language (functions, classes, methods, imports)
   - Extract nodes (`Node` objects) and edges (`Edge` objects)
   - Register in the `extractor_for_ext()` dispatcher

3. **Add file extensions** to the `EXTRACTOR_MAP` or equivalent dispatcher in the extraction module.

4. **Add a test** in `tests/test_extraction.py` covering:
   - Node extraction accuracy
   - Edge extraction (calls, imports, contains)
   - Integration test with the full pipeline

5. **Update this file** — add the language to the table above.

6. **Update `codemesh/cli/init.py`** — add the language's agent instruction template.

## Adding a New Edge Kind

1. Add the variant to the `EdgeKind` enum in `codemesh/types.py`
2. Update all extractors that should produce this edge kind
3. Add a partial index in `codemesh/db/schema.py` for query performance

## Code Style

- Modern Python: `from __future__ import annotations`, dataclasses, `pathlib`, match statements
- Every module and public function gets a docstring
- No dead code — delete orphaned helpers
- Schema changes: add columns with defaults (never breaking)

## Commit Rules

- One logical change per commit
- Write meaningful commit messages describing WHAT and WHY
- Run `ruff check . --fix && ruff format .` before committing
- Tests must pass before committing
