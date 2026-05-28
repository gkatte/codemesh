# mypy: ignore-errors
"""Install command — auto-configures CodeMesh MCP server for AI coding agents."""

from __future__ import annotations

import json
import logging
from pathlib import Path

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
