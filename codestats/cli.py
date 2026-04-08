"""CodeStats CLI -- code intelligence from the command line.

Built by ClaudeFast (https://claudefa.st).
Graph engine extracted from Repowise (https://github.com/repowise-dev/repowise).

Commands:
    codestats init [PATH]       - Index the project
    codestats dead-code [PATH]  - Show unreachable files
    codestats risk FILE [PATH]  - Blast radius for a file
    codestats deps FROM TO [PATH] - Dependency path between two files
    codestats diagram [PATH]    - Mermaid architecture diagram
    codestats status [PATH]     - Summary of last index
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
import time
from collections import deque
from datetime import datetime
from pathlib import Path

import click

from . import __version__
from .storage import (
    get_db_path,
    get_git_root,
    get_repo_name,
    init_db,
    load_dead_code,
    load_git_metadata,
    load_graph_edges,
    load_graph_nodes,
    load_meta,
    persist_dead_code,
    persist_git_metadata,
    persist_graph,
    persist_meta,
)

log = logging.getLogger(__name__)


def _resolve_repo(path: str | None) -> Path:
    """Resolve the repository root from the given path or cwd."""
    target = Path(path) if path else Path.cwd()
    if not target.exists():
        click.echo(f"Error: path does not exist: {target}", err=True)
        sys.exit(1)

    git_root = get_git_root(target)
    if git_root is None:
        click.echo(f"Error: not a git repository: {target}", err=True)
        sys.exit(1)

    return git_root


def _open_db(repo_path: Path) -> sqlite3.Connection:
    """Open the SQLite database for the project."""
    db_path = get_db_path(repo_path)
    if not db_path.exists():
        click.echo(
            f"Error: project not indexed yet. Run 'codestats init' first.\n"
            f"  Expected database at: {db_path}",
            err=True,
        )
        sys.exit(1)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@click.group()
@click.version_option(version=__version__, prog_name="codestats")
def main() -> None:
    """CodeStats -- code intelligence CLI. Built by ClaudeFast."""
    logging.basicConfig(
        level=logging.WARNING,
        format="%(levelname)s: %(message)s",
    )


@main.command()
@click.argument("path", required=False)
@click.option("--verbose", "-v", is_flag=True, help="Show detailed progress.")
def init(path: str | None, verbose: bool) -> None:
    """Index the project: traverse, parse, build graph, analyze."""
    if verbose:
        logging.getLogger("codestats").setLevel(logging.INFO)

    repo_path = _resolve_repo(path)
    repo_name = get_repo_name(repo_path)
    click.echo(f"Indexing project: {repo_name} ({repo_path})")

    start = time.monotonic()

    # Step 1: Traverse files
    click.echo("  [1/5] Traversing files...")
    from .traverser import FileTraverser

    traverser = FileTraverser(repo_path)
    files = list(traverser.traverse())
    click.echo(f"         Found {len(files)} files")

    # Step 2: Parse with tree-sitter
    click.echo("  [2/5] Parsing with tree-sitter...")
    from .parser import ASTParser

    parser = ASTParser()
    parsed_files = []
    for file_info in files:
        try:
            source = Path(file_info.abs_path).read_bytes()
            parsed = parser.parse_file(file_info, source)
            parsed_files.append(parsed)
        except Exception as exc:
            if verbose:
                click.echo(f"         Warning: failed to parse {file_info.path}: {exc}", err=True)

    total_symbols = sum(len(p.symbols) for p in parsed_files)
    total_imports = sum(len(p.imports) for p in parsed_files)
    click.echo(f"         Parsed {len(parsed_files)} files ({total_symbols} symbols, {total_imports} imports)")

    # Step 3: Build dependency graph
    click.echo("  [3/5] Building dependency graph...")
    from .graph import GraphBuilder

    builder = GraphBuilder(repo_path=repo_path)
    for parsed in parsed_files:
        builder.add_file(parsed)
    graph = builder.build()

    # Mark framework entry points
    from .framework_detect import get_framework_entry_files

    framework_entries = get_framework_entry_files(repo_path)
    for entry_path in framework_entries:
        if entry_path in graph.nodes:
            graph.nodes[entry_path]["is_entry_point"] = True

    internal_edges = sum(
        1 for _, dst in graph.edges()
        if not str(dst).startswith("external:")
    )
    external_edges = graph.number_of_edges() - internal_edges
    click.echo(f"         {graph.number_of_nodes()} nodes, {internal_edges} internal edges, {external_edges} external edges")

    # Compute metrics
    pageranks = builder.pagerank()
    betweenness = builder.betweenness_centrality()

    # Step 4: Git analytics
    click.echo("  [4/5] Running git analytics...")
    from .git_analytics import GitAnalyzer

    analyzer = GitAnalyzer(repo_path)

    def _progress(done: int, total: int) -> None:
        click.echo(f"         {done}/{total} files...", nl=False)
        click.echo("\r", nl=False)

    git_summary, git_metadata = analyzer.analyze(on_progress=_progress if verbose else None)
    click.echo(f"         {git_summary.files_indexed} files, {git_summary.hotspots} hotspots, {git_summary.duration_seconds:.1f}s")

    # Step 5: Dead code detection
    click.echo("  [5/5] Detecting dead code...")
    from .dead_code import DeadCodeAnalyzer

    git_meta_map = {m["file_path"]: m for m in git_metadata}
    dead_analyzer = DeadCodeAnalyzer(graph, git_meta_map)
    dead_findings = dead_analyzer.analyze()
    click.echo(f"         {len(dead_findings)} findings")

    # Persist everything to SQLite
    click.echo("  Saving to database...")
    db_path = get_db_path(repo_path)
    conn = init_db(db_path)

    persist_graph(conn, graph, pageranks, betweenness)
    persist_git_metadata(conn, git_metadata)
    persist_dead_code(conn, dead_findings)
    persist_meta(conn, "repo_path", str(repo_path))
    persist_meta(conn, "repo_name", repo_name)
    persist_meta(conn, "indexed_at", datetime.utcnow().isoformat())
    persist_meta(conn, "file_count", str(len(files)))
    persist_meta(conn, "edge_count", str(internal_edges))
    persist_meta(conn, "hotspot_count", str(git_summary.hotspots))
    persist_meta(conn, "dead_code_count", str(len(dead_findings)))

    conn.close()

    duration = time.monotonic() - start
    click.echo(f"\nDone in {duration:.1f}s. Database saved to {db_path}")


@main.command("dead-code")
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--min-confidence", type=float, default=0.4, help="Minimum confidence threshold.")
def dead_code(path: str | None, as_json: bool, min_confidence: float) -> None:
    """Show unreachable files (excluding framework entry points)."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    findings = load_dead_code(conn)
    conn.close()

    # Filter by confidence
    findings = [f for f in findings if f.get("confidence", 0) >= min_confidence]

    if as_json:
        click.echo(json.dumps(findings, indent=2, default=str))
        return

    if not findings:
        click.echo("No dead code found. All files are reachable or are framework entry points.")
        return

    click.echo(f"Dead code findings: {len(findings)}\n")
    click.echo(f"{'File':<60} {'Kind':<20} {'Conf':>5} {'Safe?':>5}")
    click.echo("-" * 95)

    for f in sorted(findings, key=lambda x: x.get("confidence", 0), reverse=True):
        file_path = f.get("file_path", "")
        if len(file_path) > 58:
            file_path = "..." + file_path[-55:]
        kind = f.get("kind", "")
        conf = f.get("confidence", 0)
        safe = "Yes" if f.get("safe_to_delete") else "No"
        click.echo(f"{file_path:<60} {kind:<20} {conf:>5.2f} {safe:>5}")


