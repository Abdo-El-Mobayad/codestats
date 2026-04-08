"""Dead code detection for codestats.

Extracted and adapted from Repowise (https://github.com/repowise-dev/repowise).

Pure graph traversal -- no LLM calls. Detects unreachable files, unused exports,
and zombie packages using the dependency graph and git metadata.
"""

from __future__ import annotations

import fnmatch
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class DeadCodeKind(StrEnum):
    UNREACHABLE_FILE = "unreachable_file"
    UNUSED_EXPORT = "unused_export"
    ZOMBIE_PACKAGE = "zombie_package"


@dataclass
class DeadCodeFinding:
    kind: DeadCodeKind
    file_path: str
    symbol_name: str | None
    confidence: float
    reason: str
    last_commit_at: datetime | None
    commit_count_90d: int
    importers: int
    safe_to_delete: bool
    primary_owner: str | None
    age_days: int | None


# Non-code languages that should never be flagged
_NON_CODE_LANGUAGES: frozenset[str] = frozenset(
    {
        "json", "yaml", "toml", "markdown", "sql", "shell",
        "terraform", "proto", "graphql", "dockerfile", "makefile", "unknown",
    }
)

# Patterns that should never be flagged as dead
_NEVER_FLAG_PATTERNS = (
    "*__init__.py",
    "*__main__.py",
    "*conftest.py",
    "*alembic/env.py",
    "*manage.py",
    "*wsgi.py",
    "*asgi.py",
    "*migrations*",
    "*schema*",
    "*seed*",
    "*.d.ts",
    "*setup.py",
    "*setup.cfg",
    "*next.config.*",
    "*vite.config.*",
    "*tailwind.config.*",
    "*postcss.config.*",
    "*jest.config.*",
    "*vitest.config.*",
    # Next.js framework entry points (file-system routing)
    "**/app/**/page.tsx", "**/app/**/page.ts",
    "**/app/**/route.tsx", "**/app/**/route.ts",
    "**/app/**/layout.tsx", "**/app/**/layout.ts",
    "**/app/**/loading.tsx", "**/app/**/error.tsx",
    "**/app/**/not-found.tsx", "**/app/**/template.tsx",
    "**/app/**/default.tsx",
    "**/pages/**/*.tsx", "**/pages/**/*.ts",
    "middleware.ts", "middleware.js",
    "instrumentation.ts", "instrumentation.js",
    # Next.js root config files
    "**/app/global-error.tsx",
    "**/app/global-error.ts",
    "**/app/**/opengraph-image.tsx",
    "**/app/**/twitter-image.tsx",
    "**/app/**/sitemap.ts",
    "**/app/**/robots.ts",
)

# Default dynamic patterns (plugins, handlers, etc.)
_DEFAULT_DYNAMIC_PATTERNS = (
    "*Plugin",
    "*Handler",
    "*Adapter",
    "*Middleware",
    "register_*",
    "on_*",
)

# Test fixture path segments
_FIXTURE_PATH_SEGMENTS = (
    "fixture", "fixtures", "testdata", "test_data",
    "sample_repo", "mock_data", "test_assets",
)


def _is_fixture_path(path: str) -> bool:
    path_lower = path.lower().replace("\\", "/")
    for seg in _FIXTURE_PATH_SEGMENTS:
        if f"/{seg}/" in path_lower or path_lower.startswith(f"{seg}/"):
            return True
    return False


