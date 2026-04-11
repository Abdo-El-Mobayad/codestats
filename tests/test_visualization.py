"""Tests for codestats.visualization -- D3.js HTML generation."""

from __future__ import annotations

from pathlib import Path

from codestats.visualization import generate_visualization


def _sample_data():
    """Return sample nodes, edges, communities for visualization tests."""
    nodes = [
        {"path": "src/main.py", "language": "python", "pagerank": 0.3,
         "is_entry_point": True, "is_test": False, "symbol_count": 3,
         "community_id": 0, "betweenness": 0.5},
        {"path": "src/utils.py", "language": "python", "pagerank": 0.2,
         "is_entry_point": False, "is_test": False, "symbol_count": 5,
         "community_id": 0, "betweenness": 0.1},
        {"path": "tests/test_main.py", "language": "python", "pagerank": 0.05,
         "is_entry_point": False, "is_test": True, "symbol_count": 2,
         "community_id": 1, "betweenness": 0.0},
    ]
    edges = [
        {"source": "src/main.py", "target": "src/utils.py", "edge_type": "IMPORTS_FROM"},
        {"source": "src/main.py", "target": "tests/test_main.py", "edge_type": "TESTED_BY"},
    ]
    communities = [
        {"community_id": 0, "name": "src", "member_count": 2, "cohesion": 0.8},
        {"community_id": 1, "name": "tests", "member_count": 1, "cohesion": 1.0},
    ]
    return nodes, edges, communities


def test_generate_html_output(tmp_path: Path):
    """generate_visualization creates an HTML file."""
    nodes, edges, communities = _sample_data()
    output = tmp_path / "graph.html"

    result = generate_visualization(
        nodes, edges, communities, "test-repo", str(output)
    )

    assert Path(result).exists()
    content = Path(result).read_text(encoding="utf-8")
    assert len(content) > 100


def test_html_contains_d3_script(tmp_path: Path):
    """Generated HTML includes D3.js script tag."""
    nodes, edges, communities = _sample_data()
    output = tmp_path / "graph.html"

    generate_visualization(nodes, edges, communities, "test-repo", str(output))

    content = output.read_text(encoding="utf-8")
    assert "d3.v7.min.js" in content or "d3.js" in content
    assert "const DATA" in content


def test_html_escapes_node_names(tmp_path: Path):
    """Generated HTML handles special characters in node paths."""
    nodes = [
        {"path": "src/<script>.py", "language": "python", "pagerank": 0.5,
         "is_entry_point": False, "is_test": False, "symbol_count": 1,
         "community_id": 0, "betweenness": 0.0},
    ]
    edges = []
    communities = []
    output = tmp_path / "graph.html"

    # Should not crash
    generate_visualization(nodes, edges, communities, "test-repo", str(output))
    assert output.exists()
