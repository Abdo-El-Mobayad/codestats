"""Wiki generation from community structure and execution flows.

Generates a markdown wiki with an index page, per-community pages,
and a flows summary.
"""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)


def generate_wiki(
    communities: list[dict],
    flows: list[dict],
    graph_stats: dict,
    output_dir: str,
) -> list[str]:
    """Generate markdown wiki from community structure.

    Parameters
    ----------
    communities : list[dict]
        Community data: [{community_id, name, members, member_count,
        cohesion, top_files}]
    flows : list[dict]
        Flow data: [{flow_id, entry_point, node_count, file_spread,
        criticality, has_test_coverage}]
    graph_stats : dict
        Summary stats: {repo_name, file_count, edge_count, hotspot_count,
        dead_code_count, flow_count}
    output_dir : str
        Directory to write wiki files to.

    Returns
    -------
    list[str]
        Paths of all created files.
    """
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    created: list[str] = []

    # Index page
    index_path = out / "index.md"
    index_path.write_text(_build_index(communities, graph_stats), encoding="utf-8")
    created.append(str(index_path))

    # Per-community pages
    for c in communities:
        cid = c["community_id"]
        slug = _slugify(c.get("name", f"community-{cid}"))
        page_path = out / f"community-{slug}.md"
        page_path.write_text(_build_community_page(c), encoding="utf-8")
        created.append(str(page_path))

    # Flows page
    if flows:
        flows_path = out / "flows.md"
        flows_path.write_text(_build_flows_page(flows), encoding="utf-8")
        created.append(str(flows_path))

    log.info("Wiki generated: %d files in %s", len(created), output_dir)
    return created


def _build_index(communities: list[dict], stats: dict) -> str:
    """Build the wiki index page."""
    repo = stats.get("repo_name", "Project")
    lines = [
        f"# {repo} - Architecture Wiki",
        "",
        "## Overview",
        "",
        f"| Metric | Value |",
        f"|--------|-------|",
        f"| Files | {stats.get('file_count', 'N/A')} |",
        f"| Internal Edges | {stats.get('edge_count', 'N/A')} |",
        f"| Hotspots | {stats.get('hotspot_count', 'N/A')} |",
        f"| Dead Code Findings | {stats.get('dead_code_count', 'N/A')} |",
        f"| Execution Flows | {stats.get('flow_count', 'N/A')} |",
        f"| Communities | {len(communities)} |",
        "",
        "## Communities",
        "",
        "| ID | Name | Members | Cohesion |",
        "|----|------|---------|----------|",
    ]

    for c in communities:
        cid = c["community_id"]
        name = c.get("name", f"community-{cid}")
        slug = _slugify(name)
        count = c.get("member_count", 0)
        cohesion = c.get("cohesion", 0.0)
        lines.append(
            f"| {cid} | [{name}](community-{slug}.md) | {count} | {cohesion:.3f} |"
        )

    lines.extend([
        "",
        "## Flows",
        "",
        "See [flows.md](flows.md) for execution flow details.",
        "",
    ])

    return "\n".join(lines)


def _build_community_page(community: dict) -> str:
    """Build a per-community wiki page."""
    cid = community["community_id"]
    name = community.get("name", f"community-{cid}")
    members = community.get("members", [])
    cohesion = community.get("cohesion", 0.0)
    top_files = community.get("top_files", [])

    lines = [
        f"# Community: {name}",
        "",
        f"**ID:** {cid}  ",
        f"**Members:** {len(members)}  ",
        f"**Cohesion:** {cohesion:.3f}  ",
        "",
    ]

    if top_files:
        lines.extend([
            "## Key Files",
            "",
        ])
        for f in top_files:
            lines.append(f"- `{f}`")
        lines.append("")

    if members:
        lines.extend([
            "## All Members",
            "",
        ])
        for m in sorted(members):
            lines.append(f"- `{m}`")
        lines.append("")

    lines.append("[Back to index](index.md)")
    lines.append("")

    return "\n".join(lines)


def _build_flows_page(flows: list[dict]) -> str:
    """Build the flows wiki page."""
    lines = [
        "# Execution Flows",
        "",
        f"Total flows: {len(flows)}",
        "",
        "| Flow ID | Entry Point | Nodes | Spread | Criticality | Tests |",
        "|---------|-------------|-------|--------|-------------|-------|",
    ]

    for f in flows:
        fid = f.get("flow_id", "")[:8]
        entry = f.get("entry_point", "")
        nodes = f.get("node_count", 0)
        spread = f.get("file_spread", 0)
        crit = f.get("criticality", 0.0)
        tests = "Yes" if f.get("has_test_coverage") else "No"
        lines.append(
            f"| {fid} | `{entry}` | {nodes} | {spread} | {crit:.3f} | {tests} |"
        )

    lines.append("")
    lines.append("[Back to index](index.md)")
    lines.append("")

    return "\n".join(lines)


def _slugify(name: str) -> str:
    """Convert a community name to a filename-safe slug."""
    import re
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "unknown"
