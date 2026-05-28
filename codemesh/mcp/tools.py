# mypy: ignore-errors
"""MCP tool definitions for CodeMesh."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


async def _tool_search(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.context.builder import ContextBuilder, ContextOptions
    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    query = arguments["query"]
    path = Path(arguments.get("path", str(default_root)))
    limit = arguments.get("limit", 10)
    arguments.get("format", "xml")

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)
        results = qm.structural_search(query, max_depth=3)
        if not results:
            return [TextContent(type="text", text=f"No results for: {query}")]
        builder = ContextBuilder(conn, path)
        context = builder.build(results, query, ContextOptions(max_snippets=limit))
        return [TextContent(type="text", text=context)]


async def _tool_context(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.context.builder import ContextBuilder, ContextOptions
    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.queries import get_node
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    task = arguments.get("task", "")
    symbol = arguments.get("symbol", "")
    path = Path(arguments.get("path", str(default_root)))
    max_nodes = arguments.get("max_nodes", 50)
    include_code = arguments.get("include_code", True)

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)

        # If symbol is provided, get its context; otherwise search by task
        if symbol:
            subgraph = qm.find_dependents(symbol, max_depth=3)
            nodes_with_scores = [
                (n, tr.score)
                for nid, tr in subgraph.nodes.items()
                if (n := get_node(conn, nid)) is not None
            ]
            query_str = f"Context for {symbol}"
        else:
            results = qm.structural_search(task, max_depth=3)
            if not results:
                return [TextContent(type="text", text=f"No results for: {task}")]
            nodes_with_scores = results[:max_nodes]
            query_str = task

        builder = ContextBuilder(conn, path)
        opts = ContextOptions(
            max_snippets=max_nodes if include_code else 0,
            max_tokens=8000 * 4,
        )
        context = builder.build(nodes_with_scores, query_str, opts)
        return [TextContent(type="text", text=context)]


async def _tool_callers(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    symbol = arguments["symbol"]
    path = Path(arguments.get("path", str(default_root)))
    limit = arguments.get("limit", 20)

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)
        callers = qm.find_callers(symbol)[:limit]
        if not callers:
            return [TextContent(type="text", text=f"No callers found for: {symbol}")]
        lines = [f"Callers of '{symbol}' ({len(callers)}):", ""]
        for n in callers:
            lines.append(f"  {n.qualified_name} ({n.kind.value}) - {n.file_path}:{n.start_line}")
        return [TextContent(type="text", text="\n".join(lines))]


async def _tool_callees(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    symbol = arguments["symbol"]
    path = Path(arguments.get("path", str(default_root)))
    limit = arguments.get("limit", 20)

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)
        callees = qm.find_callees(symbol)[:limit]
        if not callees:
            return [TextContent(type="text", text=f"No callees found for: {symbol}")]
        lines = [f"Callees of '{symbol}' ({len(callees)}):", ""]
        for n in callees:
            lines.append(f"  {n.qualified_name} ({n.kind.value}) - {n.file_path}:{n.start_line}")
        return [TextContent(type="text", text="\n".join(lines))]


async def _tool_impact(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.queries import get_node
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    symbol = arguments["symbol"]
    path = Path(arguments.get("path", str(default_root)))
    arguments.get("depth", 3)

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)
        subgraph = qm.what_breaks_if_changed(symbol)
        affected = [n for nid in subgraph.nodes if (n := get_node(conn, nid)) is not None]
        if not affected:
            return [TextContent(type="text", text=f"No dependents found for: {symbol}")]
        lines = [f"Impact of changing '{symbol}' — {len(affected)} affected symbols:", ""]
        by_file: dict[str, list] = {}
        for n in affected:
            by_file.setdefault(str(n.file_path), []).append(n)
        for fp, nodes in sorted(by_file.items()):
            lines.append(fp)
            for n in nodes:
                lines.append(f"  {n.kind.value:10s} {n.name}:{n.start_line}")
            lines.append("")
        return [TextContent(type="text", text="\n".join(lines))]


async def _tool_node(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    symbol = arguments["symbol"]
    path = Path(arguments.get("path", str(default_root)))
    include_source = arguments.get("include_source", False)

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)
        node = qm.find_definition(symbol)
        if node is None:
            return [TextContent(type="text", text=f"Symbol not found: {symbol}")]
        lines = [
            f"**{node.name}** ({node.kind.value})",
            f"File: {node.file_path}:{node.start_line}-{node.end_line}",
            f"Qualified: {node.qualified_name}",
        ]
        if node.signature:
            lines.append(f"Signature: {node.signature}")
        if node.docstring:
            lines.append(f"Docstring: {node.docstring}")
        # Read source code from file if requested
        if include_source:
            try:
                source = Path(node.file_path).read_text()
                lines.append(f"\n```\n{source}\n```")
            except OSError:
                lines.append("(source unavailable)")
        return [TextContent(type="text", text="\n".join(lines))]


async def _tool_status(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db

    path = Path(arguments.get("path", str(default_root)))

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        file_count = conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM nodes WHERE kind = 'file'"
        ).fetchone()[0]

        kinds = conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM nodes GROUP BY kind ORDER BY cnt DESC"
        ).fetchall()
        edge_kinds = conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC"
        ).fetchall()

        lines = [
            "CodeMesh Index Status",
            "=" * 40,
            f"  Files:    {file_count}",
            f"  Nodes:    {node_count}",
            f"  Edges:    {edge_count}",
            "",
            "  Node kinds:",
        ]
        for row in kinds:
            lines.append(f"    {row['kind']:12s} {row['cnt']}")
        lines.append("  Edge kinds:")
        for row in edge_kinds:
            lines.append(f"    {row['kind']:12s} {row['cnt']}")

        return [TextContent(type="text", text="\n".join(lines))]


async def _tool_files(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db

    path = Path(arguments.get("path", str(default_root)))

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        rows = conn.execute(
            "SELECT DISTINCT file_path, language FROM nodes WHERE kind = 'file' ORDER BY file_path"
        ).fetchall()
        if not rows:
            return [TextContent(type="text", text="No files indexed. Run 'codemesh index' first.")]
        lines = [f"Indexed files: {len(rows)}", ""]
        for row in rows:
            lines.append(f"  {row['file_path']} ({row['language']})")
        return [TextContent(type="text", text="\n".join(lines))]


async def _tool_explore(arguments: dict, default_root: Path) -> list:
    """Full exploration tool — returns source for related symbols grouped by file."""
    from mcp.types import TextContent

    from codemesh.context.builder import ContextBuilder, ContextFormat, ContextOptions
    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.queries import get_node
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager
    from codemesh.graph.traverser import GraphTraverser

    query = arguments.get("query", "")
    symbol = arguments.get("symbol", "")
    path = Path(arguments.get("path", str(default_root)))
    max_nodes = arguments.get("max_nodes", 30)

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)
        traverser = GraphTraverser()

        # Get initial results
        if symbol:
            results = qm.structural_search(symbol, max_depth=2)
        elif query:
            results = qm.structural_search(query, max_depth=2)
        else:
            return [TextContent(type="text", text="Provide 'query' or 'symbol'")]

        if not results:
            return [TextContent(type="text", text=f"No results for: {query or symbol}")]

        # Expand via graph walk
        entry_nodes = results[:5]
        bm25_ids = {n.id for n, _ in results}
        expanded = list(results)

        for node, _score in entry_nodes:
            subgraph = traverser.traverse(conn, [node.id], max_depth=2, max_nodes=max_nodes)
            for nid in subgraph.nodes:
                if nid not in bm25_ids and len(expanded) < max_nodes:
                    bm25_ids.add(nid)
                    n = get_node(conn, nid)
                    if n is not None:
                        expanded.append((n, 0.0))

        # Build structured context with source
        builder = ContextBuilder(conn, path)
        entry_points = [(n, s) for n, s in expanded[:5] if s > 0]
        related = expanded[5:]

        opts = ContextOptions(
            max_snippets=10,
            max_tokens=16000 * 4,
            format=ContextFormat.STRUCTURED,
        )
        context = builder.build(
            expanded[:15],
            query or f"Explore: {symbol}",
            opts,
            entry_points=entry_points,
            related=related,
        )

        # Also build a relationship map
        node_ids = [n.id for n, _ in expanded[:15]]
        rel_lines = ["", "## Relationship Map", ""]
        seen_edges: set[str] = set()
        for nid in node_ids[:5]:
            node = get_node(conn, nid)
            if node is None:
                continue
            edges = conn.execute(
                "SELECT target_id, kind FROM edges WHERE source_id = ? LIMIT 10",
                (nid,),
            ).fetchall()
            for e in edges:
                tid = e["target_id"]
                if tid in node_ids:
                    tnode = get_node(conn, tid)
                    if tnode:
                        edge_key = f"{nid}->{tid}"
                        if edge_key not in seen_edges:
                            seen_edges.add(edge_key)
                            rel_lines.append(f"  {node.name} --[{e['kind']}]--> {tnode.name}")

        return [TextContent(type="text", text=context + "\n".join(rel_lines))]


async def _tool_graph(arguments: dict, default_root: Path) -> list:
    """MCP tool: get knowledge graph structure."""
    from mcp.types import TextContent

    from codemesh.viz.graph_builder import build_graph

    path = Path(arguments.get("path", str(default_root)))
    symbol = arguments.get("symbol")
    kind = arguments.get("kind")
    depth = arguments.get("depth", 3)

    kind_filter = [kind] if kind else None
    g = build_graph(path, kind_filter=kind_filter, symbol_focus=symbol, depth=depth)

    result = {
        "nodes": len(g["nodes"]),
        "edges": len(g["edges"]),
        "data": g,
    }
    return [TextContent(type="text", text=json.dumps(result, default=str))]


def register_tools(server: Any, root: Path) -> None:
    """Register all tools with the MCP server."""
    from mcp.types import Tool

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="codemesh_search",
                description="Search a codebase using BM25 keyword search + graph walk. Returns relevant symbols and code context.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Search query (e.g., 'auth', 'UserService', 'login flow')",
                        },
                        "path": {
                            "type": "string",
                            "default": str(root),
                            "description": "Project root path",
                        },
                        "limit": {"type": "integer", "default": 10, "description": "Max results"},
                        "format": {
                            "type": "string",
                            "default": "xml",
                            "enum": ["xml", "markdown", "structured"],
                            "description": "Output format",
                        },
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="codemesh_context",
                description="Build relevant code context for a task or symbol. Returns entry points, related symbols, and code snippets. Use this for exploration questions like 'how does X work?'.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "task": {
                            "type": "string",
                            "description": "Natural language task description (e.g., 'how does authentication work?')",
                        },
                        "symbol": {
                            "type": "string",
                            "description": "Symbol name to get context for",
                        },
                        "path": {
                            "type": "string",
                            "default": str(root),
                            "description": "Project root path",
                        },
                        "max_nodes": {
                            "type": "integer",
                            "default": 50,
                            "description": "Max nodes to include",
                        },
                        "include_code": {
                            "type": "boolean",
                            "default": True,
                            "description": "Include source code snippets",
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="codemesh_explore",
                description="Explore code related to a query or symbol. Returns source code sections from all relevant files plus a relationship map. This is the PRIMARY exploration tool — use it instead of file reads.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query for exploration"},
                        "symbol": {"type": "string", "description": "Symbol name to explore"},
                        "path": {
                            "type": "string",
                            "default": str(root),
                            "description": "Project root path",
                        },
                        "max_nodes": {
                            "type": "integer",
                            "default": 30,
                            "description": "Max symbols to return",
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="codemesh_callers",
                description="Find all functions/methods that call a specific symbol (caller → callee tracing).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol name to find callers for",
                        },
                        "path": {
                            "type": "string",
                            "default": str(root),
                            "description": "Project root path",
                        },
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="codemesh_callees",
                description="Find all functions/methods that a specific symbol calls (callee discovery).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol name to find callees for",
                        },
                        "path": {
                            "type": "string",
                            "default": str(root),
                            "description": "Project root path",
                        },
                        "limit": {"type": "integer", "default": 20},
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="codemesh_impact",
                description="Analyze what code would be affected by changing a symbol. Returns the transitive closure of dependents.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {
                            "type": "string",
                            "description": "Symbol name to analyze impact for",
                        },
                        "path": {
                            "type": "string",
                            "default": str(root),
                            "description": "Project root path",
                        },
                        "depth": {
                            "type": "integer",
                            "default": 3,
                            "description": "Max traversal depth",
                        },
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="codemesh_node",
                description="Get details about a specific symbol by name, optionally including source code.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string", "description": "Symbol name to look up"},
                        "path": {
                            "type": "string",
                            "default": str(root),
                            "description": "Project root path",
                        },
                        "include_source": {
                            "type": "boolean",
                            "default": False,
                            "description": "Include the full source code",
                        },
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="codemesh_status",
                description="Check index health and statistics — file/node/edge counts and breakdowns.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "default": str(root),
                            "description": "Project root path",
                        },
                    },
                    "required": [],
                },
            ),
            Tool(
                name="codemesh_files",
                description="Get indexed file structure (faster than filesystem scanning for large projects).",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "default": str(root),
                            "description": "Project root path",
                        },
                    },
                    "required": [],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            match name:
                case "codemesh_search":
                    return await _tool_search(arguments, root)
                case "codemesh_context":
                    return await _tool_context(arguments, root)
                case "codemesh_explore":
                    return await _tool_explore(arguments, root)
                case "codemesh_callers":
                    return await _tool_callers(arguments, root)
                case "codemesh_callees":
                    return await _tool_callees(arguments, root)
                case "codemesh_impact":
                    return await _tool_impact(arguments, root)
                case "codemesh_node":
                    return await _tool_node(arguments, root)
                case "codemesh_status":
                    return await _tool_status(arguments, root)
                case "codemesh_files":
                    return await _tool_files(arguments, root)
                case "codemesh_graph":
                    return await _tool_graph(arguments, root)
                case _:
                    from mcp.types import TextContent

                    return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except Exception as e:
            logger.exception("Tool error")
            from mcp.types import TextContent

            return [TextContent(type="text", text=f"Error: {e}")]
