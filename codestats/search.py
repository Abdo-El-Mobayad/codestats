"""FTS5 full-text search across code symbols.

Builds a search index from parsed file symbols and provides fast
full-text search with porter stemming and case-aware boosting.
"""

from __future__ import annotations

import logging
import sqlite3

from .storage import search_fts

log = logging.getLogger(__name__)


def build_search_index(conn: sqlite3.Connection, parsed_files: list) -> int:
    """Populate the FTS5 symbols_fts table from parsed file data.

    Handles the contentless FTS5 table by dropping and recreating it,
    since contentless tables do not support DELETE operations.

    Indexes: qualified_name, name, kind, file_path, signature.
    Returns number of symbols indexed.
    """
    symbols: list[dict] = []

    for pf in parsed_files:
        file_path = pf.file_info.path
        for sym in pf.symbols:
            symbols.append({
                "qualified_name": sym.qualified_name or sym.id,
                "name": sym.name,
                "kind": sym.kind,
                "file_path": file_path,
                "signature": sym.signature or "",
            })

    if symbols:
        _rebuild_fts_table(conn, symbols)

    log.info("Search index built: %d symbols", len(symbols))
    return len(symbols)


def _rebuild_fts_table(conn: sqlite3.Connection, symbols: list[dict]) -> None:
    """Drop and recreate the FTS5 contentless table, then populate it.

    Contentless FTS5 tables (content='') do not support DELETE. The only
    way to "clear" them is to drop and recreate the virtual table.
    """
    try:
        conn.execute("DROP TABLE IF EXISTS symbols_fts")
    except Exception:
        pass

    conn.execute("""
        CREATE VIRTUAL TABLE IF NOT EXISTS symbols_fts USING fts5(
            qualified_name,
            name,
            kind,
            file_path,
            signature,
            content='',
            tokenize='porter'
        )
    """)

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
    log.info("FTS index rebuilt: %d symbols", len(rows))


def search(
    conn: sqlite3.Connection,
    query: str,
    limit: int = 20,
    kind_filter: str | None = None,
) -> list[dict]:
    """Search symbols using FTS5 with porter stemming.

    Returns [{qualified_name, name, kind, file_path, signature, rank}]
    sorted by relevance (BM25 rank).

    Applies post-search boosting:
    - PascalCase query -> 1.5x boost for class/interface nodes
    - snake_case query -> 1.5x boost for function nodes
    """
    results = search_fts(conn, query, limit=limit * 3 if kind_filter else limit)

    # Apply kind filter if specified
    if kind_filter:
        results = [r for r in results if r.get("kind") == kind_filter]

    # Apply case-aware boosting
    if _is_pascal_case(query):
        for r in results:
            if r.get("kind") in ("class", "interface", "struct", "enum"):
                r["rank"] = r.get("rank", 0) * 1.5
    elif _is_snake_case(query):
        for r in results:
            if r.get("kind") in ("function", "method"):
                r["rank"] = r.get("rank", 0) * 1.5

    # Re-sort by rank (FTS5 rank is negative; more negative = better match)
    results.sort(key=lambda r: r.get("rank", 0))

    return results[:limit]


def _is_pascal_case(s: str) -> bool:
    """Check if string is PascalCase."""
    return bool(s) and s[0].isupper() and "_" not in s


def _is_snake_case(s: str) -> bool:
    """Check if string is snake_case."""
    return "_" in s and s == s.lower()
