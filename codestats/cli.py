"""CodeStats CLI -- code intelligence from the command line.

Built by ClaudeFast (https://claudefa.st).
Graph engine extracted from Repowise (https://github.com/repowise-dev/repowise).

Commands:
    codestats init [PATH]               - Index the project
    codestats dead-code [PATH]          - Show unreachable files
    codestats risk FILE [PATH]          - Blast radius for a file
    codestats deps FROM TO [PATH]       - Dependency path between two files
    codestats diagram [PATH]            - Interactive D3.js / Mermaid diagram
    codestats status [PATH]             - Summary of last index
    codestats communities [PATH]        - List detected communities
    codestats cycles [PATH]             - Detect circular dependencies
    codestats flows [PATH]              - List execution flows by criticality
    codestats flow ENTRY [PATH]         - Show single flow details
    codestats wiki [PATH]               - Generate markdown architecture wiki
    codestats impact FILE [--path PATH] - Bidirectional impact analysis
    codestats search QUERY [PATH]       - Full-text symbol search
    codestats refactor rename T N [PATH]- Preview rename operation
    codestats refactor moves [PATH]     - Suggest misplaced files
    codestats refactor large [PATH]     - Find large functions
"""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import sqlite3
import sys
import time
from collections import deque
from concurrent.futures import ProcessPoolExecutor, as_completed
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
    load_flow_members,
    load_flows,
    load_git_metadata,
    load_graph_edges,
    load_graph_nodes,
    load_meta,
    persist_dead_code,
    persist_git_metadata,
    persist_graph,
    persist_meta,
    search_fts,
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


def _parse_worker(fi_dict: dict, source_bytes: bytes) -> dict:
    """Worker for parallel parsing. Each process gets its own parser."""
    from codestats.models import FileInfo
    from codestats.parser import ASTParser

    fi = FileInfo(**fi_dict)
    parser = ASTParser()
    result = parser.parse_file(fi, source_bytes)
    return dataclasses.asdict(result)


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
@click.option("--force", "-f", is_flag=True, help="Force full rebuild, ignoring cached index.")
@click.option("--tsconfig", "tsconfig_path", type=click.Path(), default=None,
              help="Path to tsconfig.json for alias resolution (auto-discovered if omitted).")