@main.command()
@click.argument("file")
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def risk(file: str, path: str | None, as_json: bool) -> None:
    """Show blast radius and risk analysis for a specific file."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    # Normalize the file path
    file_path = file.replace("\\", "/")

    # Load graph data
    nodes = load_graph_nodes(conn)
    edges = load_graph_edges(conn)
    git_meta = load_git_metadata(conn, file_path)

    # Find the node
    node = None
    for n in nodes:
        if n["path"] == file_path:
            node = n
            break

    if node is None:
        conn.close()
        click.echo(f"Error: file not found in index: {file_path}", err=True)
        click.echo("  Hint: run 'codestats init' to re-index, or check the file path.", err=True)
        sys.exit(1)

    # Count importers (files that import this file)
    importers = [e for e in edges if e["target"] == file_path]
    # Count dependents (files this file imports)
    dependents = [e for e in edges if e["source"] == file_path]

    # Get co-change partners from git metadata
    co_changes = []
    if git_meta:
        meta = git_meta[0]
        co_json = meta.get("co_change_partners", "[]")
        try:
            co_changes = json.loads(co_json) if co_json else []
        except (json.JSONDecodeError, TypeError):
            co_changes = []

    result = {
        "file": file_path,
        "language": node.get("language", ""),
        "symbols": node.get("symbol_count", 0),
        "is_entry_point": bool(node.get("is_entry_point")),
        "is_test": bool(node.get("is_test")),
        "pagerank": node.get("pagerank", 0.0),
        "betweenness": node.get("betweenness", 0.0),
        "importer_count": len(importers),
        "importers": [e["source"] for e in importers],
        "dependent_count": len(dependents),
        "dependents": [e["target"] for e in dependents],
        "git": {},
        "co_changes": co_changes[:10],
    }

    if git_meta:
        meta = git_meta[0]
        result["git"] = {
            "commit_count": meta.get("commit_count", 0),
            "commit_count_90d": meta.get("commit_count_90d", 0),
            "last_commit": meta.get("last_commit_at"),
            "is_hotspot": bool(meta.get("is_hotspot")),
            "hotspot_score": meta.get("hotspot_score", 0.0),
            "primary_owner": meta.get("primary_owner"),
            "bus_factor": meta.get("bus_factor", 0),
        }

    conn.close()

    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    # Human-readable output
    click.echo(f"Risk Analysis: {file_path}\n")
    click.echo(f"  Language:       {result['language']}")
    click.echo(f"  Symbols:        {result['symbols']}")
    click.echo(f"  Entry Point:    {'Yes' if result['is_entry_point'] else 'No'}")
    click.echo(f"  Test File:      {'Yes' if result['is_test'] else 'No'}")
    click.echo(f"  PageRank:       {result['pagerank']:.6f}")
    click.echo(f"  Betweenness:    {result['betweenness']:.6f}")

    click.echo(f"\n  Importers ({result['importer_count']}):")
    for imp in result["importers"][:20]:
        click.echo(f"    <- {imp}")
    if result["importer_count"] > 20:
        click.echo(f"    ... and {result['importer_count'] - 20} more")

    click.echo(f"\n  Dependencies ({result['dependent_count']}):")
    for dep in result["dependents"][:20]:
        click.echo(f"    -> {dep}")
    if result["dependent_count"] > 20:
        click.echo(f"    ... and {result['dependent_count'] - 20} more")

    if result["git"]:
        git = result["git"]
        click.echo(f"\n  Git Analytics:")
        click.echo(f"    Commits (total):  {git['commit_count']}")
        click.echo(f"    Commits (90d):    {git['commit_count_90d']}")
        click.echo(f"    Last commit:      {git['last_commit']}")
        click.echo(f"    Hotspot:          {'Yes' if git['is_hotspot'] else 'No'} (score: {git['hotspot_score']:.2f})")
        click.echo(f"    Primary owner:    {git['primary_owner']}")
        click.echo(f"    Bus factor:       {git['bus_factor']}")

    if result["co_changes"]:
        click.echo(f"\n  Co-changes (top {min(len(result['co_changes']), 10)}):")
        for cc in result["co_changes"][:10]:
            click.echo(f"    {cc.get('file_path', '')} (count: {cc.get('co_change_count', 0)})")


@main.command()
@click.argument("from_file", metavar="FROM")
@click.argument("to_file", metavar="TO")
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def deps(from_file: str, to_file: str, path: str | None, as_json: bool) -> None:
    """Find dependency path between two files (BFS)."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    from_path = from_file.replace("\\", "/")
    to_path = to_file.replace("\\", "/")

    edges = load_graph_edges(conn)
    nodes = {n["path"] for n in load_graph_nodes(conn)}
    conn.close()

    if from_path not in nodes:
        click.echo(f"Error: source file not found in index: {from_path}", err=True)
        sys.exit(1)
    if to_path not in nodes:
        click.echo(f"Error: target file not found in index: {to_path}", err=True)
        sys.exit(1)

    # Build adjacency list
    adj: dict[str, list[str]] = {}
    for e in edges:
        src = e["source"]
        tgt = e["target"]
        adj.setdefault(src, []).append(tgt)

    # BFS
    dep_path = _bfs_path(adj, from_path, to_path)

    result = {
        "from": from_path,
        "to": to_path,
        "path": dep_path,
        "hops": len(dep_path) - 1 if dep_path else -1,
    }

    if as_json:
        click.echo(json.dumps(result, indent=2))
        return

    if dep_path is None:
        click.echo(f"No dependency path found from {from_path} to {to_path}")
    else:
        click.echo(f"Dependency path ({len(dep_path) - 1} hops):\n")
        for i, node in enumerate(dep_path):
            prefix = "  " if i == 0 else "  -> "
            click.echo(f"{prefix}{node}")


