# mypy: ignore-errors
"""CodeMesh CLI entry point."""

from __future__ import annotations

from pathlib import Path

import typer

app = typer.Typer(
    name="codemesh",
    help="BM25 keyword search + graph walk for code intelligence",
    no_args_is_help=True,
)


@app.command()
def init(
    path: str = typer.Argument(".", help="Path to the project to initialize"),
    interactive: bool = typer.Option(
        False, "-i", "--interactive", help="Interactive mode — prompts before overwriting files"
    ),
    index_project: bool = typer.Option(
        False, "--index", help="Also index the project after initialization"
    ),
) -> None:
    """Initialize CodeMesh in a project.

    Creates .codemesh/ directory and writes agent instruction files
    (CLAUDE.md, .cursor/rules/codemesh.mdc, AGENTS.md).
    """
    from codemesh.cli.init import init_project

    root = Path(path).resolve()
    if not root.exists():
        typer.echo(f"Error: {root} does not exist", err=True)
        raise typer.Exit(1)

    created = init_project(root, interactive=interactive)
    typer.echo(f"CodeMesh initialized in {root}")
    for key, val in created.items():
        typer.echo(f"  {key}: {val}")

    if index_project:
        from codemesh.indexer import index_project as do_index

        typer.echo(f"\nIndexing {root}...")
        stats = do_index(root, quiet=True)
        typer.echo(
            f"Done! {stats['nodes']} nodes, {stats['edges']} edges "
            f"indexed in {stats.get('time_seconds', 0):.1f}s."
        )


