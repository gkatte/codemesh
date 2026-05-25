# CodeMesh

**Graph-enhanced retrieval-augmented generation for code.**

CodeMesh combines tree-sitter AST structural knowledge graphs with neural code embeddings
into a hybrid RAG system for LLM coding agents.

## Key Claims (to validate)

| Claim | Metric | Target |
|-------|--------|--------|
| Token efficiency | Tokens/query | 10× fewer than grep |
| Tool call reduction | Calls/query | 5× fewer than grep |
| Answer quality | LLM-judge 1-5 | ≥4.0 |
| Retrieval quality | Recall@10 | >0.85 on RepoQA |
| Scalability | Query P99 latency | <100ms for 1M LOC |

## Quick Start

```bash
# Install
uv pip install -e ".[dev]"

# Index a codebase
codemesh index /path/to/repo

# Query
codemesh query "What calls validate()?"

# Start MCP server
codemesh serve --mcp
```

## Architecture

```
Source Code
    │
    ├──── Tree-sitter AST Parser ──▶ Knowledge Graph (SQLite)
    │
    └──── Embedding Model ──▶ Vector Store (SQLite-vec)

User Query
    │
    ▼
Query Classifier (structural / semantic / hybrid / definition)
    │
    ├──── Graph Walk (weighted BFS) ──┐
    │                                  ├──▶ RRF Fusion ──▶ Cross-Encoder Re-ranker ──▶ Context Builder
    └──── Semantic Search (ANN) ──────┘
```

## Development

```bash
make dev       # Install with dev deps
make lint      # Ruff linter + formatter
make typecheck # mypy
make test      # pytest
make test-cov  # pytest with coverage
```

## License

MIT
