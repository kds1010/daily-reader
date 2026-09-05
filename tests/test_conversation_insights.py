from __future__ import annotations

import json

from daily_reader.conversation_insights import (
    api_key_available,
    chunk_utterances,
    request_insights,
)


class FakeResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode()


def test_chunk_utterances_preserves_every_character() -> None:
    utterances = [{"id": "u1", "text": "abcdefghijk", "speaker": "話者1"}]

    chunks = chunk_utterances(utterances, max_characters=4)

    assert "".join(str(chunk[0]["text"]) for chunk in chunks) == "abcdefghijk"
    assert [chunk[0]["part"] for chunk in chunks] == [1, 2, 3]


def test_request_insights_uses_non_stored_structured_response_without_tools(
    monkeypatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(
            {
                "status": "completed",
                "output": [
                    {
                        "type": "message",
                        "content": [
                            {
                                "type": "output_text",
                                "text": json.dumps(
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
                            }
                        ],
                    }
                ],
            }
        )

    monkeypatch.setattr(
        "daily_reader.conversation_insights.urllib.request.urlopen", fake_urlopen
    )

    items = request_insights(
        api_key="secret-key",
        model="gpt-5-mini",
        schema={"type": "object"},
        recorded_at="2026-09-06T09:00:00+09:00",
        timezone="Asia/Tokyo",
        utterances=[{"id": "u1", "text": "資料を確認します"}],
    )

    request = captured["request"]
    body = json.loads(request.data)
    assert body["store"] is False
    assert body["model"] == "gpt-5-mini"
    assert body["text"]["format"]["type"] == "json_schema"
    assert "tools" not in body
    assert json.loads(body["input"])["utterances"] == [
        {"id": "u1", "text": "資料を確認します"}
    ]
    assert request.headers["Authorization"] == "Bearer secret-key"
    assert items[0]["title"] == "資料を確認する"


def test_api_key_available_requires_non_empty_readable_file(tmp_path) -> None:
    missing = tmp_path / "missing"
    empty = tmp_path / "empty"
    configured = tmp_path / "configured"
    empty.write_text("\n")
    configured.write_text("secret\n")

    assert api_key_available(missing) is False
    assert api_key_available(empty) is False
    assert api_key_available(configured) is True
