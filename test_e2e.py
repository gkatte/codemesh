"""
Comprehensive E2E test for all CodeMesh MCP tools + CLI + install/uninstall.
Run from the test project directory with venv activated.
"""

import contextlib
import json
import os
import subprocess
import sys
from pathlib import Path

CODMESH_BIN = "codemesh"
PROJECT_ROOT = Path.cwd()


def mcp_call(proc_stdin, proc_stdout, msg_id, method, params):
    """Send an MCP message and read the response."""
    msg = {"jsonrpc": "2.0", "id": msg_id, "method": method, "params": params}
    proc_stdin.write(json.dumps(msg) + "\n")
    proc_stdin.flush()
    line = proc_stdout.readline()
    if not line:
        return None
    return json.loads(line)


def start_server(root=None):
    """Start the codemesh MCP server via stdio."""
    env = {**os.environ}
    if root:
        env["CODEMESH_ROOT"] = str(root)

    proc = subprocess.Popen(
        [CODMESH_BIN, "serve", "--transport", "stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )

    init_resp = mcp_call(
        proc.stdin,
        proc.stdout,
        1,
        "initialize",
        {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "e2e-test", "version": "1.0"},
        },
    )
    assert init_resp is not None, "Initialize returned no response"
    assert init_resp.get("result", {}).get("serverInfo", {}).get("name") == "codemesh"

    notif = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    proc.stdin.write(json.dumps(notif) + "\n")
    proc.stdin.flush()

    return proc


def call_tool(proc, name, arguments, expect_contains=None, expect_not_contain=None):
    """Call an MCP tool and check expectations."""
    msg_id = 100 + len(globals().get("_call_log", []))
    resp = mcp_call(
        proc.stdin, proc.stdout, msg_id, "tools/call", {"name": name, "arguments": arguments}
    )
    if resp is None:
        return "", False, "No response"

    content = resp.get("result", {}).get("content", [])
    text = "".join(c.get("text", "") for c in content if c.get("type") == "text")
    error = resp.get("error", {}).get("message", "")
    full_text = text + error

    if expect_contains:
        for s in expect_contains:
            if s not in full_text:
                return full_text, False, f"Expected '{s[:60]}' in output"
    if expect_not_contain:
        for s in expect_not_contain:
            if s in full_text:
                return full_text, False, f"Unexpected '{s[:60]}' in output"

    return full_text, True, ""


# ── Test symbol resolver ────────────────────────────────────────────────
# Symbols that exist in a typical autogen index (from samples/ and packages/)
TEST_CLASS = "BaseGroupChatAgent"  # class with known file location
TEST_FUNC = "create_agent"  # function with callers
TEST_FILE_FUNC = "extract_python_code_blocks"  # function in docs
TEST_QUERY = "GroupChat"  # BM25 query that returns results
TEST_NORESULT = "xyznonexistent12345"  # query with no results

# ── Regression test: discover_projects ──────────────────────────────────
# This symbol was previously buried at index 82/83 by graph-walk noise
# because structural_search() inverted BM25 scores (1/(1+rank)).
# Fixed in v0.1.14: BM25 scores now used directly, graph scores scaled < 0.5.
TEST_REGRESSION_SYMBOL = "discover_projects"
TEST_REGRESSION_FILE = "run_task_in_pkgs_if_exist.py"


def run_tests():
    proc = start_server(PROJECT_ROOT)
    passed = 0
    failed = 0
    results = []

    def check(label, text, ok, detail=""):
        nonlocal passed, failed
        if ok:
            passed += 1
            results.append(("PASS", label, text[:120]))
        else:
            failed += 1
            results.append(("FAIL", label, detail[:120]))

    print("=" * 60)
    print("CoDeMESH E2E TEST SUITE")
    print("=" * 60)

    # ── 1. codemesh_status ─────────────────────────────────────────────────
    print("\n[1] codemesh_status")
    text, ok, detail = call_tool(
        proc,
        "codemesh_status",
        {"path": str(PROJECT_ROOT)},
        expect_contains=["Files:", "Nodes:", "Edges:"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_status", text, ok, detail)

    # ── 2. codemesh_search (with results) ──────────────────────────────────
    print("\n[2] codemesh_search: query='GroupChat' limit=3")
    text, ok, detail = call_tool(
        proc,
        "codemesh_search",
        {"query": TEST_QUERY, "path": str(PROJECT_ROOT), "limit": 3},
        expect_contains=["GroupChat"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_search (results)", text, ok, detail)

    # ── 3. codemesh_search (no results) ────────────────────────────────────
    print("\n[3] codemesh_search: query='xyznonexistent12345' (no results)")
    text, ok, detail = call_tool(
        proc,
        "codemesh_search",
        {"query": TEST_NORESULT, "path": str(PROJECT_ROOT), "limit": 3},
        expect_contains=["No results"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_search (no results)", text, ok, detail)

    # ── 3b. codemesh_search (regression: discover_projects) ─────────────────
    # This exact query was reported broken: Claude Code called codemesh_search
    # and got back unrelated graph-walk nodes instead of the actual symbol.
    print(f"\n[3b] codemesh_search: query='{TEST_REGRESSION_SYMBOL}' (regression)")
    text, ok, detail = call_tool(
        proc,
        "codemesh_search",
        {"query": TEST_REGRESSION_SYMBOL, "path": str(PROJECT_ROOT), "limit": 5},
        expect_contains=[TEST_REGRESSION_SYMBOL, TEST_REGRESSION_FILE],
    )
    # Also verify the result appears in the top 2 (not buried at index 82)
    top_result_ok = False
    if ok:
        # Parse: the first <snippet> should contain discover_projects
        first_snippet_start = text.find("<snippet")
        first_snippet_end = text.find("</snippet>", first_snippet_start)
        first_snippet = (
            text[first_snippet_start:first_snippet_end] if first_snippet_start >= 0 else ""
        )
        top_result_ok = TEST_REGRESSION_SYMBOL in first_snippet and "relevance=" in first_snippet
        if not top_result_ok:
            detail = f"discover_projects not in first snippet: {first_snippet[:100]}"
            ok = False
    print(f"    {'PASS' if ok else 'FAIL'}: top_result={top_result_ok}, {text[:80]}")
    check("codemesh_search (regression: discover_projects)", text, ok, detail)

    # ── 3c. codemesh_explore (regression: discover_projects) ────────────────
    # Claude Code falls back to explore if search doesn't find it directly
    print(f"\n[3c] codemesh_explore: symbol='{TEST_REGRESSION_SYMBOL}' (regression)")
    text, ok, detail = call_tool(
        proc,
        "codemesh_explore",
        {"symbol": TEST_REGRESSION_SYMBOL, "path": str(PROJECT_ROOT), "max_nodes": 10},
        expect_contains=[TEST_REGRESSION_SYMBOL, TEST_REGRESSION_FILE],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_explore (regression: discover_projects)", text, ok, detail)

    # ── 4. codemesh_context (by symbol) ────────────────────────────────────
    print(f"\n[4] codemesh_context: symbol='{TEST_CLASS}'")
    text, ok, detail = call_tool(
        proc,
        "codemesh_context",
        {"symbol": TEST_CLASS, "path": str(PROJECT_ROOT), "max_nodes": 10},
        expect_contains=["code_context"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_context (symbol)", text, ok, detail)

    # ── 5. codemesh_context (by task) ──────────────────────────────────────
    print("\n[5] codemesh_context: task='how does group chat work'")
    text, ok, detail = call_tool(
        proc,
        "codemesh_context",
        {"task": "how does group chat work", "path": str(PROJECT_ROOT), "max_nodes": 5},
        expect_not_contain=["Error:", "Traceback"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_context (task)", text, ok, detail)

    # ── 6. codemesh_explore (by query) ─────────────────────────────────────
    print(f"\n[6] codemesh_explore: query='{TEST_QUERY}' max_nodes=15")
    text, ok, detail = call_tool(
        proc,
        "codemesh_explore",
        {"query": TEST_QUERY, "path": str(PROJECT_ROOT), "max_nodes": 15},
        expect_contains=["GroupChat"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_explore (query)", text, ok, detail)

    # ── 7. codemesh_explore (by symbol) ────────────────────────────────────
    print(f"\n[7] codemesh_explore: symbol='{TEST_CLASS}' max_nodes=10")
    text, ok, detail = call_tool(
        proc,
        "codemesh_explore",
        {"symbol": TEST_CLASS, "path": str(PROJECT_ROOT), "max_nodes": 10},
        expect_contains=[TEST_CLASS],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_explore (symbol)", text, ok, detail)

    # ── 8. codemesh_callers ─────────────────────────────────────────────────
    print(f"\n[8] codemesh_callers: symbol='{TEST_FUNC}' limit=5")
    text, ok, detail = call_tool(
        proc,
        "codemesh_callers",
        {"symbol": TEST_FUNC, "path": str(PROJECT_ROOT), "limit": 5},
        expect_contains=["Callers of"],
    )
    has_results = "test_" in text or "Callers of" in text
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_callers", text, ok and has_results, detail)

    # ── 9. codemesh_callees ─────────────────────────────────────────────────
    print(f"\n[9] codemesh_callees: symbol='{TEST_FUNC}' limit=5")
    text, ok, detail = call_tool(
        proc,
        "codemesh_callees",
        {"symbol": TEST_FUNC, "path": str(PROJECT_ROOT), "limit": 5},
        expect_not_contain=["Traceback"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_callees", text, ok, detail)

    # ── 10. codemesh_impact ─────────────────────────────────────────────────
    print(f"\n[10] codemesh_impact: symbol='{TEST_CLASS}' depth=2")
    text, ok, detail = call_tool(
        proc,
        "codemesh_impact",
        {"symbol": TEST_CLASS, "path": str(PROJECT_ROOT), "depth": 2},
        expect_not_contain=["Traceback"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_impact", text, ok, detail)

    # ── 11. codemesh_node (with source) ────────────────────────────────────
    print(f"\n[11] codemesh_node: symbol='{TEST_CLASS}' include_source=True")
    text, ok, detail = call_tool(
        proc,
        "codemesh_node",
        {"symbol": TEST_CLASS, "path": str(PROJECT_ROOT), "include_source": True},
        expect_contains=[TEST_CLASS, "File:", "(class)"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_node (with source)", text, ok, detail)

    # ── 12. codemesh_node (not found) ──────────────────────────────────────
    print(f"\n[12] codemesh_node: symbol='{TEST_NORESULT}' (not found)")
    text, ok, detail = call_tool(
        proc,
        "codemesh_node",
        {"symbol": TEST_NORESULT, "path": str(PROJECT_ROOT)},
        expect_contains=["Symbol not found"],
    )
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("codemesh_node (not found)", text, ok, detail)

    # ── 13. codemesh_files ──────────────────────────────────────────────────
    print("\n[13] codemesh_files")
    text, ok, detail = call_tool(
        proc, "codemesh_files", {"path": str(PROJECT_ROOT)}, expect_contains=["Indexed files:"]
    )
    # Verify many files
    file_count = 0
    if ok:
        with contextlib.suppress(IndexError, ValueError):
            file_count = int(text.split("Indexed files:")[1].split("\n")[0].strip())
    print(f"    {'PASS' if ok else 'FAIL'}: {file_count} files indexed")
    check("codemesh_files", text, ok and file_count > 10, f"Expected >10 files, got {file_count}")

    # ── 14. codemesh_graph ──────────────────────────────────────────────────
    print(f"\n[14] codemesh_graph: symbol='{TEST_CLASS}' depth=2")
    text, ok, detail = call_tool(
        proc,
        "codemesh_graph",
        {"symbol": TEST_CLASS, "path": str(PROJECT_ROOT), "depth": 2},
        expect_contains=['"nodes"', '"edges"'],
    )
    if ok:
        try:
            # Extract JSON from response
            json_start = text.find('{"nodes"')
            if json_start >= 0:
                data = json.loads(text[json_start : json_start + 200])
                print(f"    PASS: {data.get('nodes', '?')} nodes, {data.get('edges', '?')} edges")
            else:
                print("    PASS: (graph response received)")
        except json.JSONDecodeError:
            print("    PASS: (graph response, parse skipped)")
    else:
        print(f"    FAIL: {detail}")
    check("codemesh_graph", text, ok, detail)

    # ── 15. Unknown tool ────────────────────────────────────────────────────
    print("\n[17] Unknown tool: 'nonexistent_tool_xyz'")
    text, ok, detail = call_tool(proc, "nonexistent_tool_xyz", {}, expect_contains=["Unknown tool"])
    print(f"    {'PASS' if ok else 'FAIL'}: {text[:80]}")
    check("unknown_tool", text, ok, detail)

    # ── Summary ─────────────────────────────────────────────────────────────
    proc.stdin.close()  # type: ignore[union-attr]
    proc.wait(timeout=10)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    for status, label, detail in results:
        marker = "✓" if status == "PASS" else "✗"
        print(f"  [{marker}] {label}")
        if status == "FAIL":
            print(f"      → {detail}")
    print(f"\n{passed} passed, {failed} failed, {passed + failed} total")
    print("=" * 60)

    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    run_tests()
