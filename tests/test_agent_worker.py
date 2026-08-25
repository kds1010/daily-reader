import json
from pathlib import Path

from daily_reader.agent_worker import (
    _codex_command,
    _deployment_prompt,
    _follow_up_prompt,
    _initial_prompt,
    _parse_codex_events,
    build_parser,
    resolve_schema_path,
    run_deployment_turn,
)


def test_agent_worker_defaults(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["daily-reader-agent-worker"])

    args = build_parser().parse_args()

    assert args.database == Path("data/agent.sqlite3")
    assert args.repositories == Path("config/agent-repositories.toml")
    assert args.schema == Path("config/agent-result-schema.json")
    assert args.poll_seconds == 5
    assert args.max_workers == 10
    assert not args.once


def test_agent_worker_accepts_configured_parallelism(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv", ["daily-reader-agent-worker", "--max-workers", "4"]
    )

    args = build_parser().parse_args()

    assert args.max_workers == 4


def test_schema_path_is_resolved_before_using_external_worktree(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    schema = Path("config/agent-result-schema.json")

    assert resolve_schema_path(schema) == tmp_path / schema
    assert resolve_schema_path(schema).is_absolute()


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


def test_deployment_starts_fresh_automatic_approval_session(monkeypatch) -> None:
    calls = []

    def fake_run_codex_turn(worktree, schema, prompt, thread_id):
        calls.append((worktree, schema, prompt, thread_id))
        return "deployment-thread", {"state": "done"}, "deployed"

    monkeypatch.setattr(
        "daily_reader.agent_worker.run_codex_turn", fake_run_codex_turn
    )

    result = run_deployment_turn(Path("/tmp/worktree"), Path("schema.json"), "Deploy")

    assert calls == [(Path("/tmp/worktree"), Path("schema.json"), "Deploy", None)]
    assert result[0] == "deployment-thread"


def test_requirements_prompt_requires_discovery_before_implementation() -> None:
    prompt = _initial_prompt("Add a better workflow", "requirements")

    assert "do not change files" in prompt
    assert "return state=blocked" in prompt
    assert "After the user answers" in prompt


def test_deployment_prompt_requires_live_verification_before_done() -> None:
    prompt = _deployment_prompt("abc123")

    assert "integrated and pushed commit abc123" in prompt
    assert "Read all applicable AGENTS.md deployment instructions again" in prompt
    assert "Return state=done only after deployment and live verification succeed" in prompt
    assert "runtime changes remain undeployed or unverified" in prompt


def test_follow_up_prompt_is_read_only_and_uses_completion_context() -> None:
    prompt = _follow_up_prompt(
        "Add task summaries",
        "Added a completion summary and verified the UI",
        ["Which checks ran?"],
    )

    assert "Add task summaries" in prompt
    assert "Added a completion summary and verified the UI" in prompt
    assert "Which checks ran?" in prompt
    assert "read-only confirmation conversation" in prompt
    assert "do not edit files" in prompt
