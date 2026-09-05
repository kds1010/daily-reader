from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from daily_reader.conversations import (
    _replace_analysis_results,
    extract_recording_insights,
    get_recording,
    initialize_database,
    list_insight_items,
    list_recordings,
    mark_proposal_approved,
    match_recording_location,
    prepare_insight_item,
    review_insight_item,
    store_location_events,
    store_transcript,
    store_upload,
    update_speaker,
)


@pytest.fixture(autouse=True)
def allow_test_audio_storage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("daily_reader.conversations.MINIMUM_FREE_BYTES", 0)


def test_upload_keeps_original_and_deduplicates(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    audio = tmp_path / "audio"
    content = b"ID3" + b"recording" * 100

    first = store_upload(database, audio, io.BytesIO(content), len(content), "meeting.mp3")
    second = store_upload(database, audio, io.BytesIO(content), len(content), "copy.mp3")

    assert first["id"] == second["id"]
    assert (audio / str(first["id"]) / "original.mp3").read_bytes() == content
    assert len(list(audio.glob("*/original.mp3"))) == 1


def test_upload_rejects_non_mp3(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only MP3"):
        store_upload(tmp_path / "db", tmp_path / "audio", io.BytesIO(b"x"), 1, "x.wav")


def test_transcript_upload_is_completed_and_classified(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    content = "\ufeff資料を確認してください\n\n明日の会議を予定します\n".encode()

    item = store_transcript(
        database,
        io.BytesIO(content),
        len(content),
        "meeting.txt",
        "2026-09-04T10:00:00+00:00",
    )

    assert item["source_type"] == "transcript"
    assert item["status"] == "completed"
    assert item["recorded_at"] == "2026-09-04T10:00:00+00:00"
    assert "transcript_text" not in item
    assert [utterance["text"] for utterance in item["utterances"]] == [
        "資料を確認してください",
        "明日の会議を予定します",
    ]
    assert all(utterance["speaker"] == "話者1" for utterance in item["utterances"])
    assert all(utterance["confidence"] is None for utterance in item["utterances"])
    assert {topic["name"] for topic in item["topics"]} == {
        "仕事・プロジェクト",
        "予定・調整",
    }
    assert [proposal["title"] for proposal in item["task_proposals"]] == [
        "資料を確認してください"
    ]
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT audio_path, transcript_text FROM recordings WHERE id=?", (item["id"],)
        ).fetchone()
    assert stored == ("", content.decode("utf-8-sig"))


def test_transcript_upload_deduplicates_exact_content(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    content = "対応してください".encode()

    first = store_transcript(database, io.BytesIO(content), len(content), "first.txt")
    second = store_transcript(database, io.BytesIO(content), len(content), "copy.txt")

    assert first["id"] == second["id"]
    assert len(list_recordings(database)) == 1


@pytest.mark.parametrize(
    ("content", "message"),
    [
        (b"\xff", "UTF-8"),
        (b"  \n\t", "empty"),
    ],
)
def test_transcript_upload_rejects_invalid_text(
    tmp_path: Path, content: bytes, message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        store_transcript(tmp_path / "db", io.BytesIO(content), len(content), "meeting.txt")


def test_transcript_upload_rejects_invalid_extension_and_size(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only TXT"):
        store_transcript(tmp_path / "db", io.BytesIO(b"text"), 4, "meeting.md")
    with pytest.raises(ValueError, match="invalid transcript size"):
        store_transcript(tmp_path / "db", io.BytesIO(b"text"), 10 * 1024**2 + 1, "x.txt")


def test_database_migration_marks_existing_recordings_as_audio(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE recordings (
            id TEXT PRIMARY KEY, filename TEXT NOT NULL, audio_path TEXT NOT NULL,
            sha256 TEXT NOT NULL UNIQUE, byte_size INTEGER NOT NULL,
            status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL, analyzed_at TEXT
            )"""
        )
        connection.execute(
            """INSERT INTO recordings VALUES
            ('old','old.mp3','/tmp/old','hash',3,'completed',NULL,'now','now')"""
        )

    initialize_database(database)

    assert list_recordings(database)[0]["source_type"] == "audio"


def test_location_events_match_recording_by_time(tmp_path: Path) -> None:
    database = tmp_path / "db"
    item = store_upload(
        database,
        tmp_path / "audio",
        io.BytesIO(b"ID3x"),
        4,
        "x.mp3",
        "2026-09-04T10:00:00+00:00",
    )
    store_location_events(
        database,
        [
            {
                "timestamp": "2026-09-04T09:59:30+00:00",
                "latitude": 35.0,
                "longitude": 139.0,
                "horizontal_accuracy": 20,
            }
        ],
    )
    match = match_recording_location(database, str(item["id"]))
    assert match and match["latitude"] == 35.0
    assert get_recording(database, str(item["id"]))["location_time_delta"] == 30.0


def test_location_match_ignores_distant_event(tmp_path: Path) -> None:
    database = tmp_path / "db"
    item = store_upload(
        database,
        tmp_path / "audio",
        io.BytesIO(b"ID3x"),
        4,
        "x.mp3",
        "2026-09-04T10:00:00+00:00",
    )
    store_location_events(
        database,
        [
            {
                "timestamp": "2026-09-04T20:00:00+00:00",
                "latitude": 35.0,
                "longitude": 139.0,
                "horizontal_accuracy": 20,
            }
        ],
    )
    assert match_recording_location(database, str(item["id"])) is None


def test_recording_returns_speakers_utterances_topics_and_tasks(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    item = store_upload(database, tmp_path / "audio", io.BytesIO(b"ID3x"), 4, "x.mp3")
    recording_id = str(item["id"])
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO speakers(id,recording_id,label) VALUES('s1',?,'話者1')", (recording_id,)
        )
        connection.execute(
            """INSERT INTO utterances VALUES
            ('u1',?,'s1',0,2,'資料を確認して','-0.1','会話','依頼・タスク')""",
            (recording_id,),
        )
        connection.execute(
            "INSERT INTO conversation_topics VALUES('t1',?,'仕事','会話','資料の確認')",
            (recording_id,),
        )
        connection.execute(
            """INSERT INTO task_proposals
            (id,recording_id,utterance_id,title,created_at)
            VALUES('p1',?,'u1','資料を確認','now')""",
            (recording_id,),
        )

    update_speaker(database, "s1", "田中さん")
    detail = get_recording(database, recording_id)
    assert detail["speakers"][0]["display_name"] == "田中さん"
    assert detail["utterances"][0]["speaker"] == "田中さん"
    assert detail["topics"][0]["name"] == "仕事"
    assert detail["task_proposals"][0]["status"] == "awaiting_review"

    mark_proposal_approved(database, "p1", "planner", "task-1")
    assert (
        get_recording(database, recording_id)["task_proposals"][0]["approved_item_id"] == "task-1"
    )
    with pytest.raises(ValueError, match="not awaiting review"):
        mark_proposal_approved(database, "p1", "agent", "job-1")


def test_legacy_task_proposals_migrate_to_reviewable_items(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    content = "資料を確認してください".encode()

    recording = store_transcript(database, io.BytesIO(content), len(content), "meeting.txt")

    items = recording["insight_items"]
    assert len(items) == 1
    assert items[0]["kind"] == "task"
    assert items[0]["source"] == "rule"
    assert items[0]["evidence"][0]["quote"] == "資料を確認してください"
    assert list_insight_items(database)[0]["recording_filename"] == "meeting.txt"


def test_llm_reanalysis_preserves_reviewed_items_and_supersedes_pending(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    database = tmp_path / "conversations.sqlite3"
    content = "金曜日までに資料を確認してください".encode()
    recording = store_transcript(
        database,
        io.BytesIO(content),
        len(content),
        "meeting.txt",
        "2026-09-06T09:00:00+09:00",
    )
    calls = 0

    def fake_request_insights(**kwargs: object) -> list[dict[str, object]]:
        nonlocal calls
        calls += 1
        assert set(kwargs["utterances"][0]) == {
            "id",
            "start_seconds",
            "end_seconds",
            "text",
            "speaker",
        }
        utterance_id = str(kwargs["utterances"][0]["id"])
        if calls == 1:
            return [
                {
                    "kind": "decision",
                    "title": "資料を確認する方針",
                    "detail": "会議で決めた",
                    "assignee": None,
                    "due_date": None,
                    "due_date_original": None,
                    "certainty": "explicit",
                    "evidence_utterance_ids": [utterance_id],
                },
                {
                    "kind": "task",
                    "title": "資料を確認する",
                    "detail": "金曜日までに確認する",
                    "assignee": "話者1",
                    "due_date": "2026-09-11",
                    "due_date_original": "金曜日まで",
                    "certainty": "explicit",
                    "evidence_utterance_ids": [utterance_id],
                },
            ]
        return [
            {
                "kind": "friction",
                "title": "資料確認に時間がかかる",
                "detail": "確認工程を改善する余地がある",
                "assignee": None,
                "due_date": None,
                "due_date_original": None,
                "certainty": "inferred",
                "evidence_utterance_ids": [utterance_id],
            }
        ]

    monkeypatch.setattr(
        "daily_reader.conversations.request_insights", fake_request_insights
    )
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')

    extract_recording_insights(database, str(recording["id"]), schema)
    first_items = list_insight_items(database)
    decision = next(item for item in first_items if item["kind"] == "decision")
    review_insight_item(database, str(decision["id"]), {"action": "keep"})

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE utterances SET text=? WHERE recording_id=?",
            ("資料の確認に毎回時間がかかります", recording["id"]),
        )
    extract_recording_insights(database, str(recording["id"]), schema)

    kept = list_insight_items(database, "kept")
    pending = list_insight_items(database)
    superseded = list_insight_items(database, "superseded")
    assert [item["title"] for item in kept] == ["資料を確認する方針"]
    assert [item["kind"] for item in pending] == ["friction"]
    assert {item["title"] for item in superseded} >= {
        "資料を確認する",
        "金曜日までに資料を確認してください",
    }
    assert get_recording(database, str(recording["id"]))["insight_status"] == "completed"


def test_review_edits_fields_and_validates_due_date(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    content = "資料を確認してください".encode()
    recording = store_transcript(database, io.BytesIO(content), len(content), "meeting.txt")
    item_id = str(recording["insight_items"][0]["id"])

    updated = prepare_insight_item(
        database,
        item_id,
        {
            "title": "設計資料を確認する",
            "detail": "第2章を重点的に確認する",
            "assignee": "自分",
            "due_date": "2026-09-10",
        },
    )

    assert updated["title"] == "設計資料を確認する"
    assert updated["due_date"] == "2026-09-10"
    with pytest.raises(ValueError, match="due date"):
        prepare_insight_item(database, item_id, {"due_date": "明日"})


def test_replacing_transcript_invalidates_only_unreviewed_insights(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    content = "資料を確認してください".encode()
    recording = store_transcript(database, io.BytesIO(content), len(content), "meeting.txt")
    item_id = str(recording["insight_items"][0]["id"])
    review_insight_item(database, item_id, {"action": "keep"})
    with sqlite3.connect(database) as connection:
        connection.execute(
            """INSERT INTO conversation_items
            (id, recording_id, kind, title, detail, certainty, status, source,
             extractor_version, fingerprint, created_at, updated_at)
            VALUES('pending',?,'idea','自動化する','','explicit','awaiting_review',
                   'openai','conversation-insights-v1','pending','now','now')""",
            (recording["id"],),
        )
        _replace_analysis_results(
            connection,
            str(recording["id"]),
            [(0, 1, "別の文字起こし", None, "話者1")],
            "later",
        )

    assert list_insight_items(database, "kept")[0]["id"] == item_id
    assert {item["id"] for item in list_insight_items(database, "superseded")} >= {"pending"}
    assert get_recording(database, str(recording["id"]))["insight_status"] == "not_requested"


@pytest.mark.parametrize("evidence_mode", ["unknown", "duplicate"])
def test_invalid_llm_evidence_keeps_current_inbox_items(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, evidence_mode: str
) -> None:
    database = tmp_path / "conversations.sqlite3"
    content = "資料を確認してください".encode()
    recording = store_transcript(database, io.BytesIO(content), len(content), "meeting.txt")
    existing_id = str(recording["insight_items"][0]["id"])
    utterance_id = str(get_recording(database, str(recording["id"]))["utterances"][0]["id"])
    evidence_ids = ["unknown"] if evidence_mode == "unknown" else [utterance_id, utterance_id]
    monkeypatch.setattr(
        "daily_reader.conversations.request_insights",
        lambda **_kwargs: [
            {
                "kind": "task",
                "title": "根拠のない候補",
                "detail": "",
                "assignee": None,
                "due_date": None,
                "due_date_original": None,
                "certainty": "explicit",
                "evidence_utterance_ids": evidence_ids,
            }
        ],
    )
    schema = tmp_path / "schema.json"
    schema.write_text('{"type":"object"}')

    extract_recording_insights(database, str(recording["id"]), schema)

    assert [item["id"] for item in list_insight_items(database)] == [existing_id]
    failed = get_recording(database, str(recording["id"]))
    assert failed["insight_status"] == "failed"
    assert failed["insight_error"] == "Codexの根拠発話が不正です"
