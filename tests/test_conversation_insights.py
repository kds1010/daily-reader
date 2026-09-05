from __future__ import annotations

import json
import subprocess
from pathlib import Path

from daily_reader.conversation_insights import (
    chunk_utterances,
    codex_available,
    request_insights,
)


def test_chunk_utterances_preserves_every_character() -> None:
    utterances = [{"id": "u1", "text": "abcdefghijk", "speaker": "話者1"}]

    chunks = chunk_utterances(utterances, max_characters=4)

    assert "".join(str(chunk[0]["text"]) for chunk in chunks) == "abcdefghijk"
    assert [chunk[0]["part"] for chunk in chunks] == [1, 2, 3]


def test_request_insights_uses_ephemeral_read_only_codex_with_schema(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    monkeypatch.setenv("CODEX_API_KEY", "must-not-be-used")

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        if command[1:] == ["login", "status"]:
            return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")
        output_index = command.index("--output-last-message") + 1
        Path(command[output_index]).write_text(
            json.dumps(
                {
                    "items": [
                        {
                            "kind": "task",
                            "title": "資料を確認する",
                            "detail": "",
                            "assignee": None,
                            "due_date": None,
                            "due_date_original": None,
                            "certainty": "explicit",
                            "evidence_utterance_ids": ["u1"],
                        }
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("daily_reader.conversation_insights.subprocess.run", fake_run)

    items = request_insights(
        codex_command="/usr/local/bin/codex",
        model="gpt-5.6-luna",
        schema_path=schema,
        recorded_at="2026-09-06T09:00:00+09:00",
        timezone="Asia/Tokyo",
        utterances=[{"id": "u1", "text": "資料を確認します"}],
    )

    command = captured["command"]
    assert command[:2] == ["/usr/local/bin/codex", "exec"]
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert "--ignore-rules" in command
    assert command[command.index("--sandbox") + 1] == "read-only"
    assert "--skip-git-repo-check" in command
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert command[command.index("--output-schema") + 1] == str(schema.resolve())
    assert "資料を確認します" not in " ".join(command)
    kwargs = captured["kwargs"]
    assert json.loads(str(kwargs["input"]))["utterances"] == [
        {"id": "u1", "text": "資料を確認します"}
    ]
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "CODEX_API_KEY" not in kwargs["env"]
    assert items[0]["title"] == "資料を確認する"


def test_codex_available_requires_successful_chatgpt_login(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, "Logged in using ChatGPT", "")

    monkeypatch.setattr("daily_reader.conversation_insights.subprocess.run", fake_run)

    assert codex_available("codex") is True
    assert captured["command"] == ["codex", "login", "status"]


def test_codex_available_is_false_when_command_is_missing(monkeypatch) -> None:
    def missing_command(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError

    monkeypatch.setattr("daily_reader.conversation_insights.subprocess.run", missing_command)

    assert codex_available("missing-codex") is False


def test_codex_available_rejects_api_key_login(monkeypatch) -> None:
    monkeypatch.setattr(
        "daily_reader.conversation_insights.subprocess.run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command, 0, "Logged in using an API key", ""
        ),
    )

    assert codex_available("codex") is False
