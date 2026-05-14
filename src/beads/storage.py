"""SQLite storage backend for beads-py.

Single-file database at .beads/beads.db. Auto-migrates on open.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from beads.models import (
    Dependency,
    DependencyKind,
    Issue,
    IssueMinimal,
    IssueStatus,
    IssueType,
    Memory,
    ProjectStats,
)


SCHEMA_VERSION = 1

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS issues (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    description TEXT DEFAULT '',
    status      TEXT DEFAULT 'open',
    priority    INTEGER DEFAULT 2,
    issue_type  TEXT DEFAULT 'task',
    assignee    TEXT DEFAULT '',
    labels      TEXT DEFAULT '[]',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    closed_at   TEXT,
    closed_reason TEXT DEFAULT '',
    parent_id   TEXT,
    metadata    TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS dependencies (
    child_id    TEXT NOT NULL,
    parent_id   TEXT NOT NULL,
    dep_type    TEXT DEFAULT 'blocks',
    PRIMARY KEY (child_id, parent_id)
);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL
);
"""


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _row_to_issue(row: sqlite3.Row) -> Issue:
    """Convert a DB row to an Issue model."""
    d = dict(row)
    d["labels"] = json.loads(d.get("labels", "[]"))
    d["metadata"] = json.loads(d.get("metadata", "{}"))
    d["status"] = IssueStatus(d.get("status", "open"))
    d["issue_type"] = IssueType(d.get("issue_type", "task"))
    for ts_field in ("created_at", "updated_at"):
        if isinstance(d.get(ts_field), str):
            d[ts_field] = datetime.fromisoformat(d[ts_field])
    if d.get("closed_at") and isinstance(d["closed_at"], str):
        d["closed_at"] = datetime.fromisoformat(d["closed_at"])
    return Issue(**d)