class DeadCodeAnalyzer:
    """Detects unreachable files and unused exports using the dependency graph."""

    def __init__(
        self,
        graph: Any,  # nx.DiGraph
        git_meta_map: dict | None = None,
    ) -> None:
        self.graph = graph
        self.git_meta_map = git_meta_map or {}

    def analyze(self, config: dict | None = None) -> list[DeadCodeFinding]:
        """Full analysis. Returns list of findings."""
        cfg = config or {}
        findings: list[DeadCodeFinding] = []

        dynamic_patterns = cfg.get("dynamic_patterns", _DEFAULT_DYNAMIC_PATTERNS)
        whitelist = set(cfg.get("whitelist", []))

        if cfg.get("detect_unreachable_files", True):
            findings.extend(self._detect_unreachable_files(dynamic_patterns, whitelist))

        if cfg.get("detect_zombie_packages", True):
            findings.extend(self._detect_zombie_packages(whitelist))

        min_conf = cfg.get("min_confidence", 0.4)
        findings = [f for f in findings if f.confidence >= min_conf]

        return findings

    def _detect_unreachable_files(
        self,
        dynamic_patterns: tuple[str, ...],
        whitelist: set[str],
    ) -> list[DeadCodeFinding]:
        """Detect files with in_degree == 0 that are not entry points or tests."""
        findings = []

        for node in self.graph.nodes():
            if str(node).startswith("external:"):
                continue

            node_data = self.graph.nodes[node]
            if node_data.get("language", "unknown") in _NON_CODE_LANGUAGES:
                continue
            if node_data.get("is_entry_point", False):
                continue
            if node_data.get("is_test", False):
                continue
            if _is_fixture_path(str(node)):
                continue
            if self._should_never_flag(str(node), whitelist):
                continue

            in_deg = self.graph.in_degree(node)
            if in_deg > 0:
                continue

            finding = self._make_unreachable_finding(str(node), node_data, dynamic_patterns)
            if finding:
                findings.append(finding)

        return findings

    def _make_unreachable_finding(
        self,
        node: str,
        node_data: dict,
        dynamic_patterns: tuple[str, ...],
    ) -> DeadCodeFinding | None:
        git_meta = self.git_meta_map.get(node, {})
        commit_90d = git_meta.get("commit_count_90d", 0)
        last_commit = git_meta.get("last_commit_at")
        age_days = git_meta.get("age_days")
        primary_owner = git_meta.get("primary_owner_name")

        if commit_90d == 0 and last_commit and self._is_old(last_commit, days=180):
            confidence = 1.0
        elif commit_90d == 0:
            confidence = 0.7
        else:
            confidence = 0.4

        safe = confidence >= 0.7
        if safe and self._matches_dynamic_patterns(node, dynamic_patterns):
            safe = False

        return DeadCodeFinding(
            kind=DeadCodeKind.UNREACHABLE_FILE,
            file_path=node,
            symbol_name=None,
            confidence=confidence,
            reason="File has no importers (in_degree=0)",
            last_commit_at=last_commit if isinstance(last_commit, datetime) else None,
            commit_count_90d=commit_90d,
            importers=0,
            safe_to_delete=safe,
            primary_owner=primary_owner,
            age_days=age_days,
        )

    def _detect_zombie_packages(self, whitelist: set[str]) -> list[DeadCodeFinding]:
        """Detect monorepo packages with no incoming inter-package edges."""
        findings = []

        packages: dict[str, list[str]] = {}
        for node in self.graph.nodes():
            if str(node).startswith("external:"):
                continue
            parts = Path(str(node)).parts
            if len(parts) > 1:
                pkg = parts[0]
                packages.setdefault(pkg, []).append(str(node))

        if len(packages) < 2:
            return findings

        for pkg, files in packages.items():
            if pkg in whitelist:
                continue

            has_external_importers = False
            for f in files:
                for pred in self.graph.predecessors(f):
                    pred_str = str(pred)
                    if pred_str.startswith("external:"):
                        continue
                    pred_parts = Path(pred_str).parts
                    if len(pred_parts) > 0 and pred_parts[0] != pkg:
                        has_external_importers = True
                        break
                if has_external_importers:
                    break

            if not has_external_importers:
                findings.append(
                    DeadCodeFinding(
                        kind=DeadCodeKind.ZOMBIE_PACKAGE,
                        file_path=pkg,
                        symbol_name=None,
                        confidence=0.5,
                        reason=f"Package '{pkg}' has no importers from other packages",
                        last_commit_at=None,
                        commit_count_90d=0,
                        importers=0,
                        safe_to_delete=False,
                        primary_owner=None,
                        age_days=None,
                    )
                )

        return findings

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _should_never_flag(self, path: str, whitelist: set[str]) -> bool:
        if path in whitelist:
            return True
        for pattern in _NEVER_FLAG_PATTERNS:
            if fnmatch.fnmatch(path, pattern):
                return True
        return Path(path).name == "__init__.py"

    def _matches_dynamic_patterns(self, path: str, patterns: tuple[str, ...]) -> bool:
        name = Path(path).stem
        return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)

    def _is_old(self, dt: Any, days: int = 180) -> bool:
        if dt is None:
            return False
        now = datetime.now(UTC)
        if isinstance(dt, datetime):
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return (now - dt).days > days
        return False
