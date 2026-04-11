"""Git history analytics for codestats.

Extracted and adapted from Repowise's git_indexer.py.
Mines git history for per-file commit counts, hotspot scoring (temporal decay),
co-change detection, and primary owner calculation.

Uses gitpython for git operations.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Commit message classification regexes
_COMMIT_CATEGORIES: dict[str, re.Pattern[str]] = {
    "feature": re.compile(
        r"\b(add|implement|introduce|create|new|feat)\b",
        re.IGNORECASE,
    ),
    "refactor": re.compile(
        r"\b(refactor|restructure|cleanup|clean.up|rename|reorganize|extract|simplify|move)\b",
        re.IGNORECASE,
    ),
    "fix": re.compile(
        r"\b(fix|bug|patch|hotfix|revert|regression|broken|crash|error)\b",
        re.IGNORECASE,
    ),
    "dependency": re.compile(
        r"\b(upgrade|bump|update.dep|migrate.to|switch.to|dependency|dependencies)\b",
        re.IGNORECASE,
    ),
}

# Hotspot temporal decay half-life
HOTSPOT_HALFLIFE_DAYS: float = 180.0

# Co-change temporal decay tau
_CO_CHANGE_DECAY_TAU: float = 180.0

# Default commit history depth
_DEFAULT_COMMIT_LIMIT: int = 500

# Code extensions for per-file indexing
_CODE_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".py", ".pyi", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
        ".go", ".rs", ".java", ".kt", ".kts", ".scala",
        ".c", ".h", ".cpp", ".cc", ".cxx", ".hpp", ".hxx",
        ".cs", ".rb", ".php", ".swift",
    }
)

_RENAME_RE = re.compile(r"\{(.+?) => (.+?)\}")


@dataclass
class _CommitRec:
    """Lightweight commit record parsed from ``git log --numstat``."""
    sha: str
    author_name: str
    author_email: str
    ts: int
    is_merge: bool
    subject: str
    added: int = 0
    deleted: int = 0


@dataclass
class GitAnalyticsSummary:
    files_indexed: int
    hotspots: int
    stable_files: int
    duration_seconds: float = 0.0


def _extract_rename_paths(stat_path: str, known_paths: set[str]) -> tuple[str | None, str | None]:
    """Extract old/new paths from a git numstat rename line."""
    m = _RENAME_RE.search(stat_path)
    if m:
        prefix = stat_path[: m.start()]
        suffix = stat_path[m.end() :]
        old_path = prefix + m.group(1) + suffix
        new_path = prefix + m.group(2) + suffix
        known_paths.add(old_path)
        known_paths.add(new_path)
        return old_path, new_path
    return None, None


def _should_skip_index(file_path: str) -> bool:
    """Return True for files where per-file git indexing should be skipped."""
    return Path(file_path).suffix.lower() not in _CODE_EXTENSIONS


class GitAnalyzer:
    """Mines git history for per-file analytics.

    Uses gitpython for git operations. Runs synchronously (suitable for CLI).
    """

    def __init__(
        self,
        repo_path: str | Path,
        *,
        commit_limit: int | None = None,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.commit_limit = commit_limit or _DEFAULT_COMMIT_LIMIT

    def analyze(
        self,
        on_progress: Any = None,
    ) -> tuple[GitAnalyticsSummary, list[dict]]:
        """Full analysis of all tracked files. Returns summary + list of metadata dicts."""
        start = time.monotonic()
        repo = self._get_repo()
        if repo is None:
            return GitAnalyticsSummary(0, 0, 0, 0.0), []

        tracked_files = self._get_tracked_files(repo)
        if not tracked_files:
            return GitAnalyticsSummary(0, 0, 0, 0.0), []

        indexable_files = [fp for fp in tracked_files if not _should_skip_index(fp)]

        log.info("Git analytics: indexing %d code files", len(indexable_files))

        results: list[dict] = []
        for i, file_path in enumerate(indexable_files):
            meta = self._index_file(file_path, repo)
            results.append(meta)
            if on_progress and (i + 1) % 50 == 0:
                on_progress(i + 1, len(indexable_files))

        # Compute co-changes
        co_changes = self._compute_co_changes(
            repo, set(tracked_files), self.commit_limit
        )
        for meta in results:
            fp = meta["file_path"]
            if fp in co_changes:
                meta["co_change_partners_json"] = json.dumps(co_changes[fp])

        # Compute percentiles
        self._compute_percentiles(results)

        duration = time.monotonic() - start
        hotspots = sum(1 for m in results if m.get("is_hotspot", False))
        stable = sum(1 for m in results if m.get("is_stable", False))

        repo.close()

        summary = GitAnalyticsSummary(
            files_indexed=len(results),
            hotspots=hotspots,
            stable_files=stable,
            duration_seconds=duration,
        )
        log.info(
            "Git analytics complete: files=%d, hotspots=%d, stable=%d, duration=%.1fs",
            summary.files_indexed, summary.hotspots, summary.stable_files, summary.duration_seconds,
        )
        return summary, results

    def analyze_files(
        self,
        file_paths: list[str],
        on_progress: Any = None,
    ) -> tuple[GitAnalyticsSummary, list[dict]]:
        """Analyze only the specified files. Used for incremental indexing."""
        start = time.monotonic()
        repo = self._get_repo()
        if repo is None:
            return GitAnalyticsSummary(0, 0, 0, 0.0), []

        indexable_files = [fp for fp in file_paths if not _should_skip_index(fp)]

        if not indexable_files:
            return GitAnalyticsSummary(0, 0, 0, 0.0), []

        log.info("Git analytics (incremental): indexing %d code files", len(indexable_files))

        results: list[dict] = []
        for i, file_path in enumerate(indexable_files):
            meta = self._index_file(file_path, repo)
            results.append(meta)
            if on_progress and (i + 1) % 50 == 0:
                on_progress(i + 1, len(indexable_files))

        # Compute co-changes for the subset
        tracked_files = self._get_tracked_files(repo)
        co_changes = self._compute_co_changes(
            repo, set(tracked_files), self.commit_limit
        )
        for meta in results:
            fp = meta["file_path"]
            if fp in co_changes:
                meta["co_change_partners_json"] = json.dumps(co_changes[fp])

        # Compute percentiles within this subset
        self._compute_percentiles(results)

        duration = time.monotonic() - start
        hotspots = sum(1 for m in results if m.get("is_hotspot", False))
        stable = sum(1 for m in results if m.get("is_stable", False))

        repo.close()

        summary = GitAnalyticsSummary(
            files_indexed=len(results),
            hotspots=hotspots,
            stable_files=stable,
            duration_seconds=duration,
        )
        log.info(
            "Git analytics (incremental) complete: files=%d, hotspots=%d, duration=%.1fs",
            summary.files_indexed, summary.hotspots, summary.duration_seconds,
        )
        return summary, results

    # ------------------------------------------------------------------
    # Internal methods
    # ------------------------------------------------------------------

    def _get_repo(self) -> Any | None:
        try:
            import git as gitpython
            return gitpython.Repo(self.repo_path, search_parent_directories=True)
        except Exception as exc:
            log.warning("Git unavailable or not a repository: %s (%s)", self.repo_path, exc)
            return None

    def _get_tracked_files(self, repo: Any) -> list[str]:
        try:
            output = repo.git.ls_files()
            return [f for f in output.splitlines() if f.strip()]
        except Exception as exc:
            log.warning("Failed to list tracked files: %s", exc)
            return []

    def _index_file(self, file_path: str, repo: Any) -> dict:
        """Index a single file's git history."""
        now = datetime.now(UTC)
        ninety_days_ago = now - timedelta(days=90)
        ninety_days_ago_ts = ninety_days_ago.timestamp()

        meta: dict[str, Any] = {
            "file_path": file_path,
            "commit_count_total": 0,
            "commit_count_90d": 0,
            "first_commit_at": None,
            "last_commit_at": None,
            "primary_owner_name": None,
            "primary_owner_email": None,
            "co_change_partners_json": "[]",
            "commit_categories_json": "{}",
            "is_hotspot": False,
            "is_stable": False,
            "churn_percentile": 0.0,
            "age_days": 0,
            "lines_added_90d": 0,
            "lines_deleted_90d": 0,
            "bus_factor": 0,
            "contributor_count": 0,
            "temporal_hotspot_score": 0.0,
        }

        try:
            log_args = [
                f"-{self.commit_limit}",
                "--numstat",
                "--format=%x00%H%x1f%an%x1f%ae%x1f%ct%x1f%P%x1f%s",
                "--",
                file_path,
            ]
            raw = repo.git.log(*log_args)
        except Exception:
            return meta

        if not raw.strip():
            return meta

        known_paths: set[str] = {file_path}
        commits: list[_CommitRec] = []
        current: _CommitRec | None = None

        for line in raw.splitlines():
            if line.startswith("\x00"):
                parts = line.lstrip("\x00").split("\x1f")
                if len(parts) >= 6:
                    sha, an, ae, ct, parents, subj = (
                        parts[0], parts[1], parts[2], parts[3], parts[4], parts[5]
                    )
                    try:
                        ts = int(ct)
                    except ValueError:
                        ts = 0
                    current = _CommitRec(
                        sha=sha,
                        author_name=an or "unknown",
                        author_email=ae,
                        ts=ts,
                        is_merge=len(parents.split()) > 1,
                        subject=subj,
                    )
                    commits.append(current)
            elif current is not None and line.strip():
                numstat_parts = line.split("\t")
                if len(numstat_parts) >= 3:
                    stat_path = numstat_parts[2]
                    match_path = stat_path
                    if "=>" in stat_path:
                        _old, _new = _extract_rename_paths(stat_path, known_paths)
                        match_path = _new or stat_path
                    if match_path in known_paths or match_path == file_path:
                        try:
                            current.added += int(numstat_parts[0]) if numstat_parts[0] != "-" else 0
                            current.deleted += int(numstat_parts[1]) if numstat_parts[1] != "-" else 0
                        except ValueError:
                            pass

        if not commits:
            return meta

        meta["commit_count_total"] = len(commits)

        try:
            timestamps = [c.ts for c in commits if c.ts > 0]
            if timestamps:
                first_ts = min(timestamps)
                last_ts = max(timestamps)
                meta["first_commit_at"] = datetime.fromtimestamp(first_ts, tz=UTC)
                meta["last_commit_at"] = datetime.fromtimestamp(last_ts, tz=UTC)
                meta["age_days"] = (now - datetime.fromtimestamp(first_ts, tz=UTC)).days

            author_counts: Counter[str] = Counter()
            author_emails: dict[str, str] = {}

            for c in commits:
                is_recent_90 = c.ts >= ninety_days_ago_ts
                if is_recent_90:
                    meta["commit_count_90d"] += 1
                    meta["lines_added_90d"] += c.added
                    meta["lines_deleted_90d"] += c.deleted
                author_counts[c.author_name] += 1
                if c.author_name not in author_emails and c.author_email:
                    author_emails[c.author_name] = c.author_email

            # Temporal hotspot score
            _ln2 = math.log(2)
            temporal_score = 0.0
            for c in commits:
                age_days = max((now.timestamp() - c.ts) / 86400.0, 0.0)
                weight = math.exp(-_ln2 * age_days / HOTSPOT_HALFLIFE_DAYS)
                lines = min((c.added + c.deleted) / 100.0, 3.0)
                temporal_score += weight * lines
            meta["temporal_hotspot_score"] = temporal_score

            # Contributor count and bus factor
            meta["contributor_count"] = len(author_counts)
            total_commits = sum(author_counts.values())
            if total_commits > 0:
                threshold = total_commits * 0.8
                running = 0
                bus = 0
                for _name, cnt in author_counts.most_common():
                    running += cnt
                    bus += 1
                    if running >= threshold:
                        break
                meta["bus_factor"] = bus

            # Primary owner
            if author_counts:
                primary_name = author_counts.most_common(1)[0][0]
                meta["primary_owner_name"] = primary_name
                meta["primary_owner_email"] = author_emails.get(primary_name)

            # Commit categories
            category_counts: Counter[str] = Counter()
            for c in commits:
                msg = c.subject[:200]
                for cat, pattern in _COMMIT_CATEGORIES.items():
                    if pattern.search(msg):
                        category_counts[cat] += 1
                        break
            meta["commit_categories_json"] = json.dumps(dict(category_counts))

            # Stable classification
            if meta["commit_count_total"] > 10 and meta["commit_count_90d"] == 0:
                meta["is_stable"] = True

        except Exception:
            log.debug("git_analytics partial failure: %s", file_path, exc_info=True)

        return meta

    def _compute_co_changes(
        self,
        repo: Any,
        all_files: set[str],
        commit_limit: int = 500,
        min_count: int = 3,
    ) -> dict[str, list[dict]]:
        """Walk recent commits and record co-occurrence pairs for tracked files."""
        pair_scores: defaultdict[tuple[str, str], float] = defaultdict(float)
        pair_last_date: dict[tuple[str, str], int] = {}
        now_ts = time.time()

        try:
            raw = repo.git.log(
                f"-{commit_limit}",
                "--name-only",
                "--no-merges",
                "--format=%x00%ct",
            )
        except Exception:
            return {}

        current: set[str] = set()
        current_ts: int = 0

        def _flush_commit() -> None:
            if len(current) < 2:
                return
            age_days = max((now_ts - current_ts) / 86400.0, 0.0)
            weight = math.exp(-age_days / _CO_CHANGE_DECAY_TAU)
            sorted_files = sorted(current)
            for i in range(len(sorted_files)):
                for j in range(i + 1, len(sorted_files)):
                    pair = (sorted_files[i], sorted_files[j])
                    pair_scores[pair] += weight
                    if pair not in pair_last_date or current_ts > pair_last_date[pair]:
                        pair_last_date[pair] = current_ts

        for line in raw.splitlines():
            if line == "\x00" or line.startswith("\x00"):
                _flush_commit()
                current = set()
                ts_part = line.lstrip("\x00").strip()
                try:
                    current_ts = int(ts_part)
                except (ValueError, TypeError):
                    current_ts = 0
            else:
                path = line.strip()
                if path and path in all_files:
                    current.add(path)

        _flush_commit()

        result: dict[str, list[dict]] = defaultdict(list)
        for (a, b), score in pair_scores.items():
            if score >= min_count:
                last_ts = pair_last_date.get((a, b), 0)
                last_date = (
                    datetime.fromtimestamp(last_ts, tz=UTC).strftime("%Y-%m-%d")
                    if last_ts > 0
                    else None
                )
                result[a].append({
                    "file_path": b,
                    "co_change_count": round(score, 2),
                    "last_co_change": last_date,
                })
                result[b].append({
                    "file_path": a,
                    "co_change_count": round(score, 2),
                    "last_co_change": last_date,
                })

        for fp in result:
            result[fp].sort(key=lambda x: x["co_change_count"], reverse=True)

        return dict(result)

    @staticmethod
    def _compute_percentiles(metadata_list: list[dict]) -> None:
        """Compute churn_percentile and is_hotspot. Mutates in place."""
        if not metadata_list:
            return

        sorted_by_churn = sorted(
            range(len(metadata_list)),
            key=lambda i: (
                metadata_list[i].get("temporal_hotspot_score") or 0.0,
                metadata_list[i].get("commit_count_90d", 0),
            ),
        )

        total = len(metadata_list)
        for rank, idx in enumerate(sorted_by_churn):
            metadata_list[idx]["churn_percentile"] = rank / total if total > 0 else 0.0

        for meta in metadata_list:
            commit_90d = meta.get("commit_count_90d", 0)
            churn_pct = meta.get("churn_percentile", 0.0)
            if churn_pct >= 0.75 and commit_90d > 0:
                meta["is_hotspot"] = True
