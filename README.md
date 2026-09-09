# claude-orchestrator

Dispatch parallel Claude Code agents across one or more git repos, each in
its own isolated [herdr](https://herdr.dev) worktree, so independent
subtasks run at the same time instead of serially.

## How to initiate this with Claude

**Normal way — just ask, in any Claude Code session:**

```
dispatch two agents in parallel: fix the HA automation in
homeassistant-config, and add the matching DNS record in IAC
```

Claude Code Code has a global skill at `~/.claude/skills/orchestrate/SKILL.md`
that triggers automatically on requests like this (multi-repo, or an
explicit "run in parallel" / "dispatch" / "fan out" ask). When it triggers,
Claude:

1. Reads `repos.md` (below) to figure out which repo each part of the
   request belongs to
2. Writes a self-contained prompt per repo (dispatched agents start cold,
   with no memory of the conversation) and a `tasks.json`
3. Runs `orchestrate run -f tasks.json`, then `orchestrate wait`
4. Runs `orchestrate finish --dry-run` and shows you what it would push
   before ever actually pushing or opening a PR — it will not do that
   without asking first

A single-repo, single-subtask request does not need this — Claude just does
the work directly. This only kicks in when there's real parallel, multi-repo
work to fan out.

**Manual way — run it yourself:**

```sh
cat > tasks.json <<'EOF'
[
  {"repo": "/path/to/repo-a", "name": "task-a", "prompt": "..."},
  {"repo": "/path/to/repo-b", "name": "task-b", "prompt": "..."}
]
EOF
./orchestrate run -f tasks.json
./orchestrate wait
./orchestrate status
./orchestrate finish --dry-run   # preview before pushing anything
./orchestrate finish             # push + open PRs for real
```

Or dispatch a single task inline without a file:

```sh
./orchestrate run "/path/to/repo::task-name::do the thing"
```

## Requirements

- [`herdr`](https://herdr.dev) — installed and its `claude` integration
  active (`herdr integration status` should show `claude: current`)
- `git`, `gh` (GitHub CLI, authenticated), `python3` (stdlib only, no pip
  installs needed)
- `orchestrate` starts the herdr server itself if it isn't already running

## Commands

| Command | What it does |
|---|---|
| `run -f tasks.json` / `run repo::name::prompt` | Dispatch one or more tasks in parallel |
| `status` | Show each dispatched task's live status |
| `wait [names...]` | Block until tasks go idle (default: all) |
| `finish [names...] [--dry-run]` | Commit (in-worktree only) + push + open a PR |
| `logs <name>` | Show a task's recent agent output |
| `attach <name>` | Open a task's live terminal |
| `rm <name\|--all>` | Remove a task's worktree + drop its state |
| `clean` | Drop state for tasks whose worktree is already gone |

## How it works

`orchestrator/herdr_client.py` talks to herdr's local unix socket directly
(newline-delimited JSON, one connection per request, `events.subscribe` for
push-based waiting) rather than shelling out to the `herdr` CLI and parsing
its stdout. Every response is checked for an explicit `error` field, so
there's no ambiguous exit code to misinterpret — a dead server or lost agent
always surfaces as a clear failure, never a silent false success.

`dispatch_task` always runs the agent as `claude -p ...` (headless,
non-interactive). This matters for two reasons:

- It's the only thing that gets `--cwd` right: `herdr agent.start` does
  **not** inherit the worktree's checkout path on its own — it defaults to
  the caller's own cwd. Always pass `--cwd` explicitly. (This bit us once,
  for real — a test dispatch without it committed into the actual IAC repo
  instead of an isolated worktree.)
- It means a dispatched agent can never render an interactive dialog (like
  the workspace-trust prompt) and go `blocked` waiting on a human who isn't
  there — `-p` explicitly skips that. `wait_for_status`'s `blocked`
  detection exists and is verified working, but for real dispatches it's
  effectively dead code, kept as a defensive backstop.

`finish` always does its own `git add -A && commit` **inside the task's own
worktree only** — it never trusts the dispatched agent to have committed
correctly. This is intentional: under `-p` with `--permission-mode
acceptEdits`, an unanswerable Bash-tool permission prompt (e.g. `git
commit`) gets silently auto-denied rather than blocking, so the agent can
finish "successfully" having skipped a requested step. Because the worktree
is exclusive to one task, committing everything in it afterward is safe by
construction, regardless of what the agent did or didn't run itself.

## Known limitation

The silent-auto-deny behavior above is a real, currently unhandled gap: if
a dispatched task's prompt depends on some *other* Bash-gated action beyond
the commit (which `finish` doesn't cover), that action can be silently
skipped with no signal that anything went wrong. `wait_for_status` can't
catch this — there's no status transition to observe, since the agent just
continues and reports `idle`. Catching it would need inspecting the agent's
actual output/diff against what the prompt asked for, which isn't
implemented yet.
