"""Tests for codestats.dead_code -- dead code detection."""

from __future__ import annotations

import networkx as nx

from codestats.dead_code import DeadCodeAnalyzer, DeadCodeKind, _NEVER_FLAG_PATTERNS


def _build_graph_with_orphan() -> nx.DiGraph:
    """Build a graph with one connected and one orphan file."""
    g = nx.DiGraph()
    g.add_node("src/main.py", language="python", symbol_count=2,
               is_test=False, is_entry_point=True, has_error=False)
    g.add_node("src/utils.py", language="python", symbol_count=3,
               is_test=False, is_entry_point=False, has_error=False)
    g.add_node("src/orphan.py", language="python", symbol_count=1,
               is_test=False, is_entry_point=False, has_error=False)
    g.add_edge("src/main.py", "src/utils.py", imported_names=["f"])
    return g


def test_unreachable_file_detection():
    """DeadCodeAnalyzer detects files with in_degree=0."""
    g = _build_graph_with_orphan()
    analyzer = DeadCodeAnalyzer(g)
    findings = analyzer.analyze()

    # orphan.py has no importers and is not an entry point
    orphan_findings = [f for f in findings if f.file_path == "src/orphan.py"]
    assert len(orphan_findings) == 1
    assert orphan_findings[0].kind == DeadCodeKind.UNREACHABLE_FILE


def test_unreachable_excludes_entry_points():
    """DeadCodeAnalyzer does not flag entry points as unreachable."""
    g = _build_graph_with_orphan()
    analyzer = DeadCodeAnalyzer(g)
    findings = analyzer.analyze()

    # main.py is an entry point, should not be flagged
    main_findings = [f for f in findings if f.file_path == "src/main.py"]
    assert len(main_findings) == 0


def test_unreachable_excludes_test_files():
    """DeadCodeAnalyzer does not flag test files as unreachable."""
    g = nx.DiGraph()
    g.add_node("tests/test_foo.py", language="python", symbol_count=2,
               is_test=True, is_entry_point=False, has_error=False)
    analyzer = DeadCodeAnalyzer(g)
    findings = analyzer.analyze()

    test_findings = [f for f in findings if f.file_path == "tests/test_foo.py"]
    assert len(test_findings) == 0


def test_never_flag_patterns():
    """Certain patterns like __init__.py are never flagged."""
    g = nx.DiGraph()
    g.add_node("pkg/__init__.py", language="python", symbol_count=0,
               is_test=False, is_entry_point=False, has_error=False)
    g.add_node("conftest.py", language="python", symbol_count=0,
               is_test=False, is_entry_point=False, has_error=False)
    g.add_node("setup.py", language="python", symbol_count=0,
               is_test=False, is_entry_point=False, has_error=False)

    analyzer = DeadCodeAnalyzer(g)
    findings = analyzer.analyze()

    flagged_paths = {f.file_path for f in findings}
    assert "pkg/__init__.py" not in flagged_paths
    assert "conftest.py" not in flagged_paths


def test_misplaced_file_suggestions(sample_graph: nx.DiGraph):
    """suggest_moves detects files whose importers are in a different community."""
    # Build a community map where orphan is in a different community
    community_map = {}
    for node in sample_graph.nodes():
        if "test" in str(node):
            community_map[node] = 2
        elif str(node).startswith("src/"):
            community_map[node] = 0
        else:
            community_map[node] = 1

    analyzer = DeadCodeAnalyzer(sample_graph)
    suggestions = analyzer.suggest_moves(community_map)

    # All suggestions should be MISPLACED_FILE kind
    for s in suggestions:
        assert s.kind == DeadCodeKind.MISPLACED_FILE
        assert s.confidence == 0.3
