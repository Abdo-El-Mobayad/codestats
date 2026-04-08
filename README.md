# CodeStats

Code intelligence CLI -- dead code detection, blast radius analysis, dependency tracing, and architecture diagrams. All from the command line, all stored locally.

Built by [ClaudeFast](https://claudefa.st). Graph engine extracted and adapted from [Repowise](https://github.com/repowise-dev/repowise).

## What It Does

CodeStats indexes your codebase using tree-sitter parsing and git history, then stores a dependency graph in a local SQLite database. From that graph, it can answer four questions:

1. **Dead code** -- Which files are never imported by anything? (With framework-aware filtering so Next.js pages, routes, and layouts are not flagged.)
2. **Blast radius** -- How many files depend on a given file? What breaks if you change it?
3. **Dependency paths** -- What is the import chain between two files?
4. **Architecture diagrams** -- What does the dependency structure look like? (Mermaid output.)

## Installation

```bash
pip install codestats
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

# Check how risky it is to change a file
codestats risk lib/db/registry.ts

# Trace the import chain between two files
codestats deps lib/sync/scheduler.ts lib/cache/invalidation.ts

# Generate a Mermaid architecture diagram
codestats diagram

# View a summary of the last index
codestats status
```

## Command Reference

### `codestats init [PATH]`

Index the project at PATH (defaults to current directory). Traverses files, parses imports with tree-sitter, builds a dependency graph with NetworkX, mines git history, and detects dead code. Results are stored at `~/.codestats/projects/<repo-name>/graph.db`.

```
$ codestats init
Indexing project: quill (D:\Github\quill)
  [1/5] Traversing files...
         Found 400 files
  [2/5] Parsing with tree-sitter...
         Parsed 400 files (2847 symbols, 1891 imports)
  [3/5] Building dependency graph...
         400 nodes, 1033 internal edges, 858 external edges
  [4/5] Running git analytics...
         400 files, 99 hotspots, 42.3s
  [5/5] Detecting dead code...
         71 findings

Done in 178.4s. Database saved to C:\Users\You\.codestats\projects\quill\graph.db
```

**Flags:**

| Flag              | Description                            |
| ----------------- | -------------------------------------- |
| `--verbose`, `-v` | Show detailed progress during indexing |

### `codestats dead-code [PATH]`

List files that have zero importers and are not framework entry points. Useful for finding cleanup opportunities.

```
$ codestats dead-code
Dead code findings: 71

File                                                         Kind                  Conf Safe?
-----------------------------------------------------------------------------------------------
components/ui/collapsible.tsx                                unreachable_file      0.85   Yes
lib/stores/dashboard-store.ts                                unreachable_file      0.80   Yes
components/productivity/shared/time-range-selector.tsx       unreachable_file      0.75    No
...
```

Next.js pages (`app/**/page.tsx`), routes (`app/**/route.ts`), layouts, middleware, and config files are automatically excluded. Files in `.claude/` directories are also excluded from indexing.

**Flags:**

| Flag                     | Description                                 |
| ------------------------ | ------------------------------------------- |
| `--json`                 | Output as JSON                              |
| `--min-confidence FLOAT` | Minimum confidence threshold (default: 0.4) |

### `codestats risk FILE [PATH]`

Show blast radius and risk analysis for a specific file. Displays importer count, dependencies, PageRank centrality, git churn, hotspot status, and co-change partners.

```
$ codestats risk lib/db/registry.ts
Risk Analysis: lib/db/registry.ts

  Language:       typescript
  Symbols:        12
  Entry Point:    No
  Test File:      No
  PageRank:       0.008432
  Betweenness:    0.045210

  Importers (45):
    <- lib/sync/airtable.ts
    <- lib/sync/scheduler.ts
    <- lib/sync/exchange-rates.ts
    <- app/api/cli/[...path]/route.ts
    ... and 41 more

  Dependencies (3):
    -> lib/helpers/url-helpers.ts
    -> lib/cache/invalidation.ts
    -> codestats/external:pg

  Git Analytics:
    Commits (total):  18
    Commits (90d):    5
    Last commit:      2026-04-01T14:30:00
    Hotspot:          Yes (score: 0.72)
    Primary owner:    Abdo
    Bus factor:       1
```

**Flags:**

| Flag     | Description    |
| -------- | -------------- |
| `--json` | Output as JSON |

### `codestats deps FROM TO [PATH]`

Find the shortest dependency path between two files using BFS on the import graph.

```
$ codestats deps lib/sync/scheduler.ts lib/cache/invalidation.ts
Dependency path (1 hops):

  lib/sync/scheduler.ts
  -> lib/cache/invalidation.ts
```

```
$ codestats deps components/productivity/tasks/views/kanban-view.tsx lib/db/registry.ts
Dependency path (2 hops):

  components/productivity/tasks/views/kanban-view.tsx
  -> lib/sync/airtable.ts
  -> lib/db/registry.ts
```

**Flags:**

| Flag     | Description    |
| -------- | -------------- |
| `--json` | Output as JSON |

### `codestats diagram [PATH]`

Generate a Mermaid flowchart of the dependency graph. Outputs to stdout so you can pipe it to a file or clipboard. Nodes are ranked by PageRank -- the most central files appear first.

```
$ codestats diagram --max-nodes 20
flowchart LR
    n0["lib/utils.ts"]
    n1["lib/db/registry.ts"]
    n2["lib/cache/invalidation.ts"]
    ...
    n0 --> n2
    n1 --> n2
    ...
```

**Flags:**

| Flag              | Description                             |
| ----------------- | --------------------------------------- |
| `--max-nodes INT` | Maximum nodes to include (default: 100) |
| `--json`          | Output as JSON (nodes and edges arrays) |

### `codestats status [PATH]`

Show a summary of the last index.

```
$ codestats status
CodeStats Status: quill

  Repository:     D:\Github\quill
  Indexed at:     2026-04-08T03:32:00
  Files:          400
  Internal edges: 1033
  Hotspots:       99
  Dead code:      71
  Database:       C:\Users\You\.codestats\projects\quill\graph.db (392.0 KB)
```

**Flags:**

| Flag     | Description    |
| -------- | -------------- |
| `--json` | Output as JSON |

## How It Works

1. **File traversal** -- Walks the repo respecting `.gitignore` patterns. Skips `node_modules`, `.git`, `.claude`, and other non-code directories.
2. **Tree-sitter parsing** -- Parses each file using language-specific tree-sitter grammars to extract imports and symbol definitions. Supports TypeScript, JavaScript, Python, Go, Rust, Java, C, C++, Kotlin, and Ruby.
3. **Graph construction** -- Builds a directed dependency graph with NetworkX. Resolves `@/` path aliases from `tsconfig.json`, relative imports, and package imports. Computes PageRank and betweenness centrality.
4. **Git analytics** -- Mines `git log` for commit counts, churn (lines added/deleted in the last 90 days), temporal hotspot scoring with decay, co-change detection, primary ownership, and bus factor.
5. **Dead code detection** -- Identifies files with zero importers. Filters out framework entry points (Next.js pages, routes, layouts), test files, config files, and other known non-importable patterns.
6. **SQLite persistence** -- Everything is stored in a single SQLite database at `~/.codestats/projects/<repo>/graph.db`. No files are written to the project directory.

## Storage

All data lives outside your project at:

```
~/.codestats/projects/<repo-name>/graph.db
```

The repo name is auto-detected from the git remote URL. If no remote is configured, the directory name is used.

No files are added to your project directory. The only project-level artifact, if you choose to use it, is a Claude Code SKILL.md for AI-assisted development.

## Supported Languages

TypeScript, JavaScript, Python, Go, Rust, Java, C, C++, Kotlin, Ruby.

## Attribution

The graph engine (AST parsing, graph construction, dead code analysis) is extracted and adapted from [Repowise](https://github.com/repowise-dev/repowise) by the Repowise team. CodeStats adds framework-aware entry point detection, global storage, the CLI interface, and TypeScript path alias resolution fixes.

## License

MIT. See [LICENSE](LICENSE).