def init(path: str | None, verbose: bool, force: bool, tsconfig_path: str | None) -> None:
    """Index the project: traverse, parse, build graph, analyze."""
    if verbose:
        logging.getLogger("codestats").setLevel(logging.INFO)

    repo_path = _resolve_repo(path)
    repo_name = get_repo_name(repo_path)

    start = time.monotonic()

    # Determine if incremental mode is possible
    db_path = get_db_path(repo_path)
    incremental = False
    changeset = None

    # Step 1: Traverse all files (always needed to know the full file set)
    from .traverser import FileTraverser

    traverser = FileTraverser(repo_path)
    files = list(traverser.traverse())

    if db_path.exists() and not force:
        from .incremental import detect_changes

        inc_conn = sqlite3.connect(str(db_path))
        inc_conn.execute("PRAGMA journal_mode=WAL")
        changeset = detect_changes(repo_path, inc_conn, files)
        inc_conn.close()

        if changeset is not None:
            if changeset.is_empty:
                duration = time.monotonic() - start
                click.echo(f"No changes detected for {repo_name}. Index is up to date. ({duration:.1f}s)")
                return
            incremental = True

    if incremental and changeset is not None:
        click.echo(f"Updating project: {repo_name} ({repo_path})")
        click.echo(
            f"  Detected {len(changeset.added)} added, "
            f"{len(changeset.modified)} modified, "
            f"{len(changeset.deleted)} deleted, "
            f"{len(changeset.dependents)} dependents"
        )

        reprocess_paths = set(changeset.all_reprocess)
        files_to_parse = [f for f in files if f.path in reprocess_paths]
        cached_count = len(files) - len(files_to_parse)

        click.echo("  [1/8] Traversing files...")
        click.echo(f"         Scanning {len(files_to_parse)} files ({cached_count} cached)")
    else:
        click.echo(f"Indexing project: {repo_name} ({repo_path})")
        click.echo("  [1/8] Traversing files...")
        click.echo(f"         Found {len(files)} files")
        files_to_parse = files

    # Step 2: Parse with tree-sitter
    click.echo("  [2/8] Parsing with tree-sitter...")
    from .models import FileInfo, Import, ParsedFile, Symbol, compute_content_hash
    from .parser import ASTParser

    parsed_files = []

    if len(files_to_parse) >= 100 and not incremental:
        # Parallel parsing for large repos
        workers = min(4, os.cpu_count() or 2)
        click.echo(f"         Using {workers} parallel workers...")

        # Read all sources first (IO-bound, fast with OS cache)
        work_items = []
        for file_info in files_to_parse:
            try:
                source = Path(file_info.abs_path).read_bytes()
                work_items.append((file_info, source))
            except Exception as exc:
                if verbose:
                    click.echo(f"         Warning: failed to read {file_info.path}: {exc}", err=True)

        with ProcessPoolExecutor(max_workers=workers) as executor:
            futures = {}
            for file_info, source in work_items:
                fi_dict = dataclasses.asdict(file_info)
                # datetime is not JSON-safe for pickle but dataclasses.asdict handles it
                future = executor.submit(_parse_worker, fi_dict, source)
                futures[future] = (file_info, source)

            for future in as_completed(futures):
                file_info, source = futures[future]
                try:
                    result_dict = future.result()
                    # Reconstruct ParsedFile from dict
                    parsed = ParsedFile(
                        file_info=file_info,
                        symbols=[Symbol(**s) for s in result_dict.get("symbols", [])],
                        imports=[Import(**i) for i in result_dict.get("imports", [])],
                        exports=result_dict.get("exports", []),
                        docstring=result_dict.get("docstring"),
                        parse_errors=result_dict.get("parse_errors", []),
                        content_hash=compute_content_hash(source),
                    )
                    parsed_files.append(parsed)
                except Exception as exc:
                    if verbose:
                        click.echo(f"         Warning: failed to parse {file_info.path}: {exc}", err=True)
    else:
        # Sequential parsing (small repos or incremental mode)
        parser = ASTParser()
        for file_info in files_to_parse:
            try:
                source = Path(file_info.abs_path).read_bytes()
                parsed = parser.parse_file(file_info, source)
                parsed.content_hash = compute_content_hash(source)
                parsed_files.append(parsed)
            except Exception as exc:
                if verbose:
                    click.echo(f"         Warning: failed to parse {file_info.path}: {exc}", err=True)

    total_symbols = sum(len(p.symbols) for p in parsed_files)
    total_imports = sum(len(p.imports) for p in parsed_files)
    click.echo(f"         Parsed {len(parsed_files)} files ({total_symbols} symbols, {total_imports} imports)")

    # Step 3: Build dependency graph
    if incremental and changeset is not None:
        click.echo("  [3/8] Updating dependency graph...")
    else:
        click.echo("  [3/8] Building dependency graph...")
    from .graph import GraphBuilder

    # Resolve tsconfig path relative to repo root if provided
    tsconfig_rel = None
    if tsconfig_path:
        tsconfig_abs = Path(tsconfig_path).resolve()
        if not tsconfig_abs.exists():
            click.echo(f"Error: tsconfig not found: {tsconfig_path}", err=True)
            sys.exit(1)
        try:
            tsconfig_rel = str(tsconfig_abs.relative_to(repo_path.resolve()))
        except ValueError:
            click.echo(f"Error: tsconfig must be inside the repository: {tsconfig_path}", err=True)
            sys.exit(1)

    if incremental and changeset is not None:
        # Incremental: changed files already parsed; parse unchanged files too
        # for full graph rebuild (OS cache makes re-reads fast)
        all_parsed = []
        already_parsed = {p.file_info.path: p for p in parsed_files}

        for file_info in files:
            if file_info.path in already_parsed:
                all_parsed.append(already_parsed[file_info.path])
            else:
                try:
                    source = Path(file_info.abs_path).read_bytes()
                    parsed = parser.parse_file(file_info, source)
                    parsed.content_hash = compute_content_hash(source)
                    all_parsed.append(parsed)
                except Exception:
                    pass

        builder = GraphBuilder(repo_path=repo_path, tsconfig_path=tsconfig_rel)
        for parsed in all_parsed:
            builder.add_file(parsed)
        graph = builder.build()
    else:
        builder = GraphBuilder(repo_path=repo_path, tsconfig_path=tsconfig_rel)
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
    click.echo("  [4/8] Running git analytics...")
    from .git_analytics import GitAnalyzer

    analyzer = GitAnalyzer(repo_path)

    def _progress(done: int, total: int) -> None:
        click.echo(f"         {done}/{total} files...", nl=False)
        click.echo("\r", nl=False)

    if incremental and changeset is not None:
        # Only run git analytics on changed files, keep existing data for unchanged
        existing_conn = sqlite3.connect(str(db_path))
        existing_conn.execute("PRAGMA journal_mode=WAL")
        existing_git_meta = load_git_metadata(existing_conn)
        existing_conn.close()

        existing_git_map = {m["file_path"]: m for m in existing_git_meta}
        changed_paths = set(changeset.all_changed)

        # Run git analytics only on changed files
        git_summary, new_git_metadata = analyzer.analyze_files(
            list(changed_paths),
            on_progress=_progress if verbose else None,
        )

        # Merge: use new data for changed files, keep old data for unchanged
        new_git_map = {m["file_path"]: m for m in new_git_metadata}
        merged_git_metadata = []
        current_file_paths = {f.path for f in files}

        for fp in current_file_paths:
            if fp in new_git_map:
                merged_git_metadata.append(new_git_map[fp])
            elif fp in existing_git_map:
                merged_git_metadata.append(existing_git_map[fp])

        git_metadata = merged_git_metadata
        click.echo(
            f"         {git_summary.files_indexed} files analyzed "
            f"({len(git_metadata) - git_summary.files_indexed} cached), "
            f"{git_summary.hotspots} hotspots, {git_summary.duration_seconds:.1f}s"
        )
    else:
        git_summary, git_metadata = analyzer.analyze(on_progress=_progress if verbose else None)
        click.echo(f"         {git_summary.files_indexed} files, {git_summary.hotspots} hotspots, {git_summary.duration_seconds:.1f}s")

    # Step 5: Community detection
    click.echo("  [5/8] Detecting communities...")
    from .communities import detect_communities

    community_data = detect_communities(graph)
    if community_data:
        avg_cohesion = sum(c["cohesion"] for c in community_data) / len(community_data)
        click.echo(f"         {len(community_data)} communities, avg cohesion {avg_cohesion:.2f}")
    else:
        click.echo("         No communities detected")

    # Step 6: Flow tracing
    click.echo("  [6/8] Tracing execution flows...")
    from .flows import trace_flows

    flow_data = trace_flows(graph)
    if flow_data:
        avg_crit = sum(f["criticality"] for f in flow_data) / len(flow_data)
        click.echo(f"         {len(flow_data)} flows, avg criticality {avg_crit:.2f}")
    else:
        click.echo("         No flows detected")

    # Step 7: Search index
    click.echo("  [7/8] Building search index...")
    if incremental and changeset is not None:
        all_parsed_for_index = all_parsed
    else:
        all_parsed_for_index = parsed_files
    search_symbol_count = sum(len(p.symbols) for p in all_parsed_for_index)
    click.echo(f"         {search_symbol_count} symbols to index")

    # Step 8: Dead code detection (always full re-run)
    click.echo("  [8/8] Detecting dead code...")
    from .dead_code import DeadCodeAnalyzer

    git_meta_map = {m["file_path"]: m for m in git_metadata}
    dead_analyzer = DeadCodeAnalyzer(graph, git_meta_map)
    dead_findings = dead_analyzer.analyze()
    click.echo(f"         {len(dead_findings)} findings")

    # Persist everything to SQLite
    click.echo("  Saving to database...")
    conn = init_db(db_path)

    persist_graph(conn, graph, pageranks, betweenness)
    persist_git_metadata(conn, git_metadata)
    persist_dead_code(conn, dead_findings)

    # Persist communities
    from .storage import persist_communities, persist_community_members

    if community_data:
        persist_communities(conn, community_data)
        # Build community members list
        all_members = []
        for c in community_data:
            for member in c["members"]:
                all_members.append({
                    "file_path": member,
                    "community_id": c["community_id"],
                })
        persist_community_members(conn, all_members)

    # Persist flows
    from .storage import persist_flows, persist_flow_members

    if flow_data:
        persist_flows(conn, flow_data)
        for flow in flow_data:
            members = [
                {"node_qualified_name": m, "depth": i}
                for i, m in enumerate(flow["members"])
            ]
            persist_flow_members(conn, flow["flow_id"], members)

    # Build and persist search index
    from .search import build_search_index

    build_search_index(conn, all_parsed_for_index)

    persist_meta(conn, "repo_path", str(repo_path))
    persist_meta(conn, "repo_name", repo_name)
    persist_meta(conn, "indexed_at", datetime.utcnow().isoformat())
    persist_meta(conn, "file_count", str(len(files)))
    persist_meta(conn, "edge_count", str(internal_edges))
    persist_meta(conn, "hotspot_count", str(git_summary.hotspots))
    persist_meta(conn, "dead_code_count", str(len(dead_findings)))
    persist_meta(conn, "flow_count", str(len(flow_data)))

    conn.close()

    duration = time.monotonic() - start
    if incremental:
        click.echo(f"\nDone in {duration:.1f}s (incremental). Database saved to {db_path}")
    else:
        click.echo(f"\nDone in {duration:.1f}s. Database saved to {db_path}")


