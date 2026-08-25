import json
from pathlib import Path

from daily_reader.agent_worker import _parse_codex_events, build_parser


def test_agent_worker_defaults(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["daily-reader-agent-worker"])

    args = build_parser().parse_args()

    assert args.database == Path("data/agent.sqlite3")
    assert args.repositories == Path("config/agent-repositories.toml")
    assert args.schema == Path("config/agent-result-schema.json")
    assert args.poll_seconds == 5
    assert not args.once


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