def _bfs_path(adj: dict[str, list[str]], start: str, end: str) -> list[str] | None:
    """BFS to find shortest path from start to end."""
    if start == end:
        return [start]

    visited = {start}
    queue: deque[list[str]] = deque([[start]])

    while queue:
        path_so_far = queue.popleft()
        current = path_so_far[-1]

        for neighbor in adj.get(current, []):
            if neighbor in visited:
                continue
            new_path = path_so_far + [neighbor]
            if neighbor == end:
                return new_path
            visited.add(neighbor)
            queue.append(new_path)

    return None


@main.command()
@click.argument("path", required=False)
@click.option("--max-nodes", type=int, default=100, help="Maximum nodes to include.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def diagram(path: str | None, max_nodes: int, as_json: bool) -> None:
    """Generate a Mermaid flowchart from the dependency graph."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    nodes = load_graph_nodes(conn)
    edges = load_graph_edges(conn)
    conn.close()

    # Sort nodes by PageRank and take top N
    nodes_sorted = sorted(nodes, key=lambda n: n.get("pagerank", 0), reverse=True)
    top_nodes = {n["path"] for n in nodes_sorted[:max_nodes]}

    # Filter edges to only include top nodes
    filtered_edges = [
        e for e in edges
        if e["source"] in top_nodes and e["target"] in top_nodes
    ]

    if as_json:
        result = {
            "nodes": [n["path"] for n in nodes_sorted[:max_nodes]],
            "edges": [{"from": e["source"], "to": e["target"]} for e in filtered_edges],
        }
        click.echo(json.dumps(result, indent=2))
        return

    # Generate Mermaid
    lines = ["flowchart LR"]

    # Create short IDs for readability
    node_ids: dict[str, str] = {}
    for i, n in enumerate(nodes_sorted[:max_nodes]):
        node_id = f"n{i}"
        node_ids[n["path"]] = node_id
        # Shorten the path for display
        display = n["path"]
        if len(display) > 40:
            display = "..." + display[-37:]
        lines.append(f'    {node_id}["{display}"]')

    for e in filtered_edges:
        src_id = node_ids.get(e["source"])
        tgt_id = node_ids.get(e["target"])
        if src_id and tgt_id:
            lines.append(f"    {src_id} --> {tgt_id}")

    mermaid = "\n".join(lines)
    click.echo(mermaid)


@main.command()
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def status(path: str | None, as_json: bool) -> None:
    """Show summary of last index."""
    repo_path = _resolve_repo(path)
    db_path = get_db_path(repo_path)

    if not db_path.exists():
        click.echo("Project not indexed yet. Run 'codestats init' first.")
        sys.exit(1)

    conn = sqlite3.connect(str(db_path))

    result = {
        "repo_name": load_meta(conn, "repo_name") or get_repo_name(repo_path),
        "repo_path": load_meta(conn, "repo_path") or str(repo_path),
        "indexed_at": load_meta(conn, "indexed_at"),
        "file_count": load_meta(conn, "file_count") or "0",
        "edge_count": load_meta(conn, "edge_count") or "0",
        "hotspot_count": load_meta(conn, "hotspot_count") or "0",
        "dead_code_count": load_meta(conn, "dead_code_count") or "0",
        "db_path": str(db_path),
        "db_size_kb": round(db_path.stat().st_size / 1024, 1),
    }

    conn.close()

    if as_json:
        click.echo(json.dumps(result, indent=2))
        return

    click.echo(f"CodeStats Status: {result['repo_name']}\n")
    click.echo(f"  Repository:     {result['repo_path']}")
    click.echo(f"  Indexed at:     {result['indexed_at']}")
    click.echo(f"  Files:          {result['file_count']}")
    click.echo(f"  Internal edges: {result['edge_count']}")
    click.echo(f"  Hotspots:       {result['hotspot_count']}")
    click.echo(f"  Dead code:      {result['dead_code_count']}")
    click.echo(f"  Database:       {result['db_path']} ({result['db_size_kb']} KB)")
