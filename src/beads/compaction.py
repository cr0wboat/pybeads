"""Semantic memory decay — compact old closed issues into summaries.

bd compact: summarizes old closed tasks to save context window space.
Optionally calls an LLM for intelligent summarization.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from typing import Protocol

from beads.models import Issue, IssueStatus
from beads.storage import BeadsDB


class LLMClient(Protocol):
    """Protocol for an optional LLM summarizer."""
    def summarize(self, prompt: str) -> str: ...


def compact_closed_issues(
    db: BeadsDB,
    older_than_days: int = 30,
    llm: LLMClient | None = None,
    dry_run: bool = False,
) -> tuple[int, str]:
    """Summarize closed issues older than N days into a memory, then delete them.

    Returns (count_deleted, summary_text).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    rows = db.conn.execute(
        """SELECT id, title, description, closed_reason, closed_at
           FROM issues
           WHERE status = 'closed'
           AND closed_at IS NOT NULL
           AND closed_at < ?
           ORDER BY closed_at ASC""",
        (cutoff.isoformat(),),
    ).fetchall()

    if not rows:
        return 0, ""

    titles = [r["title"] for r in rows]

    if llm:
        prompt = (
            "Summarize these completed tasks into a concise project memory:\n\n"
            + "\n".join(f"- {t}" for t in titles)
            + "\n\nWrite a 2-3 sentence summary of what was accomplished."
        )
        summary = llm.summarize(prompt)
    else:
        summary = f"Completed {len(titles)} tasks: " + "; ".join(titles[:20])
        if len(titles) > 20:
            summary += f" ... and {len(titles) - 20} more"

    if not dry_run:
        for r in rows:
            db.delete_issue(r["id"])
        db.add_memory(summary)

    return len(rows), summary
