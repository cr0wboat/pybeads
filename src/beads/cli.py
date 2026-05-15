"""CLI entry point for pybeads (bd). Built with Typer + Rich.

Usage:
    bd init                  Initialize in current directory
    bd create "title"        Create a new issue
    bd list                  List issues
    bd show <id>             Show issue details
    bd ready                 Show ready work
    bd update <id> --claim   Claim and start a task
    bd close <id> "reason"   Close an issue
    bd dep add <A> <B>       Add dependency (A depends on B)
    bd prime                 Print agent workflow context
    bd remember "insight"    Store persistent memory
    bd backup export         Export JSONL
    bd compact               Summarize old closed issues
    bd stats                 Project statistics
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Annotated, Optional

import typer
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.text import Text
from rich import box

from beads.models import (
    DependencyKind,
    Issue,
    IssueStatus,
    IssueType,
    PrimeContext,
    ProjectStats,
    ReadyWorkItem,
)
from beads.storage import BeadsDB
from beads.idgen import unique_id
from beads.graph import (
    get_blockers,
    get_descendants,
    detect_cycle,
    get_dependency_graph,
)
from beads.compaction import compact_closed_issues
from beads.gitops import (
    find_git_root,
    get_git_origin,
    get_git_user,
    get_current_branch,
    install_post_commit_hook,
)

app = typer.Typer(
    name="bd",
    help="beads — distributed graph issue tracker for AI agents",
    add_completion=False,
)
console = Console()
err_console = Console(stderr=True)

# ── helpers ────────────────────────────────────────────────────────

BEADS_DIR_NAME = ".beads"
DB_FILENAME = "beads.db"
JSONL_FILENAME = "issues.jsonl"


def _resolve_project_root() -> Path:
    """Find the project root: check BEADS_DIR env, git root, or cwd."""
    env_dir = os.environ.get("BEADS_DIR")
    if env_dir:
        return Path(env_dir).parent.resolve()

    git_root = find_git_root()
    if git_root:
        return git_root

    return Path.cwd()


def _beads_dir(project_root: Path) -> Path:
    return project_root / BEADS_DIR_NAME


def _db_path(project_root: Path) -> Path:
    return _beads_dir(project_root) / DB_FILENAME


def _open_db() -> BeadsDB | None:
    """Open the beads database, returning None if not initialized."""
    root = _resolve_project_root()
    db_path = _db_path(root)
    if not db_path.exists():
        err_console.print(
            f"[red]No beads database found at {db_path}[/red]\n"
            f"Run [bold]bd init[/bold] first."
        )
        return None
    return BeadsDB(db_path)


def _require_db() -> BeadsDB:
    """Open DB or exit with error."""
    db = _open_db()
    if db is None:
        raise typer.Exit(1)
    return db


def _format_priority(p: int) -> str:
    colors = {0: "red", 1: "yellow", 2: "cyan", 3: "blue", 4: "dim"}
    labels = {0: "P0", 1: "P1", 2: "P2", 3: "P3", 4: "P4"}
    c = colors.get(p, "")
    return f"[{c}]{labels[p]}[/{c}]"


def _format_status(s: str) -> str:
    colors = {
        "open": "cyan",
        "in_progress": "yellow",
        "blocked": "red",
        "closed": "green",
    }
    c = colors.get(s, "")
    return f"[{c}]{s}[/{c}]"


def _format_type(t: str) -> str:
    icons = {
        "task": "📋",
        "bug": "🐛",
        "feature": "✨",
        "epic": "🏛️",
        "chore": "🔧",
        "decision": "🤔",
        "message": "💬",
    }
    return f"{icons.get(t, '•')} {t}"


# ── init ───────────────────────────────────────────────────────────

@app.command()
def init(
    stealth: Annotated[
        bool, typer.Option("--stealth", help="No git hooks, no AGENTS.md changes")
    ] = False,
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Suppress output")
    ] = False,
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Reinitialize even if .beads exists")
    ] = False,
):
    """Initialize beads in the current project directory."""
    root = _resolve_project_root()
    beads_dir = _beads_dir(root)
    db_path = _db_path(root)

    if db_path.exists() and not force:
        console.print(f"[yellow]beads already initialized at {beads_dir}[/yellow]")
        return

    beads_dir.mkdir(parents=True, exist_ok=True)
    db = BeadsDB(db_path)

    if not quiet:
        git_info = ""
        git_root = find_git_root()
        if git_root:
            origin = get_git_origin(git_root) or "none"
            branch = get_current_branch(git_root) or "unknown"
            git_info = f"  Git root:    {git_root}\n  Remote:      {origin}\n  Branch:      {branch}\n"

        if stealth:
            git_info += "  Mode:        stealth (no git hooks)\n"

        console.print(
            Panel.fit(
                f"[bold green]✓[/bold green] Beads initialized!\n\n"
                f"  Database:    {db_path}\n"
                f"{git_info}"
                f"  Schema:      v1 (SQLite)\n",
                title="bd init",
                border_style="green",
            )
        )

    # Install git hook (unless stealth)
    git_root = find_git_root()
    if git_root and not stealth:
        install_post_commit_hook(git_root)

    db.close()


# ── create ─────────────────────────────────────────────────────────

@app.command()
def create(
    title: Annotated[str, typer.Argument(help="Issue title")],
    priority: Annotated[
        int, typer.Option("-p", "--priority", min=0, max=4, help="Priority 0-4")
    ] = 2,
    issue_type: Annotated[
        str, typer.Option("-t", "--type", help="Issue type")
    ] = "task",
    description: Annotated[
        str, typer.Option("-d", "--description", help="Description text")
    ] = "",
    labels: Annotated[
        str, typer.Option("--labels", help="Comma-separated labels")
    ] = "",
    deps: Annotated[
        list[str] | None,
        typer.Option("--deps", help="Dependencies: bd-xxx or discovered-from:bd-xxx"),
    ] = None,
    assignee: Annotated[
        str, typer.Option("--assignee", "-a", help="Assignee name")
    ] = "",
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Create a new issue."""
    db = _require_db()

    # Validate type
    valid_types = {t.value for t in IssueType}
    if issue_type not in valid_types:
        err_console.print(f"[red]Invalid type '{issue_type}'. Valid: {', '.join(sorted(valid_types))}[/red]")
        raise typer.Exit(1)

    issue_id = unique_id(db, title)
    label_list = [l.strip() for l in labels.split(",") if l.strip()]

    issue = Issue(
        id=issue_id,
        title=title,
        description=description,
        priority=priority,
        issue_type=IssueType(issue_type),
        assignee=assignee,
        labels=label_list,
    )

    db.create_issue(issue)

    # Process --deps
    if deps:
        for dep_str in deps:
            if ":" in dep_str:
                kind_str, dep_id = dep_str.split(":", 1)
                try:
                    kind = DependencyKind(kind_str)
                except ValueError:
                    err_console.print(f"[yellow]Unknown dep type '{kind_str}', using 'related'[/yellow]")
                    kind = DependencyKind.RELATED
            else:
                kind = DependencyKind.BLOCKS
                dep_id = dep_str
            db.add_dependency(issue_id, dep_id, kind)

    if json_output:
        console.print_json(json.dumps({
            "id": issue.id,
            "title": issue.title,
            "status": issue.status.value,
            "priority": issue.priority,
            "type": issue.issue_type.value,
            "assignee": issue.assignee,
            "labels": issue.labels,
            "created_at": issue.created_at.isoformat(),
        }))
    else:
        console.print(
            f"[bold green]✓[/bold green] Created {_format_priority(issue.priority)} "
            f"{_format_type(issue.issue_type.value)} [bold]{issue.id}[/bold]: {issue.title}"
        )

    db.close()


