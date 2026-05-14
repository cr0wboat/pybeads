"""Core data models for beads-py."""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class IssueStatus(str, Enum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    CLOSED = "closed"


class IssueType(str, Enum):
    TASK = "task"
    BUG = "bug"
    FEATURE = "feature"
    EPIC = "epic"
    CHORE = "chore"
    DECISION = "decision"
    MESSAGE = "message"


class DependencyKind(str, Enum):
    BLOCKS = "blocks"
    RELATED = "related"
    PARENT_CHILD = "parent_child"
    DISCOVERED_FROM = "discovered_from"
    SUPERSEDES = "supersedes"
    DUPLICATES = "duplicates"
    REPLIES_TO = "replies_to"


# ── Issue ──────────────────────────────────────────────────────────


class Issue(BaseModel):
    id: str  # e.g. bd-a1b2
    title: str
    description: str = ""
    status: IssueStatus = IssueStatus.OPEN
    priority: int = Field(default=2, ge=0, le=4)
    issue_type: IssueType = IssueType.TASK
    assignee: str = ""
    labels: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    closed_at: datetime | None = None
    closed_reason: str = ""
    parent_id: str | None = None  # for hierarchical IDs (bd-a3f8.1)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @property
    def is_closed(self) -> bool:
        return self.status == IssueStatus.CLOSED

    @property
    def is_epic(self) -> bool:
        return self.issue_type == IssueType.EPIC


class IssueMinimal(BaseModel):
    """Lightweight issue for list views (~80% context reduction)."""
    id: str
    title: str
    status: IssueStatus
    priority: int
    issue_type: IssueType
    assignee: str = ""
    labels: list[str] = Field(default_factory=list)
    dependency_count: int = 0
    dependent_count: int = 0


# ── Dependency ─────────────────────────────────────────────────────


class Dependency(BaseModel):
    child_id: str   # depends on parent
    parent_id: str  # blocks child
    dep_type: DependencyKind = DependencyKind.BLOCKS


# ── Memory ─────────────────────────────────────────────────────────


class Memory(BaseModel):
    id: int = 0
    content: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


# ── Stats ──────────────────────────────────────────────────────────


class ProjectStats(BaseModel):
    total: int = 0
    open: int = 0
    in_progress: int = 0
    blocked: int = 0
    closed: int = 0
    epics: int = 0
    memories: int = 0


# ── Ready work result ──────────────────────────────────────────────


class ReadyWorkItem(BaseModel):
    issue: IssueMinimal
    blocks: list[str] = Field(default_factory=list)   # what this issue blocks
    blocked_by: list[str] = Field(default_factory=list)  # what blocks this issue


class PrimeContext(BaseModel):
    """Output of bd prime."""
    stats: ProjectStats
    ready: list[ReadyWorkItem]
    blocked_tasks: list[ReadyWorkItem]
    recent_memories: list[Memory]
    project_path: str
