"""Commit (in-task-worktree only), push, and open a PR for a finished task."""

from __future__ import annotations

import subprocess
from pathlib import Path

from . import state


class FinishError(Exception):
    pass


def _git(worktree: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", worktree, *args], capture_output=True, text=True
    )


def finish_task(name: str, dry_run: bool = False) -> str | None:
    entry = state.require(name)
    worktree_path = entry["worktree_path"]
    branch = entry["branch"]
    base_branch = entry["base_branch"]

    if not Path(worktree_path).is_dir():
        raise FinishError(f"worktree for '{name}' is gone ({worktree_path}) - already finished/removed?")

    # Safe here (unlike a shared main checkout): this worktree is exclusive
    # to this task, so nothing else can have unrelated changes staged in it.
    status = _git(worktree_path, "status", "--porcelain").stdout
    if status.strip():
        print(f"orchestrate: committing changes for '{name}'...")
        _git(worktree_path, "add", "-A")
        commit = _git(
            worktree_path,
            "commit",
            "-q",
            "-m",
            f"{name}: automated changes via claude-orchestrator",
            "-m",
            "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>",
        )
        if commit.returncode != 0:
            raise FinishError(f"commit failed for '{name}': {commit.stderr.strip()}")

    ahead = _git(worktree_path, "rev-list", "--count", f"{base_branch}..HEAD")
    if ahead.returncode != 0 or not ahead.stdout.strip().isdigit() or int(ahead.stdout.strip()) == 0:
        print(f"orchestrate: '{name}' has no commits ahead of {base_branch}, skipping push/PR")
        return None

    if dry_run:
        print(f"orchestrate: [dry-run] would push '{branch}' and open a PR against {base_branch} for '{name}'")
        return None

    print(f"orchestrate: pushing '{branch}' for '{name}'...")
    push = _git(worktree_path, "push", "-u", "origin", branch)
    if push.returncode != 0:
        raise FinishError(f"push failed for '{name}': {push.stderr.strip()}")

    pr = subprocess.run(
        [
            "gh",
            "pr",
            "create",
            "--base",
            base_branch,
            "--head",
            branch,
            "--title",
            name,
            "--body",
            f"Automated by claude-orchestrator. Task prompt: {entry['prompt']}",
        ],
        cwd=worktree_path,
        capture_output=True,
        text=True,
    )
    if pr.returncode != 0:
        raise FinishError(f"gh pr create failed for '{name}': {pr.stderr.strip() or pr.stdout.strip()}")

    return pr.stdout.strip()
