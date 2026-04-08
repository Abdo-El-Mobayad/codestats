"""Global SQLite storage for codestats.

All project data is stored at ~/.codestats/projects/<name>/graph.db.
Uses raw sqlite3 (no ORM).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CODESTATS_HOME = Path.home() / ".codestats"


def get_repo_name(repo_path: Path) -> str:
    """Determine project name from git remote or directory name."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=str(repo_path),
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            url = result.stdout.strip()
            # Extract repo name from URL
            # https://github.com/user/repo.git -> repo
            # git@github.com:user/repo.git -> repo
            name = url.rstrip("/").rsplit("/", 1)[-1]
            if name.endswith(".git"):
                name = name[:-4]
            if name:
                return name
    except Exception:
        pass

    return repo_path.name


def get_project_dir(repo_path: Path) -> Path:
    """Return the project storage directory."""
    name = get_repo_name(repo_path)
    return CODESTATS_HOME / "projects" / name


def get_db_path(repo_path: Path) -> Path:
    """Return the path to the SQLite database for the project."""
    return get_project_dir(repo_path) / "graph.db"


def get_git_root(path: Path) -> Path | None:
    """Find the git root directory from the given path."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            cwd=str(path),
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip())
    except Exception:
        pass
    return None


def init_db(db_path: Path) -> sqlite3.Connection:
    """Create the database and tables if they don't exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS graph_nodes (
    path TEXT PRIMARY KEY,
    language TEXT,
    symbol_count INTEGER,
    is_entry_point BOOLEAN,
    is_test BOOLEAN,
    pagerank REAL,
    betweenness REAL
);

CREATE TABLE IF NOT EXISTS graph_edges (
    source TEXT,
    target TEXT,
    imported_names TEXT,
    PRIMARY KEY (source, target)
);

CREATE TABLE IF NOT EXISTS git_metadata (
    file_path TEXT PRIMARY KEY,
    commit_count INTEGER,
    commit_count_90d INTEGER,
    last_commit_at TEXT,
    lines_added_90d INTEGER,
    lines_deleted_90d INTEGER,
    is_hotspot BOOLEAN,
    hotspot_score REAL,
    primary_owner TEXT,
    co_change_partners TEXT,
    bus_factor INTEGER
);

CREATE TABLE IF NOT EXISTS dead_code (
    file_path TEXT,
    kind TEXT,
    confidence REAL,
    reason TEXT,
    safe_to_delete BOOLEAN,
    importers INTEGER,
    PRIMARY KEY (file_path, kind)
);
"""


def persist_graph(conn: sqlite3.Connection, graph: Any, pageranks: dict, betweenness: dict) -> None:
    """Persist graph nodes and edges to SQLite."""
    # Clear old data
    conn.execute("DELETE FROM graph_nodes")
    conn.execute("DELETE FROM graph_edges")

    # Nodes
    node_rows = []
    for path, data in graph.nodes(data=True):
        if str(path).startswith("external:"):
            continue
        node_rows.append((
            str(path),
            data.get("language", ""),
            data.get("symbol_count", 0),
            bool(data.get("is_entry_point", False)),
            bool(data.get("is_test", False)),
            pageranks.get(path, 0.0),
            betweenness.get(path, 0.0),
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO graph_nodes VALUES (?,?,?,?,?,?,?)",
        node_rows,
    )

    # Edges (only internal)
    edge_rows = []
    for src, dst, data in graph.edges(data=True):
        if str(src).startswith("external:") or str(dst).startswith("external:"):
            continue
        edge_rows.append((
            str(src),
            str(dst),
            json.dumps(data.get("imported_names", [])),
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO graph_edges VALUES (?,?,?)",
        edge_rows,
    )

    conn.commit()
    log.info("Graph persisted: %d nodes, %d edges", len(node_rows), len(edge_rows))


def persist_git_metadata(conn: sqlite3.Connection, metadata_list: list[dict]) -> None:
    """Persist git analytics to SQLite."""
    conn.execute("DELETE FROM git_metadata")

    rows = []
    for meta in metadata_list:
        last_commit = meta.get("last_commit_at")
        if isinstance(last_commit, datetime):
            last_commit_str = last_commit.isoformat()
        else:
            last_commit_str = str(last_commit) if last_commit else None

        rows.append((
            meta["file_path"],
            meta.get("commit_count_total", 0),
            meta.get("commit_count_90d", 0),
            last_commit_str,
            meta.get("lines_added_90d", 0),
            meta.get("lines_deleted_90d", 0),
            bool(meta.get("is_hotspot", False)),
            meta.get("temporal_hotspot_score", 0.0),
            meta.get("primary_owner_name"),
            meta.get("co_change_partners_json", "[]"),
            meta.get("bus_factor", 0),
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO git_metadata VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    log.info("Git metadata persisted: %d files", len(rows))


def persist_dead_code(conn: sqlite3.Connection, findings: list) -> None:
    """Persist dead code findings to SQLite."""
    conn.execute("DELETE FROM dead_code")

    rows = []
    for f in findings:
        rows.append((
            f.file_path,
            f.kind,
            f.confidence,
            f.reason,
            bool(f.safe_to_delete),
            f.importers,
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO dead_code VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    log.info("Dead code findings persisted: %d", len(rows))


def persist_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    """Store a metadata key-value pair."""
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        (key, value),
    )
    conn.commit()


def load_meta(conn: sqlite3.Connection, key: str) -> str | None:
    """Load a metadata value by key."""
    row = conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def load_graph_nodes(conn: sqlite3.Connection) -> list[dict]:
    """Load all graph nodes."""
    cursor = conn.execute("SELECT * FROM graph_nodes")
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_graph_edges(conn: sqlite3.Connection) -> list[dict]:
    """Load all graph edges."""
    cursor = conn.execute("SELECT * FROM graph_edges")
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_git_metadata(conn: sqlite3.Connection, file_path: str | None = None) -> list[dict]:
    """Load git metadata, optionally filtered by file path."""
    if file_path:
        cursor = conn.execute("SELECT * FROM git_metadata WHERE file_path = ?", (file_path,))
    else:
        cursor = conn.execute("SELECT * FROM git_metadata")
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_dead_code(conn: sqlite3.Connection) -> list[dict]:
    """Load all dead code findings."""
    cursor = conn.execute("SELECT * FROM dead_code")
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]
