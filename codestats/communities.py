"""Community detection, naming, and architecture coupling analysis.

Wraps the GraphBuilder.community_detection() Louvain method with metadata
computation, naming heuristics, and cross-community coupling warnings.
"""

from __future__ import annotations

import logging
from collections import Counter
from pathlib import PurePosixPath

import networkx as nx

log = logging.getLogger(__name__)


def detect_communities(graph: nx.DiGraph) -> list[dict]:
    """Run Louvain community detection and return community metadata.

    Returns list of dicts sorted by member_count descending:
    [{community_id, name, members, member_count, cohesion, top_files}]
    """
    if graph.number_of_nodes() == 0:
        return []

    try:
        raw_communities = nx.community.louvain_communities(graph.to_undirected(), seed=42)
    except Exception as exc:
        log.warning("Community detection failed: %s", exc)
        return []

    results: list[dict] = []
    for community_id, members_set in enumerate(raw_communities):
        members = sorted(
            m for m in members_set if not str(m).startswith("external:")
        )
        if not members:
            continue

        cohesion = _compute_cohesion(graph, set(members))
        name = _name_community(members, graph)

        # Top files by PageRank (or degree if no PageRank)
        try:
            pr = nx.pagerank(graph.subgraph(members))
            top_files = sorted(pr, key=pr.get, reverse=True)[:5]
        except Exception:
            # Fallback: sort by degree
            top_files = sorted(members, key=lambda m: graph.degree(m), reverse=True)[:5]

        results.append({
            "community_id": community_id,
            "name": name,
            "members": members,
            "member_count": len(members),
            "cohesion": round(cohesion, 3),
            "top_files": top_files,
        })

    # Sort by member count descending
    results.sort(key=lambda c: c["member_count"], reverse=True)
    return results


def _name_community(members: list[str], graph: nx.DiGraph) -> str:
    """Name a community using directory prefix heuristic.

    Strategy:
    1. Most common top-level directory prefix
    2. If tied, use the most common second-level directory
    3. Fallback to "community-N" style
    """
    if not members:
        return "unknown"

    # Count directory prefixes
    dir_counts: Counter[str] = Counter()
    second_level_counts: Counter[str] = Counter()

    for member in members:
        parts = PurePosixPath(member).parts
        if len(parts) >= 2:
            dir_counts[parts[0]] += 1
            second_level_counts["/".join(parts[:2])] += 1
        elif len(parts) == 1:
            dir_counts["."] += 1

    if not dir_counts:
        return "root"

    # Get most common top-level dir
    top_dir, top_count = dir_counts.most_common(1)[0]

    # If this directory accounts for >60% of members, use it
    if top_count / len(members) > 0.6:
        # Try to get more specific with second-level
        if second_level_counts:
            best_second, second_count = second_level_counts.most_common(1)[0]
            if second_count / len(members) > 0.5:
                return best_second
        return top_dir

    # Multiple directories -- join top 2
    top_two = dir_counts.most_common(2)
    return " + ".join(d for d, _ in top_two)


def _compute_cohesion(graph: nx.DiGraph, members: set[str]) -> float:
    """Compute cohesion as internal_edges / (internal_edges + external_edges).

    Returns 0.0 if no edges at all.
    """
    internal = 0
    external = 0

    for src, dst in graph.edges():
        src_in = src in members
        dst_in = dst in members

        if src_in and dst_in:
            internal += 1
        elif src_in or dst_in:
            external += 1

    total = internal + external
    if total == 0:
        return 0.0
    return internal / total


def get_architecture_coupling(
    graph: nx.DiGraph, communities: list[dict]
) -> list[dict]:
    """Find community pairs with high cross-community edge counts.

    Returns list of coupling warnings sorted by cross_edges descending:
    [{community_a, community_b, name_a, name_b, cross_edges, warning_level}]
    """
    if len(communities) < 2:
        return []

    # Build node -> community_id map
    node_to_community: dict[str, int] = {}
    community_names: dict[int, str] = {}
    for c in communities:
        cid = c["community_id"]
        community_names[cid] = c["name"]
        for member in c["members"]:
            node_to_community[member] = cid

    # Count cross-community edges
    pair_counts: Counter[tuple[int, int]] = Counter()
    for src, dst in graph.edges():
        src_cid = node_to_community.get(src)
        dst_cid = node_to_community.get(dst)
        if src_cid is not None and dst_cid is not None and src_cid != dst_cid:
            # Normalize pair ordering
            pair = (min(src_cid, dst_cid), max(src_cid, dst_cid))
            pair_counts[pair] += 1

    # Filter to meaningful coupling (>= 3 cross edges)
    warnings: list[dict] = []
    for (cid_a, cid_b), count in pair_counts.most_common():
        if count < 3:
            continue

        if count > 10:
            level = "high"
        elif count > 5:
            level = "moderate"
        else:
            level = "low"

        warnings.append({
            "community_a": cid_a,
            "community_b": cid_b,
            "name_a": community_names.get(cid_a, f"community-{cid_a}"),
            "name_b": community_names.get(cid_b, f"community-{cid_b}"),
            "cross_edges": count,
            "warning_level": level,
        })

    return warnings
