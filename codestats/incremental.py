"""Incremental indexing -- detect changed files, skip unchanged ones.

Compares current file hashes against stored hashes in the database to
determine which files need re-processing. Uses git diff as a fast
pre-filter when available, then falls back to full hash comparison.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from .models import FileInfo, compute_content_hash

if TYPE_CHECKING:
    import sqlite3

log = logging.getLogger(__name__)


@dataclass
class ChangeSet:
    """Files that need re-processing."""

    added: list[str] = field(default_factory=list)  # new files not in previous index
    modified: list[str] = field(default_factory=list)  # files with changed content hash
    deleted: list[str] = field(default_factory=list)  # files in previous index but gone now
    dependents: list[str] = field(default_factory=list)  # files that import a changed file (2-hop)

    @property
    def all_changed(self) -> list[str]:
        """Files that are new or modified (excludes dependents)."""
        return self.added + self.modified

    @property
    def all_reprocess(self) -> list[str]:
        """Files that need re-parsing (changed + their dependents)."""
        return list(set(self.added + self.modified + self.dependents))

    @property
    def is_empty(self) -> bool:
        """True when nothing changed at all."""
        return not self.added and not self.modified and not self.deleted


def detect_changes(
    repo_path: Path,
    db_conn: sqlite3.Connection,
    current_files: list[FileInfo],
) -> ChangeSet | None:
    """Compare current files against stored hashes to find what changed.

    Returns None if no previous index exists (signals full build needed).
    Returns a ChangeSet describing added/modified/deleted/dependent files.
    """
    stored_hashes = _get_stored_hashes(db_conn)

    # No previous index at all
    if not stored_hashes:
        return None

    current_hashes = _compute_current_hashes(current_files)

    # Use git diff as a fast pre-filter when available
    git_changed = _git_changed_files(repo_path)

    stored_paths = set(stored_hashes.keys())
    current_paths = set(current_hashes.keys())

    added: list[str] = []
    modified: list[str] = []
    deleted: list[str] = sorted(stored_paths - current_paths)

    for path in sorted(current_paths):
        if path not in stored_paths:
            added.append(path)
        else:
            # If git tells us this file changed, or if hashes differ
            needs_check = git_changed is None or path in git_changed
            if needs_check and current_hashes[path] != stored_hashes[path]:
                modified.append(path)

    # Find dependents of changed files (up to 2 hops)
    changed_files = added + modified + deleted
    dependents: list[str] = []
    if changed_files:
        dependents = _find_dependents(db_conn, changed_files, max_hops=2)
        # Remove files that are already in the changed set or deleted
        already = set(changed_files)
        dependents = [d for d in dependents if d in current_paths and d not in already]

    return ChangeSet(
        added=added,
        modified=modified,
        deleted=deleted,
        dependents=dependents,
    )


def _get_stored_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """Load {file_path: content_hash} from graph_nodes."""
    cursor = conn.execute(
        "SELECT path, content_hash FROM graph_nodes WHERE content_hash IS NOT NULL AND content_hash != ''"
    )
    return {row[0]: row[1] for row in cursor.fetchall()}


def _compute_current_hashes(files: list[FileInfo]) -> dict[str, str]:
    """Compute SHA-256 for each file. Returns {relative_path: hash}."""
    result: dict[str, str] = {}
    for file_info in files:
        try:
            source = Path(file_info.abs_path).read_bytes()
            result[file_info.path] = compute_content_hash(source)
        except OSError:
            log.debug("Failed to read file for hashing: %s", file_info.abs_path)
    return result


def _find_dependents(
    conn: sqlite3.Connection,
    changed_files: list[str],
    max_hops: int = 2,
) -> list[str]:
    """Find files that import any of the changed files, up to max_hops via graph_edges.

    Uses BFS over reverse edges (target -> source means "source imports target").
    We want files that depend on changed files, so we follow edges where
    target is a changed file to find the sources (importers).
    """
    # Load all edges
    cursor = conn.execute("SELECT source, target FROM graph_edges")
    # Build reverse adjacency: target -> list of sources (who imports target)
    reverse_adj: dict[str, list[str]] = {}
    for src, tgt in cursor.fetchall():
        reverse_adj.setdefault(tgt, []).append(src)

    visited: set[str] = set()
    frontier = set(changed_files)

    for _hop in range(max_hops):
        next_frontier: set[str] = set()
        for node in frontier:
            for importer in reverse_adj.get(node, []):
                if importer not in visited and importer not in set(changed_files):
                    visited.add(importer)
                    next_frontier.add(importer)
        frontier = next_frontier

    return sorted(visited)


def _git_changed_files(repo_path: Path) -> set[str] | None:
    """Use git diff to quickly identify changed file candidates.

    Strategy chain:
    1. git diff --name-only HEAD  (uncommitted changes vs HEAD)
    2. git diff --name-only --cached  (staged only)
    3. Return None to fall back to hash comparison
    """
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
            if result.returncode == 0:
                files = {
                    f.strip().replace("\\", "/")
                    for f in result.stdout.splitlines()
                    if f.strip()
                }
                return files
        except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
            continue

    return None