@app.command()
def install(
    target: str = typer.Option(
        "auto",
        "--target",
        "-t",
        help="Agent(s) to configure: auto, all, claude, cursor, codex, or comma-separated list",
    ),
    global_config: bool = typer.Option(
        True, "--global/--local", help="Write global config (default) or project-local"
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Non-interactive mode"),
    path: str = typer.Option(".", "--path", "-p", help="Project path for local config"),
) -> None:
    """Install CodeMesh MCP server configuration for AI coding agents.

    Auto-detects installed agents and writes MCP server config + permissions.
    Supports Claude Code, Cursor, and Codex CLI.
    """
    from codemesh.cli.install_cmd import (
        detect_agents,
        install_claude,
        install_codex,
        install_cursor,
    )

    root = Path(path).resolve()
    targets = target.lower().split(",") if target not in ("auto", "all") else [target]

    if "auto" in targets:
        detected = detect_agents()
        if not detected:
            typer.echo("No AI coding agents detected. Use --target to specify manually.")
            raise typer.Exit(1)
        targets = detected
        if not yes:
            typer.echo(f"Detected agents: {', '.join(targets)}")
            typer.confirm("Configure these agents?", abort=True)

    if "all" in targets:
        targets = ["claude", "cursor", "codex"]

    results = {}
    for agent in targets:
        agent = agent.strip()
        if agent == "claude":
            r = install_claude(root, global_config=global_config)
            results["claude"] = r
        elif agent == "cursor":
            r = install_cursor(root)
            results["cursor"] = r
        elif agent == "codex":
            r = install_codex(root)
            results["codex"] = r
        else:
            typer.echo(f"Unknown agent: {agent}", err=True)

    typer.echo("CodeMesh MCP server configured:")
    for agent, r in results.items():
        for key, val in r.items():
            if val:
                typer.echo(f"  {agent}/{key}: {val}")

    # Also init the project if not already
    codemesh_dir = root / ".codemesh"
    if not codemesh_dir.exists():
        from codemesh.cli.init import init_project

        init_project(root)
        typer.echo(f"\nInitialized .codemesh/ in {root}")

    typer.echo("\nRestart your agent(s) for the MCP server to load.")


@app.command()
def index(
    path: str = typer.Argument(".", help="Path to the codebase to index"),
    workers: int | None = typer.Option(None, "--workers", "-w", help="Number of parallel workers"),
    force: bool = typer.Option(
        False, "--force", "-f", help="Force re-index even if already indexed"
    ),
    quiet: bool = typer.Option(False, "--quiet", "-q", help="Minimal output"),
) -> None:
    """Index a codebase for BM25 search."""
    from codemesh.indexer import index_project

    root = Path(path).resolve()
    if not root.exists():
        typer.echo(f"Error: {root} does not exist", err=True)
        raise typer.Exit(1)

    typer.echo(f"Indexing {root}...")
    stats = index_project(root, max_workers=workers, quiet=quiet)
    if quiet:
        typer.echo(
            f"Done! {stats['nodes']} nodes, {stats['edges']} edges "
            f"indexed in {stats.get('time_seconds', 0):.1f}s."
        )
    # When not quiet, the progress bar already shows completion


@app.command()
def sync(
    path: str = typer.Argument(".", help="Path to watch for changes"),
    debounce: float = typer.Option(1.0, "--debounce", "-d", help="Debounce delay in seconds"),
) -> None:
    """Watch for file changes and auto-sync the index.

    Uses native OS file events (FSEvents/inotify) with debounced auto-sync.
    The graph stays current as you code.
    """
    from codemesh.indexer import sync_project

    root = Path(path).resolve()
    typer.echo(f"Watching {root} for changes... (Ctrl+C to stop)")
    sync_project(root, debounce_delay=debounce)


@app.command()
def query(
    q: str = typer.Argument(..., help="Query string"),
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
    limit: int = typer.Option(10, "--limit", "-l", help="Max results"),
    fmt: str = typer.Option(
        "xml", "--format", "-f", help="Output format: xml, markdown, structured, or json"
    ),
) -> None:
    """Query the indexed codebase."""
    from codemesh.querier import query_codebase

    root = Path(path).resolve()
    result = query_codebase(root, q, limit=limit, fmt=fmt)
    typer.echo(result)


@app.command()
def callers(
    symbol: str = typer.Argument(..., help="Symbol to find callers for"),
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
) -> None:
    """Find all functions/methods that call a specific symbol."""
    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    root = Path(path).resolve()
    init_db(get_db_path(root))
    with get_connection(get_db_path(root)) as conn:
        qm = QueryManager(conn)
        callers = qm.find_callers(symbol)
        if not callers:
            typer.echo(f'No callers found for "{symbol}"')
            return
        typer.echo(f'Callers of "{symbol}" ({len(callers)}):')
        typer.echo("")
        for n in callers:
            sig = f"  {n.qualified_name} ({n.kind.value}) - {n.file_path}:{n.start_line}"
            typer.echo(sig)


@app.command()
def callees(
    symbol: str = typer.Argument(..., help="Symbol to find callees for"),
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
) -> None:
    """Find all functions/methods that a specific symbol calls."""
    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    root = Path(path).resolve()
    init_db(get_db_path(root))
    with get_connection(get_db_path(root)) as conn:
        qm = QueryManager(conn)
        callees = qm.find_callees(symbol)
        if not callees:
            typer.echo(f'No callees found for "{symbol}"')
            return
        typer.echo(f'Callees of "{symbol}" ({len(callees)}):')
        typer.echo("")
        for n in callees:
            sig = f"  {n.qualified_name} ({n.kind.value}) - {n.file_path}:{n.start_line}"
            typer.echo(sig)


@app.command()
def impact(
    symbol: str = typer.Argument(..., help="Symbol to analyze impact for"),
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
    depth: int = typer.Option(3, "--depth", "-d", help="Max traversal depth"),
) -> None:
    """Analyze what code is affected by changing a symbol."""
    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.queries import get_node
    from codemesh.db.schema import init_db
    from codemesh.graph.query_manager import QueryManager

    root = Path(path).resolve()
    init_db(get_db_path(root))
    with get_connection(get_db_path(root)) as conn:
        qm = QueryManager(conn)
        subgraph = qm.what_breaks_if_changed(symbol)
        affected = [n for nid in subgraph.nodes if (n := get_node(conn, nid)) is not None]
        if not affected:
            typer.echo(f'No dependents found for "{symbol}"')
            return
        typer.echo(f'Impact of changing "{symbol}" — {len(affected)} affected symbols:')
        typer.echo("")
        # Group by file
        by_file: dict[str, list] = {}
        for n in affected:
            fp = str(n.file_path)
            by_file.setdefault(fp, []).append(n)
        for fp, nodes in sorted(by_file.items()):
            typer.echo(fp)
            for n in nodes:
                typer.echo(f"  {n.kind.value:10s} {n.name}:{n.start_line}")
            typer.echo("")


@app.command()
def context(
    symbol: str = typer.Argument(..., help="Symbol to get context for"),
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
    tokens: int = typer.Option(8000, "--tokens", "-t", help="Token budget"),
    fmt: str = typer.Option(
        "xml", "--format", "-f", help="Output format: xml, markdown, or structured"
    ),
    max_nodes: int = typer.Option(50, "--max-nodes", "-n", help="Max nodes to include"),
    max_code: int = typer.Option(10, "--max-code", "-c", help="Max code blocks"),
    no_code: bool = typer.Option(False, "--no-code", help="Exclude code blocks"),
) -> None:
    """Get context for a symbol (or general task).

    Builds structured context with Entry Points, Related Symbols, and Code.
    Similar to a context command for code intelligence.
    """
    from codemesh.querier import get_context

    root = Path(path).resolve()

    # If format is structured, we need to handle it differently
    if fmt == "structured":
        from codemesh.context.builder import ContextBuilder, ContextFormat, ContextOptions
        from codemesh.db.connection import get_connection, get_db_path
        from codemesh.db.queries import get_node, search_nodes_fts
        from codemesh.db.schema import init_db
        from codemesh.graph.traverser import GraphTraverser

        init_db(get_db_path(root))
        with get_connection(get_db_path(root)) as conn:
            # Search for the symbol
            results = search_nodes_fts(conn, symbol, limit=max_nodes)
            if not results:
                typer.echo(f"No results for: {symbol}")
                return

            # Separate entry points (top results) from related (graph expansion)
            traverser = GraphTraverser()
            bm25_ids = {n.id for n, _ in results[:10]}
            expanded = list(results[:10])

            for node, _score in results[:5]:
                subgraph = traverser.traverse(conn, [node.id], max_depth=1, max_nodes=20)
                for nid, tr in subgraph.nodes.items():
                    if nid not in bm25_ids and len(expanded) < max_nodes:
                        bm25_ids.add(nid)
                        n = get_node(conn, nid)
                        if n is not None:
                            expanded.append((n, tr.score))

            entry_points = expanded[:5]
            related = expanded[5:max_nodes]

            builder = ContextBuilder(conn, root)
            context = builder.build(
                expanded[:max_code] if not no_code else [],
                symbol,
                ContextOptions(
                    max_snippets=max_code if not no_code else 0,
                    max_tokens=tokens * 4,
                    format=ContextFormat.STRUCTURED,
                ),
                entry_points=entry_points,
                related=related,
            )
            typer.echo(context)
        return

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


@app.command()
def files(
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
) -> None:
    """Show project file structure from the index."""
    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db

    root = Path(path).resolve()
    init_db(get_db_path(root))
    with get_connection(get_db_path(root)) as conn:
        # Get file nodes
        file_rows = conn.execute(
            "SELECT DISTINCT file_path, language FROM nodes WHERE kind = 'file' ORDER BY file_path"
        ).fetchall()
        if not file_rows:
            typer.echo("No files indexed. Run 'codemesh index' first.")
            return

        # Count nodes per file
        counts = conn.execute(
            "SELECT file_path, kind, COUNT(*) as cnt FROM nodes GROUP BY file_path, kind ORDER BY file_path"
        ).fetchall()

        by_file: dict[str, dict[str, int]] = {}
        for row in counts:
            fp = row["file_path"]
            by_file.setdefault(fp, {})[row["kind"]] = row["cnt"]

        typer.echo(f"Indexed files: {len(file_rows)}")
        typer.echo("")
        for row in file_rows:
            fp = row["file_path"]
            lang = row["language"]
            kinds = by_file.get(fp, {})
            total = sum(kinds.values())
            kind_str = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items()) if k != "file")
            typer.echo(f"  {fp} ({lang}, {total} nodes: {kind_str})")


