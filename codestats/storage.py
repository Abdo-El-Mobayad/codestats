"""Global SQLite storage for codestats.

All project data is stored at ~/.codestats/projects/<name>/graph.db.
Uses raw sqlite3 (no ORM).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import subprocess
from datetime import datetime, timedelta
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

    # Create FTS5 virtual table (separate from main schema)
    try:
        conn.executescript(_FTS_SCHEMA)
    except Exception as exc:
        log.debug("FTS5 table creation: %s", exc)

    # Migrate dead_code table if missing new columns
    try:
        cursor = conn.execute("PRAGMA table_info(dead_code)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        for col, col_type in [
            ("last_commit_at", "TEXT"),
            ("commit_count_90d", "INTEGER"),
            ("primary_owner", "TEXT"),
            ("age_days", "INTEGER"),
        ]:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE dead_code ADD COLUMN {col} {col_type}")
        conn.commit()
    except Exception as exc:
        log.debug("Dead code migration check: %s", exc)

    # Run v2 schema migration if needed
    schema_version = load_meta(conn, "schema_version")
    if schema_version != "3":
        _migrate_v2(conn)

    return conn


def _migrate_v2(conn: sqlite3.Connection) -> None:
    """Migrate schema to v2: add new columns and tables for v0.4 features."""
    log.info("Running schema migration to v2...")

    # Add new columns to graph_nodes
    try:
        cursor = conn.execute("PRAGMA table_info(graph_nodes)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        for col, col_def in [
            ("content_hash", "TEXT DEFAULT ''"),
            ("indexed_file_at", "TEXT"),
            ("community_id", "INTEGER"),
        ]:
            if col not in existing_cols:
                conn.execute(f"ALTER TABLE graph_nodes ADD COLUMN {col} {col_def}")
    except Exception as exc:
        log.debug("graph_nodes migration: %s", exc)

    # Add new column to graph_edges
    try:
        cursor = conn.execute("PRAGMA table_info(graph_edges)")
        existing_cols = {row[1] for row in cursor.fetchall()}
        if "edge_type" not in existing_cols:
            conn.execute("ALTER TABLE graph_edges ADD COLUMN edge_type TEXT DEFAULT 'IMPORTS_FROM'")
    except Exception as exc:
        log.debug("graph_edges migration: %s", exc)

    # Create new tables (executescript handles IF NOT EXISTS)
    new_tables_sql = """
    CREATE TABLE IF NOT EXISTS flows (
        flow_id TEXT PRIMARY KEY,
        entry_point TEXT NOT NULL,
        node_count INTEGER DEFAULT 0,
        file_spread INTEGER DEFAULT 0,
        criticality REAL DEFAULT 0.0,
        has_test_coverage INTEGER DEFAULT 0
    );

    CREATE TABLE IF NOT EXISTS flow_members (
        flow_id TEXT NOT NULL,
        node_qualified_name TEXT NOT NULL,
        depth INTEGER DEFAULT 0,
        PRIMARY KEY (flow_id, node_qualified_name),
        FOREIGN KEY (flow_id) REFERENCES flows(flow_id)
    );

    CREATE TABLE IF NOT EXISTS communities (
        community_id INTEGER PRIMARY KEY,
        name TEXT,
        member_count INTEGER DEFAULT 0,
        cohesion REAL DEFAULT 0.0,
        top_files TEXT
    );

    CREATE TABLE IF NOT EXISTS community_members (
        file_path TEXT NOT NULL,
        community_id INTEGER NOT NULL,
        PRIMARY KEY (file_path, community_id),
        FOREIGN KEY (community_id) REFERENCES communities(community_id)
    );

    CREATE TABLE IF NOT EXISTS refactor_previews (
        preview_id TEXT PRIMARY KEY,
        created_at TEXT NOT NULL,
        target_name TEXT NOT NULL,
        new_name TEXT,
        edits TEXT,
        kind TEXT
    );
    """
    conn.executescript(new_tables_sql)

    # Recreate FTS5 table (drop contentless version if it exists)
    try:
        conn.execute("DROP TABLE IF EXISTS symbols_fts")
    except Exception:
        pass
    try:
        conn.executescript(_FTS_SCHEMA)
    except Exception as exc:
        log.debug("FTS5 migration: %s", exc)

    # Set schema version
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", "3"),
    )
    conn.commit()
    log.info("Schema migration to v2 complete.")


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
    betweenness REAL,
    content_hash TEXT DEFAULT '',
    indexed_file_at TEXT,
    community_id INTEGER
);

CREATE TABLE IF NOT EXISTS graph_edges (
    source TEXT,
    target TEXT,
    imported_names TEXT,
    edge_type TEXT DEFAULT 'IMPORTS_FROM',
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
    last_commit_at TEXT,
    commit_count_90d INTEGER,
    primary_owner TEXT,
    age_days INTEGER,
    PRIMARY KEY (file_path, kind)
);

CREATE TABLE IF NOT EXISTS flows (
    flow_id TEXT PRIMARY KEY,
    entry_point TEXT NOT NULL,
    node_count INTEGER DEFAULT 0,
    file_spread INTEGER DEFAULT 0,
    criticality REAL DEFAULT 0.0,
    has_test_coverage INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS flow_members (
    flow_id TEXT NOT NULL,
    node_qualified_name TEXT NOT NULL,
    depth INTEGER DEFAULT 0,
    PRIMARY KEY (flow_id, node_qualified_name),
    FOREIGN KEY (flow_id) REFERENCES flows(flow_id)
);

CREATE TABLE IF NOT EXISTS communities (
    community_id INTEGER PRIMARY KEY,
    name TEXT,
    member_count INTEGER DEFAULT 0,
    cohesion REAL DEFAULT 0.0,
    top_files TEXT
);

CREATE TABLE IF NOT EXISTS community_members (
    file_path TEXT NOT NULL,
    community_id INTEGER NOT NULL,
    PRIMARY KEY (file_path, community_id),
    FOREIGN KEY (community_id) REFERENCES communities(community_id)
);

CREATE TABLE IF NOT EXISTS refactor_previews (
    preview_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    target_name TEXT NOT NULL,
    new_name TEXT,
    edits TEXT,
    kind TEXT
);
"""

