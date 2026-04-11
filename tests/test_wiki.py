"""Tests for codestats.wiki -- markdown wiki generation."""

from __future__ import annotations

from pathlib import Path

from codestats.wiki import generate_wiki


def _sample_data():
    """Return sample communities, flows, and graph stats."""
    communities = [
        {
            "community_id": 0,
            "name": "core",
            "members": ["src/main.py", "src/utils.py"],
            "member_count": 2,
            "cohesion": 0.85,
            "top_files": ["src/main.py"],
        },
        {
            "community_id": 1,
            "name": "tests",
            "members": ["tests/test_main.py"],
            "member_count": 1,
            "cohesion": 1.0,
            "top_files": ["tests/test_main.py"],
        },
    ]
    flows = [
        {
            "flow_id": "abc12345",
            "entry_point": "src/main.py",
            "node_count": 3,
            "file_spread": 2,
            "criticality": 0.6,
            "has_test_coverage": True,
        },
    ]
    stats = {
        "repo_name": "test-project",
        "file_count": 5,
        "edge_count": 3,
        "hotspot_count": 1,
        "dead_code_count": 0,
        "flow_count": 1,
    }
    return communities, flows, stats


def test_generate_wiki_creates_index(tmp_path: Path):
    """generate_wiki creates an index.md file."""
    communities, flows, stats = _sample_data()
    output_dir = tmp_path / "wiki"

    files = generate_wiki(communities, flows, stats, str(output_dir))

    index_path = output_dir / "index.md"
    assert index_path.exists()
    assert str(index_path) in files


def test_generate_wiki_creates_community_pages(tmp_path: Path):
    """generate_wiki creates per-community markdown pages."""
    communities, flows, stats = _sample_data()
    output_dir = tmp_path / "wiki"

    files = generate_wiki(communities, flows, stats, str(output_dir))

    # Should have index + 2 community pages + 1 flows page = 4
    assert len(files) >= 3

    # Check community page exists
    community_files = [f for f in files if "community-" in f]
    assert len(community_files) == 2


def test_wiki_index_content(tmp_path: Path):
    """Wiki index contains project name and community table."""
    communities, flows, stats = _sample_data()
    output_dir = tmp_path / "wiki"

    generate_wiki(communities, flows, stats, str(output_dir))

    index = (output_dir / "index.md").read_text(encoding="utf-8")
    assert "test-project" in index
    assert "core" in index
    assert "tests" in index
    assert "| ID |" in index  # table header
