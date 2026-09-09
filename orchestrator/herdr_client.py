"""Direct client for herdr's local unix socket (newline-delimited JSON).

herdr does not keep a connection open across requests - each request opens
its own connection, sends one JSON line, reads one JSON line back, and the
server closes it (confirmed empirically: reusing a connection for a second
request raises BrokenPipeError). Event subscriptions are the exception -
that connection stays open and receives pushed event lines.
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from typing import Any, Iterator


class HerdrError(Exception):
    """The socket responded with an {"error": ...} body."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class HerdrConnectionLost(Exception):
    """The socket connection dropped or refused (server not running/died)."""


@dataclass
class AgentBlocked(Exception):
    """An agent reached 'blocked' status while waiting for a different one."""

    name: str
    recent_output: str

    def __str__(self) -> str:
        return f"'{self.name}' is blocked"


def socket_path() -> str:
    return os.environ.get(
        "HERDR_SOCKET_PATH", os.path.expanduser("~/.config/herdr/herdr.sock")
    )


class HerdrClient:
    def __init__(self, timeout: float = 30.0):
        self.timeout = timeout

    def _connect(self) -> socket.socket:
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(self.timeout)
        try:
            s.connect(socket_path())
        except (FileNotFoundError, ConnectionRefusedError) as e:
            raise HerdrConnectionLost(str(e)) from e
        return s

    def request(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        req_id = uuid.uuid4().hex[:8]
        s = self._connect()
        try:
            s.sendall(
                (json.dumps({"id": req_id, "method": method, "params": params}) + "\n").encode()
            )
            f = s.makefile("r")
            line = f.readline()
        except (BrokenPipeError, ConnectionResetError, socket.timeout) as e:
            raise HerdrConnectionLost(str(e)) from e
        finally:
            s.close()

        if not line:
            raise HerdrConnectionLost("empty response (connection closed)")

        try:
            body = json.loads(line)
        except json.JSONDecodeError as e:
            raise HerdrConnectionLost(f"unparseable response from herdr: {line!r}") from e

        if "error" in body:
            err = body["error"]
            if isinstance(err, dict):
                raise HerdrError(err.get("code", "unknown"), err.get("message", str(err)))
            raise HerdrError("unknown", str(err))
        return body.get("result", {})

    def ensure_server(self, retries: int = 10, delay: float = 0.5) -> None:
        try:
            self.request("ping", {})
            return
        except HerdrConnectionLost:
            pass

        try:
            subprocess.Popen(
                ["herdr", "server"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as e:
            raise HerdrConnectionLost(f"could not start 'herdr server' ({e}) - is herdr installed and on PATH?") from e
        for _ in range(retries):
            time.sleep(delay)
            try:
                self.request("ping", {})
                return
            except HerdrConnectionLost:
                continue
        raise HerdrConnectionLost("herdr server did not come up after starting it")

    def agent_status(self, name: str) -> str:
        """idle | working | blocked | unknown | not_found"""
        try:
            result = self.request("agent.get", {"target": name})
        except HerdrError as e:
            if e.code == "agent_not_found":
                return "not_found"
            raise
        return result.get("agent", {}).get("agent_status", "unknown")

    def read_recent(self, name: str, lines: int = 40) -> str:
        try:
            result = self.request(
                "agent.read", {"target": name, "lines": lines, "source": "recent"}
            )
        except HerdrError:
            return "(could not read agent output)"
        return result.get("read", {}).get("text", "")

    def wait_for_status(self, name: str, target_status: str, timeout_ms: int) -> None:
        """Blocks until `name`'s agent reaches target_status via a live event
        subscription (not polling). Raises AgentBlocked if it reaches
        'blocked' instead, HerdrConnectionLost if the connection drops or
        herdr loses track of the agent, TimeoutError on timeout.
        """
        current = self.agent_status(name)
        if current == target_status:
            return
        if current == "not_found":
            raise HerdrConnectionLost(f"'{name}' not found (server restarted? worktree removed?)")
        if current == "blocked" and target_status != "blocked":
            raise AgentBlocked(name, self.read_recent(name))

        pane_id = self.request("agent.get", {"target": name})["agent"]["pane_id"]

        s = self._connect()
        deadline = time.time() + timeout_ms / 1000
        try:
            s.sendall(
                (
                    json.dumps(
                        {
                            "id": "sub",
                            "method": "events.subscribe",
                            "params": {
                                "subscriptions": [
                                    {"type": "pane.agent_status_changed", "pane_id": pane_id}
                                ]
                            },
                        }
                    )
                    + "\n"
                ).encode()
            )
            f = s.makefile("r")
            ack = f.readline()
            if not ack or "error" in json.loads(ack):
                raise HerdrConnectionLost("events.subscribe failed")

            while time.time() < deadline:
                s.settimeout(max(deadline - time.time(), 0.1))
                try:
                    line = f.readline()
                except socket.timeout as e:
                    raise TimeoutError(
                        f"'{name}' did not reach '{target_status}' within timeout"
                    ) from e
                if not line:
                    raise HerdrConnectionLost(
                        f"connection dropped while waiting for '{name}' "
                        "(server restarted?)"
                    )
                evt = json.loads(line)
                if evt.get("event") != "pane.agent_status_changed":
                    continue
                status = evt.get("data", {}).get("agent_status")
                if status == target_status:
                    return
                if status == "blocked":
                    raise AgentBlocked(name, self.read_recent(name))
        finally:
            s.close()

        raise TimeoutError(f"'{name}' did not reach '{target_status}' within timeout")

    def worktree_create(self, cwd: str, branch: str) -> tuple[str, str]:
        """Returns (workspace_id, worktree_path)."""
        result = self.request("worktree.create", {"cwd": cwd, "branch": branch})
        return result["workspace"]["workspace_id"], result["worktree"]["path"]

    def worktree_remove(self, workspace_id: str, force: bool = True) -> None:
        self.request("worktree.remove", {"workspace_id": workspace_id, "force": force})

    def agent_start(self, name: str, workspace_id: str, cwd: str, argv: list[str]) -> str:
        """Returns pane_id."""
        result = self.request(
            "agent.start",
            {"name": name, "workspace_id": workspace_id, "cwd": cwd, "argv": argv},
        )
        return result["agent"]["pane_id"]
