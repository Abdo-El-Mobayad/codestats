"""Shared data models for the codestats ingestion pipeline.

Extracted and adapted from Repowise (https://github.com/repowise-dev/repowise).
These are plain dataclasses for speed -- the pipeline may process tens of
thousands of files.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Literal

# ---------------------------------------------------------------------------
# Edge type constants
# ---------------------------------------------------------------------------

EDGE_IMPORTS_FROM = "IMPORTS_FROM"
EDGE_TESTED_BY = "TESTED_BY"
EDGE_CALLS = "CALLS"
EDGE_CONTAINS = "CONTAINS"

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
    "lua",
    "r",
    "elixir",
    "haskell",
    "ocaml",
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
    "vue",
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
    ".lua": "lua",
    ".r": "r",
    ".R": "r",
    ".ex": "elixir",
    ".exs": "elixir",
    ".hs": "haskell",
    ".ml": "ocaml",
    ".mli": "ocaml",
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
    ".vue": "vue",
    ".ipynb": "python",
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


# ---------------------------------------------------------------------------
# Dead code kind enum (used by dead_code.py, extended here for new kinds)
# ---------------------------------------------------------------------------

class DeadCodeKind(StrEnum):
    UNREACHABLE_FILE = "unreachable_file"
    UNUSED_EXPORT = "unused_export"
    ZOMBIE_PACKAGE = "zombie_package"
    MISPLACED_FILE = "misplaced_file"


# ---------------------------------------------------------------------------
# v0.4 dataclasses
# ---------------------------------------------------------------------------


@dataclass
class Flow:
    """An execution flow traced from an entry point through call chains."""
    flow_id: str
    entry_point: str
    members: list[str]  # qualified names in BFS order
    node_count: int
    file_spread: int  # unique files touched
    criticality: float  # 0.0-1.0
    has_test_coverage: bool


@dataclass
class Community:
    """A Louvain community cluster."""
    community_id: int
    name: str
    members: list[str]  # file paths
    member_count: int
    cohesion: float  # internal / (internal + external) edges
    top_files: list[str]


@dataclass
class ImpactResult:
    """Result of bidirectional impact analysis on changed files."""
    changed_files: list[str]
    impacted_files: list[dict]  # [{path, risk_score, depth, reason}]
    total_impacted: int
    max_risk: float
    risk_level: str  # LOW/MEDIUM/HIGH/CRITICAL


@dataclass
class RefactorPreview:
    """A preview of a rename or move refactoring operation."""
    preview_id: str
    target_name: str
    new_name: str | None
    edits: list[dict]  # [{file, line, old_text, new_text}]
    kind: str  # 'rename' or 'move'
    created_at: datetime
