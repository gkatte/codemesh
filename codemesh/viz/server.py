# mypy: ignore-errors
"""FastAPI server for CodeMesh visualization."""

from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse

from codemesh.db.connection import create_connection, get_db_path
from codemesh.db.queries import count_edges, count_nodes, get_node, search_nodes_fts
from codemesh.viz.embedding_projector import get_embedding_stats, project_embeddings
from codemesh.viz.graph_builder import build_graph

TEMPLATES_DIR = Path(__file__).parent / "templates"


def create_app(root: Path) -> FastAPI:
    """Create the FastAPI application."""
    app = FastAPI(title="CodeMesh Visualization")

    @app.get("/", response_class=HTMLResponse)
    async def index():
        html_path = TEMPLATES_DIR / "index.html"
        return HTMLResponse(content=html_path.read_text())

    @app.get("/api/graph")
    async def get_graph(
        kind: Annotated[list[str] | None, Query()] = None,
        language: Annotated[list[str] | None, Query()] = None,
        file_pattern: Annotated[list[str] | None, Query()] = None,
        symbol: Annotated[str | None, Query()] = None,
        depth: Annotated[int, Query()] = 3,
    ):
        graph = build_graph(
            root=root,
            kind_filter=kind,
            language_filter=language,
            file_filter=file_pattern,
            symbol_focus=symbol,
            depth=depth,
        )
        return JSONResponse(content=graph)

    @app.get("/api/node/{node_id}")
    async def get_node_detail(node_id: str):
        db_path = get_db_path(root)
        conn = create_connection(db_path)
        try:
            node = get_node(conn, node_id)
            if node is None:
                raise HTTPException(status_code=404, detail="Node not found")
            return JSONResponse(
                content={
                    "id": node.id,
                    "name": node.name,
                    "kind": node.kind.value,
                    "qualified_name": node.qualified_name,
                    "file_path": str(node.file_path),
                    "language": node.language.value,
                    "start_line": node.start_line,
                    "end_line": node.end_line,
                    "docstring": node.docstring,
                    "signature": node.signature,
                    "visibility": node.visibility,
                }
            )
        finally:
            conn.close()

    @app.get("/api/stats")
    async def get_stats():
        db_path = get_db_path(root)
        conn = create_connection(db_path)
        try:
            return JSONResponse(
                content={
                    "total_nodes": count_nodes(conn),
                    "total_edges": count_edges(conn),
                }
            )
        finally:
            conn.close()

    @app.get("/api/search")
    async def search(q: str = Query(..., min_length=1)):
        db_path = get_db_path(root)
        conn = create_connection(db_path)
        try:
            results = search_nodes_fts(conn, q, limit=20)
            return JSONResponse(
                content=[
                    {
                        "id": node.id,
                        "name": node.name,
                        "kind": node.kind.value,
                        "file_path": str(node.file_path),
                        "rank": rank,
                    }
                    for node, rank in results
                ]
            )
        finally:
            conn.close()

    @app.get("/api/embeddings")
    async def get_embeddings(
        dims: Annotated[int, Query()] = 2,
    ):
        points = project_embeddings(root, n_components=dims)
        return JSONResponse(content=points)

    @app.get("/api/embedding-stats")
    async def embedding_stats():
        return JSONResponse(content=get_embedding_stats(root))

    return app


def run_server(
    root: Path,
    port: int = 8765,
    open_browser: bool = True,
) -> None:
    """Run the visualization server."""
    import uvicorn

    app = create_app(root)
    if open_browser:
        webbrowser.open(f"http://localhost:{port}")
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
