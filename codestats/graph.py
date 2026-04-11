"""Dependency graph builder for codestats.

Extracted and adapted from Repowise (https://github.com/repowise-dev/repowise).

Constructs a directed graph from ParsedFile objects. Monorepo-aware tsconfig
path alias resolution: auto-discovers all tsconfig.json files, follows extends
chains, handles JSONC comments. Simplified: no co-change edges, no framework
edges, no compile_commands support.

Node types:
    "file"     -- every source file
    "external" -- third-party / unresolvable imports (prefix "external:")

Edge attributes:
    imported_names: list[str] -- specific names imported across this edge
"""

from __future__ import annotations

import json
import logging
import posixpath
import re
from pathlib import Path
from typing import Any

import networkx as nx

from .models import ParsedFile

log = logging.getLogger(__name__)

_LARGE_REPO_THRESHOLD = 30_000
_SKIP_DIRS = frozenset({
    "node_modules", "dist", "build", ".next", ".nuxt", ".output",
    "__pycache__", ".git", ".codestats", ".turbo", ".vercel",
})


class GraphBuilder:
    """Build a dependency graph from a collection of ParsedFile objects.

    Usage::

        builder = GraphBuilder(repo_path="/path/to/repo")
        for parsed in parsed_files:
            builder.add_file(parsed)
        graph = builder.build()
        pr = builder.pagerank()
    """

    def __init__(self, repo_path: Path | str | None = None, tsconfig_path: str | None = None) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._parsed_files: dict[str, ParsedFile] = {}
        self._built = False
        self._repo_path: Path | None = Path(repo_path) if repo_path else None
        self._tsconfig_override: str | None = tsconfig_path
        self._tsconfig_map: dict[str, list[tuple[str, str]]] | None = None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _best_stem_match(candidates: list[str], importer_path: str) -> str:
        """Pick the candidate whose path shares the longest common prefix with *importer_path*."""
        if len(candidates) == 1:
            return candidates[0]
        importer_parts = Path(importer_path).parts
        best: str = candidates[0]
        best_score: int = 0
        for c in candidates:
            c_parts = Path(c).parts
            score = sum(1 for a, b in zip(importer_parts, c_parts) if a == b)
            if score > best_score:
                best_score = score
                best = c
        return best

    # ------------------------------------------------------------------
    # Building
    # ------------------------------------------------------------------

    def add_file(self, parsed: ParsedFile) -> None:
        """Register one parsed file in the graph."""
        path = parsed.file_info.path
        self._parsed_files[path] = parsed
        self._built = False
        self._graph.add_node(
            path,
            language=parsed.file_info.language,
            symbol_count=len(parsed.symbols),
            has_error=bool(parsed.parse_errors),
            is_test=parsed.file_info.is_test,
            is_entry_point=parsed.file_info.is_entry_point,
            content_hash=parsed.content_hash,
        )

    def build(self) -> nx.DiGraph:
        """Resolve imports and add edges. Returns the finalized graph."""
        self._graph.remove_edges_from(list(self._graph.edges()))

        path_set = set(self._parsed_files.keys())
        stem_map: dict[str, list[str]] = {}
        for p in path_set:
            stem = Path(p).stem.lower()
            stem_map.setdefault(stem, []).append(p)

        for path, parsed in self._parsed_files.items():
            for imp in parsed.imports:
                target = self._resolve_import(
                    imp.module_path, path, path_set, stem_map, parsed.file_info.language
                )
                if target:
                    if self._graph.has_edge(path, target):
                        existing = self._graph[path][target].get("imported_names", [])
                        merged = list(set(existing + imp.imported_names))
                        self._graph[path][target]["imported_names"] = merged
                    else:
                        self._graph.add_edge(
                            path,
                            target,
                            imported_names=list(imp.imported_names),
                        )

        # Add TESTED_BY reverse edges
        self._add_tested_by_edges()

        self._built = True
        log.info(
            "Graph built: nodes=%d, edges=%d",
            self._graph.number_of_nodes(),
            self._graph.number_of_edges(),
        )
        return self._graph

    def _add_tested_by_edges(self) -> None:
        """For each test file that imports a production file, create a reverse TESTED_BY edge."""
        tested_by_count = 0
        for node, data in list(self._graph.nodes(data=True)):
            if not data.get("is_test"):
                continue
            # This test file imports production files -- create reverse edges
            for _, target, edge_data in list(self._graph.out_edges(node, data=True)):
                target_data = self._graph.nodes.get(target, {})
                if target_data and not target_data.get("is_test") and not str(target).startswith("external:"):
                    # Add reverse edge: production -> test (TESTED_BY)
                    if not self._graph.has_edge(target, node):
                        self._graph.add_edge(
                            target, node,
                            edge_type="TESTED_BY",
                            imported_names=edge_data.get("imported_names", []),
                        )
                        tested_by_count += 1
        if tested_by_count:
            log.info("Added %d TESTED_BY edges", tested_by_count)

    def graph(self) -> nx.DiGraph:
        """Return the graph (building it first if necessary)."""
        if not self._built:
            self.build()
        return self._graph

    # ------------------------------------------------------------------
    # Graph metrics
    # ------------------------------------------------------------------

    def pagerank(self, alpha: float = 0.85) -> dict[str, float]:
        """Return PageRank scores for each node."""
        g = self.graph()
        if g.number_of_nodes() == 0:
            return {}
        try:
            return nx.pagerank(g, alpha=alpha)
        except nx.PowerIterationFailedConvergence:
            log.warning("PageRank did not converge, using uniform scores")
            n = g.number_of_nodes()
            return {node: 1.0 / n for node in g.nodes()}

    def betweenness_centrality(self) -> dict[str, float]:
        """Return betweenness centrality. High value = bridge file."""
        g = self.graph()
        n = g.number_of_nodes()
        if n == 0:
            return {}
        if n > _LARGE_REPO_THRESHOLD:
            k = min(500, n)
            return nx.betweenness_centrality(g, k=k, normalized=True)
        return nx.betweenness_centrality(g, normalized=True)

    def strongly_connected_components(self) -> list[frozenset[str]]:
        """Return SCCs as frozensets. SCCs of size > 1 are circular deps."""
        return [frozenset(scc) for scc in nx.strongly_connected_components(self.graph())]

    def community_detection(self) -> dict[str, int]:
        """Assign a community ID to each node using the Louvain algorithm."""
        g = self.graph()
        if g.number_of_nodes() == 0:
            return {}
        try:
            communities = nx.community.louvain_communities(g.to_undirected(), seed=42)
            result: dict[str, int] = {}
            for community_id, members in enumerate(communities):
                for node in members:
                    result[node] = community_id
            return result
        except Exception as exc:
            log.warning("Community detection failed: %s", exc)
            return {node: 0 for node in g.nodes()}

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """Serialize the graph to a JSON-compatible dict."""
        return nx.node_link_data(self.graph())

    # ------------------------------------------------------------------
    # tsconfig path alias resolution (monorepo-aware)
    # ------------------------------------------------------------------

    @staticmethod
    def _strip_jsonc_comments(text: str) -> str:
        """Strip // and /* */ comments from JSONC text, preserving strings."""
        result: list[str] = []
        i = 0
        in_string = False
        n = len(text)
        while i < n:
            c = text[i]
            if in_string:
                result.append(c)
                if c == "\\" and i + 1 < n:
                    i += 1
                    result.append(text[i])
                elif c == '"':
                    in_string = False
                i += 1
            elif c == '"':
                in_string = True
                result.append(c)
                i += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "/":
                while i < n and text[i] != "\n":
                    i += 1
            elif c == "/" and i + 1 < n and text[i + 1] == "*":
                i += 2
                while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                    i += 1
                i += 2
            else:
                result.append(c)
                i += 1
        return "".join(result)

    def _read_tsconfig_json(self, config_path: Path) -> dict:
        """Read a tsconfig/jsconfig file, handling JSONC comments and trailing commas."""
        text = config_path.read_text(encoding="utf-8")
        text = self._strip_jsonc_comments(text)
        text = re.sub(r",\s*([}\]])", r"\1", text)
        return json.loads(text)

    def _resolve_tsconfig_options(self, config_path: Path, depth: int = 0) -> tuple[dict, Path]:
        """Parse tsconfig and follow extends to find compilerOptions with paths.

        Returns (compilerOptions, directory_of_config_that_defines_paths).
        """
        if depth > 5:
            return {}, config_path.parent

        config = self._read_tsconfig_json(config_path)
        compiler_options = config.get("compilerOptions", {})

        if "paths" in compiler_options:
            return compiler_options, config_path.parent

        extends = config.get("extends")
        if extends:
            if isinstance(extends, str):
                extends = [extends]
            for ext in extends if isinstance(extends, list) else []:
                base_path = (config_path.parent / ext).resolve()
                if not base_path.suffix:
                    base_path = base_path.with_suffix(".json")
                if base_path.exists():
                    base_options, base_dir = self._resolve_tsconfig_options(base_path, depth + 1)
                    if "paths" in base_options:
                        if "baseUrl" in compiler_options:
                            merged = {**base_options, "baseUrl": compiler_options["baseUrl"]}
                            return merged, config_path.parent
                        return base_options, base_dir

        return compiler_options, config_path.parent

    def _parse_tsconfig_paths(self, config_path: Path) -> list[tuple[str, str]]:
        """Extract path aliases from a tsconfig, following extends if needed.

        Returns list of (alias_prefix, resolved_dir) where resolved_dir
        is relative to the repo root.
        """
        try:
            compiler_options, source_dir = self._resolve_tsconfig_options(config_path)
        except Exception as exc:
            log.debug("Failed to parse %s: %s", config_path, exc)
            return []

        paths = compiler_options.get("paths", {})
        if not paths:
            return []

        base_url = compiler_options.get("baseUrl", ".")
        base_url_abs = (source_dir / base_url).resolve()
        repo_root = self._repo_path.resolve() if self._repo_path else Path.cwd().resolve()
        try:
            base_url_rel = base_url_abs.relative_to(repo_root).as_posix()
        except ValueError:
            base_url_rel = "."
        if base_url_rel == ".":
            base_url_rel = ""

        aliases: list[tuple[str, str]] = []
        for pattern, targets in paths.items():
            if not targets or not isinstance(targets, list):
                continue
            alias_prefix = pattern[:-1] if pattern.endswith("/*") else pattern
            target = targets[0]
            target_prefix = target[:-1] if target.endswith("/*") else target

            resolved = posixpath.normpath(posixpath.join(base_url_rel, target_prefix)) if base_url_rel else target_prefix
            while resolved.startswith("./"):
                resolved = resolved[2:]
            if resolved == ".":
                resolved = ""
            if resolved and not resolved.endswith("/"):
                resolved += "/"

            aliases.append((alias_prefix, resolved))

        aliases.sort(key=lambda x: len(x[0]), reverse=True)
        return aliases

    def _discover_tsconfigs(self) -> dict[str, list[tuple[str, str]]]:
        """Find all tsconfig.json/jsconfig.json and extract path aliases.

        Returns a dict mapping directory (relative posix, "" for root) to alias list.
        """
        if self._tsconfig_map is not None:
            return self._tsconfig_map

        self._tsconfig_map = {}
        if not self._repo_path:
            return self._tsconfig_map

        repo_root = self._repo_path.resolve()

        if self._tsconfig_override:
            override_path = (repo_root / self._tsconfig_override).resolve()
            if override_path.exists():
                aliases = self._parse_tsconfig_paths(override_path)
                if aliases:
                    self._tsconfig_map[""] = aliases
                    log.info("Loaded %d aliases from --tsconfig %s", len(aliases), self._tsconfig_override)
            return self._tsconfig_map

        for config_name in ("tsconfig.json", "jsconfig.json"):
            for config_path in repo_root.rglob(config_name):
                rel = config_path.relative_to(repo_root)
                if any(p in _SKIP_DIRS for p in rel.parent.parts):
                    continue
                rel_dir = rel.parent.as_posix()
                if rel_dir == ".":
                    rel_dir = ""
                if config_name == "jsconfig.json" and rel_dir in self._tsconfig_map:
                    continue
                aliases = self._parse_tsconfig_paths(config_path)
                if aliases:
                    self._tsconfig_map[rel_dir] = aliases

        total = sum(len(v) for v in self._tsconfig_map.values())
        log.info("Discovered %d tsconfigs with %d total aliases", len(self._tsconfig_map), total)
        return self._tsconfig_map

    def _find_nearest_tsconfig(self, file_path: str) -> list[tuple[str, str]] | None:
        """Find aliases from the nearest ancestor tsconfig for a file."""
        tsconfig_map = self._discover_tsconfigs()
        if not tsconfig_map:
            return None

        dir_path = posixpath.dirname(file_path)
        checked: set[str] = set()
        while dir_path not in checked:
            checked.add(dir_path)
            if dir_path in tsconfig_map:
                return tsconfig_map[dir_path]
            dir_path = posixpath.dirname(dir_path)

        return None

    def _resolve_ts_alias(self, module_path: str, importer_path: str, path_set: set[str]) -> str | None:
        """Resolve a TypeScript path alias to an internal file."""
        aliases = self._find_nearest_tsconfig(importer_path)
        if not aliases:
            return None

        for alias_prefix, target_dir in aliases:
            if not module_path.startswith(alias_prefix):
                continue
            rest = module_path[len(alias_prefix):]
            base_path = target_dir + rest

            for ext in ("", ".ts", ".tsx", ".js", ".jsx"):
                candidate = (base_path + ext) if ext else base_path
                if candidate in path_set:
                    return candidate

            for index in ("/index.ts", "/index.tsx", "/index.js", "/index.jsx"):
                candidate = base_path + index
                if candidate in path_set:
                    return candidate

        return None

    # ------------------------------------------------------------------
    # Import resolution
    # ------------------------------------------------------------------

    def _resolve_import(
        self,
        module_path: str,
        importer_path: str,
        path_set: set[str],
        stem_map: dict[str, list[str]],
        language: str,
    ) -> str | None:
        """Best-effort resolve of an import to a known file path."""
        if not module_path:
            return None

        importer_dir = Path(importer_path).parent

        # --- Python ---
        if language == "python":
            if module_path.startswith("."):
                dots = len(module_path) - len(module_path.lstrip("."))
                rest = module_path[dots:].replace(".", "/")
                base = importer_dir
                for _ in range(dots - 1):
                    base = base.parent
                candidates = [
                    (base / rest).with_suffix(".py").as_posix() if rest else None,
                    (base / rest / "__init__.py").as_posix() if rest else None,
                ]
                for c in candidates:
                    if c and c in path_set:
                        return c
                return None
            dotted = module_path.replace(".", "/")
            candidates = [
                f"{dotted}.py",
                f"{dotted}/__init__.py",
            ]
            for c in candidates:
                if c in path_set:
                    return c
            stem = module_path.split(".")[-1].lower()
            candidates = stem_map.get(stem)
            return self._best_stem_match(candidates, importer_path) if candidates else None

        # --- TypeScript / JavaScript ---
        if language in ("typescript", "javascript"):
            if module_path.startswith("."):
                base = importer_dir / module_path
                # Normalize to resolve .. segments (critical for ../foo imports)
                base_posix = posixpath.normpath(base.as_posix())
                # Try direct file with extensions
                for ext in (".ts", ".tsx", ".js", ".jsx"):
                    candidate = base_posix + ext
                    if candidate in path_set:
                        return candidate
                # Try index files in directory
                for index in ("/index.ts", "/index.tsx", "/index.js", "/index.jsx"):
                    candidate = base_posix + index
                    if candidate in path_set:
                        return candidate
                # Try exact match (extensionless or already has extension)
                if base_posix in path_set:
                    return base_posix
                return None  # Relative import that can't resolve -- don't create external node
            else:
                # Try tsconfig/jsconfig path alias resolution
                alias_resolved = self._resolve_ts_alias(module_path, importer_path, path_set)
                if alias_resolved:
                    return alias_resolved
                # External npm package
                external_key = f"external:{module_path}"
                if external_key not in self._graph.nodes:
                    self._graph.add_node(
                        external_key, language="external", symbol_count=0, has_error=False
                    )
                return external_key

        # --- Go ---
        if language == "go":
            stem = module_path.rsplit("/", 1)[-1].lower()
            candidates = stem_map.get(stem)
            return self._best_stem_match(candidates, importer_path) if candidates else None

        # --- C / C++ ---
        if language in ("cpp", "c"):
            # Try relative to the importer's directory
            repo_root = self._repo_path.resolve() if self._repo_path else None
            if repo_root:
                try:
                    rel = (importer_dir / module_path).resolve().relative_to(repo_root).as_posix()
                    if rel in path_set:
                        return rel
                except ValueError:
                    pass
            stem = Path(module_path).stem.lower()
            candidates = stem_map.get(stem)
            return self._best_stem_match(candidates, importer_path) if candidates else None

        # --- Generic fallback: stem matching ---
        stem = Path(module_path).stem.lower()
        candidates = stem_map.get(stem)
        return self._best_stem_match(candidates, importer_path) if candidates else None