# ── list ───────────────────────────────────────────────────────────

@app.command()
def list_issues(
    status: Annotated[
        str | None, typer.Option("-s", "--status", help="Filter by status")
    ] = None,
    priority: Annotated[
        int | None, typer.Option("-p", "--priority", help="Filter by priority")
    ] = None,
    issue_type: Annotated[
        str | None, typer.Option("-t", "--type", help="Filter by type")
    ] = None,
    assignee: Annotated[
        str | None, typer.Option("-a", "--assignee", help="Filter by assignee")
    ] = None,
    labels: Annotated[
        str | None, typer.Option("--labels", help="Filter by labels (comma-separated)")
    ] = None,
    search: Annotated[
        str | None, typer.Option("--search", help="Full-text search in title + description")
    ] = None,
    limit: Annotated[
        int, typer.Option("-n", "--limit", help="Max results")
    ] = 50,
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List issues with optional filters."""
    db = _require_db()

    label_list = [l.strip() for l in labels.split(",") if l.strip()] if labels else None

    if search:
        issues = db.search_issues(search, limit)
    else:
        issues = db.list_issues(
            status=status,
            priority=priority,
            issue_type=issue_type,
            assignee=assignee,
            labels=label_list,
            limit=limit,
        )

    if json_output:
        result = []
        for i in issues:
            result.append({
                "id": i.id,
                "title": i.title,
                "status": i.status.value,
                "priority": i.priority,
                "type": i.issue_type.value,
                "assignee": i.assignee,
                "labels": i.labels,
                "created_at": i.created_at.isoformat(),
                "updated_at": i.updated_at.isoformat(),
            })
        console.print_json(json.dumps(result))
    elif not issues:
        console.print("[dim]No issues found.[/dim]")
    else:
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("ID", style="dim", width=10)
        table.add_column("P", width=4)
        table.add_column("Type", width=8)
        table.add_column("Status", width=14)
        table.add_column("Title")
        table.add_column("Assignee", style="dim")

        for i in issues:
            table.add_row(
                i.id,
                _format_priority(i.priority),
                _format_type(i.issue_type.value),
                _format_status(i.status.value),
                i.title[:80] + ("…" if len(i.title) > 80 else ""),
                i.assignee or "-",
            )
        console.print(table)
        console.print(f"[dim]{len(issues)} issue(s)[/dim]")

    db.close()


# ── show ───────────────────────────────────────────────────────────

@app.command()
def show(
    issue_id: Annotated[str, typer.Argument(help="Issue ID (e.g. bd-a1b2)")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
    graph: Annotated[
        bool, typer.Option("--graph", help="Show dependency graph")
    ] = False,
):
    """Show full details of an issue."""
    db = _require_db()
    issue = db.get_issue(issue_id)

    if not issue:
        err_console.print(f"[red]Issue '{issue_id}' not found.[/red]")
        db.close()
        raise typer.Exit(1)

    if json_output:
        data = {
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
            "metadata": issue.metadata,
        }
        # Add deps
        blockers = db.get_blocked_by(issue_id)
        dependents = db.get_dependents(issue_id)
        data["blocked_by"] = blockers
        data["blocks"] = dependents
        console.print_json(json.dumps(data))
    else:
        # Header
        header = Text()
        header.append(f"{_format_type(issue.issue_type.value)} ", style="bold")
        header.append(f"{issue.id} ", style="dim")
        header.append(_format_priority(issue.priority))
        header.append(f" {_format_status(issue.status.value)}")
        header.append(f"\n{issue.title}", style="bold")

        console.print(Panel(header, title="Issue Details", border_style="cyan"))

        if issue.description:
            console.print(f"\n[bold]Description:[/bold]\n{issue.description}")

        # Metadata table
        meta = Table(box=box.SIMPLE, show_header=False)
        meta.add_column(style="bold")
        meta.add_column()
        meta.add_row("Assignee", issue.assignee or "-")
        meta.add_row("Labels", ", ".join(issue.labels) if issue.labels else "-")
        meta.add_row("Created", issue.created_at.strftime("%Y-%m-%d %H:%M"))
        meta.add_row("Updated", issue.updated_at.strftime("%Y-%m-%d %H:%M"))
        if issue.closed_at:
            meta.add_row("Closed", issue.closed_at.strftime("%Y-%m-%d %H:%M"))
            meta.add_row("Reason", issue.closed_reason or "-")
        console.print(meta)

        # Dependencies
        blockers = db.get_blocked_by(issue_id)
        dependents = db.get_dependents(issue_id)
        if blockers:
            console.print("\n[bold red]Blocked by:[/bold red]")
            for bid in blockers:
                b = db.get_issue(bid)
                if b:
                    console.print(f"  {bid} {_format_status(b.status.value)} {b.title[:60]}")
        if dependents:
            console.print("\n[bold yellow]Blocks:[/bold yellow]")
            for did in dependents:
                d = db.get_issue(did)
                if d:
                    console.print(f"  {did} {_format_status(d.status.value)} {d.title[:60]}")

        if graph:
            console.print("\n[bold]Dependency graph:[/bold]")
            g = get_dependency_graph(db, issue_id, depth=2)
            console.print_json(json.dumps(g, indent=2))

    db.close()


# ── update ─────────────────────────────────────────────────────────

@app.command()
def update(
    issue_id: Annotated[str, typer.Argument(help="Issue ID")],
    claim: Annotated[
        bool, typer.Option("--claim", help="Atomically claim task (set in_progress + assignee)")
    ] = False,
    status: Annotated[
        str | None, typer.Option("-s", "--status", help="Set status")
    ] = None,
    title: Annotated[
        str | None, typer.Option("--title", help="Change title")
    ] = None,
    description: Annotated[
        str | None, typer.Option("-d", "--description", help="Change description")
    ] = None,
    priority: Annotated[
        int | None, typer.Option("-p", "--priority", help="Change priority (0-4)")
    ] = None,
    assignee: Annotated[
        str | None, typer.Option("-a", "--assignee", help="Set assignee")
    ] = None,
    labels: Annotated[
        str | None, typer.Option("--labels", help="Set labels (comma-separated)")
    ] = None,
    add_labels: Annotated[
        str | None, typer.Option("--add-labels", help="Add labels (comma-separated)")
    ] = None,
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Update an issue."""
    db = _require_db()

    if claim:
        user = assignee or os.environ.get("USER", "agent")
        issue = db.claim_issue(issue_id, user)
        if not issue:
            err_console.print(f"[red]Issue '{issue_id}' not found.[/red]")
            db.close()
            raise typer.Exit(1)
        if json_output:
            console.print_json(json.dumps({"id": issue.id, "status": issue.status.value, "assignee": issue.assignee}))
        else:
            console.print(f"[bold green]✓[/bold green] Claimed {issue.id}: {issue.title}")
        db.close()
        return

    kwargs = {}
    if status is not None:
        try:
            kwargs["status"] = IssueStatus(status)
        except ValueError:
            err_console.print(f"[red]Invalid status '{status}'. Valid: open, in_progress, blocked, closed[/red]")
            db.close()
            raise typer.Exit(1)
    if title is not None:
        kwargs["title"] = title
    if description is not None:
        kwargs["description"] = description
    if priority is not None:
        kwargs["priority"] = priority
    if assignee is not None:
        kwargs["assignee"] = assignee

    if labels is not None:
        kwargs["labels"] = [l.strip() for l in labels.split(",") if l.strip()]
    elif add_labels is not None:
        issue = db.get_issue(issue_id)
        if issue:
            new_labels = issue.labels + [l.strip() for l in add_labels.split(",") if l.strip()]
            kwargs["labels"] = list(set(new_labels))

    if not kwargs:
        err_console.print("[yellow]No changes specified.[/yellow]")
        db.close()
        return

    issue = db.update_issue(issue_id, **kwargs)
    if not issue:
        err_console.print(f"[red]Issue '{issue_id}' not found.[/red]")
        db.close()
        raise typer.Exit(1)

    if json_output:
        console.print_json(json.dumps({"id": issue.id, "status": issue.status.value, "updated_at": issue.updated_at.isoformat()}))
    else:
        console.print(f"[bold green]✓[/bold green] Updated {issue.id}: {issue.title}")

    db.close()


# ── close ─────────────────────────────────────────────────────────

@app.command()
def close(
    issue_id: Annotated[str, typer.Argument(help="Issue ID")],
    reason: Annotated[
        str, typer.Argument(help="Closing reason (optional)")
    ] = "",
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Close an issue."""
    db = _require_db()
    issue = db.close_issue(issue_id, reason)
    if not issue:
        err_console.print(f"[red]Issue '{issue_id}' not found.[/red]")
        db.close()
        raise typer.Exit(1)

    if json_output:
        console.print_json(json.dumps({"id": issue.id, "status": "closed", "reason": reason}))
    else:
        console.print(f"[bold green]✓[/bold green] Closed {issue.id}: {issue.title}")

    db.close()


# ── dep ────────────────────────────────────────────────────────────

dep_app = typer.Typer(help="Manage dependencies")
app.add_typer(dep_app, name="dep")


@dep_app.command(name="add")
def dep_add(
    child: Annotated[str, typer.Argument(help="Child issue ID (depends on parent)")],
    parent: Annotated[str, typer.Argument(help="Parent issue ID (blocks child)")],
    dep_type: Annotated[
        str, typer.Option("-t", "--type", help="Dependency type")
    ] = "blocks",
):
    """Add a dependency: dep add <child> <parent> — child depends on parent."""
    db = _require_db()

    try:
        kind = DependencyKind(dep_type)
    except ValueError:
        err_console.print(f"[red]Invalid dep type '{dep_type}'. Valid: {', '.join(d.value for d in DependencyKind)}[/red]")
        db.close()
        raise typer.Exit(1)

    # Cycle check
    if detect_cycle(db, child, parent):
        err_console.print(f"[red]Cycle detected: {parent} already transitively depends on {child}[/red]")
        db.close()
        raise typer.Exit(1)

    dep = db.add_dependency(child, parent, kind)
    if not dep:
        err_console.print(f"[red]One or both issues not found.[/red]")
        db.close()
        raise typer.Exit(1)

    console.print(f"[bold green]✓[/bold green] {child} depends on {parent} \\[{kind.value}]")
    db.close()


@dep_app.command(name="rm")
def dep_rm(
    child: Annotated[str, typer.Argument(help="Child issue ID")],
    parent: Annotated[str, typer.Argument(help="Parent issue ID")],
):
    """Remove a dependency."""
    db = _require_db()
    ok = db.remove_dependency(child, parent)
    if ok:
        console.print(f"[bold green]✓[/bold green] Removed dependency: {child} → {parent}")
    else:
        err_console.print(f"[yellow]Dependency not found.[/yellow]")
    db.close()


# ── ready ──────────────────────────────────────────────────────────

@app.command()
def ready(
    limit: Annotated[
        int, typer.Option("-n", "--limit", help="Max results")
    ] = 20,
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """List tasks with no open blockers (ready to work on)."""
    db = _require_db()
    issues = db.get_ready_work(limit)

    if json_output:
        result = []
        for i in issues:
            result.append({
                "id": i.id,
                "title": i.title,
                "status": i.status.value,
                "priority": i.priority,
                "type": i.issue_type.value,
                "assignee": i.assignee,
                "labels": i.labels,
                "blocked_by": db.get_blocked_by(i.id),
                "blocks": db.get_dependents(i.id),
            })
        console.print_json(json.dumps(result))
    elif not issues:
        console.print("[dim]No ready work. Everything is either done or blocked.[/dim]")
    else:
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("ID", style="dim", width=10)
        table.add_column("P", width=4)
        table.add_column("Type", width=8)
        table.add_column("Title")
        table.add_column("Blocks", width=10)

        for i in issues:
            deps_count = len(db.get_dependents(i.id))
            blocks_str = str(deps_count) if deps_count else "-"
            table.add_row(
                i.id,
                _format_priority(i.priority),
                _format_type(i.issue_type.value),
                i.title[:80] + ("…" if len(i.title) > 80 else ""),
                blocks_str,
            )
        console.print(table)
        console.print(f"[dim]{len(issues)} ready task(s)[/dim]")

    db.close()


# ── prime ─────────────────────────────────────────────────────────

@app.command()
def prime(
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Print agent workflow context and persistent memories."""
    db = _require_db()
    stats = db.get_stats()
    ready_issues = db.get_ready_work(10)
    blocked_issues = db.get_blocked_issues(5)
    memories = db.get_memories(10)

    ready_items = []
    for i in ready_issues:
        ready_items.append(ReadyWorkItem(
            issue=db._issue_to_minimal(i),
            blocked_by=db.get_blocked_by(i.id),
            blocks=db.get_dependents(i.id),
        ))

    blocked_items = []
    for i in blocked_issues:
        blocked_items.append(ReadyWorkItem(
            issue=db._issue_to_minimal(i),
            blocked_by=db.get_blocked_by(i.id),
            blocks=db.get_dependents(i.id),
        ))

    ctx = PrimeContext(
        stats=stats,
        ready=ready_items,
        blocked_tasks=blocked_items,
        recent_memories=memories,
        project_path=str(_resolve_project_root()),
    )

    if json_output:
        console.print_json(ctx.model_dump_json(indent=2))
    else:
        # Rich formatted output
        console.print(Panel(
            f"[bold]Project:[/bold] {ctx.project_path}\n"
            f"[bold]Issues:[/bold] {stats.total} total, "
            f"[cyan]{stats.open} open[/cyan], "
            f"[yellow]{stats.in_progress} in progress[/yellow], "
            f"[red]{stats.blocked} blocked[/red], "
            f"[green]{stats.closed} closed[/green]",
            title="bd prime",
            border_style="cyan",
        ))

        if ready_items:
            console.print("\n[bold cyan]🔓 Ready Work:[/bold cyan]")
            for item in ready_items[:10]:
                console.print(
                    f"  {item.issue.id} {_format_priority(item.issue.priority)} "
                    f"{item.issue.title[:70]}"
                    + (f" (blocks {len(item.blocks)})" if item.blocks else "")
                )

        if blocked_items:
            console.print("\n[bold red]🔒 Blocked:[/bold red]")
            for item in blocked_items:
                console.print(
                    f"  {item.issue.id} {item.issue.title[:70]} "
                    f"[dim](waiting on: {', '.join(item.blocked_by)})[/dim]"
                )

        if memories:
            console.print("\n[bold green]🧠 Memories:[/bold green]")
            for m in memories:
                console.print(f"  [{m.id}] {m.content[:120]}")

        # Print AGENTS.md snippet
        console.print(
            "\n[dim]── AGENTS.md snippet ──[/dim]\n"
            "[dim]#[/dim] This project uses [bold]bd[/bold] (beads) for issue tracking.\n"
            "[dim]#[/dim] Run [bold]bd prime[/bold] for workflow context.\n"
            "[dim]#[/dim] Use [bold]bd ready[/bold], [bold]bd show <id>[/bold], [bold]bd close <id>[/bold].\n"
            "[dim]#[/dim] Do not use markdown TODO lists for task tracking.\n"
        )

    db.close()


# ── remember ──────────────────────────────────────────────────────

@app.command()
def remember(
    content: Annotated[str, typer.Argument(help="Memory to store")],
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Store persistent project memory. Memories are included in bd prime output."""
    db = _require_db()
    mem = db.add_memory(content)

    if json_output:
        console.print_json(json.dumps({"id": mem.id, "content": mem.content}))
    else:
        console.print(f"[bold green]✓[/bold green] Remembered [{mem.id}]: {content[:80]}")

    db.close()


@app.command()
def forget(
    memory_id: Annotated[int, typer.Argument(help="Memory ID to forget")],
):
    """Remove a stored memory by ID."""
    db = _require_db()
    ok = db.delete_memory(memory_id)
    if ok:
        console.print(f"[bold green]✓[/bold green] Forgotten [{memory_id}]")
    else:
        err_console.print(f"[yellow]Memory [{memory_id}] not found.[/yellow]")
    db.close()


# ── backup ─────────────────────────────────────────────────────────

backup_app = typer.Typer(help="Backup and restore")
app.add_typer(backup_app, name="backup")


@backup_app.command(name="export")
def backup_export(
    output: Annotated[
        str, typer.Option("-o", "--output", help="Output file path")
    ] = "",
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Suppress output")
    ] = False,
):
    """Export all issues to JSONL format."""
    db = _require_db()
    root = _resolve_project_root()
    out_path = Path(output) if output else (_beads_dir(root) / JSONL_FILENAME)

    jsonl = db.export_jsonl()
    out_path.write_text(jsonl + "\n")

    if not quiet:
        console.print(f"[bold green]✓[/bold green] Exported to {out_path}")
    db.close()


@backup_app.command(name="import")
def backup_import(
    file: Annotated[
        str, typer.Argument(help="JSONL file to import (use '-' for stdin)")
    ] = "",
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be imported")
    ] = False,
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Import issues from JSONL."""
    db = _require_db()
    root = _resolve_project_root()
    src = Path(file) if file else (_beads_dir(root) / JSONL_FILENAME)

    if file == "-":
        content = sys.stdin.read()
    elif src.exists():
        content = src.read_text()
    else:
        err_console.print(f"[red]File not found: {src}[/red]")
        db.close()
        raise typer.Exit(1)

    imported = 0
    skipped = 0
    created_ids = []

    for line in content.strip().split("\n"):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            skipped += 1
            continue

        # Check if already exists
        iid = record.get("id")
        if not iid or db.get_issue(iid):
            skipped += 1
            continue

        issue = Issue(
            id=iid,
            title=record.get("title", ""),
            description=record.get("description", ""),
            status=IssueStatus(record.get("status", "open")),
            priority=record.get("priority", 2),
            issue_type=IssueType(record.get("type", "task")),
            assignee=record.get("assignee", ""),
            labels=record.get("labels", []),
        )
        if not dry_run:
            db.create_issue(issue)
        imported += 1
        created_ids.append(iid)

    if json_output:
        console.print_json(json.dumps({"imported": imported, "skipped": skipped, "ids": created_ids}))
    elif dry_run:
        console.print(f"[dim]Would import {imported} issue(s), skip {skipped}[/dim]")
    else:
        console.print(f"[bold green]✓[/bold green] Imported {imported} issue(s), skipped {skipped}")

    db.close()


# ── compact ────────────────────────────────────────────────────────

@app.command()
def compact(
    older_than: Annotated[
        int, typer.Option("--older-than", "-d", help="Compact issues closed more than N days ago")
    ] = 30,
    dry_run: Annotated[
        bool, typer.Option("--dry-run", help="Show what would be compacted")
    ] = False,
):
    """Compact old closed issues into memories (memory decay)."""
    db = _require_db()
    count, summary = compact_closed_issues(db, older_than_days=older_than, dry_run=dry_run)

    if dry_run:
        console.print(f"[dim]Would compact {count} closed issue(s) older than {older_than} days[/dim]")
        if summary:
            console.print(f"[dim]Summary: {summary}[/dim]")
    elif count == 0:
        console.print("[dim]No closed issues to compact.[/dim]")
    else:
        console.print(f"[bold green]✓[/bold green] Compacted {count} issue(s) into memory:")
        console.print(f"  {summary}")

    db.close()


# ── stats ──────────────────────────────────────────────────────────

@app.command()
def stats(
    json_output: Annotated[
        bool, typer.Option("--json", help="Output as JSON")
    ] = False,
):
    """Show project statistics."""
    db = _require_db()
    s = db.get_stats()

    if json_output:
        console.print_json(s.model_dump_json())
    else:
        table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold")
        table.add_column("Metric")
        table.add_column("Count", justify="right")
        table.add_row("Total issues", str(s.total))
        table.add_row("Open", f"[cyan]{s.open}[/cyan]")
        table.add_row("In Progress", f"[yellow]{s.in_progress}[/yellow]")
        table.add_row("Blocked", f"[red]{s.blocked}[/red]")
        table.add_row("Closed", f"[green]{s.closed}[/green]")
        table.add_row("Epics", str(s.epics))
        table.add_row("Memories", str(s.memories))
        console.print(Panel(table, title="Project Stats", border_style="cyan"))

    db.close()


# ── delete ─────────────────────────────────────────────────────────

@app.command()
def delete(
    issue_id: Annotated[str, typer.Argument(help="Issue ID to delete")],
    force: Annotated[
        bool, typer.Option("--force", "-f", help="Skip confirmation")
    ] = False,
):
    """Delete an issue and its dependencies."""
    db = _require_db()
    issue = db.get_issue(issue_id)
    if not issue:
        err_console.print(f"[red]Issue '{issue_id}' not found.[/red]")
        db.close()
        raise typer.Exit(1)

    if not force:
        confirm = typer.confirm(f"Delete {issue.id}: {issue.title}?")
        if not confirm:
            console.print("[dim]Cancelled.[/dim]")
            db.close()
            return

    db.delete_issue(issue_id)
    console.print(f"[bold green]✓[/bold green] Deleted {issue_id}")
    db.close()


# ── version ────────────────────────────────────────────────────────

@app.command()
def version():
    """Show beads-py version."""
    from beads import __version__
    console.print(f"bd (beads-py) version {__version__}")
    console.print("[dim]SQLite backend | Python[/dim]")


# ── main ──────────────────────────────────────────────────────────

def main():
    app()


if __name__ == "__main__":
    main()
