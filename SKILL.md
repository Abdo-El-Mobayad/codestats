# CodeStats -- Code Intelligence CLI

> Copy this file to `.claude/skills/codestats/SKILL.md` in any project to give Claude Code access to codestats capabilities.

## Installation Check

```bash
codestats --version
```

If not found, install:

```bash
pip install cf-codestats
```

If pip can't resolve the package (mirror lag, restricted index, offline), install directly from the source repo:

```bash
pip install git+https://github.com/Abdo-El-Mobayad/codestats.git
```

Requires Python 3.10+ and git. The PyPI package is `cf-codestats` (not `codestats`). The CLI command is `codestats`. Source: https://github.com/Abdo-El-Mobayad/codestats

## What CodeStats Does

CodeStats builds a dependency graph from tree-sitter parsing and git history, then exposes 14 commands for codebase analysis. Everything runs locally, no API keys, no cloud.

**Core capabilities:**

- Incremental indexing (full build once, then only changed files)
- Dead code detection with framework-aware filtering
- Bidirectional impact analysis with risk scoring
- Execution flow tracing with criticality scoring
- Community detection (Louvain) with architecture coupling warnings
- Circular dependency detection
- Full-text symbol search (FTS5)
- Interactive D3.js visualization
- Refactoring tools (rename preview, move suggestions, large function finder)
- Architecture wiki generation

## Workflows

### Workflow 1: First-Time Codebase Analysis

Run this sequence to get a complete picture of a new codebase:

```bash
codestats init                          # Build the index (8-step pipeline)
codestats status                        # Quick health overview
codestats communities --coupling        # How is the code organized? Any tight coupling?
codestats flows                         # What are the critical execution paths?
codestats dead-code                     # What can be cleaned up?
codestats diagram                       # Open interactive architecture visualization
```

### Workflow 2: Before Changing a File

Always check impact before modifying a file:

```bash
codestats risk <file>                   # Blast radius, centrality, test coverage, git stats
codestats impact <file> --depth 3       # What else breaks? Risk-scored per impacted file
```

**Decision guide:**

- Importer count > 20: make changes backward-compatible or add a new interface first
- Risk level CRITICAL: get review, add tests for impacted files before changing
- No test coverage shown: write tests before refactoring

### Workflow 3: Pre-Commit Review

Check what your uncommitted changes affect:

```bash
codestats init                          # Incremental re-index (picks up your changes)
codestats impact --changed              # Auto-detects git changes, shows blast radius
codestats dead-code                     # Did your changes create orphaned files?
```

### Workflow 4: Periodic Cleanup

```bash
codestats init                          # Re-index
codestats dead-code --min-confidence 0.7  # High-confidence dead files
codestats refactor large --threshold 50   # Functions that should be split
codestats refactor moves                  # Files in the wrong module
codestats cycles                          # Circular dependency chains to break
```

### Workflow 5: Architecture Documentation

```bash
codestats diagram                       # Interactive D3.js graph (opens in browser)
codestats wiki --output docs/architecture  # Generate markdown wiki
codestats communities                   # Module breakdown with cohesion scores
codestats flows                         # Critical paths through the codebase
```

### Workflow 6: Investigating a Bug

When a bug could originate from multiple files:

```bash
codestats search "auth"                 # Find all auth-related symbols
codestats risk <suspect-file>           # Check its coupling and churn
codestats deps <file-a> <file-b>        # How are these files connected?
codestats flow <entry-point>            # Trace the execution path
```

## Command Reference

### `codestats init [PATH]`

8-step indexing pipeline. Incremental by default (only re-processes changed files after first build).

```bash
codestats init                    # Index current directory (incremental if DB exists)
codestats init --force            # Force full rebuild
codestats init --tsconfig apps/web/tsconfig.json  # Specify tsconfig for alias resolution
codestats init --verbose          # Show per-file progress
```

**Pipeline steps:** file traversal, tree-sitter parsing, graph construction, git analytics, community detection, flow tracing, search indexing, dead code detection.

**Timing:** Full build ~27s for 600 files. Incremental <2s for 1-5 changed files. Git analytics dominates full build time.

### `codestats dead-code [PATH]`

Files with zero importers, excluding framework entry points.

