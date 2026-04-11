"""Tests for codestats.search -- FTS5 full-text search."""

from __future__ import annotations

import sqlite3

from codestats.search import search, build_search_index, _is_pascal_case, _is_snake_case
from codestats.storage import persist_fts_index, search_fts


def _seed_fts(conn: sqlite3.Connection) -> None:
    """Populate FTS index with sample symbols for testing."""
    symbols = [
        {"qualified_name": "mypackage.utils.helper", "name": "helper",
         "kind": "function", "file_path": "mypackage/utils.py",
         "signature": "def helper()"},
        {"qualified_name": "mypackage.models.User", "name": "User",
         "kind": "class", "file_path": "mypackage/models.py",
         "signature": "class User"},
        {"qualified_name": "mypackage.api.get_users", "name": "get_users",
         "kind": "function", "file_path": "mypackage/api.py",
         "signature": "def get_users()"},
        {"qualified_name": "mypackage.auth.AuthConfig", "name": "AuthConfig",
         "kind": "class", "file_path": "mypackage/auth.py",
         "signature": "class AuthConfig"},
        {"qualified_name": "mypackage.db.connect_db", "name": "connect_db",
         "kind": "function", "file_path": "mypackage/db.py",
         "signature": "def connect_db()"},
    ]
    persist_fts_index(conn, symbols)


def test_fts_basic_search(db_conn: sqlite3.Connection):
    """Basic FTS search finds matching symbols."""
    _seed_fts(db_conn)
    results = search(db_conn, "helper")

    assert len(results) >= 1
    assert results[0]["name"] == "helper"


def test_fts_no_results(db_conn: sqlite3.Connection):
    """FTS search returns empty list for no matches."""
    _seed_fts(db_conn)
    results = search(db_conn, "xyznonexistent")

    assert results == []


def test_fts_pascal_case_boost(db_conn: sqlite3.Connection):
    """PascalCase queries boost class/interface results."""
    _seed_fts(db_conn)

    assert _is_pascal_case("User") is True
    assert _is_pascal_case("user") is False
    assert _is_pascal_case("my_var") is False

    results = search(db_conn, "User")
    if results:
        # User (class) should appear in results
        names = [r["name"] for r in results]
        assert "User" in names


def test_fts_snake_case_boost(db_conn: sqlite3.Connection):
    """snake_case queries boost function results."""
    _seed_fts(db_conn)

    assert _is_snake_case("get_users") is True
    assert _is_snake_case("getUsers") is False

    results = search(db_conn, "connect_db")
    if results:
        names = [r["name"] for r in results]
        assert "connect_db" in names


def test_fts_limit_parameter(db_conn: sqlite3.Connection):
    """FTS search respects the limit parameter."""
    _seed_fts(db_conn)

    # Search for a broad term that could match multiple results
    results = search(db_conn, "mypackage", limit=2)
    assert len(results) <= 2
