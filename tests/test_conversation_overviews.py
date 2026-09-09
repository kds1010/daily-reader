from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest

from daily_reader import conversations as conv
from daily_reader import life_automation
from daily_reader.conversation_insights import ConversationInsightError

SCHEMA = Path(__file__).resolve().parents[1] / "config/conversation-insight-schema.json"


@pytest.fixture
def record(tmp_path):
    database = tmp_path / "conversations.sqlite3"
    text = "資料を確認してください\n次の打合せで構成を相談します".encode()
    item = conv.store_transcript(
        database,
        io.BytesIO(text),
        len(text),
        "meeting.txt",
        "2026-09-09T10:00:00+09:00",
    )
    return database, item


def fake_overview(**kwargs):
    return {
        "points": [
            {
                "text": "資料の確認と、次の打合せで構成を相談する予定を話しました。",
                "evidence_utterance_ids": [row["id"] for row in kwargs["utterances"][:2]],
            }
        ]
    }


def candidate_snapshot(database):
    with sqlite3.connect(database) as connection:
        return {
            name: connection.execute(f"SELECT * FROM {name} ORDER BY 1").fetchall()
            for name in (
                "conversation_items",
                "conversation_item_evidence",
                "task_proposals",
            )
        }


def test_overview_only_keeps_candidates_and_failed_insights_and_returns_grounded_digest(
    record,
    monkeypatch,
):
    database, item = record
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE recordings SET insight_status='failed',insight_error='old'")
    before = candidate_snapshot(database)
    monkeypatch.setattr(conv, "request_overview", fake_overview)
    conv.extract_recording_overview(database, item["id"], SCHEMA)
    detail = conv.get_recording(database, item["id"])
    assert candidate_snapshot(database) == before
    assert (detail["insight_status"], detail["insight_error"]) == ("failed", "old")
    assert detail["overview"]["status"] == "ready"
    assert detail["overview"]["generation_status"] == "completed"
    point = detail["overview"]["points"][0]
    assert point["evidence"][0]["utterance_id"] == item["utterances"][0]["id"]
    assert point["evidence"][0]["quote"] == "資料を確認してください"
    assert point["evidence"][0]["start_seconds"] is None
    assert detail["recorded_at_verified"] is True
    listed = conv.list_recordings(database)[0]
    assert listed["digest"]["summary"]["text"] == detail["overview"]["text"]
    assert listed["duration_seconds"] is None
    assert "utterances" not in listed and "transcription_metadata" not in listed
    assert listed["digest"]["counts"] == [{"kind": "task", "status": "awaiting_review", "count": 1}]


def test_old_topic_fragments_are_not_used_as_summary(record):
    database, item = record
    assert item["topics"]
    summary = conv.list_recordings(database)[0]["digest"]["summary"]
    assert summary["status"] == "not_requested"
    assert summary["text"] is None and summary["source"] is None


def test_failed_refresh_keeps_successful_summary_and_review_state(record, monkeypatch):
    database, item = record
    monkeypatch.setattr(conv, "request_overview", fake_overview)
    conv.extract_recording_overview(database, item["id"], SCHEMA)
    before = conv.get_recording(database, item["id"])["overview"]
    candidates = candidate_snapshot(database)

    def fail(**kwargs):
        raise ConversationInsightError("要約の取得に失敗しました")

    monkeypatch.setattr(conv, "request_overview", fail)
    conv.extract_recording_overview(database, item["id"], SCHEMA, model="another-model")
    after = conv.get_recording(database, item["id"])["overview"]
    assert after["status"] == "ready" and after["generation_status"] == "failed"
    assert after["text"] == before["text"] and after["points"] == before["points"]
    assert after["generated_at"] == before["generated_at"]
    assert candidate_snapshot(database) == candidates


@pytest.mark.parametrize(
    "point",
    [
        {"text": "要点", "evidence_utterance_ids": ["unknown"]},
        {"text": "", "evidence_utterance_ids": ["existing"]},
        {"text": "要点", "evidence_utterance_ids": []},
        {"text": "要点", "evidence_utterance_ids": ["existing", "existing"]},
        {"text": "あ" * 501, "evidence_utterance_ids": ["existing"]},
    ],
)
def test_invalid_overview_is_atomic_and_does_not_change_candidates(record, monkeypatch, point):
    database, item = record
    raw = {
        **point,
        "evidence_utterance_ids": [
            item["utterances"][0]["id"] if value == "existing" else value
            for value in point["evidence_utterance_ids"]
        ],
    }
    monkeypatch.setattr(conv, "request_overview", lambda **_: {"points": [raw]})
    before = candidate_snapshot(database)
    conv.extract_recording_overview(database, item["id"], SCHEMA)
    assert candidate_snapshot(database) == before
    assert conv.get_recording(database, item["id"])["overview"]["status"] == "failed"
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT COUNT(*) FROM conversation_overviews").fetchone()[0] == 0