@main.command("dead-code")
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--min-confidence", type=float, default=0.4, help="Minimum confidence threshold.")
@click.option("--include-moves", is_flag=True, help="Include misplaced file suggestions.")
def dead_code(path: str | None, as_json: bool, min_confidence: float, include_moves: bool) -> None:
    """Show unreachable files (excluding framework entry points)."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    findings = load_dead_code(conn)

    # Optionally add move suggestions
    if include_moves:
        nodes = load_graph_nodes(conn)
        edges = load_graph_edges(conn)

        import networkx as nx
        g = nx.DiGraph()
        for n in nodes:
            g.add_node(n["path"], **{k: v for k, v in n.items() if k != "path"})
        for e in edges:
            g.add_edge(e["source"], e["target"], edge_type=e.get("edge_type", "IMPORTS_FROM"))

        # Build community map from DB
        from .storage import load_communities as _load_communities
        community_data = _load_communities(conn)
        community_map: dict[str, int] = {}
        for c in community_data:
            for member_file in c.get("top_files", []):
                community_map[member_file] = c["community_id"]
        # Also load community_members for full mapping
        for c in community_data:
            from .storage import load_community_members
            members = load_community_members(conn, c["community_id"])
            for m in members:
                community_map[m["file_path"]] = c["community_id"]

        from .dead_code import DeadCodeAnalyzer as _DCA
        git_meta = load_git_metadata(conn)
        git_meta_map = {m["file_path"]: m for m in git_meta}
        analyzer = _DCA(g, git_meta_map)
        move_findings = analyzer.suggest_moves(community_map)

        # Convert to dicts for display
        for mf in move_findings:
            findings.append({
                "file_path": mf.file_path,
                "kind": mf.kind,
                "confidence": mf.confidence,
                "reason": mf.reason,
                "safe_to_delete": mf.safe_to_delete,
            })

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

    # Count importers (files that import this file) -- exclude TESTED_BY edges
    importers = [e for e in edges if e["target"] == file_path and e.get("edge_type", "IMPORTS_FROM") == "IMPORTS_FROM"]
    # Count dependents (files this file imports) -- exclude TESTED_BY edges
    dependents = [e for e in edges if e["source"] == file_path and e.get("edge_type", "IMPORTS_FROM") == "IMPORTS_FROM"]

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
        "tested_by": [e["target"] for e in edges if e["source"] == file_path and e.get("edge_type") == "TESTED_BY"],
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

    # Test coverage via TESTED_BY edges
    tested_by = [e for e in edges if e["source"] == file_path and e.get("edge_type") == "TESTED_BY"]
    if tested_by:
        click.echo(f"\n  Test Coverage ({len(tested_by)}):")
        for tb in tested_by:
            names = ""
            try:
                imported = json.loads(tb.get("imported_names", "[]"))
                if imported:
                    names = f"  (imports {len(imported)} symbols)"
            except (json.JSONDecodeError, TypeError):
                pass
            click.echo(f"    {tb['target']}{names}")
    elif not result["is_test"]:
        click.echo(f"\n  Test Coverage: None (no test files import this file)")

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
@click.option("--format", "fmt", type=click.Choice(["d3", "mermaid"]), default="d3",
              help="Output format: d3 (interactive HTML) or mermaid.")
@click.option("--output", "output_path", type=click.Path(), default=None,
              help="Output file path (default: .codestats/diagram.html for d3).")
@click.option("--open", "auto_open", is_flag=True, help="Auto-open in browser (d3 only).")
def diagram(path: str | None, max_nodes: int, as_json: bool, fmt: str,
            output_path: str | None, auto_open: bool) -> None:
    """Generate an architecture diagram from the dependency graph."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    nodes = load_graph_nodes(conn)
    edges = load_graph_edges(conn)
    repo_name = load_meta(conn, "repo_name") or get_repo_name(repo_path)

    if fmt == "d3":
        # Load communities for D3 visualization
        from .storage import load_communities as _load_communities

        community_data = _load_communities(conn)
        conn.close()

        if output_path is None:
            output_path = str(repo_path / ".codestats" / "diagram.html")

        from .visualization import generate_visualization

        result_path = generate_visualization(
            nodes, edges, community_data, repo_name, output_path, max_nodes=max_nodes,
        )
        click.echo(f"D3 visualization written to {result_path}")

        if auto_open:
            import webbrowser

            webbrowser.open(f"file://{Path(result_path).resolve()}")
        return

    conn.close()

    # Mermaid format (legacy)
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


