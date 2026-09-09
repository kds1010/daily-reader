from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from daily_reader.conversation_insights import (
    ConversationInsightError,
    chunk_utterances,
    codex_available,
    request_insights,
    request_overview,
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
                            "evidence_utterance_ids": ["u0001"],
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
        {"id": "u0001", "text": "資料を確認します"}
    ]
    assert "OPENAI_API_KEY" not in kwargs["env"]
    assert "CODEX_API_KEY" not in kwargs["env"]
    assert items[0]["title"] == "資料を確認する"
    assert items[0]["evidence_utterance_ids"] == ["u1"]


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


@pytest.mark.parametrize("overview", [False, True])
@pytest.mark.parametrize("ids", [
    ["u0001"], ["missing"], [" u0001"], ["u0001 "], ["original-uuid"],
    ["u0001", "u0001"], [], ["u0002"],
])
def test_short_ids_are_strictly_restored_for_items_and_overview(
    monkeypatch, tmp_path, overview, ids,
):
    from daily_reader import conversation_insights as insights

    schema = tmp_path / "schema"
    name = "conversation-overview-schema.json" if overview else "conversation-insight-schema.json"
    schema.write_bytes((Path(__file__).resolve().parents[1] / "config" / name).read_bytes())

    def fake_run(command, **kwargs):
        payload = json.loads(kwargs["input"])
        assert payload["utterances"][0]["id"] == "u0001"
        assert "original-uuid" not in kwargs["input"]
        point = {"text": "資料の確認について話しました", "evidence_utterance_ids": ids}
        data = {"overview": {"points": [point]}} if overview else {"items": [point]}
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(data))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(insights, "codex_available", lambda _: True)
    monkeypatch.setattr(insights.subprocess, "run", fake_run)
    request = request_overview if overview else request_insights
    kwargs = {
        "codex_command": "unused", "model": "unused", "schema_path": tmp_path / "schema",
        "recorded_at": None, "timezone": "Asia/Tokyo",
        "utterances": [{"id": "original-uuid", "text": "資料を確認します"}],
    }
    if ids == ["u0001"]:
        result = request(**kwargs)
        entries = result["points"] if overview else result
        assert entries[0]["evidence_utterance_ids"] == ["original-uuid"]
    else:
        with pytest.raises(ConversationInsightError, match="根拠発話"):
            request(**kwargs)


@pytest.mark.parametrize("overview", [False, True])
def test_evidence_schema_uses_only_request_aliases_without_changing_source(
    monkeypatch, tmp_path, overview,
):
    from daily_reader import conversation_insights as insights

    name = "conversation-overview-schema.json" if overview else "conversation-insight-schema.json"
    source = Path(__file__).resolve().parents[1] / "config" / name
    schema = tmp_path / name
    original = source.read_bytes()
    schema.write_bytes(original)
    captured = []

    def fake_run(command, **kwargs):
        scoped_path = Path(command[command.index("--output-schema") + 1])
        scoped = json.loads(scoped_path.read_text())
        original_schema = json.loads(original)
        assert scoped_path != schema
        assert scoped_path.parent == Path(kwargs["cwd"]).resolve()
        properties = scoped["properties"]
        base_properties = original_schema["properties"]
        if overview:
            properties = properties["overview"]["properties"]["points"]
            base_properties = base_properties["overview"]["properties"]["points"]
        else:
            properties = properties["items"]
            base_properties = base_properties["items"]
        evidence = properties["items"]["properties"]["evidence_utterance_ids"]
        original_evidence = base_properties["items"]["properties"]["evidence_utterance_ids"]
        payload = json.loads(kwargs["input"])
        expected = [row["id"] for row in payload["utterances"]]
        assert evidence["items"]["enum"] == expected
        assert evidence["minItems"] == original_evidence["minItems"] == 1
        assert evidence["maxItems"] == original_evidence["maxItems"] == 8
        del evidence["items"]["enum"]
        assert scoped == original_schema  # No other output constraints change.
        assert "original-" not in scoped_path.read_text()
        captured.append((scoped_path, expected))
        point = {"text": "匿名の要点", "evidence_utterance_ids": [expected[-1]]}
        output = {"overview": {"points": [point]}} if overview else {"items": [point]}
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(output))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(insights, "codex_available", lambda _: True)
    monkeypatch.setattr(insights.subprocess, "run", fake_run)
    request = request_overview if overview else request_insights
    for ids in (["original-a", "original-b"], ["original-c"]):
        result = request(
            codex_command="unused", model="unused", schema_path=schema,
            recorded_at=None, timezone="Asia/Tokyo",
            utterances=[{"id": value, "text": "匿名発言"} for value in ids],
        )
        points = result["points"] if overview else result
        assert points[0]["evidence_utterance_ids"] == [ids[-1]]
        assert schema.read_bytes() == original
    assert captured[0][1] == ["u0001", "u0002"]
    assert captured[1][1] == ["u0001"]
    assert all(not path.exists() for path, _ in captured)