@app.command()
def status(
    path: str = typer.Option(".", "--path", "-p", help="Path to the indexed codebase"),
) -> None:
    """Show index status and statistics."""
    from codemesh.db.connection import get_connection, get_db_path
    from codemesh.db.schema import init_db

    root = Path(path).resolve()
    init_db(get_db_path(root))
    with get_connection(get_db_path(root)) as conn:
        node_count = conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        file_count = conn.execute(
            "SELECT COUNT(DISTINCT file_path) FROM nodes WHERE kind = 'file'"
        ).fetchone()[0]

        # Node kinds breakdown
        kinds = conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM nodes GROUP BY kind ORDER BY cnt DESC"
        ).fetchall()

        # Edge kinds breakdown
        edge_kinds = conn.execute(
            "SELECT kind, COUNT(*) as cnt FROM edges GROUP BY kind ORDER BY cnt DESC"
        ).fetchall()

        typer.echo("CodeMesh Index Status")
        typer.echo("=" * 40)
        typer.echo(f"  Files:    {file_count}")
        typer.echo(f"  Nodes:    {node_count}")
        typer.echo(f"  Edges:    {edge_count}")
        typer.echo("")
        typer.echo("  Node kinds:")
        for row in kinds:
            typer.echo(f"    {row['kind']:12s} {row['cnt']}")
        typer.echo("  Edge kinds:")
        for row in edge_kinds:
            typer.echo(f"    {row['kind']:12s} {row['cnt']}")