@main.command()
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--coupling", is_flag=True, help="Show architecture coupling warnings.")
def communities(path: str | None, as_json: bool, coupling: bool) -> None:
    """List detected communities (Louvain clusters) with cohesion scores."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    from .storage import load_communities as _load_communities

    community_data = _load_communities(conn)

    if not community_data:
        # Try computing on-the-fly from graph edges
        click.echo("No communities stored. Run 'codestats init' to detect communities.", err=True)
        conn.close()
        sys.exit(1)

    if as_json and not coupling:
        click.echo(json.dumps(community_data, indent=2, default=str))
        conn.close()
        return

    repo_name = load_meta(conn, "repo_name") or get_repo_name(repo_path)
    click.echo(f"Communities in {repo_name}\n")
    click.echo(f"{'ID':>4}  {'Name':<40} {'Members':>7} {'Cohesion':>8}")
    click.echo("-" * 65)

    for c in community_data:
        cid = c.get("community_id", 0)
        name = c.get("name", "")
        if len(name) > 38:
            name = name[:35] + "..."
        member_count = c.get("member_count", 0)
        cohesion = c.get("cohesion", 0.0)
        click.echo(f"{cid:>4}  {name:<40} {member_count:>7} {cohesion:>8.3f}")

    click.echo(f"\nTotal: {len(community_data)} communities")

    if coupling:
        click.echo("")
        # Rebuild graph from stored edges to compute coupling
        edges = load_graph_edges(conn)
        nodes = load_graph_nodes(conn)

        import networkx as nx
        g = nx.DiGraph()
        for n in nodes:
            g.add_node(n["path"])
        for e in edges:
            g.add_edge(e["source"], e["target"], edge_type=e.get("edge_type", "IMPORTS_FROM"))

        # Enrich community data with members from community_members table
        from .storage import load_community_members as _load_community_members
        for c in community_data:
            if "members" not in c or not c["members"]:
                member_rows = _load_community_members(conn, c["community_id"])
                c["members"] = [m["file_path"] for m in member_rows]

        from .communities import get_architecture_coupling
        warnings = get_architecture_coupling(g, community_data)

        if not warnings:
            click.echo("No significant architecture coupling detected.")
        else:
            click.echo(f"Architecture Coupling Warnings ({len(warnings)}):\n")
            click.echo(f"  {'Community A':<25} {'Community B':<25} {'Edges':>5} {'Level':>10}")
            click.echo("  " + "-" * 70)
            for w in warnings:
                name_a = w["name_a"]
                name_b = w["name_b"]
                if len(name_a) > 23:
                    name_a = name_a[:20] + "..."
                if len(name_b) > 23:
                    name_b = name_b[:20] + "..."
                click.echo(
                    f"  {name_a:<25} {name_b:<25} {w['cross_edges']:>5} {w['warning_level']:>10}"
                )

    conn.close()


@main.command()
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--min-size", type=int, default=2, help="Minimum cycle size to report.")
def cycles(path: str | None, as_json: bool, min_size: int) -> None:
    """Detect circular dependency chains (strongly connected components)."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    edges = load_graph_edges(conn)
    nodes = load_graph_nodes(conn)
    repo_name = load_meta(conn, "repo_name") or get_repo_name(repo_path)
    conn.close()

    # Rebuild graph
    import networkx as nx
    g = nx.DiGraph()
    for n in nodes:
        g.add_node(n["path"])
    for e in edges:
        # Only follow IMPORTS_FROM edges for cycle detection
        if e.get("edge_type", "IMPORTS_FROM") == "IMPORTS_FROM":
            g.add_edge(e["source"], e["target"])

    # Find SCCs
    sccs = [
        frozenset(scc) for scc in nx.strongly_connected_components(g)
        if len(scc) >= min_size
    ]

    # Sort by size descending
    sccs.sort(key=len, reverse=True)

    # Trace one cycle path through each SCC
    cycle_results = []
    for scc in sccs:
        cycle_path = _trace_cycle_path(g, scc)
        cycle_results.append({
            "size": len(scc),
            "members": sorted(scc),
            "cycle_path": cycle_path,
        })

    if as_json:
        click.echo(json.dumps(cycle_results, indent=2, default=str))
        return

    if not cycle_results:
        click.echo(f"No circular dependencies found in {repo_name} (min size: {min_size})")
        return

    click.echo(f"Circular Dependencies in {repo_name}\n")

    for i, cycle in enumerate(cycle_results, 1):
        click.echo(f"Cycle {i} ({cycle['size']} files):")
        if cycle["cycle_path"]:
            path_str = " -> ".join(cycle["cycle_path"])
            click.echo(f"  {path_str}")
        else:
            for member in cycle["members"]:
                click.echo(f"  {member}")
        click.echo()

    click.echo(f"Found {len(cycle_results)} circular dependency chain(s)")


