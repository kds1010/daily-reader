import json
from pathlib import Path

from daily_reader.agent_worker import (
    _codex_command,
    _initial_prompt,
    _parse_codex_events,
    build_parser,
)


def test_agent_worker_defaults(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["daily-reader-agent-worker"])

    args = build_parser().parse_args()

    assert args.database == Path("data/agent.sqlite3")
    assert args.repositories == Path("config/agent-repositories.toml")
    assert args.schema == Path("config/agent-result-schema.json")
    assert args.poll_seconds == 5
    assert args.max_workers == 2
    assert not args.once


def test_agent_worker_accepts_configured_parallelism(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv", ["daily-reader-agent-worker", "--max-workers", "4"]
    )

    args = build_parser().parse_args()

    assert args.max_workers == 4


def test_parse_codex_events_extracts_thread_and_messages() -> None:
    output = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Implemented the change"},
                }
            ),
            "not-json",
        ]
    )

    thread_id, messages = _parse_codex_events(output)

    assert thread_id == "thread-1"
    assert messages == ["Implemented the change"]


def test_initial_codex_command_uses_automatic_workspace_approval() -> None:
    command = _codex_command(
        Path("/tmp/worktree"),
        Path("schema.json"),
        "Complete the task",
        None,
        Path("result.json"),
    )

    assert "--approve-for-me" in command
    assert "--sandbox" not in command


def test_requirements_prompt_requires_discovery_before_implementation() -> None:
    prompt = _initial_prompt("Add a better workflow", "requirements")

    assert "do not change files" in prompt
    assert "return state=blocked" in prompt
    assert "After the user answers" in prompt
