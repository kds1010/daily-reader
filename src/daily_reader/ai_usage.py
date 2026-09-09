"""Content-free Codex usage records shared with the TSUGITATE daily report.

This module intentionally uses only the standard library: Daymeld can report
usage without installing TSUGITATE or invoking another agent process.
"""

from __future__ import annotations

import fcntl
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

_CONTEXT: ContextVar[tuple[str, str | None]] = ContextVar(
    "daymeld_usage", default=("daymeld-codex", None)
)
_TOKENS = ("input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_input_tokens")


@contextmanager
def usage_context(task_type: str, task_id: str | None = None):
    """Give nested requests a task identity, isolated between worker threads."""
    token = _CONTEXT.set((task_type, task_id if task_id is not None else _CONTEXT.get()[1]))
    try:
        yield
    finally:
        _CONTEXT.reset(token)


def _option(command: list[str], flag: str) -> str | None:
    try:
        return command[command.index(flag) + 1]
    except (ValueError, IndexError):
        return None


def _count(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _append(record: dict) -> None:
    target = os.environ.get("TSUGITATE_USAGE_LOG")
    state = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    path = Path(target).expanduser() if target else state / "tsugitate/ai-usage.jsonl"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "a", encoding="utf-8") as stream:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX)
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
            stream.flush()
    except (OSError, ValueError, TypeError):
        print("Daymeld: AI usage could not be recorded.", file=sys.stderr)


class UsageCapture:
    """Observe JSON events; never retain prompts, tool output, or model messages."""

    def __init__(self, command: list[str]):
        self.started_at = datetime.now(UTC).isoformat()
        self.started = time.monotonic()
        self.task_type, self.task_id = _CONTEXT.get()
        self.requested_model = _option(command, "--model")
        self.requested_effort = None
        for index, value in enumerate(command[:-1]):
            if value in ("--config", "-c") and command[index + 1].startswith(
                "model_reasoning_effort="
            ):
                self.requested_effort = command[index + 1].split("=", 1)[1].strip('"\'')
        self.return_code: int | None = None
        self.status: str | None = None
        self.tokens = dict.fromkeys(_TOKENS)
        self.models: dict[str, dict] = {}
        self.model: str | None = None
        self.has_usage = False

    def observe(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        # Only explicit runtime metadata establishes the actual model. A requested
        # alias/config value and an agent's prose are not execution evidence.
        if kind in ("thread.started", "turn.started", "turn_context", "session_meta"):
            payload = event.get("payload")
            model = payload.get("model") if isinstance(payload, dict) else event.get("model")
            if isinstance(model, str) and model and len(model) <= 200:
                self.model = model
        if kind != "turn.completed" or not isinstance(event.get("usage"), dict):
            return
        values = {key: _count(event["usage"].get(key)) for key in _TOKENS}
        if not any(value is not None for value in values.values()):
            return
        self.has_usage = True
        model_totals = None
        if self.model is not None:
            model_totals = self.models.setdefault(
                self.model, {"model": self.model, **dict.fromkeys(_TOKENS), "cost_usd": None}
            )
        for key, value in values.items():
            if value is not None:
                self.tokens[key] = (self.tokens[key] or 0) + value
                if model_totals is not None:
                    model_totals[key] = (model_totals[key] or 0) + value

    def observe_output(self, output: str | bytes | None) -> None:
        if isinstance(output, bytes):
            output = output.decode("utf-8", errors="replace")
        if not isinstance(output, str):
            return
        for line in output.splitlines():
            try:
                self.observe(json.loads(line))
            except (ValueError, TypeError):
                continue

    def observe_file(self, stream) -> None:
        try:
            stream.flush()
            stream.seek(0)
            for line in stream:
                self.observe_output(line)
        except (OSError, UnicodeError):
            print("Daymeld: AI usage could not be recorded.", file=sys.stderr)

    def __enter__(self):
        return self

    def __exit__(self, exception_type, exception, traceback):
        if isinstance(exception, subprocess.TimeoutExpired):
            self.status = "timeout"
        if exception is not None:
            self.observe_output(getattr(exception, "stdout", None))
        if isinstance(exception, subprocess.CalledProcessError):
            self.return_code = exception.returncode
        status = self.status or (
            "error" if exception is not None
            else "cancelled" if self.return_code is not None and self.return_code < 0
            else "error" if self.return_code not in (None, 0)
            else "success"
        )
        _append({
            "schema_version": 1,
            "event_id": str(uuid.uuid4()),
            "host": socket.gethostname(),
            "task_type": self.task_type,
            "task_id": self.task_id,
            "provider": "codex",
            "requested_model": self.requested_model,
            "requested_effort": self.requested_effort,
            "started_at": self.started_at,
            "finished_at": datetime.now(UTC).isoformat(),
            "duration_seconds": max(0, time.monotonic() - self.started),
            "status": status,
            "return_code": self.return_code,
            **self.tokens,
            "cost_usd": None,
            "cost_kind": "unavailable",
            "models": list(self.models.values()),
            "usage_source": "codex_json" if self.has_usage else "unavailable",
        })


def run_codex(command: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Preserve subprocess.run behavior while recording even partial failed usage."""
    with UsageCapture(command) as usage:
        result = subprocess.run(command, **kwargs)
        usage.return_code = getattr(result, "returncode", None)
        usage.observe_output(getattr(result, "stdout", None))
        return result