```bash
codestats dead-code                     # Standard report
codestats dead-code --min-confidence 0.7  # High-confidence only
codestats dead-code --include-moves     # Also show misplaced file suggestions
codestats dead-code --json              # Machine-readable output
```

**Kinds:** `unreachable_file` (zero importers), `zombie_package` (unused monorepo package), `misplaced_file` (structurally misplaced, with `--include-moves`).

**Confidence:** 1.0 = old file, no recent commits. 0.7 = no 90-day activity. 0.4 = recently active but still unreachable.

### `codestats risk FILE [PATH]`

Blast radius, centrality, git stats, test coverage, and co-change partners for one file.

```bash
codestats risk src/api/auth.py
codestats risk src/api/auth.py --json
```

**Output includes:** language, symbol count, entry point status, PageRank, betweenness centrality, importer list, dependency list, test coverage (TESTED_BY edges), git analytics (commits, churn, hotspot score, bus factor, primary owner), co-change partners.

### `codestats impact FILE... [--path PATH]`

Bidirectional BFS impact analysis. Walks both forward (what this file depends on) and backward (what depends on this file).

```bash
codestats impact src/api/auth.py                    # Single file
codestats impact src/api/auth.py src/api/routes.py  # Multiple files
codestats impact --changed                          # Auto-detect from git diff
codestats impact src/api/auth.py --depth 5          # Deeper traversal (default: 3)
codestats impact --changed --json                   # Machine-readable
```

**Risk scoring (per impacted file):** 7-factor additive model -- caller count, no test coverage (heaviest: 0.30), community crossing, hotspot status, low bus factor, high centrality, security-sensitive name.

**Risk levels:** LOW (<0.3), MEDIUM (0.3-0.5), HIGH (0.5-0.7), CRITICAL (>0.7).

### `codestats communities [PATH]`

Louvain community detection. Groups files into logical modules.

```bash
codestats communities                   # List all communities
codestats communities --coupling        # Also show architecture coupling warnings
codestats communities --json
```

**Cohesion:** internal_edges / (internal + external). Higher = more self-contained module.

**Coupling warnings:** Community pairs with >10 cross-edges = "high coupling", >5 = "moderate".

### `codestats cycles [PATH]`

Detect circular dependency chains (strongly connected components).

```bash
codestats cycles                        # All cycles (default min-size: 2)
codestats cycles --min-size 3           # Only larger cycles
codestats cycles --json
```

### `codestats flows [PATH]`

Execution flows traced from entry points through the import graph, sorted by criticality.

```bash
codestats flows                         # List all flows
codestats flows --json
```

**Criticality (0.0-1.0):** Weighted by file spread (0.30), external calls (0.20), security-sensitive names (0.25), test coverage gap (0.15), BFS depth (0.10).

### `codestats flow ENTRY [PATH]`

Detailed view of a single execution flow. Accepts entry point path or flow ID (substring match).

```bash
codestats flow src/api/routes.py
codestats flow a1b2c3d4                 # By flow ID prefix
codestats flow src/api/routes.py --json
```

### `codestats search QUERY [PATH]`

FTS5 full-text search across all indexed symbols (functions, classes, methods, interfaces).

```bash
codestats search "parse"                # Search all symbols
codestats search "parse" --kind function  # Filter by kind
codestats search "Auth" --limit 5       # Limit results
codestats search "parse" --json
```

**Smart boosting:** PascalCase queries boost class/interface results. snake_case queries boost function results.

### `codestats deps FROM TO [PATH]`

Shortest import path between two files (BFS).

```bash
codestats deps src/api/auth.py src/db/models.py
codestats deps src/api/auth.py src/db/models.py --json
```

### `codestats diagram [PATH]`

Architecture visualization. Default: interactive D3.js HTML opened in browser.

```bash
codestats diagram                       # D3.js interactive (opens browser)
codestats diagram --format mermaid      # Mermaid flowchart to stdout
codestats diagram --max-nodes 30        # Limit node count
codestats diagram --output arch.html    # Custom output path
codestats diagram --json                # Raw nodes + edges
```

**D3.js features:** Dark theme, force-directed layout, community coloring toggle, edge type styling (solid=imports, dashed=tested_by), search bar, click-to-inspect detail panel, zoom/pan.

