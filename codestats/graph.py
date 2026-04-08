"""Dependency graph builder for codestats.

Extracted and adapted from Repowise (https://github.com/repowise-dev/repowise).

Constructs a directed graph from ParsedFile objects. The tsconfig @/ path alias
fix is baked in (not patched). Simplified: no co-change edges, no framework
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
from pathlib import Path
from typing import Any

import networkx as nx

from .models import ParsedFile

log = logging.getLogger(__name__)

_LARGE_REPO_THRESHOLD = 30_000


class GraphBuilder:
    """Build a dependency graph from a collection of ParsedFile objects.

    Usage::

        builder = GraphBuilder(repo_path="/path/to/repo")
        for parsed in parsed_files:
            builder.add_file(parsed)
        graph = builder.build()
        pr = builder.pagerank()
    """

    def __init__(self, repo_path: Path | str | None = None) -> None:
        self._graph: nx.DiGraph = nx.DiGraph()
        self._parsed_files: dict[str, ParsedFile] = {}
        self._built = False
        self._repo_path: Path | None = Path(repo_path) if repo_path else None
        self._tsconfig_aliases: list[tuple[str, str]] | None = None

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
        )

    def build(self) -> nx.DiGraph:
        """Resolve imports and add edges. Returns the finalized graph."""
        self._graph.remove_edges_from(list(self._graph.edges()))

        path_set = set(self._parsed_files.keys())
        stem_map: dict[str, str] = {}
        for p in path_set:
            stem = Path(p).stem.lower()
            stem_map[stem] = p

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

        self._built = True
        log.info(
            "Graph built: nodes=%d, edges=%d",
            self._graph.number_of_nodes(),
            self._graph.number_of_edges(),
        )
        return self._graph

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
    # tsconfig path alias resolution (BAKED IN)
    # ------------------------------------------------------------------

    def _load_tsconfig_aliases(self) -> list[tuple[str, str]]:
        """Load path aliases from tsconfig.json / jsconfig.json.

        Returns a list of (prefix, replacement) tuples sorted by specificity
        (longest prefix first). For example, {"@/*": ["./*"]} becomes
        [("@/", "./")].
        """
        if self._tsconfig_aliases is not None:
            return self._tsconfig_aliases

        self._tsconfig_aliases = []
        if not self._repo_path:
            return self._tsconfig_aliases

        for config_name in ("tsconfig.json", "jsconfig.json"):
            config_path = self._repo_path / config_name
            if not config_path.exists():
                continue
            try:
                with open(config_path) as f:
                    config = json.load(f)
                paths = config.get("compilerOptions", {}).get("paths", {})
                base_url = config.get("compilerOptions", {}).get("baseUrl", ".")
                for pattern, targets in paths.items():
                    if not targets or not isinstance(targets, list):
                        continue
                    if pattern.endswith("/*"):
                        alias_prefix = pattern[:-1]
                    else:
                        alias_prefix = pattern

                    target = targets[0]
                    if target.endswith("/*"):
                        target_prefix = target[:-1]
                    else:
                        target_prefix = target

                    resolved = (Path(base_url) / target_prefix).as_posix()
                    while resolved.startswith("./"):
                        resolved = resolved[2:]
                    if resolved == ".":
                        resolved = ""
                    if resolved and not resolved.endswith("/"):
                        resolved = resolved + "/"

                    self._tsconfig_aliases.append((alias_prefix, resolved))

                self._tsconfig_aliases.sort(key=lambda x: len(x[0]), reverse=True)
                log.info(
                    "Loaded tsconfig path aliases: config=%s, aliases=%d",
                    config_name,
                    len(self._tsconfig_aliases),
                )
                break
            except Exception as exc:
                log.debug("Failed to load tsconfig aliases: %s", exc)

        return self._tsconfig_aliases

    def _resolve_ts_alias(self, module_path: str, path_set: set[str]) -> str | None:
        """Resolve a TypeScript path alias to an internal file."""
        aliases = self._load_tsconfig_aliases()
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
        stem_map: dict[str, str],
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
            return stem_map.get(stem)

        # --- TypeScript / JavaScript ---
        if language in ("typescript", "javascript"):
            if module_path.startswith("."):
                base = importer_dir / module_path
                for ext in (".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"):
                    candidate = Path(str(base) + ext).as_posix()
                    if candidate in path_set:
                        return candidate
                    candidate = (
                        base.with_suffix(ext).as_posix()
                        if not ext.startswith("/")
                        else (base / "index.ts").as_posix()
                    )
                    if candidate in path_set:
                        return candidate
            else:
                # Try tsconfig/jsconfig path alias resolution
                alias_resolved = self._resolve_ts_alias(module_path, path_set)
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
            return stem_map.get(stem)

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
            return stem_map.get(stem)

        # --- Generic fallback: stem matching ---
        stem = Path(module_path).stem.lower()
        return stem_map.get(stem)
