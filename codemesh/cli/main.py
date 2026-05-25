# mypy: ignore-errors
"""CodeMesh CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    name="codemesh",
    help="Graph-enhanced retrieval-augmented generation for code",
    no_args_is_help=True,
)


@app.command()
def index(
    path: str = typer.Argument(".", help="Path to the codebase to index"),
    workers: int | None = typer.Option(None, "--workers", "-w", help="Number of parallel workers"),
    embed: bool = typer.Option(True, "--embed/--no-embed", help="Compute neural embeddings"),
) -> None:
    """Index a codebase."""
    from codemesh.indexer import index_project

    root = Path(path).resolve()
    if not root.exists():
        typer.echo(f"Error: {root} does not exist", err=True)
        raise typer.Exit(1)

    typer.echo(f"Indexing {root}...")
    stats = index_project(root, max_workers=workers, embed=embed)
    parts = [f"{stats['nodes']} nodes", f"{stats['edges']} edges"]
    if stats.get("embeddings", 0) > 0:
        parts.append(f"{stats['embeddings']} embeddings")
    typer.echo(f"Done! {', '.join(parts)} indexed in {stats.get('time_seconds', 0):.1f}s.")


@app.command()
def sync(
    path: str = typer.Argument(".", help="Path to watch for changes"),
) -> None:
    """Watch for file changes and sync the index."""
    from codemesh.indexer import sync_project

    root = Path(path).resolve()
    typer.echo(f"Watching {root} for changes... (Ctrl+C to stop)")
    sync_project(root)


@app.command()
def query(
    q: str = typer.Argument(..., help="Query string"),
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max results"),
    fmt: str = typer.Option("xml", "--format", "-f", help="Output format: xml or markdown"),
) -> None:
    """Query the indexed codebase."""
    from codemesh.querier import query_codebase

    root = Path(path).resolve()
    result = query_codebase(root, q, limit=limit, fmt=fmt)
    typer.echo(result)


@app.command()
def context(
    symbol: str = typer.Argument(..., help="Symbol to get context for"),
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
    tokens: int = typer.Option(8000, "--tokens", "-t", help="Token budget"),
) -> None:
    """Get context for a symbol."""
    from codemesh.querier import get_context

    root = Path(path).resolve()
    result = get_context(root, symbol, max_tokens=tokens)
    typer.echo(result)


@app.command()
def serve(
    transport: str = typer.Option("stdio", "--transport", help="Transport: stdio or sse"),
    port: int = typer.Option(3000, "--port", "-p", help="Port for SSE transport"),
) -> None:
    """Start the MCP server."""
    from codemesh.mcp.server import run_server

    run_server(transport=transport, port=port)


@app.command()
def graph(
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
    port: int = typer.Option(8765, "--port", help="Port for the visualization server"),
    symbol: str | None = typer.Option(None, "--symbol", "-s", help="Focus on a specific symbol"),
    kind: str | None = typer.Option(None, "--kind", "-k", help="Filter by node kind"),
    depth: int = typer.Option(3, "--depth", "-d", help="BFS depth for symbol focus"),
    export_json: str | None = typer.Option(None, "--json", help="Export graph as JSON to file"),
) -> None:
    """Open interactive graph visualization in browser."""
    import json as json_mod
    from pathlib import Path as Path2

    from codemesh.viz.graph_builder import build_graph

    root = Path(path).resolve()

    if export_json:
        g = build_graph(
            root, kind_filter=[kind] if kind else None, symbol_focus=symbol, depth=depth
        )
        Path2(export_json).write_text(json_mod.dumps(g, indent=2, default=str))
        typer.echo(
            f"Graph exported to {export_json} ({len(g['nodes'])} nodes, {len(g['edges'])} edges)"
        )
        return

    from codemesh.viz.server import run_server

    run_server(root=root, port=port, open_browser=True)
