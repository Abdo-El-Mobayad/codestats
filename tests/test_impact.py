"""Tests for codestats.impact -- bidirectional impact analysis."""

from __future__ import annotations

import networkx as nx

from codestats.impact import analyze_impact, _classify_risk


def test_impact_single_file(sample_graph: nx.DiGraph):
    """Impact analysis on a single changed file finds impacted neighbors."""
    result = analyze_impact(
        sample_graph,
        changed_files=["src/utils.py"],
        git_meta={},
        communities={},
        max_depth=2,
    )

    assert result["total_impacted"] > 0
    impacted_paths = [f["path"] for f in result["impacted_files"]]
    # main.py imports utils.py so should be impacted
    assert "src/main.py" in impacted_paths


def test_impact_bidirectional_bfs(sample_graph: nx.DiGraph):
    """Impact analysis traverses both directions (predecessors and successors)."""
    result = analyze_impact(
        sample_graph,
        changed_files=["src/auth.py"],
        git_meta={},
        communities={},
        max_depth=3,
    )

    impacted_paths = [f["path"] for f in result["impacted_files"]]
    # auth.py is imported by main.py and api.py (backward via predecessors)
    assert "src/main.py" in impacted_paths or "src/api.py" in impacted_paths
    # auth.py is tested by test_auth.py (TESTED_BY edge)
    assert "tests/test_auth.py" in impacted_paths


def test_impact_risk_scoring_no_tests(sample_graph: nx.DiGraph):
    """Files without test coverage get higher risk scores."""
    # src/models.py has no TESTED_BY edges
    result = analyze_impact(
        sample_graph,
        changed_files=["src/db.py"],
        git_meta={},
        communities={},
        max_depth=3,
    )

    for f in result["impacted_files"]:
        if f["path"] == "src/models.py":
            # No tests for models.py, should have elevated risk
            assert f["risk_score"] >= 0.3
            break


def test_impact_risk_level_classification():
    """Risk level classifier returns correct buckets."""
    assert _classify_risk(0.0) == "LOW"
    assert _classify_risk(0.29) == "LOW"
    assert _classify_risk(0.3) == "MEDIUM"
    assert _classify_risk(0.5) == "HIGH"
    assert _classify_risk(0.7) == "CRITICAL"
    assert _classify_risk(1.0) == "CRITICAL"


def test_impact_empty_graph():
    """Impact analysis on an empty graph returns empty results."""
    g = nx.DiGraph()
    result = analyze_impact(g, ["nonexistent.py"], {}, {})

    assert result["total_impacted"] == 0
    assert result["risk_level"] == "LOW"


def test_impact_disconnected_file(sample_graph: nx.DiGraph):
    """Impact analysis on a disconnected file (orphan) has zero impacted."""
    result = analyze_impact(
        sample_graph,
        changed_files=["src/orphan.py"],
        git_meta={},
        communities={},
        max_depth=3,
    )

    # Orphan has no edges, so impact should be 0
    assert result["total_impacted"] == 0
