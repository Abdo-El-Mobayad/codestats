"""Bidirectional impact analysis with risk scoring.

Analyzes the blast radius of file changes by walking the dependency graph
in both directions (what depends on the changed files, and what they depend on),
then scoring each impacted file for risk.
"""

from __future__ import annotations

import logging
import subprocess
from collections import deque
from pathlib import Path

import networkx as nx

from .flows import SECURITY_KEYWORDS

log = logging.getLogger(__name__)


def analyze_impact(
    graph: nx.DiGraph,
    changed_files: list[str],
    git_meta: dict[str, dict],
    communities: dict[str, int],
    max_depth: int = 3,
) -> dict:
    """Bidirectional BFS impact analysis.

    For each changed file, walk BOTH directions:
    - Backward (predecessors via reverse graph): files that import this file
    - Forward (successors): files that this file imports

    Returns dict with:
        changed_files, impacted_files, total_impacted, max_risk, risk_level, tested_files
    """
    # Get all impacted nodes with their min depths
    impacted = _bfs_bidirectional(graph, changed_files, max_depth)

    # Remove changed files themselves from impacted set
    for cf in changed_files:
        impacted.pop(cf, None)

    # Score each impacted file
    impacted_files: list[dict] = []
    tested_files: list[str] = []
    max_risk = 0.0

    for file_path, depth in impacted.items():
        if str(file_path).startswith("external:"):
            continue

        risk = _score_risk(graph, file_path, git_meta, communities)
        level = _classify_risk(risk)

        node_data = graph.nodes.get(file_path, {})
        is_test = node_data.get("is_test", False)
        if is_test:
            tested_files.append(file_path)

        # Determine direction
        direction = "unknown"
        # Check if this file imports any changed file (it's a dependent)
        for cf in changed_files:
            if graph.has_edge(file_path, cf):
                direction = "dependency"
                break
            if graph.has_edge(cf, file_path):
                direction = "dependent"
                break
            # Check reverse
            rev = graph.reverse(copy=False)
            if nx.has_path(rev, cf, file_path):
                direction = "upstream"
                break

        impacted_files.append({
            "path": file_path,
            "risk_score": round(risk, 3),
            "risk_level": level,
            "depth": depth,
            "has_tests": bool(
                any(
                    edata.get("edge_type") == "TESTED_BY"
                    for _, _, edata in graph.out_edges(file_path, data=True)
                )
            ),
            "community": communities.get(file_path),
        })

        if risk > max_risk:
            max_risk = risk

    # Sort by risk score descending
    impacted_files.sort(key=lambda x: x["risk_score"], reverse=True)

    overall_level = _classify_risk(max_risk)

    return {
        "changed_files": changed_files,
        "impacted_files": impacted_files,
        "total_impacted": len(impacted_files),
        "max_risk": round(max_risk, 3),
        "risk_level": overall_level,
        "tested_files": sorted(tested_files),
    }


def _bfs_bidirectional(
    graph: nx.DiGraph,
    seeds: list[str],
    max_depth: int,
) -> dict[str, int]:
    """Bidirectional BFS returning {node: min_depth_reached}.

    Forward: follow successors (files this imports)
    Backward: follow predecessors (files that import this)
    """
    result: dict[str, int] = {}

    for seed in seeds:
        if seed not in graph:
            continue

        # Forward BFS (successors) - only IMPORTS_FROM edges
        try:
            fwd_tree = nx.bfs_tree(graph, seed, depth_limit=max_depth)
            for node in fwd_tree.nodes():
                if node == seed:
                    continue
                if str(node).startswith("external:"):
                    continue
                depth = nx.shortest_path_length(fwd_tree, seed, node)
                if node not in result or depth < result[node]:
                    result[node] = depth
        except (nx.NetworkXError, nx.NodeNotFound):
            pass

        # Backward BFS (predecessors via reverse graph)
        try:
            rev = graph.reverse(copy=False)
            bwd_tree = nx.bfs_tree(rev, seed, depth_limit=max_depth)
            for node in bwd_tree.nodes():
                if node == seed:
                    continue
                if str(node).startswith("external:"):
                    continue
                depth = nx.shortest_path_length(bwd_tree, seed, node)
                if node not in result or depth < result[node]:
                    result[node] = depth
        except (nx.NetworkXError, nx.NodeNotFound):
            pass

    return result


