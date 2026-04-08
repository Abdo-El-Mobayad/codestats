"""File traversal for the codestats ingestion pipeline.

Extracted and adapted from Repowise (https://github.com/repowise-dev/repowise).

FileTraverser walks a repository tree and yields FileInfo objects for each
source file. It respects .gitignore, blocked dirs/extensions, file-size limits,
and generated-file detection.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pathspec

from .models import (
    EXTENSION_TO_LANGUAGE,
    SPECIAL_FILENAMES,
    FileInfo,
    LanguageTag,
)

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blocklists
# ---------------------------------------------------------------------------

_BLOCKED_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".tox",
        "dist",
        "build",
        ".next",
        "target",
        ".gradle",
        "vendor",
        "coverage",
        "htmlcov",
        ".eggs",
        "site-packages",
        ".cache",
        ".idea",
        ".vscode",
        # codestats-specific
        ".claude",
        ".codestats",
    }
)

_BLOCKED_EXTENSIONS: frozenset[str] = frozenset(
    {".pyc", ".pyo", ".pyd", ".so", ".dll", ".dylib", ".exe", ".o", ".a", ".wasm"}
)

_BLOCKED_FILENAME_PATTERNS: list[str] = [
    "*.min.js",
    "*.min.css",
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "go.sum",
    "Cargo.lock",
    "poetry.lock",
    "uv.lock",
    "*.lock",
]

_GENERATED_MARKERS: tuple[str, ...] = (
    "Code generated",
    "DO NOT EDIT",
    "This file was automatically generated",
    "GENERATED CODE",
    "AUTO-GENERATED",
    "@generated",
)

_GENERATED_SUFFIXES: tuple[str, ...] = (
    "_pb2.py",
    "_pb2_grpc.py",
    "_pb.ts",
    "_pb.js",
    "_grpc.pb.go",
)

_ENTRY_POINT_NAMES: frozenset[str] = frozenset(
    {
        "main.py",
        "app.py",
        "run.py",
        "server.py",
        "wsgi.py",
        "asgi.py",
        "index.ts",
        "index.js",
        "main.ts",
        "main.js",
        "app.ts",
        "main.go",
        "main.rs",
        "lib.rs",
        "Main.java",
        "Application.java",
    }
)

_ENTRY_POINT_STEMS: frozenset[str] = frozenset(
    {"main", "index", "app", "run", "server", "start", "wsgi", "asgi"}
)

_DEFAULT_MAX_FILE_SIZE_BYTES: int = 500 * 1024  # 500 KB

_SKIP_GENERATED_CHECK: frozenset[str] = frozenset(
    {
        "json",
        "yaml",
        "toml",
        "markdown",
        "sql",
        "shell",
        "terraform",
        "proto",
        "graphql",
        "dockerfile",
        "makefile",
    }
)


class FileTraverser:
    """Traverse a repository and yield FileInfo for each documentable file."""

    def __init__(
        self,
        repo_root: Path,
        *,
        max_file_size_kb: int = 500,
        extra_exclude_patterns: list[str] | None = None,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.max_file_size_bytes = max_file_size_kb * 1024
        self._gitignore = _load_gitignore_spec(self.repo_root)
        self._blocked_patterns = pathspec.PathSpec.from_lines(
            "gitwildmatch", _BLOCKED_FILENAME_PATTERNS
        )
        patterns = extra_exclude_patterns or []
        self._extra_exclude = pathspec.PathSpec.from_lines("gitwildmatch", patterns)
        log.info(
            "FileTraverser initialised: repo_root=%s, max_file_size_kb=%d",
            self.repo_root,
            max_file_size_kb,
        )

    def traverse(self) -> Iterator[FileInfo]:
        """Yield FileInfo for every includable source file in the repo."""
        for abs_path in self._walk():
            info = self._build_file_info(abs_path)
            if info is not None:
                yield info

    def _walk(self) -> Iterator[Path]:
        for dirpath, dirnames, filenames in os.walk(self.repo_root):
            dirpath_obj = Path(dirpath)
            rel_dir = dirpath_obj.relative_to(self.repo_root)

            dirnames[:] = sorted(
                d for d in dirnames if not self._should_skip_dir(d, rel_dir / d)
            )

            for filename in sorted(filenames):
                yield dirpath_obj / filename

    def _should_skip_dir(self, dirname: str, rel_path: Path) -> bool:
        if dirname in _BLOCKED_DIRS:
            return True
        rel_str = rel_path.as_posix()
        if self._gitignore.match_file(rel_str + "/"):
            return True
        if self._extra_exclude.match_file(rel_str + "/"):
            return True
        return False

    def _build_file_info(self, abs_path: Path) -> FileInfo | None:
        try:
            stat = abs_path.stat()
        except OSError:
            return None

        size_bytes = stat.st_size
        rel_path = abs_path.relative_to(self.repo_root)
        rel_str = rel_path.as_posix()

        if size_bytes > self.max_file_size_bytes:
            return None

        if abs_path.suffix.lower() in _BLOCKED_EXTENSIONS:
            return None

        if self._gitignore.match_file(rel_str):
            return None
        if self._extra_exclude.match_file(rel_str):
            return None

        if self._blocked_patterns.match_file(rel_str):
            return None

        language = _language_from_name_or_ext(abs_path)
        if language is None:
            if _is_binary(abs_path):
                return None
            language = _detect_by_shebang(abs_path)
            if language == "unknown":
                return None

        if language not in _SKIP_GENERATED_CHECK and _is_generated(abs_path):
            return None

        filename = abs_path.name
        return FileInfo(
            path=rel_str,
            abs_path=str(abs_path),
            language=language,
            size_bytes=size_bytes,
            last_modified=datetime.fromtimestamp(stat.st_mtime),
            is_test=_is_test_file(rel_str, filename),
            is_config=_is_config_file(language),
            is_entry_point=filename in _ENTRY_POINT_NAMES or _stem_is_entry_point(abs_path),
        )


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _language_from_name_or_ext(abs_path: Path) -> LanguageTag | None:
    filename = abs_path.name
    if filename in SPECIAL_FILENAMES:
        return SPECIAL_FILENAMES[filename]
    return EXTENSION_TO_LANGUAGE.get(abs_path.suffix.lower())


def _detect_by_shebang(abs_path: Path) -> LanguageTag:
    try:
        with open(abs_path, encoding="utf-8", errors="ignore") as f:
            first_line = f.readline(200)
        if not first_line.startswith("#!"):
            return "unknown"
        if "python" in first_line:
            return "python"
        if "node" in first_line:
            return "javascript"
        if "bash" in first_line or " sh" in first_line:
            return "shell"
        if "ruby" in first_line:
            return "ruby"
    except OSError:
        pass
    return "unknown"


def _is_binary(abs_path: Path) -> bool:
    try:
        with open(abs_path, "rb") as f:
            return b"\x00" in f.read(8192)
    except OSError:
        return True


def _is_generated(abs_path: Path) -> bool:
    name = abs_path.name
    if any(name.endswith(sfx) for sfx in _GENERATED_SUFFIXES):
        return True
    try:
        with open(abs_path, encoding="utf-8", errors="ignore") as f:
            header = f.read(512)
        header_upper = header.upper()
        return any(marker.upper() in header_upper for marker in _GENERATED_MARKERS)
    except OSError:
        return False


def _is_test_file(rel_path: str, filename: str) -> bool:
    stem = Path(filename).stem.lower()
    if stem.startswith("test_") or stem.endswith("_test"):
        return True
    if stem.startswith("spec_") or stem.endswith("_spec"):
        return True
    path_lower = rel_path.lower()
    return "/test/" in path_lower or "/tests/" in path_lower or "/spec/" in path_lower


def _is_config_file(language: LanguageTag) -> bool:
    return language in ("yaml", "toml", "json", "dockerfile", "makefile")


def _stem_is_entry_point(abs_path: Path) -> bool:
    stem = abs_path.stem.lower()
    return stem in _ENTRY_POINT_STEMS


def _load_gitignore_spec(repo_root: Path) -> pathspec.PathSpec:
    gitignore = repo_root / ".gitignore"
    lines: list[str] = []
    if gitignore.exists():
        lines = gitignore.read_text(encoding="utf-8", errors="ignore").splitlines()
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)