# FTS5 virtual table must be created separately (can't use IF NOT EXISTS
# in executescript for virtual tables in all SQLite versions reliably).
_FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
    qualified_name,
    name,
    kind,
    file_path,
    signature,
    tokenize='porter'
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
            data.get("content_hash", ""),
            data.get("indexed_file_at"),
            data.get("community_id"),
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO graph_nodes (path, language, symbol_count, is_entry_point, "
        "is_test, pagerank, betweenness, content_hash, indexed_file_at, community_id) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
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
            data.get("edge_type", "IMPORTS_FROM"),
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO graph_edges (source, target, imported_names, edge_type) "
        "VALUES (?,?,?,?)",
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
        last_commit = f.last_commit_at
        if isinstance(last_commit, datetime):
            last_commit_str = last_commit.isoformat()
        else:
            last_commit_str = str(last_commit) if last_commit else None

        rows.append((
            f.file_path,
            f.kind,
            f.confidence,
            f.reason,
            bool(f.safe_to_delete),
            f.importers,
            last_commit_str,
            f.commit_count_90d,
            f.primary_owner,
            f.age_days,
        ))

    conn.executemany(
        "INSERT OR REPLACE INTO dead_code VALUES (?,?,?,?,?,?,?,?,?,?)",
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


# ---------------------------------------------------------------------------
# Flow persistence
# ---------------------------------------------------------------------------


def persist_flows(conn: sqlite3.Connection, flows: list[dict]) -> None:
    """Persist execution flows to SQLite."""
    conn.execute("DELETE FROM flows")
    rows = []
    for f in flows:
        rows.append((
            f["flow_id"],
            f["entry_point"],
            f.get("node_count", 0),
            f.get("file_spread", 0),
            f.get("criticality", 0.0),
            int(f.get("has_test_coverage", False)),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO flows (flow_id, entry_point, node_count, "
        "file_spread, criticality, has_test_coverage) VALUES (?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    log.info("Flows persisted: %d", len(rows))


def persist_flow_members(conn: sqlite3.Connection, flow_id: str, members: list[dict]) -> None:
    """Persist flow membership records."""
    conn.execute("DELETE FROM flow_members WHERE flow_id = ?", (flow_id,))
    rows = []
    for m in members:
        rows.append((
            flow_id,
            m["node_qualified_name"],
            m.get("depth", 0),
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO flow_members (flow_id, node_qualified_name, depth) "
        "VALUES (?,?,?)",
        rows,
    )
    conn.commit()


def load_flows(conn: sqlite3.Connection) -> list[dict]:
    """Load all execution flows."""
    cursor = conn.execute("SELECT * FROM flows")
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def load_flow_members(conn: sqlite3.Connection, flow_id: str) -> list[dict]:
    """Load members for a specific flow."""
    cursor = conn.execute(
        "SELECT * FROM flow_members WHERE flow_id = ? ORDER BY depth",
        (flow_id,),
    )
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# Community persistence
# ---------------------------------------------------------------------------


def persist_communities(conn: sqlite3.Connection, communities: list[dict]) -> None:
    """Persist community clusters to SQLite."""
    conn.execute("DELETE FROM communities")
    rows = []
    for c in communities:
        top_files = c.get("top_files", [])
        if isinstance(top_files, list):
            top_files = json.dumps(top_files)
        rows.append((
            c["community_id"],
            c.get("name", ""),
            c.get("member_count", 0),
            c.get("cohesion", 0.0),
            top_files,
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO communities (community_id, name, member_count, "
        "cohesion, top_files) VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    log.info("Communities persisted: %d", len(rows))


def persist_community_members(conn: sqlite3.Connection, members: list[dict]) -> None:
    """Persist community membership records."""
    conn.execute("DELETE FROM community_members")
    rows = []
    for m in members:
        rows.append((
            m["file_path"],
            m["community_id"],
        ))
    conn.executemany(
        "INSERT OR REPLACE INTO community_members (file_path, community_id) "
        "VALUES (?,?)",
        rows,
    )
    conn.commit()
    log.info("Community members persisted: %d", len(rows))


def load_communities(conn: sqlite3.Connection) -> list[dict]:
    """Load all communities."""
    cursor = conn.execute("SELECT * FROM communities")
    columns = [desc[0] for desc in cursor.description]
    results = []
    for row in cursor.fetchall():
        d = dict(zip(columns, row))
        # Parse top_files JSON
        if d.get("top_files"):
            try:
                d["top_files"] = json.loads(d["top_files"])
            except (json.JSONDecodeError, TypeError):
                d["top_files"] = []
        else:
            d["top_files"] = []
        results.append(d)
    return results


def load_community_members(conn: sqlite3.Connection, community_id: int) -> list[dict]:
    """Load members for a specific community."""
    cursor = conn.execute(
        "SELECT * FROM community_members WHERE community_id = ?",
        (community_id,),
    )
    columns = [desc[0] for desc in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ---------------------------------------------------------------------------
# FTS5 full-text search
# ---------------------------------------------------------------------------


def persist_fts_index(conn: sqlite3.Connection, symbols: list[dict]) -> None:
    """Populate the FTS5 symbols search index."""
    # Clear existing index
    conn.execute("DELETE FROM symbols_fts")
    rows = []
    for s in symbols:
        rows.append((
            s.get("qualified_name", ""),
            s.get("name", ""),
            s.get("kind", ""),
            s.get("file_path", ""),
            s.get("signature", ""),
        ))
    conn.executemany(
        "INSERT INTO symbols_fts (qualified_name, name, kind, file_path, signature) "
        "VALUES (?,?,?,?,?)",
        rows,
    )
    conn.commit()
    log.info("FTS index populated: %d symbols", len(rows))


def search_fts(conn: sqlite3.Connection, query: str, limit: int = 20) -> list[dict]:
    """Search the FTS5 symbols index. Returns matching rows."""
    # Sanitize the query for FTS5 syntax
    safe_query = query.replace('"', '""')
    try:
        cursor = conn.execute(
            "SELECT qualified_name, name, kind, file_path, signature, "
            "rank FROM symbols_fts WHERE symbols_fts MATCH ? "
            "ORDER BY rank LIMIT ?",
            (safe_query, limit),
        )
        columns = ["qualified_name", "name", "kind", "file_path", "signature", "rank"]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]
    except Exception as exc:
        log.debug("FTS search failed for query '%s': %s", query, exc)
        return []


# ---------------------------------------------------------------------------
# Refactor preview persistence
# ---------------------------------------------------------------------------


def persist_refactor_preview(conn: sqlite3.Connection, preview: dict) -> None:
    """Persist a refactor preview."""
    edits = preview.get("edits", [])
    if isinstance(edits, list):
        edits = json.dumps(edits)
    created_at = preview.get("created_at", "")
    if isinstance(created_at, datetime):
        created_at = created_at.isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO refactor_previews "
        "(preview_id, created_at, target_name, new_name, edits, kind) "
        "VALUES (?,?,?,?,?,?)",
        (
            preview["preview_id"],
            created_at,
            preview["target_name"],
            preview.get("new_name"),
            edits,
            preview.get("kind", "rename"),
        ),
    )
    conn.commit()


def load_refactor_preview(conn: sqlite3.Connection, preview_id: str) -> dict | None:
    """Load a refactor preview by ID."""
    cursor = conn.execute(
        "SELECT * FROM refactor_previews WHERE preview_id = ?",
        (preview_id,),
    )
    columns = [desc[0] for desc in cursor.description]
    row = cursor.fetchone()
    if row is None:
        return None
    d = dict(zip(columns, row))
    # Parse edits JSON
    if d.get("edits"):
        try:
            d["edits"] = json.loads(d["edits"])
        except (json.JSONDecodeError, TypeError):
            d["edits"] = []
    else:
        d["edits"] = []
    return d


def delete_stale_previews(conn: sqlite3.Connection, max_age_minutes: int = 10) -> int:
    """Delete refactor previews older than max_age_minutes. Returns count deleted."""
    cutoff = (datetime.utcnow() - timedelta(minutes=max_age_minutes)).isoformat()
    cursor = conn.execute(
        "DELETE FROM refactor_previews WHERE created_at < ?",
        (cutoff,),
    )
    count = cursor.rowcount
    conn.commit()
    if count > 0:
        log.info("Deleted %d stale refactor previews", count)
    return count


# ---------------------------------------------------------------------------
# Incremental indexing helpers
# ---------------------------------------------------------------------------


def delete_nodes_and_edges(conn: sqlite3.Connection, paths: list[str]) -> None:
    """Remove graph nodes and their edges for the given file paths."""
    if not paths:
        return
    placeholders = ",".join("?" for _ in paths)
    conn.execute(f"DELETE FROM graph_nodes WHERE path IN ({placeholders})", paths)
    conn.execute(
        f"DELETE FROM graph_edges WHERE source IN ({placeholders}) OR target IN ({placeholders})",
        paths + paths,
    )
    conn.commit()
    log.info("Deleted nodes/edges for %d paths", len(paths))


def load_file_hashes(conn: sqlite3.Connection) -> dict[str, str]:
    """Load content hashes for all indexed files. Returns {path: hash}."""
    cursor = conn.execute("SELECT path, content_hash FROM graph_nodes WHERE content_hash != ''")
    return {row[0]: row[1] for row in cursor.fetchall()}


def persist_file_hash(conn: sqlite3.Connection, path: str, content_hash: str) -> None:
    """Update the content hash for a specific file node."""
    conn.execute(
        "UPDATE graph_nodes SET content_hash = ? WHERE path = ?",
        (content_hash, path),
    )
    conn.commit()
