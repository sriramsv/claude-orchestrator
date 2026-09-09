"""Dispatch a single task: isolated herdr worktree + a claude agent in it."""

from __future__ import annotations

import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from . import state
from .herdr_client import HerdrClient


def detect_base_branch(repo: str) -> str:
    for ref_args in (
        ["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        ["symbolic-ref", "--quiet", "--short", "HEAD"],
    ):
        result = subprocess.run(
            ["git", "-C", repo, *ref_args], capture_output=True, text=True
        )
        if result.returncode == 0 and result.stdout.strip():
            ref = result.stdout.strip()
            return ref.removeprefix("origin/")
    return "main"


class DispatchError(Exception):
    pass


def dispatch_task(
    client: HerdrClient,
    repo: str,
    name: str,
    prompt: str,
    permission_mode: str = "acceptEdits",
    base_branch: str | None = None,
) -> None:
    if state.get(name) is not None:
        raise DispatchError(
            f"task name '{name}' already in use (pick a different name or 'orchestrate rm {name}' first)"
        )

    repo = str(Path(repo).expanduser().resolve())
    base_branch = base_branch or detect_base_branch(repo)

    print(f"orchestrate: creating worktree '{name}' in {repo} (base: {base_branch})...")
    workspace_id, worktree_path = client.worktree_create(repo, name)

    try:
        # --cwd is mandatory: herdr agent.start does NOT inherit the
        # worktree's checkout path on its own, it defaults to the daemon's
        # own cwd. Getting this wrong dispatches the agent into the wrong
        # directory entirely - learned the hard way (see repos.md).
        print(f"orchestrate: dispatching '{name}' -> {worktree_path}")
        client.agent_start(
            name,
            workspace_id,
            worktree_path,
            ["claude", "-p", "--permission-mode", permission_mode, prompt],
        )

        # Confirm herdr actually has the agent tracked before declaring
        # success - catches a dead/just-restarted server immediately instead
        # of discovering it later at 'orchestrate wait' with no explanation.
        # 'unknown' is the normal status right after start (before the
        # claude integration hook reports in) - only 'not_found' means herdr
        # genuinely lost it.
        time.sleep(0.5)
        status = client.agent_status(name)
        if status == "not_found":
            raise DispatchError(
                f"'{name}' was dispatched but herdr can't confirm it right after start "
                f"(status: {status}) - is the herdr server still running?"
            )
    except Exception:
        client.worktree_remove(workspace_id, force=True)
        raise

    state.set(
        name,
        {
            "repo": repo,
            "workspace_id": workspace_id,
            "worktree_path": worktree_path,
            "branch": name,
            "base_branch": base_branch,
            "prompt": prompt,
            "started_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    print(f"orchestrate: '{name}' dispatched")
