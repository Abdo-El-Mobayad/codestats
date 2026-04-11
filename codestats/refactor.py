"""Refactoring tools -- rename preview and move suggestions.

Provides read-only analysis for potential refactoring operations:
- Rename preview: shows all files/lines affected by a rename
- Move suggestions: files that might be in the wrong community
- Large function detection: functions exceeding a line count threshold
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime

import networkx as nx

log = logging.getLogger(__name__)


def preview_rename(conn, graph: nx.DiGraph, target_name: str, new_name: str) -> dict:
    """Preview a rename operation.

    1. Find the target symbol/file in the graph
    2. Find all references: IMPORTS_FROM edges, TESTED_BY edges
    3. For file renames: find all import statements referencing this file
    4. Store preview with 10-minute TTL

    Returns: {preview_id, target, new_name, edits, edit_count, expires_at}
    """
    from .storage import persist_refactor_preview

    preview_id = str(uuid.uuid4())[:8]
    edits: list[dict] = []
    now = datetime.now(UTC)

    # Normalize target name
    target_normalized = target_name.replace("\\", "/")

    # Check if target is a file path in the graph
    if target_normalized in graph.nodes:
        # File rename: find all files that import this file
        for pred in graph.predecessors(target_normalized):
            if str(pred).startswith("external:"):
                continue
            edge_data = graph.get_edge_data(pred, target_normalized)
            imported_names = []
            if edge_data:
                raw = edge_data.get("imported_names", [])
                if isinstance(raw, str):
                    try:
                        imported_names = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        imported_names = []
                else:
                    imported_names = raw

            edits.append({
                "file": pred,
                "line": None,
                "old_text": target_name,
                "new_text": new_name,
                "kind": "import_path",
                "imported_names": imported_names,
            })

        # Also check TESTED_BY edges (reverse direction)
        for succ in graph.successors(target_normalized):
            edge_data = graph.get_edge_data(target_normalized, succ)
            if edge_data and edge_data.get("edge_type") == "TESTED_BY":
                edits.append({
                    "file": succ,
                    "line": None,
                    "old_text": target_name,
                    "new_text": new_name,
                    "kind": "test_import",
                })
    else:
        # Symbol rename: search across all edges for matching imported names
        for src, dst, edata in graph.edges(data=True):
            imported = edata.get("imported_names", [])
            if isinstance(imported, str):
                try:
                    imported = json.loads(imported)
                except (json.JSONDecodeError, TypeError):
                    imported = []
            if target_name in imported:
                edits.append({
                    "file": src,
                    "line": None,
                    "old_text": target_name,
                    "new_text": new_name,
                    "kind": "symbol_reference",
                    "source_file": dst,
                })

    preview = {
        "preview_id": preview_id,
        "target_name": target_name,
        "new_name": new_name,
        "edits": edits,
        "edit_count": len(edits),
        "kind": "rename",
        "created_at": now,
    }

    # Persist preview
    try:
        persist_refactor_preview(conn, preview)
    except Exception as exc:
        log.debug("Failed to persist refactor preview: %s", exc)

    return preview


def get_move_suggestions(
    graph: nx.DiGraph,
    communities: dict[str, int],
) -> list[dict]:
    """Suggest files that might be in the wrong community.

    A file is a move candidate if ALL its callers (nodes that import it)
    belong to exactly one DIFFERENT community than the file itself.

    Returns: [{file_path, current_community, suggested_community, caller_count, reason}]
    """
    suggestions: list[dict] = []

    for node in graph.nodes():
        if str(node).startswith("external:"):
            continue

        node_community = communities.get(node)
        if node_community is None:
            continue

        # Get all callers (predecessors via IMPORTS_FROM)
        callers = []
        for pred in graph.predecessors(node):
            if str(pred).startswith("external:"):
                continue
            edge_data = graph.get_edge_data(pred, node)
            if edge_data and edge_data.get("edge_type", "IMPORTS_FROM") == "IMPORTS_FROM":
                callers.append(pred)

        if not callers:
            continue

        # Check if ALL callers are in a single different community
        caller_communities = set()
        for caller in callers:
            cc = communities.get(caller)
            if cc is not None:
                caller_communities.add(cc)

        # All callers must be in exactly one community, different from ours
        if len(caller_communities) == 1:
            suggested = caller_communities.pop()
            if suggested != node_community:
                suggestions.append({
                    "file_path": node,
                    "current_community": node_community,
                    "suggested_community": suggested,
                    "caller_count": len(callers),
                    "reason": (
                        f"All {len(callers)} caller(s) are in community {suggested}, "
                        f"but this file is in community {node_community}"
                    ),
                })

    # Sort by caller count descending
    suggestions.sort(key=lambda s: s["caller_count"], reverse=True)
    return suggestions


def find_large_functions(parsed_files: list, threshold: int = 50) -> list[dict]:
    """Find functions exceeding a line count threshold.

    Returns: [{file_path, name, kind, start_line, end_line, line_count}]
    sorted by line_count descending.
    """
    large_fns: list[dict] = []

    for pf in parsed_files:
        file_path = pf.file_info.path
        for sym in pf.symbols:
            if sym.kind not in ("function", "method"):
                continue
            line_count = sym.end_line - sym.start_line + 1
            if line_count >= threshold:
                large_fns.append({
                    "file_path": file_path,
                    "name": sym.qualified_name or sym.name,
                    "kind": sym.kind,
                    "start_line": sym.start_line,
                    "end_line": sym.end_line,
                    "line_count": line_count,
                })

    large_fns.sort(key=lambda f: f["line_count"], reverse=True)
    return large_fns
