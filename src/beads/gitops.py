"""Git integration for beads-py.

- Discovers git repo root for project detection
- Installs git hooks (post-commit for auto-export)
- Detects origin remote for sync hints
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def find_git_root(path: Path | None = None) -> Path | None:
    """Find the root of the git repository containing path."""
    start = path or Path.cwd()
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=str(start),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def get_git_origin(git_root: Path) -> str | None:
    """Get the origin remote URL."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def get_git_user(git_root: Path) -> str | None:
    """Get git user.name."""
    try:
        result = subprocess.run(
            ["git", "config", "user.name"],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def get_current_branch(git_root: Path) -> str | None:
    """Get the current git branch name."""
    try:
        result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=str(git_root),
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        return result.stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return None


def install_post_commit_hook(git_root: Path) -> bool:
    """Install a post-commit hook that auto-exports JSONL."""
    hook_path = git_root / ".git" / "hooks" / "post-commit"
    hook_script = """#!/bin/sh
# beads-py auto-export hook
if command -v bd >/dev/null 2>&1; then
    bd backup export --quiet 2>/dev/null || true
fi
"""
    try:
        hook_path.parent.mkdir(parents=True, exist_ok=True)
        hook_path.write_text(hook_script)
        hook_path.chmod(0o755)
        return True
    except (OSError, PermissionError):
        return False


def should_auto_setup(git_root: Path) -> bool:
    """Check if beads already initialized in this repo."""
    beads_dir = git_root / ".beads"
    return beads_dir.exists() and (beads_dir / "beads.db").exists()
