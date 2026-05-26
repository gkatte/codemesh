# mypy: ignore-errors
"""MCP tool definitions for CodeMesh."""

from __future__ import annotations

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

    symbol = arguments["symbol"]
    path = Path(arguments.get("path", str(default_root)))

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)
        subgraph = qm.find_dependents(symbol, max_depth=3)
        nodes_with_scores = [
            (n, tr.score)
            for nid, tr in subgraph.nodes.items()
            if (n := get_node(conn, nid)) is not None
        ]
        builder = ContextBuilder(conn, path)
        context = builder.build(nodes_with_scores, f"Context for {symbol}", ContextOptions())
        return [TextContent(type="text", text=context)]


async def _tool_definition(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    symbol = arguments["symbol"]
    path = Path(arguments.get("path", str(default_root)))

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)
        node = qm.find_definition(symbol)
        if node is None:
            return [TextContent(type="text", text=f"Symbol not found: {symbol}")]
        result = f"**{node.name}** ({node.kind.value})\nFile: {node.file_path}:{node.start_line}-{node.end_line}\nQualified: {node.qualified_name}\n"
        if node.signature:
            result += f"Signature: {node.signature}\n"
        return [TextContent(type="text", text=result)]


async def _tool_impact(arguments: dict, default_root: Path) -> list:
    from mcp.types import TextContent

    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.queries import get_node
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    symbol = arguments["symbol"]
    path = Path(arguments.get("path", str(default_root)))

    init_db(get_db_path(path))
    with get_connection(get_db_path(path)) as conn:
        qm = QueryManager(conn)
        subgraph = qm.what_breaks_if_changed(symbol)
        affected = [
            f"  - {n.qualified_name} ({n.file_path}:{n.start_line})"
            for nid in subgraph.nodes
            if (n := get_node(conn, nid)) is not None
        ]
        if not affected:
            return [TextContent(type="text", text=f"No dependents found for: {symbol}")]
        return [
            TextContent(
                type="text",
                text=f"**Impact analysis for {symbol}:**\n{len(affected)} symbols depend on {symbol}:\n"
                + "\n".join(affected),
            )
        ]


def register_tools(server: Any, root: Path) -> None:
    """Register all tools with the MCP server."""
    from mcp.types import Tool

    @server.list_tools()
    async def list_tools():
        return [
            Tool(
                name="codemesh_search",
                description="Search a codebase using BM25 keyword search + graph walk",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "path": {"type": "string", "default": str(root)},
                        "limit": {"type": "integer", "default": 10},
                    },
                    "required": ["query"],
                },
            ),
            Tool(
                name="codemesh_context",
                description="Get code context for a symbol",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "path": {"type": "string", "default": str(root)},
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="codemesh_definition",
                description="Find the definition of a symbol",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "path": {"type": "string", "default": str(root)},
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="codemesh_impact",
                description="Analyze what breaks if a symbol is changed",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "path": {"type": "string", "default": str(root)},
                    },
                    "required": ["symbol"],
                },
            ),
            Tool(
                name="codemesh_graph",
                description="Get the knowledge graph structure for a codebase. Returns nodes and edges in JSON format. Optionally filter by symbol, kind, or depth.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "default": str(root)},
                        "symbol": {"type": "string"},
                        "kind": {"type": "string"},
                        "depth": {"type": "integer", "default": 3},
                    },
                    "required": [],
                },
            ),
        ]

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        try:
            if name == "codemesh_search":
                return await _tool_search(arguments, root)
            elif name == "codemesh_context":
                return await _tool_context(arguments, root)
            elif name == "codemesh_definition":
                return await _tool_definition(arguments, root)
            elif name == "codemesh_impact":
                return await _tool_impact(arguments, root)
            elif name == "codemesh_graph":
                return await _tool_graph(arguments, root)
            else:
                from mcp.types import TextContent

                return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except Exception as e:
            logger.exception("Tool error")
            from mcp.types import TextContent

            return [TextContent(type="text", text=f"Error: {e}")]


async def _tool_graph(arguments: dict, default_root: Path) -> list:
    """MCP tool: get knowledge graph structure."""
    import json

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
