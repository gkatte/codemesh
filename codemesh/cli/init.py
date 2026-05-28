# mypy: ignore-errors
"""Project initialization — creates .codemesh/ directory and agent instructions."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_CLAUDE_MD_TEMPLATE = """## CodeMesh

CodeMesh builds a semantic knowledge graph of codebases for faster, smarter code exploration.

### If `.codemesh/` exists in the project

**NEVER call `codemesh_explore` or `codemesh_context` directly in the main session.** These tools return large amounts of source code that fills up main session context. Instead, ALWAYS spawn an Explore agent for any exploration question (e.g., "how does X work?", "explain the Y system", "where is Z implemented?").

**When spawning Explore agents**, include this instruction in the prompt:

> This project has CodeMesh initialized (.codemesh/ exists). Use `codemesh_explore` as your PRIMARY tool — it returns full source code sections from all relevant files in one call.
>
> **Rules:**
> 1. Follow the explore call budget in the `codemesh_explore` tool description — it scales automatically based on project size.
> 2. Do NOT re-read files that codemesh_explore already returned source code for. The source sections are complete and authoritative.
> 3. Only fall back to grep/glob/read for files listed under "Additional relevant files" if you need more detail, or if codemesh returned no results.

**The main session may only use these lightweight tools directly** (for targeted lookups before making edits, not for exploration):

| Tool | Use For |
|------|---------|
| `codemesh_search` | Find symbols by name |
| `codemesh_callers` / `codemesh_callees` | Trace call flow |
| `codemesh_impact` | Check what's affected before editing |
| `codemesh_node` | Get a single symbol's details |

### If `.codemesh/` does NOT exist

At the start of a session, ask the user if they'd like to initialize CodeMesh:

"I notice this project doesn't have CodeMesh initialized. Would you like me to run `codemesh init -i` to build a code knowledge graph?"
"""

_CURSOR_RULES_TEMPLATE = """---
description: CodeMesh code intelligence
globs: *
alwaysApply: true
---

## CodeMesh

CodeMesh builds a semantic knowledge graph of codebases for faster, smarter code exploration.

### If `.codemesh/` exists in the project

Use CodeMesh MCP tools as your PRIMARY exploration method:

1. `codemesh_explore` — Return source for several related symbols grouped by file, plus a relationship map, in one call. Use this as your main exploration tool.
2. `codemesh_callers` / `codemesh_callees` — Trace call flow
3. `codemesh_impact` — Check what's affected before editing
4. `codemesh_search` — Find symbols by name

### If `.codemesh/` does NOT exist

At the start of a session, ask the user if they'd like to initialize CodeMesh:

"I notice this project doesn't have CodeMesh initialized. Would you like me to run `codemesh init -i` to build a code knowledge graph?"
"""

_CODEX_TEMPLATE = """## CodeMesh

CodeMesh builds a semantic knowledge graph of codebases for faster, smarter code exploration.

### If `.codemesh/` exists in the project

Use CodeMesh MCP tools as your PRIMARY exploration method:
- `codemesh_explore` — Return source for related symbols grouped by file (main exploration tool)
- `codemesh_callers` / `codemesh_callees` — Trace call flow
- `codemesh_impact` — Check what's affected before editing
- `codemesh_search` — Find symbols by name

### If `.codemesh/` does NOT exist

At the start of a session, ask: "I notice this project doesn't have CodeMesh initialized. Would you like me to run `codemesh init -i` to build a code knowledge graph?"
"""

_HERMES_TEMPLATE = """## CodeMesh

CodeMesh builds a semantic knowledge graph of codebases for faster, smarter code exploration.

### If `.codemesh/` exists in the project

Use CodeMesh MCP tools as your PRIMARY exploration method:
- `codemesh_explore` — Return source for related symbols grouped by file (main exploration tool)
- `codemesh_callers` / `codemesh_callees` — Trace call flow
- `codemesh_impact` — Check what's affected before editing
- `codemesh_search` — Find symbols by name

