"""Tests for codestats.traverser -- file traversal and discovery."""

from __future__ import annotations

from pathlib import Path

from codestats.traverser import FileTraverser


def test_traverse_finds_python_files(tmp_repo: Path):
    """Traverser discovers .py files in the repo."""
    traverser = FileTraverser(tmp_repo)
    files = list(traverser.traverse())
    paths = [f.path for f in files]
    assert "mypackage/main.py" in paths
    assert "mypackage/utils.py" in paths


def test_traverse_skips_node_modules(tmp_repo: Path):
    """Traverser skips node_modules directories."""
    nm = tmp_repo / "node_modules" / "pkg"
    nm.mkdir(parents=True)
    (nm / "index.js").write_text("module.exports = 1;", encoding="utf-8")

    traverser = FileTraverser(tmp_repo)
    files = list(traverser.traverse())
    paths = [f.path for f in files]
    assert all("node_modules" not in p for p in paths)


def test_traverse_skips_binary_files(tmp_repo: Path):
    """Traverser skips files with null bytes (binary)."""
    traverser = FileTraverser(tmp_repo)
    files = list(traverser.traverse())
    paths = [f.path for f in files]
    assert "binary.bin" not in paths


def test_traverse_detects_test_files(tmp_repo: Path):
    """Traverser marks test files with is_test=True."""
    traverser = FileTraverser(tmp_repo)
    files = list(traverser.traverse())
    test_files = [f for f in files if f.is_test]
    test_paths = [f.path for f in test_files]
    assert "tests/test_utils.py" in test_paths


def test_traverse_handles_special_filenames(tmp_repo: Path):
    """Traverser recognizes Dockerfile and Makefile."""
    traverser = FileTraverser(tmp_repo)
    files = list(traverser.traverse())
    file_map = {f.path: f for f in files}

    assert "Makefile" in file_map
    assert file_map["Makefile"].language == "makefile"


def test_traverse_skips_large_files(tmp_repo: Path):
    """Traverser skips files exceeding the size limit."""
    traverser = FileTraverser(tmp_repo, max_file_size_kb=500)
    files = list(traverser.traverse())
    paths = [f.path for f in files]
    assert "data/big_file.py" not in paths


def test_traverse_respects_gitignore(tmp_repo: Path):
    """Traverser honors .gitignore patterns."""
    # .pyc files are in .gitignore
    cached = tmp_repo / "mypackage" / "utils.pyc"
    cached.write_bytes(b"compiled")

    traverser = FileTraverser(tmp_repo)
    files = list(traverser.traverse())
    paths = [f.path for f in files]
    assert all(not p.endswith(".pyc") for p in paths)


def test_traverse_handles_empty_dir(tmp_path: Path):
    """Traverser handles an empty directory gracefully."""
    (tmp_path / ".gitignore").write_text("", encoding="utf-8")
    traverser = FileTraverser(tmp_path)
    files = list(traverser.traverse())
    assert len(files) == 0


def test_traverse_detects_entry_points(tmp_repo: Path):
    """Traverser marks conventional entry points (main.py, index.ts)."""
    traverser = FileTraverser(tmp_repo)
    files = list(traverser.traverse())
    file_map = {f.path: f for f in files}

    assert file_map["mypackage/main.py"].is_entry_point is True
    assert file_map["src/index.ts"].is_entry_point is True


def test_traverse_detects_config_files(tmp_repo: Path):
    """Traverser marks YAML/TOML/JSON as config files."""
    traverser = FileTraverser(tmp_repo)
    files = list(traverser.traverse())
    file_map = {f.path: f for f in files}

    assert file_map["pyproject.toml"].is_config is True