def _trace_cycle_path(graph: "nx.DiGraph", scc: frozenset[str]) -> list[str]:
    """Trace one cycle path through an SCC by following edges.

    Returns a list of file paths forming a cycle (last -> first completes it).
    """
    if not scc:
        return []

    # Start from the lexicographically first node for deterministic output
    start = min(scc)
    subgraph = graph.subgraph(scc)

    # DFS to find a cycle
    visited: set[str] = set()
    path: list[str] = [start]
    current = start

    while True:
        visited.add(current)
        # Find a successor in the SCC
        found_next = False
        for neighbor in subgraph.successors(current):
            if neighbor == start and len(path) > 1:
                # Found cycle back to start
                path.append(start)
                return path
            if neighbor not in visited:
                path.append(neighbor)
                current = neighbor
                found_next = True
                break

        if not found_next:
            # Try to find any successor that leads back to start
            for neighbor in subgraph.successors(current):
                if neighbor == start and len(path) > 1:
                    path.append(start)
                    return path

            # Backtrack
            if len(path) > 1:
                path.pop()
                current = path[-1]
            else:
                break

    # Fallback: use networkx cycle detection
    try:
        cycle = nx.find_cycle(subgraph, source=start)
        if cycle:
            result = [edge[0] for edge in cycle]
            result.append(cycle[-1][1])
            return result
    except nx.NetworkXNoCycle:
        pass

    # Last resort: just list members
    return sorted(scc)


