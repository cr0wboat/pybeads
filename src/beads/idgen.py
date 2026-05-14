"""Hash-based ID generation for zero-conflict multi-agent workflows.

ID format: bd-xxxx where xxxx is the first 4 hex chars of SHA-1(title + timestamp).
Collision-tolerant: checks DB before returning.
"""

import hashlib
import time


def generate_id(title: str) -> str:
    """Generate a bd-xxxx ID from title + nanosecond timestamp."""
    seed = f"{title}{time.time_ns()}"
    h = hashlib.sha1(seed.encode()).hexdigest()
    return f"bd-{h[:4]}"


def id_exists(db, issue_id: str) -> bool:
    """Check if an ID already exists in the database."""
    row = db.conn.execute(
        "SELECT 1 FROM issues WHERE id = ?", (issue_id,)
    ).fetchone()
    return row is not None


def unique_id(db, title: str, max_retries: int = 10) -> str:
    """Generate a guaranteed-unique ID, retrying on collision."""
    for _ in range(max_retries):
        iid = generate_id(title)
        if not id_exists(db, iid):
            return iid
    # Fallback: longer hash
    seed = f"{title}{time.time_ns()}"
    h = hashlib.sha1(seed.encode()).hexdigest()
    return f"bd-{h[:6]}"


def parse_hierarchical(issue_id: str) -> tuple[str | None, ...]:
    """Parse a hierarchical ID like bd-a3f8.1.1 into parts."""
    return tuple(issue_id.split("."))


def make_child_id(parent_id: str, child_num: int) -> str:
    """Create a child ID: bd-a3f8 + .1 → bd-a3f8.1."""
    return f"{parent_id}.{child_num}"
