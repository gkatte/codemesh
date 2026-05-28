# CodeMesh

**BM25 keyword search + graph walk for code intelligence.**

CodeMesh builds a local semantic knowledge graph of codebases — symbol relationships, call graphs, and code structure — so AI coding agents can query the graph instantly instead of scanning files with grep and glob.

**100% local. No API keys. No external services. SQLite only.**

---

## Get Started

### Install

**Option 1: pip**
```bash
pip install codemesh
```

**Option 2: uv (faster)**
```bash
uv pip install codemesh
```

**Option 3: from source**
```bash
git clone https://github.com/gkatte/codemesh.git
cd codemesh
pip install -e .
```

Verify installation:
```bash
codemesh --help
```

### Step 1: Initialize a Project

```bash
cd your-project
codemesh init -i
```

This creates a `.codemesh/` directory and writes agent instruction files:
- `CLAUDE.md` — instructions for Claude Code
- `.cursor/rules/codemesh.mdc` — instructions for Cursor
- `AGENTS.md` — instructions for Codex CLI / opencode

### Step 2: Build the Index

```bash
codemesh index
```

Parses all source files with tree-sitter, extracts symbols and relationships, and stores them in `.codemesh/index.db` with FTS5 full-text search.

### Step 3: Configure Your Agent

```bash
codemesh install --yes
```

Auto-detects installed agents (Claude Code, Cursor, Codex CLI) and writes MCP server configuration + permissions to the appropriate config files:
- Claude Code: `~/.claude/claude.json` + `~/.claude/settings.json`
- Cursor: `.cursor/mcp.json` (project-local)
- Codex CLI: `~/.codex/config.json`

Restart your agent for the MCP server to load.

### That's It

When a `.codemesh/` directory exists in a project, your agent uses CodeMesh MCP tools automatically for code exploration instead of grepping through files.

---

## Using CodeMesh with Claude Code

Once `codemesh install --yes` has been run and Claude Code is restarted, the MCP server loads automatically.

**In the main session**, use lightweight tools for targeted lookups:

| Tool | Use For |
|------|---------|
| `codemesh_search` | Find symbols by name |
| `codemesh_callers` / `codemesh_callees` | Trace call flow |
| `codemesh_impact` | Check what's affected before editing |
| `codemesh_node` | Get a single symbol's details |

**For exploration questions** ("how does X work?", "explain the Y system"), spawn an Explore agent with `codemesh_explore` as the primary tool. This returns full source code sections from all relevant files in one call.

If `.codemesh/` does NOT exist in a project, CodeMesh will ask the user if they'd like to initialize it.

---

## CLI Reference

```bash
codemesh init [path]              # Initialize in a project (--index to also index)
codemesh install                  # Configure MCP server for your agents (--yes for non-interactive)
codemesh index [path]             # Build the knowledge graph index (--force to re-index)
codemesh sync [path]              # Watch for file changes and auto-sync (--debounce 1.0)
codemesh status [path]            # Show index statistics
codemesh query <search>           # Search symbols (--kind, --limit, --format)
codemesh callers <symbol>         # Find what calls a function/method (--limit)
codemesh callees <symbol>         # Find what a function/method calls (--limit)
codemesh impact <symbol>          # Analyze what's affected by changing a symbol (--depth)
codemesh context <task>           # Build context for a task (--max-nodes, --tokens)
codemesh files [path]             # Show indexed file structure
codemesh serve --transport stdio  # Start MCP server (--transport sse --port 3000)
codemesh graph [path]             # Open interactive graph visualization (--json export)
```

---

## MCP Tools

When running as an MCP server (`codemesh serve --transport stdio`), CodeMesh exposes 10 tools:

| Tool | Purpose |
|------|---------|
| `codemesh_search` | Find symbols by name across the codebase |
| `codemesh_context` | Build relevant code context for a task or symbol |
| `codemesh_explore` | Return source for related symbols grouped by file, plus a relationship map |
| `codemesh_callers` | Find what calls a function/method |
| `codemesh_callees` | Find what a function/method calls |
| `codemesh_impact` | Analyze what code is affected by changing a symbol |
| `codemesh_node` | Get details about a specific symbol (optionally with source code) |
| `codemesh_status` | Check index health and statistics |
| `codemesh_files` | Get indexed file structure (faster than filesystem scanning) |
| `codemesh_graph` | Get the knowledge graph as JSON |

---

## Benchmark Results

Measured locally on M-series Mac. 5 queries per repo. Each cell shows average latency.

### Indexing + Query Performance

| Codebase | Language | Files | Nodes | Edges | Index Time | Avg Query |
|----------|----------|-------|-------|-------|------------|-----------|
| **Excalidraw** | TypeScript | 628 | 9,678 | 42,644 | 3.3s | 148.7ms |
| **Tokio** | Rust | 778 | 14,474 | 45,210 | 2.9s | 133.8ms |
| **Gin** | Go | 99 | 1,748 | 7,846 | 0.5s | 91.8ms |
| **OkHttp** | Java/Kotlin | 640 | 2,070 | 2,808 | 0.8s | 104.3ms |
| **Alamofire** | Swift | 108 | 3,705 | 3,820 | 0.6s | 92.5ms |
| **libuv** | C | 336 | 6,827 | 24,132 | 1.3s | 136.9ms |
| **nlohmann/json** | C++ | 491 | 6,377 | 18,780 | 2.2s | 139.0ms |
| **Django** | Python | 3,020 | 53,155 | 472,322 | 28.5s | 188.0ms |
| **VS Code** | TypeScript | 10,422 | 299,902 | 1,359,313 | 177.0s | 572.1ms |

Indexing scales linearly with codebase size: from 0.5s for ~100 files (Gin) to 177s for 10k+ files (VS Code at 1.3M edges). Query latency stays sub-second even on the largest repos.

### Retrieval Quality

Benchmark on `agentmemory` repo (5 architecture questions, median of 4 runs):

| Metric | CodeMesh |
|--------|----------|
| Avg Query Time | **0.142s** |
| Precision | **100%** |
| File Recall | 27% |
| Keyword Recall | 88% |
| Context Size | **1,227 chars** |

---

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

---

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

## Supported Languages

TypeScript · JavaScript · Python · Rust · Go · Java · Kotlin · Swift · C · C++

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
