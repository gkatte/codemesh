# mypy: ignore-errors
"""Install command — auto-configures CodeMesh MCP server for AI coding agents."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import typer

from codemesh.cli.init import _CLAUDE_MD_TEMPLATE, _CODEX_TEMPLATE, _CURSOR_RULES_TEMPLATE

logger = logging.getLogger(__name__)

_CLAUDE_MCP_CONFIG = {
    "mcpServers": {
        "codemesh": {
            "type": "stdio",
            "command": "codemesh",
            "args": ["serve", "--transport", "stdio"],
        }
    }
}

_CLAUDE_PERMISSIONS = {
    "permissions": {
        "allow": [
            "mcp__codemesh__codemesh_search",
            "mcp__codemesh__codemesh_context",
            "mcp__codemesh__codemesh_callers",
            "mcp__codemesh__codemesh_callees",
            "mcp__codemesh__codemesh_impact",
            "mcp__codemesh__codemesh_node",
            "mcp__codemesh__codemesh_status",
            "mcp__codemesh__codemesh_files",
            "mcp__codemesh__codemesh_explore",
        ]
    }
}


def _find_claude_json_dir() -> Path | None:
    """Find the Claude Code configuration directory."""
    candidates = [
        Path.home() / ".claude",
        Path.home() / ".config" / "claude",
    ]
    for c in candidates:
        if c.exists():
            return c
    # Create the default location
    claude_dir = Path.home() / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)
    return claude_dir


def _merge_json_file(path: Path, new_data: dict) -> dict:
    """Merge new data into an existing JSON file."""
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    # Merge MCP servers
    if "mcpServers" in new_data:
        existing.setdefault("mcpServers", {})
        existing["mcpServers"].update(new_data["mcpServers"])

    # Merge permissions
    if "permissions" in new_data and "allow" in new_data["permissions"]:
        existing.setdefault("permissions", {"allow": []})
        perms_allow = existing["permissions"].get("allow", [])
        for item in new_data["permissions"]["allow"]:
            if item not in perms_allow:
                perms_allow.append(item)
        existing["permissions"]["allow"] = perms_allow

    return existing


def install_claude(root: Path, global_config: bool = True) -> dict:
    """Configure Claude Code to use CodeMesh MCP server.

    Args:
        root: Project root (for project-local config)
        global_config: If True, write to ~/.claude.json (global).
                      If False, write to .claude.json in project root.
    """
    result = {"claude_json": None, "claude_settings": None, "claude_md": None}

    if global_config:
        claude_dir = _find_claude_json_dir()
        if claude_dir is None:
            return result
        claude_json = claude_dir / "claude.json"
        claude_settings = claude_dir / "settings.json"
    else:
        claude_json = root / ".claude.json"
        claude_settings = root / ".claude_settings.json"

    # Merge MCP config
    if claude_json.exists():
        existing = json.loads(claude_json.read_text())
        existing_mcp = existing.get("mcpServers", {})
        if "codemesh" in existing_mcp:
            result["claude_json"] = str(claude_json) + " (already configured)"
            return result

    merged = _merge_json_file(claude_json, _CLAUDE_MCP_CONFIG)
    claude_json.write_text(json.dumps(merged, indent=2))
    result["claude_json"] = str(claude_json)

    # Merge permissions
    merged_settings = _merge_json_file(claude_settings, _CLAUDE_PERMISSIONS)
    claude_settings.write_text(json.dumps(merged_settings, indent=2))
    result["claude_settings"] = str(claude_settings)

    return result


def install_cursor(root: Path) -> dict:
    """Configure Cursor to use CodeMesh MCP server.

    Cursor uses .cursor/mcp.json in the project directory.
    """
    result = {"cursor_mcp": None}

    cursor_dir = root / ".cursor"
    cursor_dir.mkdir(parents=True, exist_ok=True)
    mcp_json = cursor_dir / "mcp.json"

    config = {}
    if mcp_json.exists():
        try:
            config = json.loads(mcp_json.read_text())
        except (json.JSONDecodeError, OSError):
            config = {}

    config.setdefault("mcpServers", {})
    if "codemesh" in config.get("mcpServers", {}):
        result["cursor_mcp"] = str(mcp_json) + " (already configured)"
        return result

    config["mcpServers"]["codemesh"] = {
        "type": "stdio",
        "command": "codemesh",
        "args": ["serve", "--transport", "stdio"],
    }
    mcp_json.write_text(json.dumps(config, indent=2))
    result["cursor_mcp"] = str(mcp_json)

    return result


def install_codex(root: Path) -> dict:
    """Configure Codex CLI to use CodeMesh MCP server.

    Codex uses ~/.codex/config.json.
    """
    result = {"codex_config": None}

    codex_dir = Path.home() / ".codex"
    codex_dir.mkdir(parents=True, exist_ok=True)
    config_file = codex_dir / "config.json"

    config: dict = {}
    if config_file.exists():
        try:
            config = json.loads(config_file.read_text())
        except (json.JSONDecodeError, OSError):
            config = {}

    config.setdefault("mcpServers", {})
    if "codemesh" in config.get("mcpServers", {}):
        result["codex_config"] = str(config_file) + " (already configured)"
        return result

    config["mcpServers"]["codemesh"] = {
        "type": "stdio",
        "command": "codemesh",
        "args": ["serve", "--transport", "stdio"],
    }
    config_file.write_text(json.dumps(config, indent=2))
    result["codex_config"] = str(config_file)

    return result


def uninstall_claude(root: Path, global_config: bool = True) -> dict:
    """Remove CodeMesh MCP server configuration from Claude Code.

    Args:
        root: Project root (for project-local config).
        global_config: If True, modify ~/.claude.json (global).
                      If False, modify .claude.json in project root.
    """
    result = {"claude_json": None, "claude_settings": None}

    if global_config:
        claude_dir = _find_claude_json_dir()
        if claude_dir is None:
            return result
        claude_json = claude_dir / "claude.json"
        claude_settings = claude_dir / "settings.json"
    else:
        claude_json = root / ".claude.json"
        claude_settings = root / ".claude_settings.json"

    # Remove codemesh from claude.json
    if claude_json.exists():
        try:
            data = json.loads(claude_json.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
        if "codemesh" in data.get("mcpServers", {}):
            del data["mcpServers"]["codemesh"]
            if not data["mcpServers"]:
                del data["mcpServers"]
            claude_json.write_text(json.dumps(data, indent=2))
            result["claude_json"] = str(claude_json)
        else:
            result["claude_json"] = "not configured"

    # Remove codemesh permissions from settings.json
    if claude_settings.exists():
        try:
            settings = json.loads(claude_settings.read_text())
        except (json.JSONDecodeError, OSError):
            settings = {}
        perms = settings.get("permissions", {}).get("allow", [])
        codemesh_perms = [p for p in perms if p.startswith("mcp__codemesh__")]
        if codemesh_perms:
            settings.setdefault("permissions", {})["allow"] = [
                p for p in perms if not p.startswith("mcp__codemesh__")
            ]
            claude_settings.write_text(json.dumps(settings, indent=2))
            result["claude_settings"] = str(claude_settings)
        else:
            result["claude_settings"] = "not configured"

    return result


def uninstall_cursor(root: Path) -> dict:
    """Remove CodeMesh MCP server configuration from Cursor."""
    result = {"cursor_mcp": None}

    mcp_json = root / ".cursor" / "mcp.json"
    if not mcp_json.exists():
        result["cursor_mcp"] = "not configured"
        return result

    try:
        config = json.loads(mcp_json.read_text())
    except (json.JSONDecodeError, OSError):
        return result

    if "codemesh" in config.get("mcpServers", {}):
        del config["mcpServers"]["codemesh"]
        if not config["mcpServers"]:
            del config["mcpServers"]
        mcp_json.write_text(json.dumps(config, indent=2))
        result["cursor_mcp"] = str(mcp_json)
    else:
        result["cursor_mcp"] = "not configured"

    return result


def uninstall_codex(root: Path) -> dict:
    """Remove CodeMesh MCP server configuration from Codex CLI."""
    result = {"codex_config": None}

    codex_dir = Path.home() / ".codex"
    config_file = codex_dir / "config.json"
    if not config_file.exists():
        result["codex_config"] = "not configured"
        return result

    try:
        config = json.loads(config_file.read_text())
    except (json.JSONDecodeError, OSError):
        return result

    if "codemesh" in config.get("mcpServers", {}):
        del config["mcpServers"]["codemesh"]
        if not config["mcpServers"]:
            del config["mcpServers"]
        config_file.write_text(json.dumps(config, indent=2))
        result["codex_config"] = str(config_file)
    else:
        result["codex_config"] = "not configured"

    return result


def detect_agents() -> list[str]:
    """Detect which AI coding agents are installed."""
    agents = []

    # Check for Claude Code
    claude_dir = _find_claude_json_dir()
    if claude_dir and claude_dir.exists():
        agents.append("claude")

    # Check for Cursor
    cursor_check = Path.home() / ".cursor"
    if cursor_check.exists():
        agents.append("cursor")

    # Check for Codex CLI
    import shutil

    if shutil.which("codex"):
        agents.append("codex")

    return agents


def _remove_codemesh_section(content: str, heading: str = "## CodeMesh") -> tuple[str, bool]:
    """Remove the CodeMesh section from a markdown file.

    Returns (new_content, was_modified). If the file only contained the CodeMesh
    section, returns ("", True) to signal the caller it can be deleted.
    If no CodeMesh section is found, returns (content, False).
    """
    if heading not in content:
        return content, False

    # Split on the CodeMesh heading
    parts = content.split(heading, 1)
    before = parts[0]
    after = parts[1] if len(parts) > 1 else ""

    # The CodeMesh section runs until the next ## heading or end-of-file
    # Find the next ## heading in the remaining content
    next_section_idx = -1
    if "\n## " in after:
        next_section_idx = after.index("\n## ")

    if next_section_idx >= 0:
        # There's another section after CodeMesh — keep everything after it
        after = after[next_section_idx:]  # keep the "\n## ..."
        new_content = (before.rstrip("\n") + "\n\n" + after.lstrip("\n")).strip("\n") + "\n"
        if not new_content.strip():
            return "", True  # only had CodeMesh, file is now empty
        return new_content, True
    else:
        # CodeMesh was the last (or only) section
        if before.strip():
            # There's content before CodeMesh — keep it
            return before.rstrip("\n") + "\n", True
        else:
            # File only contained CodeMesh — signal for deletion
            return "", True


def clean_project(root: Path, force: bool = False) -> dict:
    """Remove CodeMesh project artifacts (.codemesh/, CLAUDE.md, AGENTS.md, .cursor/rules/).

    Uses surgical removal for shared files (CLAUDE.md, AGENTS.md):
    - If the file is EXACTLY our template, it's deleted
    - If the file contains CodeMesh section mixed with user content, only the
      CodeMesh section is extracted and the rest is preserved
    - If the file doesn't contain CodeMesh content, it's left untouched

    Returns a dict with paths removed or modified.
    """
    import shutil as _shutil

    removed = []
    modified = []

    # --- .codemesh/ directory: always safe to remove entirely ---
    codemesh_dir = root / ".codemesh"
    if codemesh_dir.exists():
        _shutil.rmtree(codemesh_dir)
        removed.append(str(codemesh_dir))

    # --- CLAUDE.md: surgical removal ---
    claude_md = root / "CLAUDE.md"
    if claude_md.exists():
        content = claude_md.read_text()
        if _CLAUDE_MD_TEMPLATE.strip() == content.strip():
            # Exact match — safe to delete entirely
            claude_md.unlink()
            removed.append(str(claude_md))
        elif "## CodeMesh" in content:
            new_content, changed = _remove_codemesh_section(content)
            if changed:
                if new_content.strip():
                    claude_md.write_text(new_content)
                    modified.append(str(claude_md))
                else:
                    claude_md.unlink()
                    removed.append(str(claude_md))
        # else: file has no CodeMesh content — leave it alone

    # --- AGENTS.md: surgical removal ---
    agents_md = root / "AGENTS.md"
    if agents_md.exists():
        content = agents_md.read_text()
        if _CODEX_TEMPLATE.strip() == content.strip():
            agents_md.unlink()
            removed.append(str(agents_md))
        elif "## CodeMesh" in content:
            new_content, changed = _remove_codemesh_section(content)
            if changed:
                if new_content.strip():
                    agents_md.write_text(new_content)
                    modified.append(str(agents_md))
                else:
                    agents_md.unlink()
                    removed.append(str(agents_md))

    # --- .cursor/rules/codemesh.mdc: dedicated file, safe to delete ---
    cursor_rules = root / ".cursor" / "rules" / "codemesh.mdc"
    if cursor_rules.exists():
        content = cursor_rules.read_text()
        if _CURSOR_RULES_TEMPLATE.strip() == content.strip() or "CodeMesh" in content:
            cursor_rules.unlink()
            removed.append(str(cursor_rules))

    return {"removed": removed, "modified": modified}