class BeadsDB:
    """SQLite-backed storage for beads issues, dependencies, and memories."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._migrate()

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> "BeadsDB":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    # ── migrations ─────────────────────────────────────────────

    def _migrate(self) -> None:
        # Check if schema_version table exists yet
        row = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
        ).fetchone()

        if row is None:
            # Fresh database: run all DDL
            self.conn.executescript(CREATE_TABLES)
            self.conn.execute(
                "INSERT OR REPLACE INTO schema_version VALUES (?)",
                (SCHEMA_VERSION,),
            )
            self.conn.commit()
            return

        ver = self.conn.execute(
            "SELECT version FROM schema_version"
        ).fetchone()
        current = ver["version"] if ver else 0

        if current < 1:
            self.conn.executescript(CREATE_TABLES)
            self.conn.execute(
                "INSERT OR REPLACE INTO schema_version VALUES (?)",
                (SCHEMA_VERSION,),
            )
            self.conn.commit()

    # ── issues ─────────────────────────────────────────────────

    def create_issue(self, issue: Issue) -> Issue:
        self.conn.execute(
            """INSERT INTO issues (id, title, description, status, priority,
               issue_type, assignee, labels, created_at, updated_at,
               closed_at, closed_reason, parent_id, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                issue.id,
                issue.title,
                issue.description,
                issue.status.value,
                issue.priority,
                issue.issue_type.value,
                issue.assignee,
                json.dumps(issue.labels),
                issue.created_at.isoformat(),
                issue.updated_at.isoformat(),
                issue.closed_at.isoformat() if issue.closed_at else None,
                issue.closed_reason,
                issue.parent_id,
                json.dumps(issue.metadata),
            ),
        )
        self.conn.commit()
        return issue

    def get_issue(self, issue_id: str) -> Issue | None:
        row = self.conn.execute(
            "SELECT * FROM issues WHERE id = ?", (issue_id,)
        ).fetchone()
        return _row_to_issue(row) if row else None

    def update_issue(self, issue_id: str, **kwargs) -> Issue | None:
        """Update issue fields. Always bumps updated_at."""
        issue = self.get_issue(issue_id)
        if not issue:
            return None

        updatable = {
            "title", "description", "status", "priority", "issue_type",
            "assignee", "labels", "closed_at", "closed_reason",
            "parent_id", "metadata",
        }
        for k, v in kwargs.items():
            if k in updatable and hasattr(issue, k):
                setattr(issue, k, v)

        issue.updated_at = datetime.now(timezone.utc)

        self.conn.execute(
            """UPDATE issues SET title=?, description=?, status=?, priority=?,
               issue_type=?, assignee=?, labels=?, updated_at=?,
               closed_at=?, closed_reason=?, parent_id=?, metadata=?
               WHERE id=?""",
            (
                issue.title,
                issue.description,
                issue.status.value,
                issue.priority,
                issue.issue_type.value,
                issue.assignee,
                json.dumps(issue.labels),
                issue.updated_at.isoformat(),
                issue.closed_at.isoformat() if issue.closed_at else None,
                issue.closed_reason,
                issue.parent_id,
                json.dumps(issue.metadata),
                issue_id,
            ),
        )
        self.conn.commit()
        return issue

    def list_issues(
        self,
        status: str | None = None,
        priority: int | None = None,
        issue_type: str | None = None,
        assignee: str | None = None,
        labels: list[str] | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Issue]:
        clauses = []
        params: list = []

        if status:
            clauses.append("status = ?")
            params.append(status)
        if priority is not None:
            clauses.append("priority = ?")
            params.append(priority)
        if issue_type:
            clauses.append("issue_type = ?")
            params.append(issue_type)
        if assignee:
            clauses.append("assignee = ?")
            params.append(assignee)
        if labels:
            for lbl in labels:
                clauses.append("labels LIKE ?")
                params.append(f'%"{lbl}"%')

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM issues {where} ORDER BY priority ASC, created_at ASC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = self.conn.execute(sql, params).fetchall()
        return [_row_to_issue(r) for r in rows]

    def search_issues(self, query: str, limit: int = 20) -> list[Issue]:
        """Full-text search on title + description."""
        rows = self.conn.execute(
            """SELECT * FROM issues
               WHERE title LIKE ? OR description LIKE ?
               ORDER BY priority ASC, created_at ASC
               LIMIT ?""",
            (f"%{query}%", f"%{query}%", limit),
        ).fetchall()
        return [_row_to_issue(r) for r in rows]

    def close_issue(self, issue_id: str, reason: str = "") -> Issue | None:
        return self.update_issue(
            issue_id,
            status=IssueStatus.CLOSED,
            closed_at=datetime.now(timezone.utc),
            closed_reason=reason,
        )

    def claim_issue(self, issue_id: str, assignee: str = "agent") -> Issue | None:
        """Atomically claim a task (set in_progress + assignee)."""
        return self.update_issue(
            issue_id, status=IssueStatus.IN_PROGRESS, assignee=assignee
        )

    def delete_issue(self, issue_id: str) -> bool:
        """Delete an issue and its dependencies (rare, mostly for tests)."""
        self.conn.execute("DELETE FROM dependencies WHERE child_id = ? OR parent_id = ?",
                          (issue_id, issue_id))
        cur = self.conn.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ── dependencies ───────────────────────────────────────────

    def add_dependency(
        self, child_id: str, parent_id: str, dep_type: DependencyKind = DependencyKind.BLOCKS
    ) -> Dependency | None:
        # Validate both exist
        if not self.get_issue(child_id) or not self.get_issue(parent_id):
            return None
        self.conn.execute(
            "INSERT OR REPLACE INTO dependencies (child_id, parent_id, dep_type) VALUES (?, ?, ?)",
            (child_id, parent_id, dep_type.value),
        )
        self.conn.commit()
        return Dependency(child_id=child_id, parent_id=parent_id, dep_type=dep_type)

    def remove_dependency(self, child_id: str, parent_id: str) -> bool:
        cur = self.conn.execute(
            "DELETE FROM dependencies WHERE child_id = ? AND parent_id = ?",
            (child_id, parent_id),
        )
        self.conn.commit()
        return cur.rowcount > 0

    def get_blockers(self, issue_id: str) -> list[Dependency]:
        """Get all dependencies where issue_id is the child (what blocks it)."""
        rows = self.conn.execute(
            "SELECT * FROM dependencies WHERE child_id = ?",
            (issue_id,),
        ).fetchall()
        return [
            Dependency(
                child_id=r["child_id"],
                parent_id=r["parent_id"],
                dep_type=DependencyKind(r["dep_type"]),
            )
            for r in rows
        ]

    def get_blocked_by(self, issue_id: str) -> list[str]:
        """Get IDs of issues that block this one."""
        rows = self.conn.execute(
            """SELECT parent_id FROM dependencies
               WHERE child_id = ? AND dep_type = 'blocks'""",
            (issue_id,),
        ).fetchall()
        return [r["parent_id"] for r in rows]

    def get_dependents(self, issue_id: str) -> list[str]:
        """Get IDs of issues that depend on this one."""
        rows = self.conn.execute(
            """SELECT child_id FROM dependencies
               WHERE parent_id = ? AND dep_type = 'blocks'""",
            (issue_id,),
        ).fetchall()
        return [r["child_id"] for r in rows]

    # ── ready work ─────────────────────────────────────────────

    def get_ready_work(self, limit: int = 20) -> list[Issue]:
        """Issues that are open and have no open blockers."""
        rows = self.conn.execute(
            """SELECT * FROM issues
               WHERE status = 'open'
               AND id NOT IN (
                   SELECT d.child_id FROM dependencies d
                   JOIN issues i ON d.parent_id = i.id
                   WHERE i.status != 'closed'
                   AND d.dep_type = 'blocks'
               )
               ORDER BY priority ASC, created_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [_row_to_issue(r) for r in rows]

    def get_blocked_issues(self, limit: int = 10) -> list[Issue]:
        """Open issues that are blocked by at least one open issue."""
        rows = self.conn.execute(
            """SELECT DISTINCT i.* FROM issues i
               JOIN dependencies d ON i.id = d.child_id
               JOIN issues parent ON d.parent_id = parent.id
               WHERE i.status IN ('open', 'blocked')
               AND parent.status != 'closed'
               AND d.dep_type = 'blocks'
               ORDER BY i.priority ASC, i.created_at ASC
               LIMIT ?""",
            (limit,),
        ).fetchall()
        return [_row_to_issue(r) for r in rows]

    def _issue_to_minimal(self, issue: Issue) -> IssueMinimal:
        # Count dependencies
        dep_count = self.conn.execute(
            "SELECT COUNT(*) FROM dependencies WHERE child_id = ?",
            (issue.id,),
        ).fetchone()[0]
        depon_count = self.conn.execute(
            "SELECT COUNT(*) FROM dependencies WHERE parent_id = ?",
            (issue.id,),
        ).fetchone()[0]
        return IssueMinimal(
            id=issue.id,
            title=issue.title,
            status=issue.status,
            priority=issue.priority,
            issue_type=issue.issue_type,
            assignee=issue.assignee,
            labels=issue.labels,
            dependency_count=dep_count,
            dependent_count=depon_count,
        )

    # ── memories ───────────────────────────────────────────────

    def add_memory(self, content: str) -> Memory:
        now = _iso_now()
        cur = self.conn.execute(
            "INSERT INTO memories (content, created_at) VALUES (?, ?)",
            (content, now),
        )
        self.conn.commit()
        return Memory(id=cur.lastrowid, content=content,
                      created_at=datetime.fromisoformat(now))

    def get_memories(self, limit: int = 20) -> list[Memory]:
        rows = self.conn.execute(
            "SELECT * FROM memories ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [
            Memory(
                id=r["id"],
                content=r["content"],
                created_at=datetime.fromisoformat(r["created_at"]),
            )
            for r in rows
        ]

    def delete_memory(self, memory_id: int) -> bool:
        cur = self.conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        self.conn.commit()
        return cur.rowcount > 0

    # ── stats ──────────────────────────────────────────────────

    def get_stats(self) -> ProjectStats:
        rows = self.conn.execute(
            "SELECT status, issue_type, COUNT(*) as cnt FROM issues GROUP BY status, issue_type"
        ).fetchall()

        stats = ProjectStats()
        for r in rows:
            stats.total += r["cnt"]
            if r["status"] == "open":
                stats.open += r["cnt"]
            elif r["status"] == "in_progress":
                stats.in_progress += r["cnt"]
            elif r["status"] == "blocked":
                stats.blocked += r["cnt"]
            elif r["status"] == "closed":
                stats.closed += r["cnt"]
            if r["issue_type"] == "epic":
                stats.epics += r["cnt"]

        mem = self.conn.execute("SELECT COUNT(*) FROM memories").fetchone()
        stats.memories = mem[0] if mem else 0

        return stats

    # ── export / import ────────────────────────────────────────

    def export_jsonl(self) -> str:
        """Export all issues as JSONL (compatible with upstream beads format)."""
        rows = self.conn.execute("SELECT * FROM issues ORDER BY created_at ASC").fetchall()
        lines = []
        for r in rows:
            issue = _row_to_issue(r)
            record = {
                "id": issue.id,
                "title": issue.title,
                "description": issue.description,
                "status": issue.status.value,
                "priority": issue.priority,
                "type": issue.issue_type.value,
                "assignee": issue.assignee,
                "labels": issue.labels,
                "created_at": issue.created_at.isoformat(),
                "updated_at": issue.updated_at.isoformat(),
                "closed_at": issue.closed_at.isoformat() if issue.closed_at else None,
                "closed_reason": issue.closed_reason,
                "parent_id": issue.parent_id,
            }
            lines.append(json.dumps(record, default=str))
        return "\n".join(lines)
