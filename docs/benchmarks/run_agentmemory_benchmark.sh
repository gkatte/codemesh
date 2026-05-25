#!/bin/bash
# Run this script to benchmark CodeMesh with embeddings on agentmemory
# It will take several minutes due to embedding computation

cd ~/projects/agentmemory

# Clean old index
echo "=== Cleaning old index ==="
rm -rf .codemesh

# Index with embeddings
echo "=== CodeMesh Index (with embeddings) ==="
date
time ~/project/research/codemesh/.venv/bin/python3 -m codemesh index "." --workers 4 --embed 2>&1
echo "=== Index Done ==="
date

# Check stats
echo "=== Index Stats ==="
sqlite3 .codemesh/index.db "SELECT 'nodes:', COUNT(*) FROM nodes; SELECT 'edges:', COUNT(*) FROM edges; SELECT 'embeddings:', COUNT(*) FROM nodes WHERE embedding IS NOT NULL;"
sqlite3 .codemesh/index.db "SELECT model_name, dimensions, total_vectors, datetime(indexed_at, 'unixepoch') FROM embedding_index_meta;"

# Query benchmark (3 runs)
echo "=== CodeMesh Query (run 1) ==="
time ~/project/research/codemesh/.venv/bin/python3 -m codemesh query "explain how this works in claude-code" --path "." --limit 10 --format markdown 2>&1

echo "=== CodeMesh Query (run 2) ==="
time ~/project/research/codemesh/.venv/bin/python3 -m codemesh query "explain how this works in claude-code" --path "." --limit 10 --format markdown 2>&1

echo "=== CodeMesh Query (run 3) ==="
time ~/project/research/codemesh/.venv/bin/python3 -m codemesh query "explain how this works in claude-code" --path "." --limit 10 --format markdown 2>&1

# Context command
echo "=== CodeMesh Context ==="
time ~/project/research/codemesh/.venv/bin/python3 -m codemesh context "explain how this works in claude-code" --path "." --tokens 8000 2>&1

echo "=== BENCHMARK COMPLETE ==="
