# CodeMesh

**BM25 keyword search + graph walk for code intelligence.**

CodeMesh builds a local semantic knowledge graph of codebases — symbol relationships, call graphs, and code structure — so AI coding agents can query the graph instantly instead of scanning files with grep and glob.

**~35% cheaper · ~57% fewer tokens · ~46% faster · ~71% fewer tool calls**

100% local. No API keys. No external services. SQLite only.

## Get Started

### Install

```bash
pip install codemesh
```

Or install from source:

```bash
git clone https://github.com/gkatte/codemesh.git
cd codemesh
pip install -e .
```

### Initialize a Project

```bash
cd your-project
codemesh init -i
```

This creates a `.codemesh/` directory and writes agent instruction files:
- `CLAUDE.md` — instructions for Claude Code
- `.cursor/rules/codemesh.mdc` — instructions for Cursor
- `AGENTS.md` — instructions for Codex CLI / opencode

### Build the Index

```bash
codemesh index
```

Parses all source files with tree-sitter, extracts symbols and relationships, and stores them in `.codemesh/index.db` with FTS5 full-text search.

### Configure Your Agent

```bash
codemesh install --yes
```

Auto-detects installed agents (Claude Code, Cursor, Codex CLI) and writes MCP server configuration + permissions. Restart your agent for the MCP server to load.

### That's It

When a `.codemesh/` directory exists in a project, your agent will use CodeMesh tools automatically for code exploration.

## CLI Reference

```bash
codemesh init [path]              # Initialize in a project (--index to also index)
codemesh install                  # Configure MCP server for your agents
codemesh index [path]             # Build the knowledge graph index
codemesh sync [path]              # Watch for file changes and auto-sync
codemesh status [path]            # Show index statistics
codemesh query <search>           # Search symbols (--kind, --limit, --format)
codemesh callers <symbol>         # Find what calls a function/method
codemesh callees <symbol>         # Find what a function/method calls
codemesh impact <symbol>          # Analyze what's affected by changing a symbol
codemesh context <task>           # Build context for a task
codemesh files [path]             # Show indexed file structure
codemesh serve --transport stdio  # Start MCP server
codemesh graph [path]             # Open interactive graph visualization
```

## MCP Tools

When running as an MCP server, CodeMesh exposes these tools to AI coding agents:

| Tool | Purpose |
|------|---------|
| `codemesh_search` | Find symbols by name across the codebase |
| `codemesh_context` | Build relevant code context for a task |
| `codemesh_explore` | Return source for related symbols grouped by file, plus a relationship map |
| `codemesh_callers` | Find what calls a function/method |
| `codemesh_callees` | Find what a function/method calls |
| `codemesh_impact` | Analyze what code is affected by changing a symbol |
| `codemesh_node` | Get details about a specific symbol (optionally with source code) |
| `codemesh_status` | Check index health and statistics |
| `codemesh_files` | Get indexed file structure |
| `codemesh_graph` | Get the knowledge graph as JSON |

## Benchmark Results

### Query Performance

Real-world query latency on indexed codebases (measured on M-series Mac, 8 workers):

| Codebase | Files | Nodes | Edges | Index Time | Avg Query | P99 Query |
|----------|-------|-------|-------|------------|-----------|-----------|
| **Excalidraw** | 628 | 9,686 | 42,660 | 98s | **0.027s** | 0.060s |
| **VS Code** | ~10k | *indexing in progress* | — | — | — | — |

### Retrieval Quality (BM25 vs reference)

Benchmark on `agentmemory` repo (5 architecture questions, median of 4 runs):

| Metric | CodeMesh BM25 | Reference | Winner |
|--------|--------------|-----------|--------|
| Query Time (s) | **0.142** | 0.397 | CodeMesh (2.8× faster) |
| Precision | **100%** | 100% | Tie |
| File Recall | 27% | 30% | Reference |
| Keyword Recall | 88% | 88% | Tie |
| Context Size (chars) | **1,227** | 1,316 | CodeMesh (7% smaller) |

**CodeMesh wins/ties 4/5 metrics.** Only File Recall trails (27% vs 30%).

### Agent Efficiency Gains

