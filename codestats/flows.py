"""Execution flow tracing and criticality scoring.

Traces execution flows from entry points through the dependency graph,
scoring each flow by criticality based on file spread, external dependencies,
security sensitivity, test coverage gaps, and depth.
"""

from __future__ import annotations

import logging
import uuid
from collections import deque

import networkx as nx

log = logging.getLogger(__name__)

# Security-sensitive keywords for criticality scoring
SECURITY_KEYWORDS = frozenset({
    "auth", "login", "password", "token", "secret", "credential",
    "encrypt", "decrypt", "hash", "session", "permission", "admin",
    "api_key", "apikey", "oauth", "jwt", "csrf", "sanitize",
    "validate", "certificate", "private_key",
})


def discover_entry_points(graph: nx.DiGraph) -> list[str]:
    """Find entry points using three strategies:

    1. Nodes marked is_entry_point=True (from framework detection)
    2. True roots: nodes with in_degree==0 for IMPORTS_FROM edges
       (excluding external nodes and test files)
    3. Conventional names: main, app, index, server, handler
    """
    entry_points: set[str] = set()

    for node, data in graph.nodes(data=True):
        if str(node).startswith("external:"):
            continue

        # Strategy 1: framework-marked entry points
        if data.get("is_entry_point"):
            entry_points.add(node)
            continue

        # Strategy 2: true roots (in_degree == 0, not test, not external)
        if not data.get("is_test"):
            # Count only IMPORTS_FROM in-edges (not TESTED_BY)
            in_count = 0
            for pred in graph.predecessors(node):
                edge_data = graph.get_edge_data(pred, node)
                if edge_data and edge_data.get("edge_type", "IMPORTS_FROM") == "IMPORTS_FROM":
                    in_count += 1
            if in_count == 0:
                entry_points.add(node)

    # Strategy 3: conventional entry point names
    conventional_stems = {"main", "app", "index", "server", "cli", "handler", "__main__"}
    for node in graph.nodes():
        if str(node).startswith("external:"):
            continue
        from pathlib import PurePosixPath
        stem = PurePosixPath(str(node)).stem.lower()
        if stem in conventional_stems:
            entry_points.add(node)

    return sorted(entry_points)


def trace_flows(graph: nx.DiGraph, max_depth: int = 15) -> list[dict]:
    """Trace execution flows from each entry point via forward BFS.

    Follows IMPORTS_FROM edges (outgoing successors) from entry points.
    Discards trivial flows (< 2 nodes).

    Returns list of flow dicts:
    [{flow_id, entry_point, members, node_count, file_spread, criticality, has_test_coverage}]
    """
    entry_points = discover_entry_points(graph)
    flows: list[dict] = []

    for entry in entry_points:
        members = _bfs_forward(graph, entry, max_depth)
        if len(members) < 2:
            continue

        # Count unique files (directory spread)
        from pathlib import PurePosixPath
        dirs = {str(PurePosixPath(m).parent) for m in members}
        file_spread = len(dirs)

        # Check test coverage: any member has TESTED_BY edges?
        has_test_coverage = False
        for member in members:
            for _, _, edata in graph.out_edges(member, data=True):
                if edata.get("edge_type") == "TESTED_BY":
                    has_test_coverage = True
                    break
            if has_test_coverage:
                break

        criticality = _score_criticality(graph, members, max_depth)

        flows.append({
            "flow_id": str(uuid.uuid4())[:8],
            "entry_point": entry,
            "members": members,
            "node_count": len(members),
            "file_spread": file_spread,
            "criticality": round(criticality, 3),
            "has_test_coverage": has_test_coverage,
        })

    # Sort by criticality descending
    flows.sort(key=lambda f: f["criticality"], reverse=True)
    return flows


def _bfs_forward(graph: nx.DiGraph, start: str, max_depth: int) -> list[str]:
    """BFS forward through IMPORTS_FROM edges (successors), returning visited nodes in order."""
    visited: list[str] = [start]
    visited_set: set[str] = {start}
    queue: deque[tuple[str, int]] = deque([(start, 0)])

    while queue:
        current, depth = queue.popleft()
        if depth >= max_depth:
            continue

        for succ in graph.successors(current):
            if succ in visited_set:
                continue
            if str(succ).startswith("external:"):
                continue
            # Only follow IMPORTS_FROM edges
            edge_data = graph.get_edge_data(current, succ)
            if edge_data and edge_data.get("edge_type", "IMPORTS_FROM") != "IMPORTS_FROM":
                continue

            visited.append(succ)
            visited_set.add(succ)
            queue.append((succ, depth + 1))

    return visited


def _score_criticality(graph: nx.DiGraph, flow_members: list[str], max_depth: int) -> float:
    """Score flow criticality 0.0-1.0 using five weighted factors:

    | Factor              | Weight | Normalization              |
    |---------------------|--------|----------------------------|
    | File spread         | 0.30   | 0 at 1 file, 1.0 at 5+    |
    | External calls      | 0.20   | 0 at 0 external, 1.0 at 5+|
    | Security sensitivity| 0.25   | keyword hits / node count  |
    | Test coverage gap   | 0.15   | 1.0 - (tested/total)      |
    | BFS depth           | 0.10   | depth/10, capped at 1.0    |
    """
    if not flow_members:
        return 0.0

    # File spread factor
    from pathlib import PurePosixPath
    dirs = {str(PurePosixPath(m).parent) for m in flow_members}
    spread_score = min((len(dirs) - 1) / 4.0, 1.0) if len(dirs) > 1 else 0.0

    # External calls factor
    external_count = 0
    for member in flow_members:
        for succ in graph.successors(member):
            if str(succ).startswith("external:"):
                external_count += 1
    external_score = min(external_count / 5.0, 1.0)

    # Security sensitivity factor
    security_hits = sum(1 for m in flow_members if _has_security_keyword(m))
    security_score = min(security_hits / max(len(flow_members), 1), 1.0)

    # Test coverage gap factor
    tested_count = 0
    non_test_count = 0
    for member in flow_members:
        node_data = graph.nodes.get(member, {})
        if node_data.get("is_test"):
            continue
        non_test_count += 1
        for _, _, edata in graph.out_edges(member, data=True):
            if edata.get("edge_type") == "TESTED_BY":
                tested_count += 1
                break
    test_gap = 1.0 - (tested_count / max(non_test_count, 1))

    # Depth factor
    depth_score = min(len(flow_members) / 10.0, 1.0)

    # Weighted sum
    score = (
        0.30 * spread_score
        + 0.20 * external_score
        + 0.25 * security_score
        + 0.15 * test_gap
        + 0.10 * depth_score
    )

    return min(score, 1.0)


def _has_security_keyword(name: str) -> bool:
    """Check if any security keyword appears in the node name."""
    name_lower = name.lower()
    return any(kw in name_lower for kw in SECURITY_KEYWORDS)