@pytest.mark.parametrize("content", [None, b"invalid", b"[]", b"\xff"])
def test_unreadable_evidence_schema_fails_before_model_execution(monkeypatch, tmp_path, content):
    from daily_reader import conversation_insights as insights

    schema = tmp_path / "schema"
    if content is not None:
        schema.write_bytes(content)
    monkeypatch.setattr(insights, "codex_available", lambda _: True)
    monkeypatch.setattr(insights.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(ConversationInsightError, match="スキーマ"):
        request_insights(
            codex_command="unused", model="unused", schema_path=schema,
            recorded_at=None, timezone="Asia/Tokyo",
            utterances=[{"id": "original-a", "text": "匿名発言"}],
        )


def test_empty_evidence_scope_cannot_produce_ungrounded_entries(monkeypatch):
    from daily_reader import conversation_insights as insights

    monkeypatch.setattr(insights, "codex_available", lambda _: True)
    monkeypatch.setattr(insights.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    schema = Path(__file__).resolve().parents[1] / "config/conversation-overview-schema.json"
    with pytest.raises(ConversationInsightError, match="根拠となる発話"):
        request_overview(
            codex_command="unused", model="unused", schema_path=schema,
            recorded_at=None, timezone="Asia/Tokyo", utterances=[],
        )


@pytest.mark.parametrize("items", [None, False, "wrong"])
def test_malformed_evidence_schema_is_rejected(monkeypatch, tmp_path, items):
    from daily_reader import conversation_insights as insights

    schema = tmp_path / "schema"
    schema.write_text(json.dumps({"properties": {
        "evidence_utterance_ids": {"type": "array", "items": items}
    }}))
    monkeypatch.setattr(insights, "codex_available", lambda _: True)
    monkeypatch.setattr(insights.subprocess, "run", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(ConversationInsightError, match="根拠スキーマ"):
        request_insights(
            codex_command="unused", model="unused", schema_path=schema,
            recorded_at=None, timezone="Asia/Tokyo",
            utterances=[{"id": "original-a", "text": "匿名発言"}],
        )


@pytest.mark.parametrize("stage", ["read", "write", "model"])
def test_evidence_schema_failures_are_safe_and_cleanup_temporary_copy(
    monkeypatch, stage,
):
    from daily_reader import conversation_insights as insights

    schema = Path(__file__).resolve().parents[1] / "config/conversation-overview-schema.json"
    captured = []
    real_read, real_write = Path.read_text, Path.write_text

    def read(path, *args, **kwargs):
        if path == schema and stage == "read":
            raise PermissionError("private-path-not-for-display")
        return real_read(path, *args, **kwargs)

    def write(path, *args, **kwargs):
        if path.name == "evidence-schema.json":
            captured.append(path)
            if stage == "write":
                raise PermissionError("private-path-not-for-display")
        return real_write(path, *args, **kwargs)

    def fail_model(command, **kwargs):
        assert stage == "model"
        raise subprocess.CalledProcessError(1, command, stderr="private-model-output")

    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr(Path, "write_text", write)
    monkeypatch.setattr(insights, "codex_available", lambda _: True)
    monkeypatch.setattr(insights.subprocess, "run", fail_model)
    with pytest.raises(ConversationInsightError) as raised:
        request_overview(
            codex_command="unused", model="unused", schema_path=schema,
            recorded_at=None, timezone="Asia/Tokyo",
            utterances=[{"id": "original-a", "text": "匿名発言"}],
        )
    assert "private" not in str(raised.value)
    assert "CLIが見つかりません" not in str(raised.value)
    assert all(not path.exists() and not path.parent.exists() for path in captured)