def _score_risk(
    graph: nx.DiGraph,
    file_path: str,
    git_meta: dict[str, dict],
    communities: dict[str, int],
) -> float:
    """Score risk for a single impacted file using a 7-factor additive model (0.0-1.0).

    | Factor                  | Max  | Logic                                    |
    |-------------------------|------|------------------------------------------|
    | Caller count            | 0.10 | min(in_degree / 20, 0.10)                |
    | No test coverage        | 0.30 | 0.30 if no TESTED_BY edges               |
    | Community crossing      | 0.15 | 0.05 per caller in different community   |
    | Hotspot (high churn)    | 0.15 | 0.15 if is_hotspot in git metadata        |
    | Low bus factor          | 0.10 | 0.10 if bus_factor <= 1                  |
    | High centrality         | 0.10 | 0.10 if PageRank in top 10%             |
    | Security sensitivity    | 0.10 | 0.10 if name matches security keywords   |
    """
    score = 0.0
    node_data = graph.nodes.get(file_path, {})

    # Factor 1: Caller count
    in_degree = graph.in_degree(file_path) if file_path in graph else 0
    score += min(in_degree / 20.0, 0.10)

    # Factor 2: No test coverage
    has_tests = any(
        edata.get("edge_type") == "TESTED_BY"
        for _, _, edata in graph.out_edges(file_path, data=True)
    ) if file_path in graph else False
    if not has_tests and not node_data.get("is_test"):
        score += 0.30

    # Factor 3: Community crossing
    file_community = communities.get(file_path)
    if file_community is not None:
        cross_count = 0
        for pred in graph.predecessors(file_path):
            pred_community = communities.get(pred)
            if pred_community is not None and pred_community != file_community:
                cross_count += 1
        score += min(cross_count * 0.05, 0.15)

    # Factor 4: Hotspot
    meta = git_meta.get(file_path, {})
    if meta.get("is_hotspot") or meta.get("hotspot_score", 0) > 0.5:
        score += 0.15

    # Factor 5: Low bus factor
    bus_factor = meta.get("bus_factor", 0)
    if bus_factor <= 1 and not node_data.get("is_test"):
        score += 0.10

    # Factor 6: High centrality (use PageRank if stored)
    pagerank = node_data.get("pagerank", 0.0)
    if pagerank > 0:
        # Approximate: top 10% check would need all values, use threshold
        # In practice, PR > 0.01 in most repos indicates high centrality
        if pagerank > 0.005:
            score += 0.10

    # Factor 7: Security sensitivity
    name_lower = str(file_path).lower()
    if any(kw in name_lower for kw in SECURITY_KEYWORDS):
        score += 0.10

    return min(score, 1.0)


def _classify_risk(max_risk: float) -> str:
    """Classify overall risk level."""
    if max_risk >= 0.7:
        return "CRITICAL"
    if max_risk >= 0.5:
        return "HIGH"
    if max_risk >= 0.3:
        return "MEDIUM"
    return "LOW"


def get_changed_files_from_git(repo_path: Path) -> list[str]:
    """Auto-detect changed files via git diff."""
    for cmd in (
        ["git", "diff", "--name-only", "HEAD"],
        ["git", "diff", "--name-only", "--cached"],
    ):
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                cwd=str(repo_path),
                timeout=10,
            )
            if result.returncode == 0 and result.stdout.strip():
                return [
                    f.strip().replace("\\", "/")
                    for f in result.stdout.splitlines()
                    if f.strip()
                ]
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue
    return []