# ---------------------------------------------------------------------------
# Shared helper: reconstruct NetworkX graph from DB
# ---------------------------------------------------------------------------


def _reconstruct_graph(conn: sqlite3.Connection) -> "nx.DiGraph":
    """Rebuild NetworkX DiGraph from stored nodes and edges."""
    import networkx as nx

    g = nx.DiGraph()
    nodes = load_graph_nodes(conn)
    edges = load_graph_edges(conn)
    for n in nodes:
        g.add_node(n["path"], **{k: v for k, v in n.items() if k != "path"})
    for e in edges:
        g.add_edge(
            e["source"],
            e["target"],
            edge_type=e.get("edge_type", "IMPORTS_FROM"),
            imported_names=e.get("imported_names", "[]"),
        )
    return g


# ---------------------------------------------------------------------------
# flows command
# ---------------------------------------------------------------------------


@main.command()
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def flows(path: str | None, as_json: bool) -> None:
    """List execution flows sorted by criticality."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    flow_data = load_flows(conn)
    conn.close()

    if as_json:
        click.echo(json.dumps(flow_data, indent=2, default=str))
        return

    if not flow_data:
        click.echo("No execution flows found. Run 'codestats init' first.")
        return

    click.echo(f"Execution Flows ({len(flow_data)})\n")
    click.echo(f"{'ID':<10} {'Entry Point':<50} {'Nodes':>5} {'Spread':>6} {'Crit':>6} {'Tests':>5}")
    click.echo("-" * 87)

    for f in flow_data:
        flow_id = f.get("flow_id", "")[:8]
        entry = f.get("entry_point", "")
        if len(entry) > 48:
            entry = "..." + entry[-45:]
        node_count = f.get("node_count", 0)
        spread = f.get("file_spread", 0)
        crit = f.get("criticality", 0.0)
        has_tests = "Yes" if f.get("has_test_coverage") else "No"
        click.echo(f"{flow_id:<10} {entry:<50} {node_count:>5} {spread:>6} {crit:>6.3f} {has_tests:>5}")


# ---------------------------------------------------------------------------
# flow command (single flow detail)
# ---------------------------------------------------------------------------


@main.command()
@click.argument("entry_point")
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def flow(entry_point: str, path: str | None, as_json: bool) -> None:
    """Show detailed single execution flow by entry point or flow ID."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    flow_data = load_flows(conn)

    # Match by entry_point substring or flow_id prefix
    target_flow = None
    for f in flow_data:
        if f.get("flow_id", "").startswith(entry_point) or f.get("entry_point", "") == entry_point:
            target_flow = f
            break

    # Fuzzy match: entry_point is a substring of the stored entry_point
    if target_flow is None:
        for f in flow_data:
            if entry_point in f.get("entry_point", ""):
                target_flow = f
                break

    if target_flow is None:
        conn.close()
        click.echo(f"Error: no flow found matching '{entry_point}'", err=True)
        click.echo("  Hint: use 'codestats flows' to list all flows.", err=True)
        sys.exit(1)

    members = load_flow_members(conn, target_flow["flow_id"])
    conn.close()

    result = {
        "flow_id": target_flow["flow_id"],
        "entry_point": target_flow["entry_point"],
        "node_count": target_flow.get("node_count", 0),
        "file_spread": target_flow.get("file_spread", 0),
        "criticality": target_flow.get("criticality", 0.0),
        "has_test_coverage": bool(target_flow.get("has_test_coverage")),
        "members": members,
    }

    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.echo(f"Flow: {result['entry_point']}\n")
    click.echo(f"  Flow ID:        {result['flow_id']}")
    click.echo(f"  Nodes:          {result['node_count']}")
    click.echo(f"  File Spread:    {result['file_spread']}")
    click.echo(f"  Criticality:    {result['criticality']:.3f}")
    click.echo(f"  Test Coverage:  {'Yes' if result['has_test_coverage'] else 'No'}")

    click.echo(f"\n  Members ({len(members)}):")
    for m in members:
        depth = m.get("depth", 0)
        indent = "    " + "  " * depth
        click.echo(f"{indent}{m.get('node_qualified_name', '')}")


# ---------------------------------------------------------------------------
# wiki command
# ---------------------------------------------------------------------------


@main.command()
@click.argument("path", required=False)
@click.option("--output", "output_dir", type=click.Path(), default=None,
              help="Output directory (default: .codestats/wiki/).")