### `codestats refactor {large|moves|rename}`

Refactoring analysis tools.

```bash
codestats refactor large                  # Functions over 50 lines
codestats refactor large --threshold 30   # Custom threshold
codestats refactor moves                  # Files in the wrong community
codestats refactor rename old_name new_name  # Preview rename impact
codestats refactor large --json           # All support --json
```

### `codestats wiki [PATH]`

Generate markdown architecture wiki from community structure and flow data.

```bash
codestats wiki                            # Output to .codestats/wiki/
codestats wiki --output docs/architecture # Custom output directory
codestats wiki --json                     # List generated files
```

**Generates:** `index.md` (overview + community table), per-community pages (members, key files), `flows.md` (execution flow table).

### `codestats status [PATH]`

Summary of last index.

```bash
codestats status
codestats status --json
```

## Key Concepts

### Incremental Indexing

After the first full build, `codestats init` auto-detects changed files via git diff and content hash comparison. Only changed files + their dependents (up to 2 hops on the import graph) are re-processed. Git analytics runs only on changed files, merging with cached metadata for unchanged files.

### TESTED_BY Edges

When a test file imports a production file, CodeStats creates a reverse TESTED_BY edge from production to test. This enables:

- `risk` command showing which tests cover a file
- Impact analysis factoring test coverage into risk scores
- Flow tracing identifying untested critical paths

### Risk Scoring Model

Impact analysis scores each impacted file on 7 additive factors (capped at 1.0):

| Factor               | Max Weight | Trigger                                |
| -------------------- | ---------- | -------------------------------------- |
| No test coverage     | 0.30       | No TESTED_BY edges                     |
| Community crossing   | 0.15       | Callers from different communities     |
| Hotspot (high churn) | 0.15       | is_hotspot flag from git analytics     |
| Caller count         | 0.10       | in_degree / 20                         |
| Low bus factor       | 0.10       | bus_factor <= 1                        |
| High centrality      | 0.10       | PageRank in top 10%                    |
| Security sensitivity | 0.10       | Name contains auth/token/password/etc. |

### Criticality Scoring

Flow criticality scores each execution path on 5 weighted factors:

| Factor               | Weight | Normalization                |
| -------------------- | ------ | ---------------------------- |
| File spread          | 0.30   | 0 at 1 file, 1.0 at 5+       |
| Security sensitivity | 0.25   | keyword hits / node count    |
| External calls       | 0.20   | 0 at 0 external, 1.0 at 5+   |
| Test coverage gap    | 0.15   | 1.0 - (tested nodes / total) |
| BFS depth            | 0.10   | depth / 10, capped at 1.0    |

### Community Detection

Louvain algorithm on the undirected import graph. Cohesion = internal_edges / (internal + external). Architecture coupling = cross-community edge count between pairs.

## Storage

All data stored at `~/.codestats/projects/<repo-name>/graph.db` (SQLite). Tables: `meta`, `graph_nodes`, `graph_edges`, `git_metadata`, `dead_code`, `flows`, `flow_members`, `communities`, `community_members`, `symbols_fts`, `refactor_previews`.

No files are written to the project directory.

## Supported Languages

**Full AST parsing (19+):** TypeScript, JavaScript, TSX, JSX, Python, Go, Rust, Java, C, C++, Ruby, Kotlin, Scala, C#, PHP, Swift, Lua, R, Elixir, Haskell, OCaml

**Special files:** Vue SFCs (`.vue`), Jupyter Notebooks (`.ipynb`)

**Traversed but not parsed:** YAML, JSON, TOML, Markdown, SQL, Shell, Terraform, Proto, GraphQL, Dockerfile, Makefile

## Troubleshooting

**Results seem stale:** Run `codestats init` to re-index. Incremental mode is fast.

**"Project not indexed yet":** Run `codestats init` first.

**Monorepo aliases not resolving:** CodeStats auto-discovers all tsconfig.json files. If aliases still fail, use `--tsconfig apps/web/tsconfig.json`.

**Slow full build:** Git analytics dominates. Incremental mode avoids this after the first build.

**Want to start fresh:** Use `codestats init --force` for a full rebuild.
