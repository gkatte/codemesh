# CodeMesh

[![PyPI version](https://img.shields.io/pypi/v/codemesh)](https://pypi.org/project/codemesh/)
[![Python](https://img.shields.io/pypi/pyversions/codemesh)](https://pypi.org/project/codemesh/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![CI](https://github.com/gkatte/codemesh/actions/workflows/ci.yml/badge.svg)](https://github.com/gkatte/codemesh/actions/workflows/ci.yml)
[![Tests](https://img.shields.io/badge/tests%20passing-63%20passed%2C%204%20skipped-green)](tests/)

**BM25 keyword search + graph walk for code intelligence.**

CodeMesh builds a local semantic knowledge graph of codebases — symbol relationships, call graphs, and code structure — so AI coding agents can query the graph instantly instead of scanning files with grep and glob.

**100% local. No API keys. No external services. SQLite only.**

---

## Why CodeMesh?

**The problem:** AI coding agents waste tokens and time scanning files with `grep` and `glob`. On every question about code, they read entire files into context — even when the answer is in one function.

**The solution:** CodeMesh parses your codebase into a structured knowledge graph at index time. At query time, agents get concise, relevant context — not raw file dumps.

- **86% fewer tokens** per query on average (measured across 9 real-world repos)
- **66% faster** agent loops — 2 MCP calls vs 4+ grep/read cycles
- **<0.2s** query latency on codebases up to 50K nodes; <0.3s on 300K+ nodes
- **Zero configuration** — no API keys, no cloud services, no model downloads

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

### Agent Efficiency

Measured across all 9 repos. For each query, we model the full agent loop — including model inference, tool execution, and token consumption — comparing an agent using CodeMesh MCP tools against one using only grep + read_file.

> **Average: 85% cheaper · 86% fewer tokens · 66% faster · 50% fewer tool calls**

| Codebase | Cost Savings | Token Savings | Time Savings | Tool Call Savings |
|----------|-------------|---------------|--------------|-------------------|
| **nlohmann/json** | 98.6% | 98.9% | 93.3% | 50% |
| **Alamofire** | 96.0% | 96.8% | 85.1% | 50% |
| **VS Code** | 90.9% | 92.3% | 14.8% | 50% |
| **Gin** | 89.9% | 91.9% | 70.6% | 50% |
| **Django** | 89.3% | 90.3% | 72.7% | 50% |
| **Tokio** | 78.0% | 80.6% | 62.4% | 50% |
| **OkHttp** | 76.4% | 79.4% | 65.0% | 50% |
| **Excalidraw** | 72.8% | 72.6% | 61.5% | 50% |
| **libuv** | 71.0% | 71.1% | 69.3% | 50% |

The savings come from two sources: (1) CodeMesh returns compact structured results (hundreds of tokens) instead of full source files (thousands of tokens per file), and (2) fewer agent turns are needed — 2 MCP calls vs 4+ grep/read cycles. On large codebases like nlohmann/json and Django, the baseline agent reads hundreds of thousands of tokens per query while CodeMesh answers from a few thousand.


---

## How It Works

```
┌─────────────────────────────────────────────────────────────────┐
│                        Claude Code                              │
│                                                                 │
│  "Implement user authentication"                                │
│           │                                                     │
│           ▼                                                     │
│  ┌─────────────────┐      ┌─────────────────┐                   │
│  │  Explore Agent  │ ──── │  Explore Agent  │                   │
│  └────────┬────────┘      └────────┬────────┘                   │
│           │                        │                            │
└───────────┼────────────────────────┼────────────────────────────┘
            │                        │
            ▼                        ▼
┌───────────────────────────────────────────────────────────────────┐
│                     CodeMesh MCP Server                           │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                │
│  │   Search    │  │   Callers   │  │   Context   │                │
│  │  "auth"     │  │  "login()"  │  │  for task   │                │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘                │
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

---

<div align="center">

**Made for AI coding agents — Claude Code, Cursor, Codex CLI, opencode, Hermes Agent, Gemini CLI, Antigravity IDE, and Kiro**

[Report Bug](https://github.com/gkatte/codemesh/issues) · [Request Feature](https://github.com/gkatte/codemesh/issues)

</div>