@click.option("--json", "as_json", is_flag=True, help="Output file list as JSON.")
def wiki(path: str | None, output_dir: str | None, as_json: bool) -> None:
    """Generate a markdown wiki from the project's architecture."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    from .storage import (
        load_communities as _load_communities,
        load_community_members as _lcm,
    )

    community_data = _load_communities(conn)

    # Enrich communities with full member lists
    for c in community_data:
        if "members" not in c or not c["members"]:
            member_rows = _lcm(conn, c["community_id"])
            c["members"] = [m["file_path"] for m in member_rows]

    flow_data = load_flows(conn)
    repo_name = load_meta(conn, "repo_name") or get_repo_name(repo_path)

    graph_stats = {
        "repo_name": repo_name,
        "file_count": load_meta(conn, "file_count") or "0",
        "edge_count": load_meta(conn, "edge_count") or "0",
        "hotspot_count": load_meta(conn, "hotspot_count") or "0",
        "dead_code_count": load_meta(conn, "dead_code_count") or "0",
        "flow_count": load_meta(conn, "flow_count") or str(len(flow_data)),
    }

    conn.close()

    if output_dir is None:
        output_dir = str(repo_path / ".codestats" / "wiki")

    from .wiki import generate_wiki

    created_files = generate_wiki(community_data, flow_data, graph_stats, output_dir)

    if as_json:
        click.echo(json.dumps(created_files, indent=2))
        return

    click.echo(f"Wiki generated: {len(created_files)} files in {output_dir}")
    for f in created_files:
        click.echo(f"  {f}")


# ---------------------------------------------------------------------------
# impact command
# ---------------------------------------------------------------------------


@main.command()
@click.argument("files", nargs=-1)
@click.option("--path", default=None, help="Repository path.")
@click.option("--depth", default=3, type=int, help="Max BFS depth for impact analysis.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--changed", is_flag=True, help="Auto-detect changed files via git.")
def impact(files: tuple[str, ...], path: str | None, depth: int, as_json: bool, changed: bool) -> None:
    """Analyze blast radius of file changes."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    # Determine which files to analyze
    if changed:
        from .impact import get_changed_files_from_git

        file_list = get_changed_files_from_git(repo_path)
        if not file_list:
            conn.close()
            click.echo("No changed files detected via git.")
            return
    elif files:
        file_list = [f.replace("\\", "/") for f in files]
    else:
        conn.close()
        click.echo("Error: provide FILE argument(s) or use --changed flag.", err=True)
        sys.exit(1)

    # Reconstruct graph
    graph = _reconstruct_graph(conn)

    # Build git meta map and community map
    git_meta_raw = load_git_metadata(conn)
    git_meta_map = {m["file_path"]: m for m in git_meta_raw}

    from .storage import load_communities as _load_communities, load_community_members as _lcm

    community_data = _load_communities(conn)
    community_map: dict[str, int] = {}
    for c in community_data:
        members = _lcm(conn, c["community_id"])
        for m in members:
            community_map[m["file_path"]] = c["community_id"]

    conn.close()

    # Run impact analysis
    from .impact import analyze_impact

    result = analyze_impact(graph, file_list, git_meta_map, community_map, max_depth=depth)

    if as_json:
        click.echo(json.dumps(result, indent=2, default=str))
        return

    click.echo(f"Impact Analysis\n")
    click.echo(f"  Changed files:    {', '.join(result['changed_files'])}")
    click.echo(f"  Total impacted:   {result['total_impacted']}")
    click.echo(f"  Max risk:         {result['max_risk']:.3f}")
    click.echo(f"  Risk level:       {result['risk_level']}")

    if result.get("tested_files"):
        click.echo(f"  Test files hit:   {len(result['tested_files'])}")

    if result["impacted_files"]:
        click.echo(f"\n  {'File':<55} {'Risk':>6} {'Level':<10} {'Depth':>5} {'Tests':>5}")
        click.echo("  " + "-" * 85)
        for imp in result["impacted_files"]:
            fp = imp.get("path", "")
            if len(fp) > 53:
                fp = "..." + fp[-50:]
            risk = imp.get("risk_score", 0.0)
            level = imp.get("risk_level", "")
            d = imp.get("depth", 0)
            has_tests = "Yes" if imp.get("has_tests") else "No"
            click.echo(f"  {fp:<55} {risk:>6.3f} {level:<10} {d:>5} {has_tests:>5}")


# ---------------------------------------------------------------------------
# search command
# ---------------------------------------------------------------------------


