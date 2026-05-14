# beads-py — `bd`

Distributed graph issue tracker for AI agents — Python port of [beads](https://github.com/gastownhall/beads).

**Key difference from upstream:** SQLite instead of Dolt. Zero native dependencies, single-file database, `pip install` ready.

## Quick Start

```bash
# Install
pip install beads-py

# Or from source
cd beads-py && pip install -e .

# Initialize in your project
cd your-project
bd init

# Create your first issue
bd create "Set up CI pipeline" -p 1 -t task

# See ready work
bd ready

# Agent workflow context
bd prime
```

## Features

- **SQLite-powered** — single `.beads/beads.db` file, zero external deps
- **Agent-optimized** — `--json` on every command, `bd prime` for context injection
- **Dependency graph** — `blocks`, `related`, `parent_child`, `discovered_from`, `supersedes`, `duplicates`
- **Ready work detection** — auto-finds unblocked tasks
- **Hash-based IDs** — `bd-xxxx` format, collision-tolerant
- **Compaction** — `bd compact` summarizes old closed issues into memories
- **JSONL import/export** — compatible with upstream beads format
- **Git integration** — auto-discovers repo root, installs post-commit hooks

## Essential Commands

| Command | Action |
|---------|--------|
| `bd init` | Initialize in current project |
| `bd ready` | List tasks with no open blockers |
| `bd create "Title" -p 0` | Create a P0 task |
| `bd update <id> --claim` | Atomically claim a task |
| `bd dep add <child> <parent>` | Link tasks |
| `bd show <id>` | View task details |
| `bd close <id> "Done"` | Close a task |
| `bd prime` | Print agent workflow context |
| `bd remember "insight"` | Store persistent memory |
| `bd stats` | Project statistics |
| `bd backup export` | Export JSONL |
| `bd compact` | Summarize old closed tasks |

## For AI Agents

This project uses `bd` (beads) for issue tracking.

- Run `bd prime` for workflow context and command guidance.
- Use `bd ready`, `bd show <id>`, `bd update <id> --claim`, and `bd close <id>`.
- Use `bd remember "insight"` for persistent project memory; do not create MEMORY.md files.
- Do not use markdown TODO lists for task tracking.

## Storage

Everything lives in `.beads/`:

```
.beads/
├── beads.db       # SQLite database (all data)
└── issues.jsonl   # Auto-export (compatible with upstream beads)
```

## License

MIT
