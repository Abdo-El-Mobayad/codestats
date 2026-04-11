"""Tests for codestats.storage -- SQLite persistence layer."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import networkx as nx

from codestats.storage import (
    init_db,
    load_communities,
    load_community_members,
    load_flows,
    load_graph_edges,
    load_graph_nodes,
    load_meta,
    persist_communities,
    persist_community_members,
    persist_flows,
    persist_flow_members,
    persist_fts_index,
    persist_graph,
    persist_meta,
    search_fts,
    load_flow_members,
)


def test_init_db_creates_tables(tmp_path: Path):
    """init_db creates all required tables."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    cursor = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    tables = {row[0] for row in cursor.fetchall()}

    expected = {
        "meta", "graph_nodes", "graph_edges", "git_metadata",
        "dead_code", "flows", "flow_members", "communities",
        "community_members", "refactor_previews",
    }
    for t in expected:
        assert t in tables, f"Missing table: {t}"

    conn.close()


def test_schema_migration_v2(tmp_path: Path):
    """Schema migration sets version to 3 and creates all new tables."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)

    version = load_meta(conn, "schema_version")
    assert version == "3"
    conn.close()


def test_persist_and_load_graph_nodes(db_conn: sqlite3.Connection):
    """Persist and load graph nodes round-trip."""
    g = nx.DiGraph()
    g.add_node("src/main.py", language="python", symbol_count=3,
               is_entry_point=True, is_test=False, content_hash="abc",
               indexed_file_at=None, community_id=0)
    g.add_node("src/utils.py", language="python", symbol_count=5,
               is_entry_point=False, is_test=False, content_hash="def",
               indexed_file_at=None, community_id=0)

    persist_graph(db_conn, g, {"src/main.py": 0.6, "src/utils.py": 0.4},
                  {"src/main.py": 0.3, "src/utils.py": 0.1})

    nodes = load_graph_nodes(db_conn)
    assert len(nodes) == 2
    paths = {n["path"] for n in nodes}
    assert "src/main.py" in paths
    assert "src/utils.py" in paths


def test_persist_and_load_graph_edges(db_conn: sqlite3.Connection):
    """Persist and load graph edges round-trip."""
    g = nx.DiGraph()
    g.add_node("a.py", language="python", symbol_count=1,
               is_entry_point=False, is_test=False, content_hash="x")
    g.add_node("b.py", language="python", symbol_count=1,
               is_entry_point=False, is_test=False, content_hash="y")
    g.add_edge("a.py", "b.py", imported_names=["foo"], edge_type="IMPORTS_FROM")

    persist_graph(db_conn, g, {"a.py": 0.5, "b.py": 0.5}, {"a.py": 0.1, "b.py": 0.1})

    edges = load_graph_edges(db_conn)
    assert len(edges) == 1
    assert edges[0]["source"] == "a.py"
    assert edges[0]["target"] == "b.py"
    assert json.loads(edges[0]["imported_names"]) == ["foo"]


def test_persist_and_load_communities(db_conn: sqlite3.Connection):
    """Persist and load community data round-trip."""
    communities = [
        {
            "community_id": 0,
            "name": "core",
            "member_count": 5,
            "cohesion": 0.8,
            "top_files": ["a.py", "b.py"],
        },
        {
            "community_id": 1,
            "name": "utils",
            "member_count": 3,
            "cohesion": 0.6,
            "top_files": ["c.py"],
        },
    ]
    persist_communities(db_conn, communities)

    loaded = load_communities(db_conn)
    assert len(loaded) == 2
    assert loaded[0]["name"] == "core"
    assert loaded[0]["top_files"] == ["a.py", "b.py"]


def test_persist_fts_index_and_search(db_conn: sqlite3.Connection):
    """Persist FTS index and search for symbols."""
    symbols = [
        {
            "qualified_name": "mypackage.utils.helper",
            "name": "helper",
            "kind": "function",
            "file_path": "mypackage/utils.py",
            "signature": "def helper()",
        },
        {
            "qualified_name": "mypackage.models.User",
            "name": "User",
            "kind": "class",
            "file_path": "mypackage/models.py",
            "signature": "class User",
        },
    ]
    persist_fts_index(db_conn, symbols)

    results = search_fts(db_conn, "helper")
    assert len(results) >= 1
    assert results[0]["name"] == "helper"


def test_persist_and_load_flows(db_conn: sqlite3.Connection):
    """Persist and load flow data round-trip."""
    flows = [
        {
            "flow_id": "abc123",
            "entry_point": "main.py",
            "node_count": 5,
            "file_spread": 3,
            "criticality": 0.7,
            "has_test_coverage": True,
        },
    ]
    persist_flows(db_conn, flows)

    members = [
        {"node_qualified_name": "main.run", "depth": 0},
        {"node_qualified_name": "utils.helper", "depth": 1},
    ]
    persist_flow_members(db_conn, "abc123", members)

    loaded_flows = load_flows(db_conn)
    assert len(loaded_flows) == 1
    assert loaded_flows[0]["entry_point"] == "main.py"

    loaded_members = load_flow_members(db_conn, "abc123")
    assert len(loaded_members) == 2


def test_get_repo_name(tmp_path: Path):
    """get_repo_name falls back to directory name without git remote."""
    from codestats.storage import get_repo_name
    name = get_repo_name(tmp_path)
    assert name == tmp_path.name
