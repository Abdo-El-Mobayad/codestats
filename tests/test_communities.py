"""Tests for codestats.communities -- Louvain community detection."""

from __future__ import annotations

import networkx as nx

from codestats.communities import detect_communities, get_architecture_coupling, _compute_cohesion


def test_detect_communities_basic(sample_graph: nx.DiGraph):
    """detect_communities returns community dicts with required keys."""
    communities = detect_communities(sample_graph)

    assert len(communities) > 0
    for c in communities:
        assert "community_id" in c
        assert "name" in c
        assert "members" in c
        assert "member_count" in c
        assert "cohesion" in c
        assert "top_files" in c
        assert c["member_count"] == len(c["members"])


def test_community_naming():
    """Community naming uses directory prefix heuristic."""
    g = nx.DiGraph()
    # All files under src/
    for name in ["src/a.py", "src/b.py", "src/c.py", "src/d.py"]:
        g.add_node(name, language="python", symbol_count=1, has_error=False,
                   is_test=False, is_entry_point=False)
    g.add_edge("src/a.py", "src/b.py")
    g.add_edge("src/c.py", "src/d.py")

    communities = detect_communities(g)
    # With all files under src/, name should reference src
    names = [c["name"] for c in communities]
    assert any("src" in n for n in names)


def test_community_cohesion():
    """Cohesion is between 0 and 1."""
    g = nx.DiGraph()
    members = {"a.py", "b.py"}
    g.add_node("a.py")
    g.add_node("b.py")
    g.add_node("c.py")
    g.add_edge("a.py", "b.py")  # internal
    g.add_edge("a.py", "c.py")  # external

    cohesion = _compute_cohesion(g, members)
    assert 0.0 <= cohesion <= 1.0
    # 1 internal, 1 external: cohesion = 0.5
    assert abs(cohesion - 0.5) < 0.01


def test_architecture_coupling(sample_graph: nx.DiGraph):
    """get_architecture_coupling returns coupling warnings between communities."""
    communities = detect_communities(sample_graph)
    warnings = get_architecture_coupling(sample_graph, communities)

    # Result should be a list (may be empty if no significant coupling)
    assert isinstance(warnings, list)
    for w in warnings:
        assert "community_a" in w
        assert "community_b" in w
        assert "cross_edges" in w
        assert "warning_level" in w


def test_empty_graph_communities():
    """detect_communities returns empty list for empty graph."""
    g = nx.DiGraph()
    communities = detect_communities(g)
    assert communities == []
