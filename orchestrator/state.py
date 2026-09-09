"""Local task state: ~/.orchestrator/state.json maps task name -> metadata."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

STATE_DIR = Path(os.environ.get("ORCHESTRATOR_STATE_DIR", os.path.expanduser("~/.orchestrator")))
STATE_FILE = STATE_DIR / "state.json"


def _load() -> dict[str, Any]:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return {}
    return json.loads(STATE_FILE.read_text())


def _save(data: dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=2))


def get(name: str) -> dict[str, Any] | None:
    return _load().get(name)


def set(name: str, entry: dict[str, Any]) -> None:
    data = _load()
    data[name] = entry
    _save(data)


def remove(name: str) -> None:
    data = _load()
    data.pop(name, None)
    _save(data)


def names() -> list[str]:
    return list(_load().keys())


def require(name: str) -> dict[str, Any]:
    entry = get(name)
    if entry is None:
        raise KeyError(f"no known task named '{name}' (run 'orchestrate status' to list)")
    return entry