@main.command()
@click.argument("query")
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--limit", default=20, type=int, help="Max results to return.")
@click.option("--kind", default=None, help="Filter by kind: function, class, method, etc.")
def search(query: str, path: str | None, as_json: bool, limit: int, kind: str | None) -> None:
    """Search code symbols via full-text search."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    from .search import search as _search

    results = _search(conn, query, limit=limit, kind_filter=kind)
    conn.close()

    if as_json:
        click.echo(json.dumps(results, indent=2, default=str))
        return

    if not results:
        click.echo(f"No symbols found matching '{query}'.")
        return

    click.echo(f"Search results for '{query}' ({len(results)} matches)\n")
    click.echo(f"  {'Name':<40} {'Kind':<12} {'File':<50}")
    click.echo("  " + "-" * 105)

    for r in results:
        name = r.get("qualified_name") or r.get("name") or ""
        if len(name) > 38:
            name = "..." + name[-35:]
        sym_kind = r.get("kind") or ""
        fp = r.get("file_path") or ""
        if len(fp) > 48:
            fp = "..." + fp[-45:]
        click.echo(f"  {name:<40} {sym_kind:<12} {fp:<50}")


# ---------------------------------------------------------------------------
# refactor group
# ---------------------------------------------------------------------------


@main.group()
def refactor() -> None:
    """Refactoring tools."""
    pass


@refactor.command("large")
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
@click.option("--threshold", default=50, type=int, help="Line count threshold for large functions.")
def refactor_large(path: str | None, as_json: bool, threshold: int) -> None:
    """Find large functions exceeding line threshold."""
    repo_path = _resolve_repo(path)

    # Re-parse files to get symbol line ranges
    from .traverser import FileTraverser
    from .parser import ASTParser

    traverser = FileTraverser(repo_path)
    file_infos = list(traverser.traverse())

    parser = ASTParser()
    parsed_files = []
    for fi in file_infos:
        try:
            source = Path(fi.abs_path).read_bytes()
            parsed_files.append(parser.parse_file(fi, source))
        except Exception:
            pass

    from .refactor import find_large_functions

    results = find_large_functions(parsed_files, threshold=threshold)

    if as_json:
        click.echo(json.dumps(results, indent=2, default=str))
        return

    if not results:
        click.echo(f"No functions found exceeding {threshold} lines.")
        return

    click.echo(f"Large Functions (>{threshold} lines): {len(results)}\n")
    click.echo(f"  {'Function':<50} {'File':<40} {'Lines':>5}")
    click.echo("  " + "-" * 100)

    for r in results:
        name = r.get("name", "")
        if len(name) > 48:
            name = "..." + name[-45:]
        fp = r.get("file_path", "")
        if len(fp) > 38:
            fp = "..." + fp[-35:]
        lines = r.get("line_count", 0)
        click.echo(f"  {name:<50} {fp:<40} {lines:>5}")


@refactor.command("moves")
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def refactor_moves(path: str | None, as_json: bool) -> None:
    """Suggest files that may be in the wrong module."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    graph = _reconstruct_graph(conn)

    # Build community map
    from .storage import load_communities as _load_communities, load_community_members as _lcm

    community_data = _load_communities(conn)
    community_map: dict[str, int] = {}
    for c in community_data:
        members = _lcm(conn, c["community_id"])
        for m in members:
            community_map[m["file_path"]] = c["community_id"]

    conn.close()

    from .refactor import get_move_suggestions

    suggestions = get_move_suggestions(graph, community_map)

    if as_json:
        click.echo(json.dumps(suggestions, indent=2, default=str))
        return

    if not suggestions:
        click.echo("No move suggestions. All files appear well-placed.")
        return

    click.echo(f"Move Suggestions ({len(suggestions)})\n")
    click.echo(f"  {'File':<50} {'Current':>7} {'Suggested':>9} {'Callers':>7}")
    click.echo("  " + "-" * 78)

    for s in suggestions:
        fp = s.get("file_path", "")
        if len(fp) > 48:
            fp = "..." + fp[-45:]
        current = s.get("current_community", "")
        suggested = s.get("suggested_community", "")
        callers = s.get("caller_count", 0)
        click.echo(f"  {fp:<50} {current:>7} {suggested:>9} {callers:>7}")


@refactor.command("rename")
@click.argument("target")
@click.argument("new_name")
@click.argument("path", required=False)
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def refactor_rename(target: str, new_name: str, path: str | None, as_json: bool) -> None:
    """Preview a rename operation (read-only)."""
    repo_path = _resolve_repo(path)
    conn = _open_db(repo_path)

    graph = _reconstruct_graph(conn)

    from .refactor import preview_rename

    preview = preview_rename(conn, graph, target, new_name)
    conn.close()

    if as_json:
        click.echo(json.dumps(preview, indent=2, default=str))
        return

    click.echo(f"Rename Preview: {target} -> {new_name}\n")
    click.echo(f"  Preview ID:   {preview.get('preview_id', '')}")
    click.echo(f"  Edits:        {preview.get('edit_count', 0)}")

    edits = preview.get("edits", [])
    if edits:
        click.echo(f"\n  {'File':<55} {'Kind':<18} {'Old':<20} {'New':<20}")
        click.echo("  " + "-" * 115)
        for e in edits:
            fp = e.get("file", "")
            if len(fp) > 53:
                fp = "..." + fp[-50:]
            edit_kind = e.get("kind", "")
            old = e.get("old_text", "")
            new = e.get("new_text", "")
            if len(old) > 18:
                old = old[:15] + "..."
            if len(new) > 18:
                new = new[:15] + "..."
            click.echo(f"  {fp:<55} {edit_kind:<18} {old:<20} {new:<20}")
    else:
        click.echo("\n  No references found for this target.")
