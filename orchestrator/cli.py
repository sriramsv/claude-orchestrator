"""orchestrate - dispatch parallel Claude Code agents across git repos via herdr."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import state
from .dispatch import DispatchError, dispatch_task
from .finish import FinishError, finish_task
from .herdr_client import AgentBlocked, HerdrClient, HerdrConnectionLost, HerdrError


def _client() -> HerdrClient:
    return HerdrClient()


def cmd_run(args: argparse.Namespace) -> int:
    client = _client()
    client.ensure_server()

    tasks: list[dict] = []
    if args.tasks_file:
        tasks.extend(json.loads(Path(args.tasks_file).read_text()))
    for spec in args.inline_tasks:
        parts = spec.split("::", 2)
        if len(parts) != 3:
            print(f"orchestrate: bad task spec '{spec}', expected repo::name::prompt", file=sys.stderr)
            return 1
        repo, name, prompt = parts
        tasks.append({"repo": repo, "name": name, "prompt": prompt})

    failed = False
    for t in tasks:
        if args.dry_run:
            print(f"orchestrate: [dry-run] would dispatch '{t['name']}' in {t['repo']}: {t['prompt']}")
            continue
        try:
            dispatch_task(
                client,
                t["repo"],
                t["name"],
                t["prompt"],
                args.permission_mode,
                t.get("base_branch"),
            )
        except (DispatchError, HerdrError, HerdrConnectionLost) as e:
            print(f"orchestrate: failed to dispatch '{t['name']}': {e}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def cmd_status(_args: argparse.Namespace) -> int:
    client = _client()
    client.ensure_server()
    print(f"{'NAME':<24} {'STATUS':<10} {'REPO':<30} BRANCH")
    for name in state.names():
        entry = state.get(name)
        if entry is None:
            continue
        repo_base = Path(entry["repo"]).name
        try:
            status = client.agent_status(name)
        except (HerdrConnectionLost, HerdrError):
            status = "error"
        print(f"{name:<24} {status:<10} {repo_base:<30} {entry['branch']}")
    return 0


def cmd_wait(args: argparse.Namespace) -> int:
    client = _client()
    client.ensure_server()
    names = args.names or state.names()
    failed = False
    for name in names:
        if state.get(name) is None:
            print(f"orchestrate: no known task named '{name}'", file=sys.stderr)
            failed = True
            continue
        print(f"orchestrate: waiting for '{name}'...")
        try:
            client.wait_for_status(name, "idle", timeout_ms=1_800_000)
            print(f"orchestrate: '{name}' is idle")
        except AgentBlocked as e:
            print(f"orchestrate: '{name}' is blocked (likely a permission prompt nothing can answer) - recent output:", file=sys.stderr)
            print(e.recent_output, file=sys.stderr)
            failed = True
        except (HerdrConnectionLost, HerdrError, TimeoutError) as e:
            print(f"orchestrate: '{name}' did not reach idle: {e}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def cmd_finish(args: argparse.Namespace) -> int:
    names = args.names or state.names()
    failed = False
    for name in names:
        try:
            pr_url = finish_task(name, dry_run=args.dry_run)
            if pr_url:
                print(pr_url)
        except (FinishError, KeyError) as e:
            print(f"orchestrate: {e}", file=sys.stderr)
            failed = True
    return 1 if failed else 0


def cmd_logs(args: argparse.Namespace) -> int:
    client = _client()
    try:
        state.require(args.name)
        print(client.read_recent(args.name, lines=200))
    except (KeyError, HerdrError, HerdrConnectionLost) as e:
        print(f"orchestrate: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_attach(args: argparse.Namespace) -> int:
    # Interactive terminal takeover - not a data operation, so this is the
    # one place we shell out to the herdr CLI rather than the socket.
    import os

    try:
        state.require(args.name)
    except KeyError as e:
        print(f"orchestrate: {e}", file=sys.stderr)
        return 1
    os.execvp("herdr", ["herdr", "agent", "attach", args.name])


def cmd_rm(args: argparse.Namespace) -> int:
    client = _client()
    names = list(state.names()) if args.target == "--all" else [args.target]
    for name in names:
        entry = state.get(name)
        if entry is None:
            print(f"orchestrate: no known task named '{name}'", file=sys.stderr)
            continue
        try:
            client.worktree_remove(entry["workspace_id"], force=True)
        except (HerdrError, HerdrConnectionLost):
            print(f"orchestrate: could not remove worktree for '{name}' (already gone?)", file=sys.stderr)
        state.remove(name)
        print(f"orchestrate: removed '{name}'")
    return 0


def cmd_clean(_args: argparse.Namespace) -> int:
    for name in state.names():
        entry = state.get(name)
        if entry and not Path(entry["worktree_path"]).is_dir():
            state.remove(name)
            print(f"orchestrate: dropped stale state for '{name}'")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="orchestrate",
        description="Dispatch parallel Claude Code agents across git repos via herdr.",
    )
    sub = p.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="dispatch one or more tasks")
    run.add_argument("-f", dest="tasks_file", help="tasks.json file")
    run.add_argument("--permission-mode", default="acceptEdits")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("inline_tasks", nargs="*", help="repo::name::prompt")
    run.set_defaults(func=cmd_run)

    status = sub.add_parser("status", help="show dispatched task status")
    status.set_defaults(func=cmd_status)

    wait = sub.add_parser("wait", help="block until tasks go idle")
    wait.add_argument("names", nargs="*")
    wait.set_defaults(func=cmd_wait)

    finish = sub.add_parser("finish", help="commit/push/PR finished tasks")
    finish.add_argument("names", nargs="*")
    finish.add_argument("--dry-run", action="store_true")
    finish.set_defaults(func=cmd_finish)

    logs = sub.add_parser("logs", help="show a task's recent agent output")
    logs.add_argument("name")
    logs.set_defaults(func=cmd_logs)

    attach = sub.add_parser("attach", help="open a task's live terminal")
    attach.add_argument("name")
    attach.set_defaults(func=cmd_attach)

    rm = sub.add_parser("rm", help="remove a task's worktree + state")
    rm.add_argument("target", help="task name or --all")
    rm.set_defaults(func=cmd_rm)

    clean = sub.add_parser("clean", help="drop state for tasks whose worktree is gone")
    clean.set_defaults(func=cmd_clean)

    return p


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
