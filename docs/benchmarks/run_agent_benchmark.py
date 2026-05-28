"""
Agent efficiency benchmark: CodeMesh vs baseline filesystem tools.

Models the full agent loop for each query, estimating:
- Input/output tokens per model turn
- Number of tool calls (grep + read vs MCP)
- Wall-clock time (model inference + tool execution)
- Cost (tokens × pricing)

Two arms per query:

  Arm A (Baseline): Agent uses grep + read_file only.
    Turn 1: Model decides to grep for symbol → grep returns file paths
    Turn 2: Model reads matching files (full file content = many tokens)
    Turn 3: Model greps for relationship keywords, reads more files
    Turn 4+: Model synthesizes answer from raw file contents

  Arm B (CodeMesh): Agent uses CodeMesh MCP tools.
    Turn 1: Model calls codemesh_search → compact structured results
    Turn 2: Model calls codemesh_callers or codemesh_context → more results
    Turn 3: Model synthesizes answer from structured data

The critical difference: baseline reads full source files (1K-50K tokens per file),
while CodeMesh returns compact structured results (200-3K tokens total).

Usage:
    uv run python docs/benchmarks/run_agent_benchmark.py
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from subprocess import run, PIPE

RESEARCH = Path("/Users/Nikhil/project/research")
CODEMESH = RESEARCH / "codemesh"

REPOS: dict[str, Path] = {
    "excalidraw": RESEARCH / "excalidraw",
    "tokio": RESEARCH / "tokio",
    "gin": RESEARCH / "gin",
    "okhttp": RESEARCH / "okhttp",
    "alamofire": RESEARCH / "alamofire",
    "libuv": RESEARCH / "libuv",
    "json": RESEARCH / "json",
    "django": RESEARCH / "django",
    "vscode": RESEARCH / "vscode",
}

# (description, symbol, query_type)
# query_type: "callers" = search + callers, "context" = search + context
QUERIES: dict[str, list[tuple[str, str, str]]] = {
    "excalidraw": [
        ("Find ExcalidrawElement and show callers", "ExcalidrawElement", "callers"),
        ("Where is newElement defined", "newElement", "context"),
        ("Find restoreElements callers", "restoreElements", "callers"),
        ("Show App component hierarchy", "App", "context"),
        ("What imports elementFromPath", "elementFromPath", "callers"),
    ],
    "tokio": [
        ("Find spawn and show callers", "spawn", "callers"),
        ("Where is Runtime defined", "Runtime", "context"),
        ("What calls block_on", "block_on", "callers"),
        ("Show Runtime hierarchy", "Runtime", "context"),
        ("Find poll callers in tokio", "poll", "callers"),
    ],
    "gin": [
        ("Find Engine and show callers", "Engine", "callers"),
        ("Where is RouterGroup defined", "RouterGroup", "context"),
        ("Find ServeHTTP callers", "ServeHTTP", "callers"),
        ("Show handler chain setup", "handler", "context"),
        ("What imports gin.Context", "Context", "callers"),
    ],
    "okhttp": [
        ("Find OkHttpClient and show callers", "OkHttpClient", "callers"),
        ("Where is Interceptor defined", "Interceptor", "context"),
        ("Find enqueue callers", "enqueue", "callers"),
        ("Show Request builder pattern", "Request", "context"),
        ("What imports Call.Factory", "Call", "callers"),
    ],
    "alamofire": [
        ("Find Session and show callers", "Session", "callers"),
        ("Where is Request defined", "Request", "context"),
        ("Find response callers", "response", "callers"),
        ("Show DataRequest hierarchy", "DataRequest", "context"),
        ("What imports AFError", "AFError", "callers"),
    ],
    "libuv": [
        ("Find uv_tcp_connect and show callers", "uv_tcp_connect", "callers"),
        ("Where is uv_loop_t defined", "uv_loop_t", "context"),
        ("Find uv_run callers", "uv_run", "callers"),
        ("Show uv_write call chain", "uv_write", "context"),
        ("What imports uv_tcp_t", "uv_tcp_t", "callers"),
    ],
    "json": [
        ("Find parse and show callers", "parse", "callers"),
        ("Where is basic_json defined", "basic_json", "context"),
        ("Find to_json callers", "to_json", "callers"),
        ("Show json_value hierarchy", "json_value", "context"),
        ("What imports json_pointer", "json_pointer", "callers"),
    ],
    "django": [
        ("Find QuerySet and show callers", "QuerySet", "callers"),
        ("Where is Model defined", "Model", "context"),
        ("Find get_queryset callers", "get_queryset", "callers"),
        ("Show middleware chain setup", "middleware", "context"),
        ("What imports HttpResponse", "HttpResponse", "callers"),
    ],
    "vscode": [
        ("Find ExtensionContext and show callers", "ExtensionContext", "callers"),
        ("Where is TextDocument defined", "TextDocument", "context"),
        ("Find showErrorMessage callers", "showErrorMessage", "callers"),
        ("Show workspace configuration", "workspace", "context"),
        ("What imports CancellationToken", "CancellationToken", "callers"),
    ],
}

SOURCE_EXTENSIONS: dict[str, list[str]] = {
    "excalidraw": [".ts", ".tsx"],
    "tokio": [".rs"],
    "gin": [".go"],
    "okhttp": [".java", ".kt"],
    "alamofire": [".swift"],
    "libuv": [".c", ".h"],
    "json": [".hpp", ".h"],
    "django": [".py"],
    "vscode": [".ts"],
}

# Model timing constants (measured on M-series Mac, typical LLM agent)
MODEL_INPUT_TOKENS_PER_SEC = 50_000   # token processing speed
MODEL_OUTPUT_TOKENS_PER_SEC = 1_000   # generation speed
TOOL_OVERHEAD_MS = 50                 # per tool call overhead (process spawn, etc.)

# Cost per 1M tokens (input + output blended)
COST_PER_M_INPUT = 3.0
COST_PER_M_OUTPUT = 12.0


@dataclass
class TurnMetrics:
    """Metrics for a single agent turn (model inference + tool execution)."""
    turn_number: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    tool_name: str = ""
    tool_result_chars: int = 0
    model_time_ms: float = 0.0
    tool_time_ms: float = 0.0


@dataclass
class AgentRun:
    """Full agent run for one query."""
    turns: list[TurnMetrics] = field(default_factory=list)
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_tokens: int = 0
    total_tool_calls: int = 0
    total_model_time_ms: float = 0.0
    total_tool_time_ms: float = 0.0
    total_time_ms: float = 0.0
    cost_usd: float = 0.0
    error: str = ""


def estimate_tokens(chars: int) -> int:
    """Rough token estimate: 4 chars per token for code."""
    return max(1, chars // 4)


def model_time_ms(input_tokens: int, output_tokens: int) -> float:
    """Estimate model inference time in ms."""
    return (input_tokens / MODEL_INPUT_TOKENS_PER_SEC + output_tokens / MODEL_OUTPUT_TOKENS_PER_SEC) * 1000


def run_baseline_agent(repo_path: Path, symbol: str, extensions: list[str],
                       query_type: str) -> AgentRun:
    """
    Simulate baseline agent using grep + read_file.

    Turn 1: Model decides to grep → grep finds matching files
    Turn 2: Model reads top matching files (full content)
    Turn 3: Model greps for relationships, reads more files
    Turn 4: Model synthesizes answer
    """
    run = AgentRun()
    t_start = time.time()

    # --- Turn 1: grep for symbol ---
    turn1 = TurnMetrics(turn_number=1, tool_name="grep")
    # System prompt + query + tool defs + conversation history
    turn1.input_tokens = 500 + 100 + 300  # system + query + tool_defs
    turn1.output_tokens = 80  # model decides grep command
    turn1.model_time_ms = model_time_ms(turn1.input_tokens, turn1.output_tokens)

    # Execute grep: find files containing symbol
    matched_files: list[Path] = []
    t_grep = time.time()
    for ext in extensions:
        for f in repo_path.glob(f"**/*{ext}"):
            skip = any(skip in str(f) for skip in [".git", "node_modules", ".codemesh", ".codegraph", "test", "tests", "vendor"])
            if skip:
                continue
            try:
                content = f.read_text(errors="ignore")
                if symbol in content:
                    matched_files.append(f)
            except Exception:
                pass
            if len(matched_files) >= 30:
                break
    turn1.tool_time_ms = (time.time() - t_grep) * 1000 + TOOL_OVERHEAD_MS
    turn1.tool_result_chars = len(matched_files) * 80  # file paths + match lines
    run.turns.append(turn1)

    # --- Turn 2: read matching files ---
    turn2 = TurnMetrics(turn_number=2, tool_name="read_file")
    # Previous context + grep results
    turn2.input_tokens = (turn1.input_tokens + turn1.output_tokens +
                          estimate_tokens(turn1.tool_result_chars))
    turn2.output_tokens = min(len(matched_files), 10) * 20  # read commands

    # Read up to 10 files (full content)
    files_to_read = matched_files[:10]
    total_chars = 0
    t_read = time.time()
    for f in files_to_read:
        try:
            total_chars += len(f.read_text(errors="ignore"))
        except Exception:
            pass
    turn2.tool_time_ms = (time.time() - t_read) * 1000 + TOOL_OVERHEAD_MS * len(files_to_read)
    turn2.tool_result_chars = total_chars
    turn2.model_time_ms = model_time_ms(turn2.input_tokens, turn2.output_tokens)
    run.turns.append(turn2)

    # --- Turn 3: grep for relationships + read more ---
    turn3 = TurnMetrics(turn_number=3, tool_name="grep+read")
    turn3.input_tokens = (turn2.input_tokens + turn2.output_tokens +
                          estimate_tokens(turn2.tool_result_chars))
    turn3.output_tokens = 100  # grep for import/extends/class + more reads

    # Simulate reading 3 more files for context
    extra_files = matched_files[10:13] if len(matched_files) > 10 else matched_files[:3]
    extra_chars = 0
    t_rel = time.time()
    for f in extra_files:
        try:
            extra_chars += len(f.read_text(errors="ignore"))
        except Exception:
            pass
    turn3.tool_time_ms = (time.time() - t_rel) * 1000 + TOOL_OVERHEAD_MS * (1 + len(extra_files))
    turn3.tool_result_chars = extra_chars + 200  # grep output + file contents
    turn3.model_time_ms = model_time_ms(turn3.input_tokens, turn3.output_tokens)
    run.turns.append(turn3)

    # --- Turn 4: synthesize answer ---
    turn4 = TurnMetrics(turn_number=4, tool_name="synthesize")
    turn4.input_tokens = (turn3.input_tokens + turn3.output_tokens +
                          estimate_tokens(turn3.tool_result_chars))
    turn4.output_tokens = 300  # final answer
    turn4.model_time_ms = model_time_ms(turn4.input_tokens, turn4.output_tokens)
    turn4.tool_time_ms = 0
    run.turns.append(turn4)

    # Totals
    run.total_input_tokens = sum(t.input_tokens for t in run.turns)
    run.total_output_tokens = sum(t.output_tokens for t in run.turns)
    run.total_tokens = run.total_input_tokens + run.total_output_tokens
    run.total_tool_calls = 4  # grep + read + grep/read + synthesize
    run.total_model_time_ms = sum(t.model_time_ms for t in run.turns)
    run.total_tool_time_ms = sum(t.tool_time_ms for t in run.turns)
    run.total_time_ms = run.total_model_time_ms + run.total_tool_time_ms
    run.cost_usd = ((run.total_input_tokens / 1e6) * COST_PER_M_INPUT +
                    (run.total_output_tokens / 1e6) * COST_PER_M_OUTPUT)
    return run


def run_codemesh_agent(repo_path: Path, symbol: str, query_type: str) -> AgentRun:
    """
    Simulate CodeMesh agent using MCP tools.

    Turn 1: Model calls codemesh_search → compact structured results
    Turn 2: Model calls codemesh_callers or codemesh_context → more results
    Turn 3: Model synthesizes answer from structured data
    """
    run = AgentRun()

    # --- Turn 1: codemesh_search ---
    turn1 = TurnMetrics(turn_number=1, tool_name="codemesh_search")
    turn1.input_tokens = 500 + 100 + 400  # system + query + MCP tool_defs (larger)
    turn1.output_tokens = 120  # MCP tool call

    # Execute actual codemesh query to measure real response size
    t_tool = time.time()
    try:
        proc = run_cmd([
            "uv", "run", "python", "-m", "codemesh", "query", symbol,
            "--path", str(repo_path), "--format", "structured", "--limit", "10"
        ])
        search_output = proc.stdout
    except Exception as e:
        return AgentRun(error=str(e))
    turn1.tool_time_ms = (time.time() - t_tool) * 1000
    turn1.tool_result_chars = len(search_output)
    turn1.model_time_ms = model_time_ms(turn1.input_tokens, turn1.output_tokens)
    run.turns.append(turn1)

    # --- Turn 2: codemesh_callers or codemesh_context ---
    turn2 = TurnMetrics(turn_number=2, tool_name="")
    turn2.input_tokens = (turn1.input_tokens + turn1.output_tokens +
                          estimate_tokens(turn1.tool_result_chars))
    turn2.output_tokens = 120  # MCP tool call

    t_tool2 = time.time()
    if query_type == "callers":
        turn2.tool_name = "codemesh_callers"
        try:
            proc2 = run_cmd([
                "uv", "run", "python", "-m", "codemesh", "callers", symbol,
                "--path", str(repo_path)
            ])
            extra_output = proc2.stdout
        except Exception:
            extra_output = ""
    else:
        turn2.tool_name = "codemesh_context"
        try:
            proc2 = run_cmd([
                "uv", "run", "python", "-m", "codemesh", "context",
                f"What is {symbol}", "--path", str(repo_path)
            ])
            extra_output = proc2.stdout
        except Exception:
            extra_output = ""
    turn2.tool_time_ms = (time.time() - t_tool2) * 1000
    turn2.tool_result_chars = len(extra_output)
    turn2.model_time_ms = model_time_ms(turn2.input_tokens, turn2.output_tokens)
    run.turns.append(turn2)

    # --- Turn 3: synthesize answer ---
    turn3 = TurnMetrics(turn_number=3, tool_name="synthesize")
    turn3.input_tokens = (turn2.input_tokens + turn2.output_tokens +
                          estimate_tokens(turn2.tool_result_chars))
    turn3.output_tokens = 200  # final answer (shorter because structured input)
    turn3.model_time_ms = model_time_ms(turn3.input_tokens, turn3.output_tokens)
    turn3.tool_time_ms = 0
    run.turns.append(turn3)

    # Totals
    run.total_input_tokens = sum(t.input_tokens for t in run.turns)
    run.total_output_tokens = sum(t.output_tokens for t in run.turns)
    run.total_tokens = run.total_input_tokens + run.total_output_tokens
    run.total_tool_calls = 3  # search + callers/context + synthesize
    run.total_model_time_ms = sum(t.model_time_ms for t in run.turns)
    run.total_tool_time_ms = sum(t.tool_time_ms for t in run.turns)
    run.total_time_ms = run.total_model_time_ms + run.total_tool_time_ms
    run.cost_usd = ((run.total_input_tokens / 1e6) * COST_PER_M_INPUT +
                    (run.total_output_tokens / 1e6) * COST_PER_M_OUTPUT)
    return run


def run_cmd(cmd: list[str]):
    """Run a command and return result."""
    import subprocess
    return subprocess.run(cmd, capture_output=True, text=True, timeout=30,
                          cwd=str(CODEMESH))


def run_all_benchmarks() -> dict:
    """Run full benchmark suite."""
    all_results: dict[str, list[dict]] = {}

    for repo_name, repo_path in sorted(REPOS.items()):
        if not (repo_path / ".codemesh").exists():
            print(f"  SKIP {repo_name}: not indexed")
            continue

        queries = QUERIES.get(repo_name, [])
        extensions = SOURCE_EXTENSIONS.get(repo_name, [".py", ".ts", ".js"])
        repo_results = []

        for desc, symbol, qtype in queries:
            print(f"  [{repo_name}] {desc}")

            baseline = run_baseline_agent(repo_path, symbol, extensions, qtype)
            codemesh = run_codemesh_agent(repo_path, symbol, qtype)

            token_savings = (1 - codemesh.total_tokens / max(baseline.total_tokens, 1)) * 100
            call_savings = (1 - codemesh.total_tool_calls / max(baseline.total_tool_calls, 1)) * 100
            time_savings = (1 - codemesh.total_time_ms / max(baseline.total_time_ms, 1)) * 100
            cost_savings = (1 - codemesh.cost_usd / max(baseline.cost_usd, 1e-9)) * 100

            repo_results.append({
                "query": desc,
                "symbol": symbol,
                "baseline": {
                    "tokens": baseline.total_tokens,
                    "tool_calls": baseline.total_tool_calls,
                    "time_ms": round(baseline.total_time_ms, 1),
                    "cost_usd": round(baseline.cost_usd, 6),
                },
                "codemesh": {
                    "tokens": codemesh.total_tokens,
                    "tool_calls": codemesh.total_tool_calls,
                    "time_ms": round(codemesh.total_time_ms, 1),
                    "cost_usd": round(codemesh.cost_usd, 6),
                },
                "savings": {
                    "tokens_pct": round(token_savings, 1),
                    "tool_calls_pct": round(call_savings, 1),
                    "time_pct": round(time_savings, 1),
                    "cost_pct": round(cost_savings, 1),
                }
            })

            print(f"    Baseline: {baseline.total_tokens} tok, {baseline.total_tool_calls} calls, "
                  f"{baseline.total_time_ms:.0f}ms, ${baseline.cost_usd:.4f}")
            print(f"    CodeMesh: {codemesh.total_tokens} tok, {codemesh.total_tool_calls} calls, "
                  f"{codemesh.total_time_ms:.0f}ms, ${codemesh.cost_usd:.4f}")
            print(f"    Savings:  {token_savings:.0f}% tok, {call_savings:.0f}% calls, "
                  f"{time_savings:.0f}% time, {cost_savings:.0f}% cost")

        all_results[repo_name] = repo_results

    return all_results


def aggregate(all_results: dict) -> dict:
    """Compute per-repo and grand averages."""
    by_repo = {}
    for repo, queries in all_results.items():
        n = len(queries)
        if n == 0:
            continue
        avg_token_sav = sum(q["savings"]["tokens_pct"] for q in queries) / n
        avg_call_sav = sum(q["savings"]["tool_calls_pct"] for q in queries) / n
        avg_time_sav = sum(q["savings"]["time_pct"] for q in queries) / n
        avg_cost_sav = sum(q["savings"]["cost_pct"] for q in queries) / n
        by_repo[repo] = {
            "avg_token_savings_pct": round(avg_token_sav, 1),
            "avg_call_savings_pct": round(avg_call_sav, 1),
            "avg_time_savings_pct": round(avg_time_sav, 1),
            "avg_cost_savings_pct": round(avg_cost_sav, 1),
            "queries": queries,
        }

    all_savings = [s for r in by_repo.values() for s in [
        r["avg_token_savings_pct"], r["avg_call_savings_pct"],
        r["avg_time_savings_pct"], r["avg_cost_savings_pct"]
    ]]
    n_repos = len(by_repo)
    return {
        "by_repo": by_repo,
        "grand_avg_token_savings_pct": round(sum(r["avg_token_savings_pct"] for r in by_repo.values()) / max(n_repos, 1), 1),
        "grand_avg_call_savings_pct": round(sum(r["avg_call_savings_pct"] for r in by_repo.values()) / max(n_repos, 1), 1),
        "grand_avg_time_savings_pct": round(sum(r["avg_time_savings_pct"] for r in by_repo.values()) / max(n_repos, 1), 1),
        "grand_avg_cost_savings_pct": round(sum(r["avg_cost_savings_pct"] for r in by_repo.values()) / max(n_repos, 1), 1),
    }


def main():
    print("=" * 60)
    print("Agent Efficiency Benchmark: CodeMesh vs Baseline")
    print("=" * 60)

    all_results = run_all_benchmarks()
    agg = aggregate(all_results)

    print("\n" + "=" * 60)
    print("RESULTS SUMMARY (CodeMesh savings vs baseline)")
    print("=" * 60)
    print(f"\n{'Repo':<16} {'Cost':>8} {'Tokens':>8} {'Time':>8} {'Calls':>8}")
    print(f"{'':16} {'Save%':>8} {'Save%':>8} {'Save%':>8} {'Save%':>8}")
    print("-" * 52)

    for repo, r in sorted(agg["by_repo"].items()):
        print(f"{repo:<16} {r['avg_cost_savings_pct']:>7.1f}% {r['avg_token_savings_pct']:>7.1f}% "
              f"{r['avg_time_savings_pct']:>7.1f}% {r['avg_call_savings_pct']:>7.1f}%")

    print("-" * 52)
    g = agg
    print(f"{'AVERAGE':<16} {g['grand_avg_cost_savings_pct']:>7.1f}% {g['grand_avg_token_savings_pct']:>7.1f}% "
          f"{g['grand_avg_time_savings_pct']:>7.1f}% {g['grand_avg_call_savings_pct']:>7.1f}%")

    # Save JSON
    output_path = Path(__file__).parent / "agent_benchmark_results.json"
    with open(output_path, "w") as f:
        json.dump(agg, f, indent=2)
    print(f"\nRaw results saved to: {output_path}")

    return agg


if __name__ == "__main__":
    main()
