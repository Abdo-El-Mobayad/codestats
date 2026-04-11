# CodeStats

Code intelligence CLI -- dead code detection, impact analysis, execution flow tracing, community detection, architecture visualization, and more. All from the command line, all stored locally.

Built by [ClaudeFast](https://claudefa.st).

## What It Does

CodeStats indexes your codebase using tree-sitter parsing and git history, then stores a dependency graph in a local SQLite database. From that graph, it answers questions like:

- **What's dead?** -- Files never imported by anything, with framework-aware filtering.
- **What breaks if I change this?** -- Bidirectional impact analysis with risk scoring.
- **What are the critical paths?** -- Execution flow tracing from entry points with criticality scoring.
- **How is the codebase organized?** -- Community detection, architecture coupling warnings, circular dependency detection.
- **Where is this symbol?** -- Full-text search across all functions, classes, and methods.
- **What should I refactor?** -- Large function detection, misplaced file suggestions, rename previews.

## Installation

```bash
pip install cf-codestats
```

Or install from source:

```bash
pip install git+https://github.com/Abdo-El-Mobayad/codestats.git
```

Requires Python 3.10+ and git.

## Quick Start

```bash
# Index your project (run from the repo root)
codestats init

# Find unused files
codestats dead-code

# What breaks if I change this file?
codestats impact src/api/auth.py

# What are the riskiest execution flows?
codestats flows

# How is the codebase organized?
codestats communities

# Search for a symbol
codestats search "parse"

# Interactive architecture visualization
codestats diagram

# Generate a markdown wiki
codestats wiki
```

## Features

### Incremental Indexing

After the first full index, subsequent runs only re-process changed files and their dependents (up to 2 hops). A 600-file repo re-indexes in under 2 seconds when a few files change.

```
$ codestats init
Updating project: my-app (~/projects/my-app)
  Detected 2 added, 1 modified, 0 deleted, 3 dependents
  [1/8] Traversing files...
         Scanning 6 files (594 cached)
  ...
Done in 1.3s (incremental).
```

Use `--force` to trigger a full rebuild.

### Impact Analysis

Bidirectional BFS finds everything affected by a change -- both what depends on the file (backwards) and what the file depends on (forwards). Each impacted file gets a risk score based on test coverage, bus factor, centrality, hotspot status, and security sensitivity.

```
$ codestats impact src/api/auth.py
Impact Analysis

  Changed files:    src/api/auth.py
  Total impacted:   8
  Max risk:         0.65
  Risk level:       HIGH

  Impacted files:
    src/api/routes.py          depth=1  risk=0.45  MEDIUM
    src/middleware/session.py   depth=1  risk=0.70  CRITICAL  [no tests, bus_factor=1]
    ...
```

Use `--changed` to auto-detect modified files from git.

### Execution Flows

Traces paths from entry points through the import graph. Each flow gets a criticality score based on file spread, security sensitivity, test coverage gaps, and depth.

```
$ codestats flows
Execution Flows (12)

ID         Entry Point                        Nodes Spread   Crit Tests
------------------------------------------------------------------------
a1b2c3d4   src/api/routes.py                     8      5  0.675    No
e5f6g7h8   src/workers/scheduler.py              6      4  0.520    No
...
```

### Community Detection

Louvain algorithm groups files into logical modules. Architecture coupling warnings flag communities that are too tightly connected.

```
$ codestats communities --coupling
Communities in my-app

  ID  Name              Members Cohesion
  ----------------------------------------
   0  src/api                 8    0.750
   1  src/db                  5    0.800
   2  src/workers             4    0.600
   ...

Architecture Coupling:
  src/api <-> src/db: 12 cross-edges (high coupling)
```

### Full-Text Search

FTS5-powered search across all indexed symbols with porter stemming and smart boosting (PascalCase queries boost class results, snake_case boosts functions).

```
$ codestats search "parse"
Search results for 'parse' (6 matches)

  Name                                     Kind         File
  -----------------------------------------------------------------
  parser.ASTParser.parse_file              method       src/parser.py
  parser.parse_file                        function     src/parser.py
  ...
```

### Interactive Visualization

D3.js force-directed graph with dark theme, community coloring, search, click-to-inspect, and zoom/pan. Falls back to Mermaid for terminal output.

```bash
codestats diagram                    # Interactive D3.js HTML (opens in browser)
codestats diagram --format mermaid   # Mermaid flowchart to stdout
```

### Refactoring Tools

```bash
codestats refactor large              # Find functions over 50 lines
codestats refactor moves              # Files that might be in the wrong module
codestats refactor rename old new     # Preview a rename across all references
```

### Dead Code Detection

Files with zero importers, excluding framework entry points. Enhanced with community-aware move suggestions for files that are technically used but structurally misplaced.

```bash
codestats dead-code                   # Standard dead code report
codestats dead-code --include-moves   # Also show misplaced file suggestions
```

### Additional Commands

```bash
codestats risk FILE         # Blast radius, git analytics, test coverage for one file
codestats deps FROM TO      # Shortest import path between two files
codestats cycles            # Detect circular dependency chains
codestats flow ENTRY        # Detailed view of a single execution flow
codestats wiki              # Generate markdown architecture wiki
codestats status            # Summary of last index
```

## Command Reference

| Command                             | Description                                                                  |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| `codestats init [PATH]`             | Index the project (incremental by default, `--force` for full rebuild)       |
| `codestats dead-code [PATH]`        | Find unreachable files (`--include-moves` for misplaced suggestions)         |
| `codestats risk FILE [PATH]`        | Blast radius, centrality, git stats, test coverage for one file              |
| `codestats impact FILE... [PATH]`   | Bidirectional impact analysis (`--changed` for git auto-detect, `--depth N`) |
| `codestats communities [PATH]`      | List Louvain communities (`--coupling` for architecture warnings)            |
| `codestats cycles [PATH]`           | Detect circular dependency chains (`--min-size N`)                           |
| `codestats flows [PATH]`            | List execution flows sorted by criticality                                   |
| `codestats flow ENTRY [PATH]`       | Detailed single flow with member list                                        |
| `codestats search QUERY [PATH]`     | Full-text symbol search (`--limit N`, `--kind KIND`)                         |
| `codestats deps FROM TO [PATH]`     | Shortest dependency path between two files                                   |
| `codestats diagram [PATH]`          | Architecture visualization (`--format d3\|mermaid`, `--open`)                |
| `codestats refactor large [PATH]`   | Find large functions (`--threshold N`)                                       |
| `codestats refactor moves [PATH]`   | Community-based move suggestions                                             |
| `codestats refactor rename OLD NEW` | Preview rename across all references                                         |
| `codestats wiki [PATH]`             | Generate markdown wiki (`--output DIR`)                                      |
| `codestats status [PATH]`           | Summary of last index                                                        |

All commands support `--json` for machine-readable output.

## How It Works

1. **File traversal** -- Walks the repo respecting `.gitignore`. Skips `node_modules`, `.git`, `.claude`, vendor directories, binary files, and generated code.
2. **Tree-sitter parsing** -- Extracts imports and symbols using language-specific grammars. Supports 19+ languages via tree-sitter-language-pack. Vue SFCs and Jupyter notebooks get special handling.
3. **Graph construction** -- Builds a directed dependency graph with NetworkX. Resolves tsconfig path aliases (monorepo-aware), relative imports, and package imports. Computes PageRank and betweenness centrality. Creates reverse TESTED_BY edges linking production files to their test files.
4. **Git analytics** -- Mines `git log` for commit counts, churn, temporal hotspot scoring with exponential decay, co-change detection, primary ownership, and bus factor.
5. **Community detection** -- Louvain algorithm clusters files into logical modules. Computes cohesion scores and architecture coupling warnings.
6. **Flow tracing** -- Discovers entry points (framework routes, main files, root nodes) and traces execution flows forward through the import graph. Scores each flow by criticality.
7. **Search indexing** -- Builds an FTS5 full-text index across all symbols with porter stemming.
8. **Dead code detection** -- Identifies files with zero importers. Filters out framework entry points, test files, config files, and known non-importable patterns.
9. **SQLite persistence** -- Everything stored in `~/.codestats/projects/<repo>/graph.db`. No files written to the project directory.

## Supported Languages

**Full AST parsing:** TypeScript, JavaScript (including TSX/JSX), Python, Go, Rust, Java, C, C++, Ruby, Kotlin, Scala, C#, PHP, Swift, Lua, R, Elixir, Haskell, OCaml

**Special file types:** Vue Single File Components (`.vue`), Jupyter Notebooks (`.ipynb`)

**Traversed but not parsed:** YAML, JSON, TOML, Markdown, SQL, Shell, Terraform, Proto, GraphQL, Dockerfile, Makefile

## Storage

All data lives outside your project at:

```
~/.codestats/projects/<repo-name>/graph.db
```

The repo name is auto-detected from the git remote URL. Nothing is added to your project directory.

## Performance

| Repo size | Full index | Incremental (1-5 files changed) |
| --------- | ---------- | ------------------------------- |
| 15 files  | 1.2s       | <0.5s                           |
| 600 files | 27s        | 1-4s                            |
| 900 files | 37s        | 2-5s                            |

Git analytics (commit history mining) dominates full index time. Incremental mode skips unchanged files entirely.

## Attribution

The original graph engine (AST parsing, graph construction, dead code analysis) was extracted and adapted from [Repowise](https://github.com/repowise-dev/repowise). v0.4 incorporates design patterns from [code-review-graph](https://github.com/tirth8205/code-review-graph) including impact analysis, flow tracing, community detection exposure, and incremental indexing.

## License

MIT. See [LICENSE](LICENSE).
