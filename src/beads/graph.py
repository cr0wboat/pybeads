"""Dependency graph algorithms for beads-py.

Core operations:
- Ready work detection (open tasks with no open blockers)
- Transitive closure for "what does this block?"
- Cycle detection
"""

from beads.models import Issue, IssueStatus
from beads.storage import BeadsDB


def get_blockers(db: BeadsDB, issue_id: str) -> set[str]:
    """Get the set of all (transitive) blocker IDs for an issue."""
    blockers: set[str] = set()
    frontier = set(db.get_blocked_by(issue_id))
    while frontier:
        bid = frontier.pop()
        if bid in blockers:
            continue
        blockers.add(bid)
        # Recurse: blockers of blockers
        inner = db.get_blocked_by(bid)
        frontier.update(inner)
    return blockers


def get_descendants(db: BeadsDB, issue_id: str) -> set[str]:
    """Get all issues that transitively depend on issue_id."""
    deps: set[str] = set()
    frontier = set(db.get_dependents(issue_id))
    while frontier:
        did = frontier.pop()
        if did in deps:
            continue
        deps.add(did)
        inner = db.get_dependents(did)
        frontier.update(inner)
    return deps


def is_ready(db: BeadsDB, issue_id: str) -> bool:
    """Check if an issue has no open blockers."""
    issue = db.get_issue(issue_id)
    if not issue or issue.is_closed:
        return False
    blockers = db.get_blocked_by(issue_id)
    for bid in blockers:
        b = db.get_issue(bid)
        if b and not b.is_closed:
            return False
    return True


def detect_cycle(db: BeadsDB, child_id: str, parent_id: str) -> bool:
    """Would adding child→parent create a cycle? (parent must not transitively depend on child)."""
    descendants = get_descendants(db, child_id)
    return parent_id in descendants


def get_dependency_graph(db: BeadsDB, root_id: str, depth: int = 3) -> dict:
    """Build a subgraph centered on root_id for visualization."""
    result = {
        "id": root_id,
        "title": "",
        "blocks": [],
        "blocked_by": [],
    }

    issue = db.get_issue(root_id)
    if issue:
        result["title"] = issue.title
        result["status"] = issue.status.value

    if depth <= 0:
        return result

    for dep_id in db.get_blocked_by(root_id):
        result["blocked_by"].append(
            get_dependency_graph(db, dep_id, depth - 1)
        )

    for dep_id in db.get_dependents(root_id):
        result["blocks"].append(
            get_dependency_graph(db, dep_id, depth - 1)
        )

    return result
