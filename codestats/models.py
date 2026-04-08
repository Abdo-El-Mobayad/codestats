"""Shared data models for the codestats ingestion pipeline.

Extracted and adapted from Repowise (https://github.com/repowise-dev/repowise).
These are plain dataclasses for speed -- the pipeline may process tens of
thousands of files.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

# ---------------------------------------------------------------------------
# Language tags
# ---------------------------------------------------------------------------

LanguageTag = Literal[
    "python",
    "typescript",
    "javascript",
    "go",
    "rust",
    "java",
    "cpp",
    "c",
    "csharp",
    "ruby",
    "php",
    "swift",
    "kotlin",
    "scala",
    "shell",
    "yaml",
    "json",
    "toml",
    "proto",
    "graphql",
    "terraform",
    "dockerfile",
    "makefile",
    "markdown",
    "sql",
    "openapi",
    "unknown",
]

# ---------------------------------------------------------------------------
# Extension -> language map
# ---------------------------------------------------------------------------

EXTENSION_TO_LANGUAGE: dict[str, LanguageTag] = {
    ".py": "python",
    ".pyi": "python",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".go": "go",
    ".rs": "rust",
    ".java": "java",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".c": "c",
    ".h": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".proto": "proto",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".tf": "terraform",
    ".hcl": "terraform",
    ".md": "markdown",
    ".mdx": "markdown",
    ".sql": "sql",
}

SPECIAL_FILENAMES: dict[str, LanguageTag] = {
    "Dockerfile": "dockerfile",
    "dockerfile": "dockerfile",
    "Makefile": "makefile",
    "makefile": "makefile",
    "GNUmakefile": "makefile",
}

# ---------------------------------------------------------------------------
# Symbol kinds
# ---------------------------------------------------------------------------

SymbolKind = Literal[
    "function",
    "class",
    "method",
    "interface",
    "enum",
    "constant",
    "type_alias",
    "decorator",
    "trait",
    "impl",
    "struct",
    "module",
    "macro",
    "variable",
]

# ---------------------------------------------------------------------------
# Core models
# ---------------------------------------------------------------------------


@dataclass
class FileInfo:
    """Metadata about a single source file discovered during traversal."""

    path: str  # POSIX path relative to repo root
    abs_path: str  # absolute filesystem path
    language: LanguageTag
    size_bytes: int
    last_modified: datetime
    is_test: bool
    is_config: bool
    is_entry_point: bool


@dataclass
class Symbol:
    """A code symbol (function, class, method, ...) extracted from a file."""

    id: str  # "<rel_path>::<name>" or "<rel_path>::<class>::<method>"
    name: str
    qualified_name: str
    kind: SymbolKind
    signature: str
    start_line: int
    end_line: int
    docstring: str | None
    decorators: list[str] = field(default_factory=list)
    visibility: Literal["public", "private", "protected", "internal"] = "public"
    is_async: bool = False
    language: str = ""
    parent_name: str | None = None


@dataclass
class Import:
    """An import statement extracted from a source file."""

    raw_statement: str
    module_path: str
    imported_names: list[str]
    is_relative: bool
    resolved_file: str | None


@dataclass
class ParsedFile:
    """Full result of parsing a single source file."""

    file_info: FileInfo
    symbols: list[Symbol]
    imports: list[Import]
    exports: list[str]
    docstring: str | None
    parse_errors: list[str]
    content_hash: str = ""


def compute_content_hash(source: bytes) -> str:
    """Return the SHA-256 hex digest of *source*."""
    return hashlib.sha256(source).hexdigest()
