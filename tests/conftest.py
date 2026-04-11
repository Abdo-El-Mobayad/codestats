"""Shared fixtures for codestats test suite."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import textwrap
from pathlib import Path

import networkx as nx
import pytest
from click.testing import CliRunner

from codestats.models import EDGE_IMPORTS_FROM, EDGE_TESTED_BY
from codestats.storage import init_db


@pytest.fixture()
def tmp_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with sample Python + TypeScript files."""
    # Python files
    pkg = tmp_path / "mypackage"
    pkg.mkdir()

    (pkg / "__init__.py").write_text("", encoding="utf-8")

    (pkg / "main.py").write_text(
        textwrap.dedent("""\
        \"\"\"Main entry point.\"\"\"
        from mypackage.utils import helper

        def run():
            return helper()

        if __name__ == "__main__":
            run()
        """),
        encoding="utf-8",
    )

    (pkg / "utils.py").write_text(
        textwrap.dedent("""\
        \"\"\"Utility functions.\"\"\"

        def helper():
            \"\"\"A helper function.\"\"\"
            return 42

        def _private_helper():
            return 0

        class MyClass:
            \"\"\"A sample class.\"\"\"
            def method(self):
                pass
        """),
        encoding="utf-8",
    )

    (pkg / "auth.py").write_text(
        textwrap.dedent("""\
        \"\"\"Auth module with security keywords.\"\"\"
        import hashlib

        def check_password(password: str) -> bool:
            return len(password) >= 8

        def generate_token() -> str:
            return "token123"
        """),
        encoding="utf-8",
    )

    # Test file
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("", encoding="utf-8")
    (tests_dir / "test_utils.py").write_text(
        textwrap.dedent("""\
        from mypackage.utils import helper

        def test_helper():
            assert helper() == 42
        """),
        encoding="utf-8",
    )

    # TypeScript files
    src = tmp_path / "src"
    src.mkdir()

    (src / "index.ts").write_text(
        textwrap.dedent("""\
        import { greet } from './utils';
        export function main() { greet('world'); }
        """),
        encoding="utf-8",
    )

    (src / "utils.ts").write_text(
        textwrap.dedent("""\
        /**
         * Greet someone.
         */
        export function greet(name: string): string {
            return `Hello, ${name}!`;
        }

        export interface Config {
            debug: boolean;
        }
        """),
        encoding="utf-8",
    )

    # JavaScript file with require
    (src / "legacy.js").write_text(
        textwrap.dedent("""\
        const path = require('path');
        module.exports = { join: path.join };
        """),
        encoding="utf-8",
    )

    # Config files
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'test'\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("build:\n\techo hi\n", encoding="utf-8")

    # .gitignore
    (tmp_path / ".gitignore").write_text(
        "*.pyc\n__pycache__/\nnode_modules/\n.venv/\n",
        encoding="utf-8",
    )

    # Large file (should be skipped)
    large_dir = tmp_path / "data"
    large_dir.mkdir()
    (large_dir / "big_file.py").write_text("x = 1\n" * 100_000, encoding="utf-8")

    # Binary file (should be skipped)
    (tmp_path / "binary.bin").write_bytes(b"\x00\x01\x02\x03")

    # Git init
    env = {**os.environ, "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@test.com",
           "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@test.com"}
    subprocess.run(["git", "init"], cwd=str(tmp_path), capture_output=True, env=env, check=True)
    subprocess.run(["git", "add", "."], cwd=str(tmp_path), capture_output=True, env=env, check=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(tmp_path), capture_output=True, env=env, check=True,
    )

    return tmp_path


@pytest.fixture()
def sample_graph() -> nx.DiGraph:
    """Create a NetworkX DiGraph with known structure (10 nodes, various edge types)."""
    g = nx.DiGraph()

    # File nodes
    g.add_node("src/main.py", language="python", symbol_count=3, has_error=False,
               is_test=False, is_entry_point=True, content_hash="aaa", pagerank=0.15)
    g.add_node("src/utils.py", language="python", symbol_count=4, has_error=False,
               is_test=False, is_entry_point=False, content_hash="bbb", pagerank=0.08)
    g.add_node("src/auth.py", language="python", symbol_count=2, has_error=False,
               is_test=False, is_entry_point=False, content_hash="ccc", pagerank=0.05)
    g.add_node("src/models.py", language="python", symbol_count=5, has_error=False,
               is_test=False, is_entry_point=False, content_hash="ddd", pagerank=0.06)
    g.add_node("src/api.py", language="python", symbol_count=3, has_error=False,
               is_test=False, is_entry_point=False, content_hash="eee", pagerank=0.04)
    g.add_node("src/db.py", language="python", symbol_count=2, has_error=False,
               is_test=False, is_entry_point=False, content_hash="fff", pagerank=0.03)
    g.add_node("src/config.py", language="python", symbol_count=1, has_error=False,
               is_test=False, is_entry_point=False, content_hash="ggg", pagerank=0.02)
    g.add_node("tests/test_utils.py", language="python", symbol_count=2, has_error=False,
               is_test=True, is_entry_point=False, content_hash="hhh", pagerank=0.01)
    g.add_node("tests/test_auth.py", language="python", symbol_count=1, has_error=False,
               is_test=True, is_entry_point=False, content_hash="iii", pagerank=0.01)
    g.add_node("src/orphan.py", language="python", symbol_count=1, has_error=False,
               is_test=False, is_entry_point=False, content_hash="jjj", pagerank=0.005)

    # IMPORTS_FROM edges
    g.add_edge("src/main.py", "src/utils.py", imported_names=["helper"], edge_type=EDGE_IMPORTS_FROM)
    g.add_edge("src/main.py", "src/auth.py", imported_names=["check"], edge_type=EDGE_IMPORTS_FROM)
    g.add_edge("src/api.py", "src/models.py", imported_names=["User"], edge_type=EDGE_IMPORTS_FROM)
    g.add_edge("src/api.py", "src/db.py", imported_names=["connect"], edge_type=EDGE_IMPORTS_FROM)
    g.add_edge("src/api.py", "src/auth.py", imported_names=["verify"], edge_type=EDGE_IMPORTS_FROM)
    g.add_edge("src/db.py", "src/config.py", imported_names=["DB_URL"], edge_type=EDGE_IMPORTS_FROM)
    g.add_edge("src/main.py", "src/api.py", imported_names=["app"], edge_type=EDGE_IMPORTS_FROM)

    # Test imports
    g.add_edge("tests/test_utils.py", "src/utils.py", imported_names=["helper"], edge_type=EDGE_IMPORTS_FROM)
    g.add_edge("tests/test_auth.py", "src/auth.py", imported_names=["check_password"], edge_type=EDGE_IMPORTS_FROM)

    # TESTED_BY edges (reverse)
    g.add_edge("src/utils.py", "tests/test_utils.py", imported_names=["helper"], edge_type=EDGE_TESTED_BY)
    g.add_edge("src/auth.py", "tests/test_auth.py", imported_names=["check_password"], edge_type=EDGE_TESTED_BY)

    return g


@pytest.fixture()
def db_conn(tmp_path: Path) -> sqlite3.Connection:
    """Create a fresh SQLite DB with init_db()."""
    db_path = tmp_path / "test.db"
    conn = init_db(db_path)
    yield conn
    conn.close()


@pytest.fixture()
def cli_runner() -> CliRunner:
    """Click CliRunner instance."""
    return CliRunner()
