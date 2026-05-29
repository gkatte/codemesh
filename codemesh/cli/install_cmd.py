# mypy: ignore-errors
"""Install/uninstall commands — configure CodeMesh MCP server for AI coding agents."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import typer

from codemesh.cli.init import _CLAUDE_MD_TEMPLATE, _CODEX_TEMPLATE, _CURSOR_RULES_TEMPLATE

logger = logging.getLogger(__name__)


# ── MCP server config templates ──────────────────────────────────────────────

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


# ── Agent metadata ───────────────────────────────────────────────────────────

@dataclass
class AgentInfo:
    name: str                          # canonical key: "claude", "cursor", etc.
    display: str                       # human name: "Claude Code"
    detected: bool = False             # is the agent installed on this machine?
    configured: bool = False           # is codemesh already configured for this agent?
    scope: str = "project"             # "project" or "global"
    detail: str = ""                   # extra info for the UI, e.g. path


def detect_agents(root: Path | None = None) -> list[AgentInfo]:
    """Detect all supported agents and their codemesh configuration status.

    Returns a list of AgentInfo for every known agent, with detected/configured
    flags set accordingly.
    """
    if root is None:
        root = Path.cwd()

    agents: list[AgentInfo] = []

    # ── Claude Code ─────────────────────────────────────────────────────────
    claude_dir = _find_claude_json_dir()
    claude_json = claude_dir / "claude.json" if claude_dir else None
    claude_configured = (
        claude_json is not None
        and claude_json.exists()
        and "codemesh" in json.loads(claude_json.read_text()).get("mcpServers", {})
    )
    agents.append(AgentInfo(
        name="claude",
        display="Claude Code",
        detected=(claude_dir is not None and claude_dir.exists()),
        configured=claude_configured,
        scope="global",
        detail=str(claude_json) if claude_json else "",
    ))

    # ── Cursor ──────────────────────────────────────────────────────────────
    cursor_mcp = root / ".cursor" / "mcp.json"
    cursor_configured = (
        cursor_mcp.exists()
        and "codemesh" in json.loads(cursor_mcp.read_text()).get("mcpServers", {})
    )
    agents.append(AgentInfo(
        name="cursor",
        display="Cursor",
        detected=(root / ".cursor").exists(),
        configured=cursor_configured,
        scope="project",
        detail=str(cursor_mcp),
    ))

    # ── Codex CLI ───────────────────────────────────────────────────────────
    codex_dir = Path.home() / ".codex"
    codex_config = codex_dir / "config.json"
    codex_configured = (
        codex_config.exists()
        and "codemesh" in json.loads(codex_config.read_text()).get("mcpServers", {})
    )
    agents.append(AgentInfo(
        name="codex",
        display="Codex CLI",
        detected=shutil.which("codex") is not None,
        configured=codex_configured,
        scope="global",
        detail=str(codex_config),
    ))

    # ── Hermes Agent ────────────────────────────────────────────────────────
    hermes_config = Path.home() / ".hermes" / "config.yaml"
    hermes_configured = False
    if hermes_config.exists():
        try:
            import yaml
            hermes_data = yaml.safe_load(hermes_config.read_text()) or {}
            mcp_servers = hermes_data.get("mcp_servers", {})
            hermes_configured = "codemesh" in mcp_servers
        except Exception:
            pass
    agents.append(AgentInfo(
        name="hermes",
        display="Hermes Agent",
        detected=hermes_config.exists() or shutil.which("hermes") is not None,
        configured=hermes_configured,
        scope="global",
        detail=str(hermes_config),
    ))

    return agents


# ── Interactive agent selection ──────────────────────────────────────────────

def select_agents_interactive(
    agents: list[AgentInfo],
    mode: str = "install",   # "install" or "uninstall"
) -> list[str]:
    """Present an interactive checklist and return the selected agent names.

    Uses a simple numbered input — works in any terminal without requiring
    an interactive TUI library.

    For install:   pre-selects detected agents that are NOT yet configured.
    For uninstall: pre-selects agents that ARE configured.
    """
    typer.echo("")
    if mode == "install":
        typer.echo("  Which agents should codemesh configure?")
    else:
        typer.echo("  Which agents should codemesh uninstall from?")
    typer.echo("")

    pre_selected: set[str] = set()
    for i, a in enumerate(agents, 1):
        if mode == "install" and a.detected and not a.configured:
            pre_selected.add(a.name)

        # Build annotation
        annotations: list[str] = []
        if a.configured:
            annotations.append("already configured")
        elif not a.detected:
            annotations.append("not found")
        if a.scope == "global" and a.detected:
            annotations.append("global only")

        ann_str = f" — {', '.join(annotations)}" if annotations else ""
        marker = "◼" if a.name in pre_selected else "◻"

        typer.echo(f"  {i}. {marker} {a.display}{ann_str}")

    typer.echo("")
    typer.echo("  Enter numbers to toggle (e.g. 1,3,4), 'all', or 'none'.")
    typer.echo("  Press Enter with no input to accept the pre-selection.")
    typer.echo("")

    selected: set[str] = set(pre_selected)

    while True:
        raw = typer.prompt("  Select", default="", show_default=False).strip()

        if raw == "":
            break
        elif raw.lower() == "all":
            selected = {a.name for a in agents if a.detected}
            break
        elif raw.lower() == "none":
            selected = set()
            break
        else:
            # Parse comma/space-separated numbers
            parts = raw.replace(",", " ").split()
            for p in parts:
                try:
                    idx = int(p) - 1
                    if 0 <= idx < len(agents):
                        name = agents[idx].name
                        if name in selected:
                            selected.discard(name)
                        else:
                            selected.add(name)
                    else:
                        typer.echo(f"  Ignoring out-of-range number: {p}")
                except ValueError:
                    # Match by name
                    matched = False
                    for a in agents:
                        if a.name == p.lower() or a.display.lower() == p.lower():
                            if a.name in selected:
                                selected.discard(a.name)
                            else:
                                selected.add(a.name)
                            matched = True
                            break
                    if not matched:
                        typer.echo(f"  Unknown agent: {p}")
            break

    if not selected:
        typer.echo("  No agents selected.")
        raise typer.Exit(1)

    return list(selected)


# ── Install helpers ──────────────────────────────────────────────────────────

def _find_claude_json_dir() -> Path | None:
    """Find the Claude Code configuration directory."""
    candidates = [
        Path.home() / ".claude",
        Path.home() / ".config" / "claude",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _merge_json_file(path: Path, new_data: dict) -> dict:
    """Merge new data into an existing JSON file."""
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            existing = {}

    if "mcpServers" in new_data:
        existing.setdefault("mcpServers", {})
        existing["mcpServers"].update(new_data["mcpServers"])

    if "permissions" in new_data and "allow" in new_data["permissions"]:
        existing.setdefault("permissions", {"allow": []})
        perms_allow = existing["permissions"].get("allow", [])
        for item in new_data["permissions"]["allow"]:
            if item not in perms_allow:
                perms_allow.append(item)
        existing["permissions"]["allow"] = perms_allow

    return existing


def install_claude(root: Path, global_config: bool = True) -> dict:
    """Configure Claude Code to use CodeMesh MCP server."""
    result = {"claude_json": None, "claude_settings": None}

    if global_config:
        claude_dir = _find_claude_json_dir()
        if claude_dir is None:
            claude_dir = Path.home() / ".claude"
            claude_dir.mkdir(parents=True, exist_ok=True)
        claude_json = claude_dir / "claude.json"
        claude_settings = claude_dir / "settings.json"
    else:
        claude_json = root / ".claude.json"
        claude_settings = root / ".claude_settings.json"

    # Check if already configured
    if claude_json.exists():
        existing = json.loads(claude_json.read_text()) if claude_json.exists() else {}
        if "codemesh" in existing.get("mcpServers", {}):
            result["claude_json"] = str(claude_json) + " (already configured)"
            return result

    merged = _merge_json_file(claude_json, _CLAUDE_MCP_CONFIG)
    claude_json.write_text(json.dumps(merged, indent=2))
    result["claude_json"] = str(claude_json)

    merged_settings = _merge_json_file(claude_settings, _CLAUDE_PERMISSIONS)
    claude_settings.write_text(json.dumps(merged_settings, indent=2))
    result["claude_settings"] = str(claude_settings)

    return result


def install_cursor(root: Path) -> dict:
    """Configure Cursor to use CodeMesh MCP server."""
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
    """Configure Codex CLI to use CodeMesh MCP server."""
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


def install_hermes(_root: Path) -> dict:
    """Configure Hermes Agent to use CodeMesh MCP server.

    Hermes uses ~/.hermes/config.yaml with an mcp_servers section.
    Silently returns an empty result if PyYAML is not installed.
    """
    result: dict = {}

    try:
        import yaml  # noqa: F401
    except ImportError:
        return result  # PyYAML not installed — skip silently

    hermes_config_path = Path.home() / ".hermes" / "config.yaml"
    if not hermes_config_path.parent.exists():
        hermes_config_path.parent.mkdir(parents=True, exist_ok=True)

    config: dict = {}
    if hermes_config_path.exists():
        try:
            config = yaml.safe_load(hermes_config_path.read_text()) or {}
        except Exception:
            config = {}

    config.setdefault("mcp_servers", {})
    if "codemesh" in config.get("mcp_servers", {}):
        return result  # already configured

    config["mcp_servers"]["codemesh"] = {
        "command": "codemesh",
        "args": ["serve", "--transport", "stdio"],
        "enabled": True,
    }
    hermes_config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
    result["hermes_config"] = str(hermes_config_path)

    return result


# ── Uninstall helpers ────────────────────────────────────────────────────────

def uninstall_claude(root: Path, global_config: bool = True) -> dict:
    """Remove CodeMesh MCP server configuration from Claude Code."""
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


def uninstall_codex(_root: Path) -> dict:
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


def uninstall_hermes(_root: Path) -> dict:
    """Remove CodeMesh MCP server configuration from Hermes Agent.

    Silently returns an empty result if PyYAML is not installed.
    """
    result: dict = {}

    try:
        import yaml  # noqa: F401
    except ImportError:
        return result

    hermes_config_path = Path.home() / ".hermes" / "config.yaml"
    if not hermes_config_path.exists():
        return result

    try:
        config = yaml.safe_load(hermes_config_path.read_text()) or {}
    except Exception:
        return result

    if "codemesh" in config.get("mcp_servers", {}):
        del config["mcp_servers"]["codemesh"]
        if not config["mcp_servers"]:
            del config["mcp_servers"]
        hermes_config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
        result["hermes_config"] = str(hermes_config_path)

    return result


# ── Project artifact cleanup (surgical) ──────────────────────────────────────

def _remove_codemesh_section(content: str, heading: str = "## CodeMesh") -> tuple[str, bool]:
    """Remove the CodeMesh section from a markdown file.

    Returns (new_content, was_modified).
    """
    if heading not in content:
        return content, False

    parts = content.split(heading, 1)
    before = parts[0]
    after = parts[1] if len(parts) > 1 else ""

    next_section_idx = -1
    if "\n## " in after:
        next_section_idx = after.index("\n## ")

    if next_section_idx >= 0:
        after = after[next_section_idx:]
        new_content = (before.rstrip("\n") + "\n\n" + after.lstrip("\n")).strip("\n") + "\n"
        if not new_content.strip():
            return "", True
        return new_content, True
    else:
        if before.strip():
            return before.rstrip("\n") + "\n", True
        else:
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

    removed: list[str] = []
    modified: list[str] = []

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
        cursor_rules.unlink()
        removed.append(str(cursor_rules))

    return {"removed": removed, "modified": modified}