### If `.codemesh/` does NOT exist

At the start of a session, ask: "I notice this project doesn't have CodeMesh initialized. Would you like me to run `codemesh init -i` to build a code knowledge graph?"
"""


def init_project(root: Path, interactive: bool = False) -> dict:
    """Initialize CodeMesh in a project.

    Creates .codemesh/ directory and writes agent instruction files.
    Returns a dict with paths created.
    """
    root = root.resolve()
    codemesh_dir = root / ".codemesh"

    created = {}

    # Create .codemesh directory
    codemesh_dir.mkdir(parents=True, exist_ok=True)
    created["codemesh_dir"] = str(codemesh_dir)

    # Write CLAUDE.md (project-level instructions for Claude Code)
    claude_md = root / "CLAUDE.md"
    if interactive and claude_md.exists() and not _confirm_overwrite("CLAUDE.md"):
        # Write to .codemesh/CLAUDE.md instead
        claude_md = codemesh_dir / "CLAUDE.md"
    claude_md.write_text(_CLAUDE_MD_TEMPLATE)
    created["claude_md"] = str(claude_md)

    # Write Cursor rules
    cursor_dir = root / ".cursor" / "rules"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    cursor_rules = cursor_dir / "codemesh.mdc"
    cursor_rules.write_text(_CURSOR_RULES_TEMPLATE)
    created["cursor_rules"] = str(cursor_rules)

    # Write AGENTS.md (global instructions for Codex/opencode)
    agents_md = root / "AGENTS.md"
    if interactive and agents_md.exists() and not _confirm_overwrite("AGENTS.md"):
        agents_md = codemesh_dir / "AGENTS.md"
    agents_md.write_text(_CODEX_TEMPLATE)
    created["agents_md"] = str(agents_md)

    # Write Hermes instructions
    hermes_md = codemesh_dir / "HERMES.md"
    hermes_md.write_text(_HERMES_TEMPLATE)
    created["hermes_md"] = str(hermes_md)

    # Write config file
    config_file = codemesh_dir / "config.json"
    if not config_file.exists():
        import json

        config = {
            "version": 1,
            "root": str(root),
            "ignore": [".git", "node_modules", "__pycache__", ".venv", "venv"],
        }
        config_file.write_text(json.dumps(config, indent=2))
        created["config"] = str(config_file)

    return created


def _confirm_overwrite(name: str) -> bool:
    """Ask user to confirm overwriting an existing file."""
    response = input(f"{name} already exists. Overwrite? [y/N] ").strip().lower()
    return response in ("y", "yes")


def uninit_project(root: Path, force: bool = False) -> dict:
    """Remove CodeMesh from a project.

    Removes .codemesh/ directory and agent instruction files.
    Returns a dict with paths removed.
    """
    root = root.resolve()
    codemesh_dir = root / ".codemesh"
    removed = []

    # Remove instruction files
    for f in ["CLAUDE.md", "AGENTS.md"]:
        p = root / f
        if p.exists():
            content = p.read_text()
            if "CodeMesh" in content and (force or _confirm_remove(f)):
                p.unlink()
                removed.append(str(p))

    # Remove Cursor rules
    cursor_rules = root / ".cursor" / "rules" / "codemesh.mdc"
    if cursor_rules.exists() and (force or _confirm_remove("cursor rules")):
        cursor_rules.unlink()
        removed.append(str(cursor_rules))

    # Remove .codemesh directory
    if codemesh_dir.exists():
        import shutil

        if force or _confirm_remove(".codemesh/"):
            shutil.rmtree(codemesh_dir)
            removed.append(str(codemesh_dir))

    return {"removed": removed}


def _confirm_remove(name: str) -> bool:
    """Ask user to confirm removing a file."""
    response = input(f"Remove {name}? [y/N] ").strip().lower()
    return response in ("y", "yes")
