# mypy: ignore-errors
"""MCP server implementation using official SDK."""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_DEFAULT_ROOT: Path = Path.cwd()


def create_server(root: Path | None = None) -> object:
    """Create and configure the MCP server."""
    from mcp.server import Server  # type: ignore[import-untyped]

    from codemesh.mcp.tools import register_tools

    server = Server("codemesh")
    register_tools(server, root or _DEFAULT_ROOT)
    return server


def run_server(transport: str = "stdio", port: int = 3000, root: Path | None = None) -> None:
    """Run the MCP server."""
    if transport == "sse":
        import asyncio

        from mcp.server.sse import SseServerTransport  # type: ignore[import-untyped]
        from starlette.applications import Starlette
        from starlette.routing import Mount, Route

        server = create_server(root)
        sse = SseServerTransport("/messages")

        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as streams:
                await server.run(streams[0], streams[1], server.create_initialization_options())

        starlette_app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Mount("/messages", app=sse.handle_post_messages),
            ]
        )
        import uvicorn

        uvicorn.run(starlette_app, host="0.0.0.0", port=port)
    else:
        import asyncio

        from mcp.server.stdio import stdio_server

        async def main():
            server = create_server(root)
            async with stdio_server() as (read_stream, write_stream):
                await server.run(read_stream, write_stream, server.create_initialization_options())

        asyncio.run(main())