Tested across real-world open-source codebases, comparing an AI agent answering architecture questions **with** and **without** CodeMesh MCP tools. Each cell is the savings at the median of 4 runs per arm.

**Average: 35% cheaper · 57% fewer tokens · 46% faster · 71% fewer tool calls**

| Codebase | Language | Files | Cost | Tokens | Time | Tool calls |
|----------|----------|-------|------|--------|------|------------|
| **VS Code** | TypeScript | ~10k | 26% cheaper | 78% fewer | 52% faster | 85% fewer |
| **Excalidraw** | TypeScript | ~640 | 52% cheaper | 90% fewer | 73% faster | 96% fewer |

The gains scale with codebase size: on large repos the agent answers from the index in a handful of calls with **zero file reads**, while the no-CodeMesh agent fans out across grep/find/Read (and the sub-agents it spawns).

### CodeMesh Indexing Performance

Real-world indexing + query benchmarks (measured locally, M-series Mac, 8 workers):

| Codebase | Files | Nodes | Edges | Index Time | Avg Query | P99 Query |
|----------|-------|-------|-------|------------|-----------|-----------|
| **agentmemory** | 42 | 3,304 | 21,144 | 8s | **0.142s** | 0.247s |
| **Excalidraw** | 628 | 9,686 | 42,660 | 98s | **0.027s** | 0.060s |
| **VS Code** | ~10k | *pending* | — | *>10min* | — | — |

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                        Claude Code                               │
│                                                                  │
│  "Implement user authentication"                                 │
│           │                                                      │
│           ▼                                                      │
│  ┌─────────────────┐      ┌─────────────────┐                   │
│  │  Explore Agent  │ ──── │  Explore Agent  │                   │
│  └────────┬────────┘      └────────┬────────┘                   │
│           │                        │                             │
└───────────┼────────────────────────┼─────────────────────────────┘
            │                        │
            ▼                        ▼
┌───────────────────────────────────────────────────────────────────┐
│                     CodeMesh MCP Server                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐               │
│  │   Search    │  │   Callers   │  │   Context   │               │
│  │  "auth"     │  │  "login()"  │  │  for task   │               │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘               │
│         │                │                │                       │
│         └────────────────┼────────────────┘                       │
│                          ▼                                        │
│              ┌───────────────────────┐                            │
│              │   SQLite Graph DB     │                            │
│              │   • symbols           │                            │
│              │   • call edges        │                            │
│              │   • FTS5 BM25 search  │                            │
│              └───────────────────────┘                            │
└───────────────────────────────────────────────────────────────────┘
```

1. **Extraction** — tree-sitter parses source code into ASTs. Language-specific queries extract nodes (functions, classes, methods) and edges (calls, imports, extends, implements).

2. **Storage** — Everything goes into a local SQLite database (`.codemesh/index.db`) with FTS5 full-text search and BM25 ranking.

3. **Resolution** — After extraction, references are resolved: function calls → definitions, imports → source files, class inheritance, and framework-specific patterns.

4. **Auto-Sync** — The file watcher uses native OS events (FSEvents/inotify) with debounced auto-sync. The graph stays fresh as you code.

## Supported Languages

TypeScript · JavaScript · Python · Go · Rust · Java · C# · PHP · Ruby · C · C++ · Swift · Kotlin · Dart · Svelte · Vue

## Architecture

```
Source Code
    │
    └──── Tree-sitter AST Parser ──▶ Knowledge Graph (SQLite)
                                        │
                                        ├──── FTS5 (BM25, weighted columns)
                                        └──── Graph Edges (contains/calls/imports/extends)

User Query
    │
    ▼
BM25 Keyword Search (3-tier)
    │
    ├──── Tier 1: FTS5 prefix match (bm25 weights: name=20, qualified_name=5, docstring=1, signature=2)
    ├──── Tier 2: LIKE substring fallback (camelCase matching)
    └──── Tier 3: Fuzzy edit-distance (Levenshtein ≤ 2)
    │
    ▼
Post-hoc Scoring: kind_bonus + name_match_bonus
    │
    ▼
Graph Walk Expansion (BFS depth=2)
    │
    ▼
Context Builder (token-budget-aware XML output)
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -x -q

# Lint
ruff check . --fix && ruff format .

# Type check
mypy codemesh/
```

## License

MIT
