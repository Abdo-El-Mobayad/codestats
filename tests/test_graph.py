"""Tests for codestats.graph -- dependency graph building and metrics."""

from __future__ import annotations

import textwrap
from datetime import datetime
from pathlib import Path

import networkx as nx

from codestats.graph import GraphBuilder
from codestats.models import FileInfo, Import, ParsedFile, Symbol


def _make_parsed(path: str, language: str, imports: list[Import] | None = None,
                 symbols: list[Symbol] | None = None, is_test: bool = False,
                 is_entry: bool = False) -> ParsedFile:
    """Helper to create a ParsedFile with sensible defaults."""
    fi = FileInfo(
        path=path,
        abs_path=str(Path.cwd() / path),
        language=language,
        size_bytes=100,
        last_modified=datetime.now(),
        is_test=is_test,
        is_config=False,
        is_entry_point=is_entry,
    )
    return ParsedFile(
        file_info=fi,
        symbols=symbols or [],
        imports=imports or [],
        exports=[],
        docstring=None,
        parse_errors=[],
        content_hash="",
    )


def test_graph_adds_nodes():
    """GraphBuilder registers nodes for each added file."""
    builder = GraphBuilder()
    builder.add_file(_make_parsed("a.py", "python"))
    builder.add_file(_make_parsed("b.py", "python"))
    g = builder.build()

    assert "a.py" in g.nodes
    assert "b.py" in g.nodes
    assert g.number_of_nodes() == 2


def test_graph_resolves_python_imports():
    """GraphBuilder resolves Python dotted imports to files."""
    builder = GraphBuilder()
    builder.add_file(_make_parsed("utils.py", "python"))
    builder.add_file(_make_parsed(
        "main.py", "python",
        imports=[Import(
            raw_statement="from utils import helper",
            module_path="utils",
            imported_names=["helper"],
            is_relative=False,
            resolved_file=None,
        )],
    ))
    g = builder.build()

    assert g.has_edge("main.py", "utils.py")
    assert g["main.py"]["utils.py"]["imported_names"] == ["helper"]


def test_graph_resolves_typescript_imports():
    """GraphBuilder resolves TypeScript relative imports."""
    builder = GraphBuilder()
    builder.add_file(_make_parsed("src/utils.ts", "typescript"))
    builder.add_file(_make_parsed(
        "src/index.ts", "typescript",
        imports=[Import(
            raw_statement="import { greet } from './utils';",
            module_path="./utils",
            imported_names=["greet"],
            is_relative=True,
            resolved_file=None,
        )],
    ))
    g = builder.build()

    assert g.has_edge("src/index.ts", "src/utils.ts")


def test_graph_creates_external_nodes():
    """GraphBuilder creates external: nodes for unresolvable TS/JS imports."""
    builder = GraphBuilder()
    builder.add_file(_make_parsed(
        "src/index.ts", "typescript",
        imports=[Import(
            raw_statement="import express from 'express';",
            module_path="express",
            imported_names=["express"],
            is_relative=False,
            resolved_file=None,
        )],
    ))
    g = builder.build()

    assert "external:express" in g.nodes
    assert g.has_edge("src/index.ts", "external:express")


def test_graph_pagerank_computation():
    """GraphBuilder.pagerank() returns scores summing to ~1.0."""
    builder = GraphBuilder()
    builder.add_file(_make_parsed("a.py", "python"))
    builder.add_file(_make_parsed(
        "b.py", "python",
        imports=[Import("import a", "a", ["a"], False, None)],
    ))
    builder.add_file(_make_parsed(
        "c.py", "python",
        imports=[Import("import a", "a", ["a"], False, None)],
    ))
    builder.build()
    pr = builder.pagerank()

    assert len(pr) == 3
    assert abs(sum(pr.values()) - 1.0) < 0.01


def test_graph_betweenness_centrality():
    """GraphBuilder.betweenness_centrality() returns values in [0, 1]."""
    builder = GraphBuilder()
    for name in ["a.py", "b.py", "c.py"]:
        builder.add_file(_make_parsed(name, "python"))
    builder.add_file(_make_parsed(
        "b.py", "python",
        imports=[Import("import a", "a", ["a"], False, None)],
    ))
    builder.add_file(_make_parsed(
        "c.py", "python",
        imports=[Import("import b", "b", ["b"], False, None)],
    ))
    builder.build()
    bc = builder.betweenness_centrality()

    assert all(0 <= v <= 1 for v in bc.values())


def test_graph_tested_by_edges():
    """GraphBuilder creates TESTED_BY reverse edges from test files."""
    builder = GraphBuilder()
    builder.add_file(_make_parsed("src/utils.py", "python"))
    builder.add_file(_make_parsed(
        "tests/test_utils.py", "python",
        imports=[Import("from src.utils import f", "src.utils", ["f"], False, None)],
        is_test=True,
    ))
    g = builder.build()

    # Forward edge: test imports utils
    assert g.has_edge("tests/test_utils.py", "src/utils.py")
    # Reverse TESTED_BY edge
    assert g.has_edge("src/utils.py", "tests/test_utils.py")
    assert g["src/utils.py"]["tests/test_utils.py"]["edge_type"] == "TESTED_BY"


def test_graph_handles_missing_imports():
    """GraphBuilder handles imports that cannot be resolved without crashing."""
    builder = GraphBuilder()
    builder.add_file(_make_parsed(
        "main.py", "python",
        imports=[Import("import nonexistent", "nonexistent", ["nonexistent"], False, None)],
    ))
    g = builder.build()

    # Should not crash, and no edges to nonexistent module
    assert g.number_of_nodes() == 1


def test_graph_community_detection():
    """GraphBuilder.community_detection() assigns community IDs."""
    builder = GraphBuilder()
    for name in ["a.py", "b.py", "c.py", "d.py"]:
        builder.add_file(_make_parsed(name, "python"))
    # Create some edges to form communities
    builder.add_file(_make_parsed(
        "a.py", "python",
        imports=[Import("import b", "b", ["b"], False, None)],
    ))
    builder.add_file(_make_parsed(
        "c.py", "python",
        imports=[Import("import d", "d", ["d"], False, None)],
    ))
    builder.build()
    communities = builder.community_detection()

    assert len(communities) > 0
    assert all(isinstance(v, int) for v in communities.values())


def test_graph_strongly_connected_components():
    """GraphBuilder.strongly_connected_components() detects cycles."""
    builder = GraphBuilder()
    builder.add_file(_make_parsed(
        "a.py", "python",
        imports=[Import("import b", "b", ["b"], False, None)],
    ))
    builder.add_file(_make_parsed(
        "b.py", "python",
        imports=[Import("import a", "a", ["a"], False, None)],
    ))
    g = builder.build()
    sccs = builder.strongly_connected_components()

    # a.py and b.py form a cycle
    cycle_sccs = [scc for scc in sccs if len(scc) > 1]
    assert len(cycle_sccs) == 1
    assert "a.py" in cycle_sccs[0]
    assert "b.py" in cycle_sccs[0]