def test_later_chunk_failure_does_not_save_partial_summary(record, monkeypatch):
    database, item = record
    monkeypatch.setattr(conv, "chunk_utterances", lambda rows: [[row] for row in rows])
    calls = 0

    def request(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise ConversationInsightError("取得に失敗しました")
        return fake_overview(**kwargs)

    monkeypatch.setattr(conv, "request_overview", request)
    conv.extract_recording_overview(database, item["id"], SCHEMA)
    assert calls == 2
    assert conv.get_recording(database, item["id"])["overview"]["points"] == []


def test_chunk_scope_and_cross_chunk_evidence_are_explicit(record, monkeypatch):
    database, item = record
    monkeypatch.setattr(conv, "chunk_utterances", lambda rows: [[row] for row in rows])
    monkeypatch.setattr(conv, "request_overview", fake_overview)
    conv.extract_recording_overview(database, item["id"], SCHEMA)
    overview = conv.get_recording(database, item["id"])["overview"]
    assert overview["scope"] == "chunked" and overview["chunk_count"] == 2
    assert [point["chunk_index"] for point in overview["points"]] == [1, 2]
    assert overview["chunks"][1]["first_utterance_id"] == item["utterances"][1]["id"]
    assert overview["quality_warnings"]
    monkeypatch.setattr(
        conv,
        "request_overview",
        lambda **_: {
            "points": [
                {
                    "text": "別区間の根拠",
                    "evidence_utterance_ids": [item["utterances"][1]["id"]],
                }
            ]
        },
    )
    conv.extract_recording_overview(database, item["id"], SCHEMA, model="new-model")
    assert conv.get_recording(database, item["id"])["overview"]["generation_status"] == "failed"


def test_stale_summary_not_presented_after_transcript_change(record, monkeypatch):
    database, item = record
    monkeypatch.setattr(conv, "request_overview", fake_overview)
    conv.extract_recording_overview(database, item["id"], SCHEMA)
    with sqlite3.connect(database) as connection:
        conv._replace_analysis_results(
            connection, item["id"], [(0, 1, "別の話題", None, "話者1")], "new"
        )
    summary = conv.get_recording(database, item["id"])["overview"]
    assert summary["status"] == "stale" and summary["text"] is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT data FROM conversation_overviews").fetchone()[0]


def test_input_change_during_request_cannot_save_stale_evidence(record, monkeypatch):
    database, item = record

    def request(**kwargs):
        with sqlite3.connect(database) as connection:
            connection.execute("UPDATE utterances SET text='変更後'")
        return fake_overview(**kwargs)

    monkeypatch.setattr(conv, "request_overview", request)
    conv.extract_recording_overview(database, item["id"], SCHEMA)
    assert conv.get_recording(database, item["id"])["overview"]["status"] == "failed"


def test_list_joins_recording_gps_without_reading_all_contexts(record, monkeypatch):
    database, item = record
    conv.store_location_events(
        database,
        [
            {
                "timestamp": "2026-09-09T10:00:00+09:00",
                "latitude": 35.0,
                "longitude": 139.0,
                "horizontal_accuracy": 10,
                "is_approximate": False,
            }
        ],
    )
    monkeypatch.setattr(conv, "read_contexts", lambda *_: pytest.fail("full GPS read on list"))
    from daily_reader import device_context

    monkeypatch.setattr(
        device_context, "context_at", lambda *_: pytest.fail("device context on list")
    )
    row = conv.list_recordings(database)[0]
    assert row["digest"]["location_context"]["state"] == "matched_estimate"
    assert row["digest"]["location_context"]["time_basis"] == "recording_start"
    assert row["digest"]["location_context"]["location"]["latitude"] == 35.0
    assert "device_context" not in row["digest"]["location_context"]


def test_short_asr_coverage_warning_is_diagnostic_not_accuracy(record):
    database, _ = record
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE recordings SET source_type='audio',transcription_metadata=?",
            (json.dumps({"duration_seconds": 2400, "speech_seconds": 40, "warnings": []}),),
        )
    row = conv.list_recordings(database)[0]
    assert row["duration_seconds"] == 2400
    assert "一部の内容" in row["digest"]["summary"]["quality_warnings"][0]


