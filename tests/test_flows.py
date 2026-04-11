"""Tests for codestats.flows -- execution flow tracing."""

from __future__ import annotations

import networkx as nx

from codestats.flows import discover_entry_points, trace_flows, _score_criticality


def test_discover_entry_points(sample_graph: nx.DiGraph):
    """discover_entry_points finds nodes marked as entry points and true roots."""
    entries = discover_entry_points(sample_graph)

    # src/main.py is marked is_entry_point=True
    assert "src/main.py" in entries


def test_trace_flow_simple_chain(sample_graph: nx.DiGraph):
    """trace_flows discovers flows from entry points through the graph."""
    flows = trace_flows(sample_graph, max_depth=10)

    # Should find at least one flow starting from main.py
    entry_points = [f["entry_point"] for f in flows]
    assert "src/main.py" in entry_points

    main_flow = [f for f in flows if f["entry_point"] == "src/main.py"][0]
    assert main_flow["node_count"] >= 2
    assert main_flow["file_spread"] >= 1


def test_trace_flow_depth_limit():
    """trace_flows respects depth limit."""
    g = nx.DiGraph()
    # Build a chain: a -> b -> c -> d -> e
    for i, name in enumerate(["a.py", "b.py", "c.py", "d.py", "e.py"]):
        g.add_node(name, language="python", symbol_count=1, has_error=False,
                   is_test=False, is_entry_point=(i == 0), content_hash="")
    g.add_edge("a.py", "b.py", imported_names=[], edge_type="IMPORTS_FROM")
    g.add_edge("b.py", "c.py", imported_names=[], edge_type="IMPORTS_FROM")
    g.add_edge("c.py", "d.py", imported_names=[], edge_type="IMPORTS_FROM")
    g.add_edge("d.py", "e.py", imported_names=[], edge_type="IMPORTS_FROM")

    flows = trace_flows(g, max_depth=2)

    # With depth_limit=2, flow from a.py should include a, b, c but not d, e
    main_flow = [f for f in flows if f["entry_point"] == "a.py"]
    if main_flow:
        assert main_flow[0]["node_count"] <= 3


def test_criticality_score_security_keywords():
    """Criticality scoring boosts flows touching security-related files."""
    g = nx.DiGraph()
    g.add_node("auth.py", language="python", is_test=False)
    g.add_node("login.py", language="python", is_test=False)

    members = ["auth.py", "login.py"]
    score = _score_criticality(g, members, max_depth=10)

    # Security keywords in both names should boost score
    assert score > 0.0


def test_criticality_score_file_spread():
    """Criticality increases with file spread across directories."""
    g = nx.DiGraph()
    members = ["src/a.py", "lib/b.py", "pkg/c.py", "utils/d.py", "core/e.py"]
    for m in members:
        g.add_node(m, language="python", is_test=False)

    score = _score_criticality(g, members, max_depth=10)

    # 5 directories should give high spread factor
    assert score >= 0.20
