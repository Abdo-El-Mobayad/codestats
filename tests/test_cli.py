"""Tests for codestats.cli -- Click CLI commands."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import networkx as nx
from click.testing import CliRunner

from codestats.cli import main
from codestats.storage import (
    get_db_path,
    init_db,
    persist_communities,
    persist_community_members,
    persist_dead_code,
    persist_flows,
    persist_flow_members,
    persist_fts_index,
    persist_graph,
    persist_meta,
)


def _seed_db(repo_path: Path) -> None:
    """Create and seed a database for CLI tests."""
    db_path = get_db_path(repo_path)
    conn = init_db(db_path)

    # Build a simple graph
    g = nx.DiGraph()
    g.add_node("src/main.py", language="python", symbol_count=2,
               is_entry_point=True, is_test=False, content_hash="a")
    g.add_node("src/utils.py", language="python", symbol_count=3,
               is_entry_point=False, is_test=False, content_hash="b")
    g.add_edge("src/main.py", "src/utils.py", imported_names=["f"],
               edge_type="IMPORTS_FROM")

    persist_graph(conn, g, {"src/main.py": 0.6, "src/utils.py": 0.4},
                  {"src/main.py": 0.3, "src/utils.py": 0.1})
    persist_meta(conn, "repo_path", str(repo_path))
    persist_meta(conn, "repo_name", "test-repo")
    persist_meta(conn, "indexed_at", "2024-01-01T00:00:00")
    persist_meta(conn, "file_count", "2")
    persist_meta(conn, "edge_count", "1")
    persist_meta(conn, "hotspot_count", "0")
    persist_meta(conn, "dead_code_count", "0")
    persist_meta(conn, "flow_count", "0")

    # Communities
    persist_communities(conn, [
        {"community_id": 0, "name": "src", "member_count": 2,
         "cohesion": 0.8, "top_files": ["src/main.py", "src/utils.py"]},
    ])
    persist_community_members(conn, [
        {"file_path": "src/main.py", "community_id": 0},
        {"file_path": "src/utils.py", "community_id": 0},
    ])

    # Flows
    persist_flows(conn, [
        {"flow_id": "test123", "entry_point": "src/main.py",
         "node_count": 2, "file_spread": 1, "criticality": 0.5,
         "has_test_coverage": False},
    ])
    persist_flow_members(conn, "test123", [
        {"node_qualified_name": "src.main.run", "depth": 0},
    ])

    # FTS
    persist_fts_index(conn, [
        {"qualified_name": "src.main.run", "name": "run",
         "kind": "function", "file_path": "src/main.py",
         "signature": "def run()"},
    ])

    conn.close()


def test_cli_init(tmp_repo: Path, cli_runner: CliRunner):
    """codestats init indexes a project."""
    result = cli_runner.invoke(main, ["init", "--force", str(tmp_repo)])
    assert result.exit_code == 0, result.output
    assert "Indexing project" in result.output or "Done" in result.output


def test_cli_status(tmp_repo: Path, cli_runner: CliRunner):
    """codestats status shows project summary."""
    _seed_db(tmp_repo)
    result = cli_runner.invoke(main, ["status", str(tmp_repo)])
    assert result.exit_code == 0, result.output
    assert "test-repo" in result.output or "status" in result.output.lower() or "file" in result.output.lower()


def test_cli_dead_code(tmp_repo: Path, cli_runner: CliRunner):
    """codestats dead-code runs without error."""
    _seed_db(tmp_repo)
    result = cli_runner.invoke(main, ["dead-code", str(tmp_repo)])
    assert result.exit_code == 0, result.output


def test_cli_communities(tmp_repo: Path, cli_runner: CliRunner):
    """codestats communities lists detected communities."""
    _seed_db(tmp_repo)
    result = cli_runner.invoke(main, ["communities", str(tmp_repo)])
    assert result.exit_code == 0, result.output


def test_cli_search(tmp_repo: Path, cli_runner: CliRunner):
    """codestats search finds symbols."""
    _seed_db(tmp_repo)
    result = cli_runner.invoke(main, ["search", "run", str(tmp_repo)])
    assert result.exit_code == 0, result.output


def test_cli_flows(tmp_repo: Path, cli_runner: CliRunner):
    """codestats flows lists execution flows."""
    _seed_db(tmp_repo)
    result = cli_runner.invoke(main, ["flows", str(tmp_repo)])
    assert result.exit_code == 0, result.output


def test_cli_cycles(tmp_repo: Path, cli_runner: CliRunner):
    """codestats cycles detects circular dependencies."""
    _seed_db(tmp_repo)
    result = cli_runner.invoke(main, ["cycles", str(tmp_repo)])
    assert result.exit_code == 0, result.output


def test_cli_help(cli_runner: CliRunner):
    """codestats --help shows usage information."""
    result = cli_runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "CodeStats" in result.output or "codestats" in result.output.lower()