def test_legacy_fragments_hidden_without_mutation_and_edits_preserved(record):
    database, item = record
    original = item["insight_items"][0]["id"]
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE conversation_items SET title='必要なかった' WHERE id=?", (original,)
        )
        connection.execute("UPDATE task_proposals SET title='必要なかった' WHERE id=?", (original,))
    before = candidate_snapshot(database)
    assert conv.list_insight_items(database) == []
    detail = conv.get_recording(database, item["id"])
    assert detail["task_proposals"] == [] and detail["insight_items"] == []
    assert conv.list_recordings(database)[0]["insight_item_count"] == 0
    assert detail["digest"]["counts"] == [] and detail["digest"]["preview_items"] == []
    with pytest.raises(KeyError):
        conv.mark_insight_item_approved(database, original, "planner", "x")
    with pytest.raises(KeyError):
        conv.mark_proposal_approved(database, original, "planner", "x")
    assert candidate_snapshot(database) == before
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE conversation_items SET updated_at='manually-edited' WHERE id=?", (original,)
        )
    assert conv.list_insight_items(database)[0]["certainty"] == "ambiguous"
    assert conv.list_recordings(database)[0]["insight_item_count"] == 1


def test_reviewed_items_appear_in_counts_and_preview(record):
    database, item = record
    item_id = item["insight_items"][0]["id"]
    conv.review_insight_item(database, item_id, {"action": "keep"})
    row = conv.list_recordings(database)[0]
    assert row["digest"]["counts"] == [{"kind": "task", "status": "kept", "count": 1}]
    assert row["digest"]["preview_items"][0]["id"] == item_id


def test_auto_extraction_queue_generates_overview_after_candidates(record, monkeypatch):
    database, item = record
    life_automation.settings(database)
    calls = []
    monkeypatch.setattr(conv, "request_insights", lambda **_: calls.append("items") or [])
    monkeypatch.setattr(
        conv, "request_overview", lambda **kw: calls.append("overview") or fake_overview(**kw)
    )

    class ImmediateThread:
        def __init__(self, target, args, kwargs, daemon):
            self.target, self.args, self.kwargs = target, args, kwargs

        def start(self):
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(conv.threading, "Thread", ImmediateThread)
    worker = life_automation.AutomationWorker(database, SCHEMA, "unused", "unused")
    worker._queue_recording({"since": "2020-01-01T00:00:00+00:00"})
    assert calls == ["items", "overview"]
    assert conv.get_recording(database, item["id"])["overview"]["status"] == "ready"


@pytest.mark.parametrize("state", ["queued", "extracting"])
def test_auto_attempts_not_consumed_while_independent_overview_runs(record, monkeypatch, state):
    database, item = record
    life_automation.settings(database)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE recordings SET overview_status=?", (state,))
    queued = []
    monkeypatch.setattr(conv, "queue_insight_extraction", lambda *args: queued.append(args[1]))
    worker = life_automation.AutomationWorker(database, SCHEMA, "unused", "unused")
    policy = {"since": "2020-01-01T00:00:00+00:00"}
    for _ in range(4):
        worker._queue_recording(policy)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM life_automation_recordings").fetchone()[0] == 0
        )
        connection.execute("UPDATE recordings SET overview_status='completed'")
    assert queued == []
    worker._queue_recording(policy)
    assert queued == [item["id"]]


@pytest.mark.parametrize("state", ["queued", "extracting"])
def test_overview_blocks_asr_and_restart_retains_previous_summary(record, monkeypatch, state):
    database, item = record
    monkeypatch.setattr(conv, "request_overview", fake_overview)
    conv.extract_recording_overview(database, item["id"], SCHEMA)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE recordings SET source_type='audio',overview_status=?", (state,))
    with pytest.raises(conv.AnalysisConflict):
        conv.start_analysis(database, item["id"], Path("unused"))
    assert not conv.queue_insight_extraction(
        database, item["id"], SCHEMA, "unused", overview_only=True
    )
    conv.recover_interrupted_conversations(database)
    overview = conv.get_recording(database, item["id"])["overview"]
    assert overview["status"] == "ready" and overview["generation_status"] == "failed"
