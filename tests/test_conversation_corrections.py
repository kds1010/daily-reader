from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from daily_reader import conversation_correction_engine as engine
from daily_reader import conversation_corrections as correction
from daily_reader import conversations as conv
from daily_reader import life_assistant as life
from daily_reader import life_automation as auto
from daily_reader.conversation_insights import ConversationInsightError

SCHEMA = Path(__file__).resolve().parents[1] / "config/conversation-insight-schema.json"


@pytest.fixture
def record(tmp_path):
    database = tmp_path / "conversations.sqlite3"
    auto.settings(database)
    raw = "資料を確認しす。\nここは不明です".encode()
    item = conv.store_transcript(
        database, io.BytesIO(raw), len(raw), "meeting.txt", "2026-09-09T10:00:00+09:00"
    )
    return database, item["id"]


def result(row, *, accepted=True):
    return {
        "utterance_id": row["id"],
        "original_text": row["text"],
        "proposed_text": row["text"].replace("確認しす", "確認します"),
        "corrected_text": row["text"].replace("確認しす", "確認します") if accepted else None,
        "status": "accepted" if accepted else "retained",
        "reason": "grammar" if accepted else "uncertain_source",
        "verification": "verified" if accepted else "uncertain",
        "context_ids": [],
    }


def run(record):
    return correction.ensure_corrected(*record, SCHEMA, "unused", "unused")


def snapshot(database):
    with sqlite3.connect(database) as connection:
        return {
            table: connection.execute(f"SELECT * FROM {table} ORDER BY 1").fetchall()
            for table in (
                "utterances",
                "speakers",
                "conversation_items",
                "conversation_item_evidence",
                "task_proposals",
            )
        }


def test_overlay_preserves_originals_ids_candidates_and_cache(record, monkeypatch):
    calls = []

    def request(rows, contexts, **kw):
        calls.append(contexts)
        kw["on_stage"]("verifying")
        return [result(rows[0]), result(rows[1], accepted=False)]

    monkeypatch.setattr(engine, "run_correction_passes", request)
    before = snapshot(record[0])
    assert run(record)
    detail = conv.get_recording(*record)
    assert snapshot(record[0]) == before
    state = detail["correction"]
    assert (state["status"], state["corrected_count"], state["flagged_count"]) == (
        "completed",
        1,
        1,
    )
    assert not state["automatic_blocked"]
    assert detail["utterances"][0]["text"] == "資料を確認しす。"
    assert detail["utterances"][0]["correction"]["corrected_text"] == "資料を確認します。"
    with conv._connect(record[0]) as connection:
        _, rows = conv._insight_input(connection, record[1])
        assert rows[0]["text"] == "資料を確認します。"
        assert rows[0]["raw_text"] == "資料を確認しす。"
        assert rows[1]["text"] == "ここは不明です"
        assert rows[1]["correction_uncertain"]
    assert run(record)
    assert len(calls) == 1
    assert conv.list_recordings(record[0])[0]["correction"]["revision_id"] == state["revision_id"]


@pytest.mark.parametrize(
    "change", ["unknown", "duplicate", "original", "unverified", "retained_text", "context"]
)
def test_invalid_results_fail_without_overlay(record, monkeypatch, change):
    def request(rows, contexts, **kw):
        item = result(rows[0])
        if change == "unknown":
            item["utterance_id"] = "not-current"
        if change == "original":
            item["original_text"] = "different"
        if change == "unverified":
            item["verification"] = "uncertain"
        if change == "retained_text":
            item["status"] = "retained"
        if change == "context":
            item["context_ids"] = ["past-unknown"]
        return [item, item] if change == "duplicate" else [item]

    monkeypatch.setattr(engine, "run_correction_passes", request)
    before = snapshot(record[0])
    assert not run(record)
    state = conv.get_recording(*record)["correction"]
    assert state["status"] == "failed" and state["items"] == []
    assert snapshot(record[0]) == before


def test_second_pass_failure_retry_and_restart(record, monkeypatch):
    def failure(*args, **kw):
        kw["on_stage"]("verifying")
        raise ConversationInsightError("検証できません")

    monkeypatch.setattr(engine, "run_correction_passes", failure)
    assert not run(record)
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])
    assert run(record)
    with conv._connect(record[0]) as connection:
        connection.execute("UPDATE recordings SET correction_status='verifying'")
    conv.recover_interrupted_conversations(record[0])
    detail = conv.get_recording(*record)
    assert detail["correction"]["status"] == "failed"
    assert detail["correction"]["items"][0]["corrected_text"]
    assert run(record)
    with conv._connect(record[0]) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM conversation_correction_runs").fetchone()[0]
            == 1
        )


def test_source_changed_during_generation_discards_result(record, monkeypatch):
    before = conv.get_recording(*record)

    def request(rows, *a, **kw):
        conv.update_speaker(record[0], before["speakers"][0]["id"], "本人")
        return [result(rows[0])]

    monkeypatch.setattr(engine, "run_correction_passes", request)
    assert not run(record)
    assert conv.get_recording(*record)["correction"]["status"] == "stale"
    with conv._connect(record[0]) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM conversation_corrections").fetchone()[0] == 0
        )


def test_profile_change_invalidates_completed_dependent_and_blocks_adoption(record, monkeypatch):
    from daily_reader import correction_context

    source = "past-recording"

    def contexts(*a):
        return [
            {
                "id": "c001",
                "source_type": "confirmed_self_utterance",
                "source_id": "past-id",
                "recording_id": source,
                "recorded_at": "2026-09-08T00:00:00Z",
                "title": "本人の過去発言",
                "text": "資料",
                "target_speakers": ["話者1"],
            }
        ]

    monkeypatch.setattr(correction_context, "reference_context", contexts)
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])
    assert run(record)
    with conv._connect(record[0]) as connection:
        life._confirm_owner(connection, {"recording_id": source, "subject": "話者1"}, "self")
    detail = conv.get_recording(*record)
    assert detail["correction"]["status"] == "stale" and detail["correction"]["items"] == []
    assert all(row["correction"] is None for row in detail["utterances"])


def extracted(**kwargs):
    return [
        {
            "kind": "task",
            "title": "資料を確認する",
            "detail": "資料を確認します",
            "assignee": "話者1",
            "due_date": None,
            "due_date_original": None,
            "certainty": "explicit",
            "evidence_utterance_ids": [kwargs["utterances"][0]["id"]],
            "life_data": {"intent": "committed", "time_basis": "absolute"},
        }
    ]


def test_correction_precedes_extraction_original_evidence_snapshot_and_auto_adoption(
    record, monkeypatch
):
    calls = []
    monkeypatch.setattr(
        engine,
        "run_correction_passes",
        lambda rows, *a, **kw: (
            calls.append("correction") or [result(rows[0]), result(rows[1], accepted=False)]
        ),
    )

    def request(**kw):
        calls.append("extract")
        assert kw["utterances"][0]["text"] == "資料を確認します。"
        return extracted(**kw)

    monkeypatch.setattr(conv, "request_insights", request)
    conv.extract_recording_insights(*record, SCHEMA, correct_first=True)
    assert calls == ["correction", "extract"]
    item = conv.get_recording(*record)["insight_items"][0]
    evidence = item["evidence"][0]
    assert evidence["quote"] == "資料を確認しす。"
    assert evidence["corrected_quote"] == "資料を確認します。"
    assert evidence["correction_is_snapshot"] and not evidence["correction_uncertain"]
    worker = auto.AutomationWorker(record[0], SCHEMA, "unused", "unused")
    monkeypatch.setattr(worker, "_queue_recording", lambda _: None)
    worker.step()
    assert len(life.snapshot(record[0])["entries"]) == 1


def test_correction_failure_blocks_extraction(record, monkeypatch):
    monkeypatch.setattr(
        engine,
        "run_correction_passes",
        lambda *a, **kw: (_ for _ in ()).throw(ConversationInsightError("失敗")),
    )
    monkeypatch.setattr(conv, "request_insights", lambda **kw: pytest.fail("must not extract"))
    conv.extract_recording_insights(*record, SCHEMA, correct_first=True)
    detail = conv.get_recording(*record)
    assert detail["insight_status"] == detail["correction"]["status"] == "failed"


def test_previous_candidates_stay_immutable_and_require_manual_adoption(record, monkeypatch):
    monkeypatch.setattr(conv, "request_insights", extracted)
    conv.extract_recording_insights(*record, SCHEMA)
    before = snapshot(record[0])
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])
    assert run(record)
    assert snapshot(record[0]) == before
    assert conv.get_recording(*record)["correction"]["automatic_blocked"]
    worker = auto.AutomationWorker(record[0], SCHEMA, "unused", "unused")
    monkeypatch.setattr(worker, "_queue_recording", lambda _: None)
    worker.step()
    assert not life.snapshot(record[0])["entries"]
    assert len(auto.drafts(record[0])) == 1


def test_commit_time_overlay_race_leaves_candidates_unchanged(record, monkeypatch):
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])

    def request(**kw):
        with conv._connect(record[0]) as connection:
            correction.invalidate(connection, record[1])
        return extracted(**kw)

    monkeypatch.setattr(conv, "request_insights", request)
    before = snapshot(record[0])
    conv.extract_recording_insights(*record, SCHEMA, correct_first=True)
    assert conv.get_recording(*record)["insight_status"] == "failed"
    assert snapshot(record[0]) == before


def test_overview_uses_original_quote_with_corrected_snapshot(record, monkeypatch):
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])
    monkeypatch.setattr(
        conv,
        "request_overview",
        lambda **kw: {
            "points": [
                {
                    "text": "資料の確認を話しました",
                    "evidence_utterance_ids": [kw["utterances"][0]["id"]],
                }
            ]
        },
    )
    conv.extract_recording_overview(*record, SCHEMA, correct_first=True)
    quote = conv.get_recording(*record)["overview"]["points"][0]["evidence"][0]
    assert quote["quote"] == "資料を確認しす。" and quote["corrected_quote"] == "資料を確認します。"
    assert quote["correction_is_snapshot"]


@pytest.mark.parametrize("stage", ["queued", "correcting", "verifying"])
def test_active_correction_blocks_audio_insights_and_auto_attempts(record, monkeypatch, stage):
    with conv._connect(record[0]) as connection:
        connection.execute(
            "UPDATE recordings SET correction_status=?,source_type='audio'", (stage,)
        )
    with pytest.raises(conv.AnalysisConflict):
        conv.start_analysis(*record, None)
    assert not conv.queue_insight_extraction(*record, SCHEMA, "unused")
    worker = auto.AutomationWorker(record[0], SCHEMA, "unused", "unused")
    worker._queue_recording({"since": "2000-01-01T00:00:00Z"})
    with conv._connect(record[0]) as connection:
        assert (
            connection.execute("SELECT COUNT(*) FROM life_automation_recordings").fetchone()[0] == 0
        )


def test_context_hash_changes_during_verification_discard_result(record, monkeypatch):
    from daily_reader import correction_context

    selected = []
    monkeypatch.setattr(correction_context, "reference_context", lambda *a: selected)

    def request(rows, *a, **kw):
        selected.append(
            {"id": "c001", "text": "資料", "recording_id": "past", "target_speakers": ["話者1"]}
        )
        return [result(rows[0])]

    monkeypatch.setattr(engine, "run_correction_passes", request)
    assert not run(record)
    assert conv.get_recording(*record)["correction"]["status"] == "failed"


def test_expired_context_is_stale_without_model_or_per_record_history_reads(record, monkeypatch):
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])
    assert run(record)
    with conv._connect(record[0]) as connection:
        connection.execute(
            "UPDATE recordings SET correction_context_count=1,"
            "correction_valid_until='2000-01-01T00:00:00Z'"
        )
        _, rows = conv._insight_input(connection, record[1])
        assert rows[0]["text"] == "資料を確認しす。"
        state = dict(connection.execute("SELECT * FROM recordings").fetchone())
        assert not correction.automatic_allowed(
            connection,
            {
                "type": "conversation",
                "recording_id": record[1],
                "quotes": [{"correction_revision_id": state["correction_revision_id"]}],
            },
        )
    assert conv.list_recordings(record[0])[0]["correction"]["status"] == "stale"
    detail = conv.get_recording(*record)["correction"]
    assert detail["status"] == "stale" and detail["items"] == []


def test_same_identity_confirmation_does_not_invalidate_success(record, monkeypatch):
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])
    assert run(record)
    with conv._connect(record[0]) as connection:
        connection.execute(
            "INSERT INTO life_speaker_people VALUES(?,?,?)", (record[1], "話者1", "self")
        )
        connection.execute("UPDATE recordings SET correction_context_count=1")
        life._confirm_owner(connection, {"recording_id": record[1], "subject": "話者1"}, "self")
    assert conv.get_recording(*record)["correction"]["status"] == "completed"


def test_pending_draft_refreshes_payload_and_evidence_together(record, monkeypatch):
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [])
    monkeypatch.setattr(
        conv, "request_insights", lambda **kw: [{**extracted(**kw)[0], "certainty": "ambiguous"}]
    )
    conv.extract_recording_insights(*record, SCHEMA, correct_first=True)
    worker = auto.AutomationWorker(record[0], SCHEMA, "unused", "unused")
    monkeypatch.setattr(worker, "_queue_recording", lambda _: None)
    worker.step()
    old = auto.drafts(record[0])[0]
    monkeypatch.setattr(engine, "VERSION", "test-next-correction")
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])
    monkeypatch.setattr(
        conv,
        "request_insights",
        lambda **kw: [
            {**extracted(**kw)[0], "detail": "新版の具体的な内容", "certainty": "ambiguous"}
        ],
    )
    conv.extract_recording_insights(*record, SCHEMA, correct_first=True)
    with pytest.raises(ValueError, match="再読み込み"):
        auto.adopt(record[0], old["id"], {})
    worker.step()
    updated = auto.drafts(record[0])[0]
    assert updated["id"] == old["id"]
    assert updated["data"]["detail"] == "新版の具体的な内容"
    assert (
        updated["evidence"]["quotes"][0]["correction_revision_id"]
        != old["evidence"]["quotes"][0]["correction_revision_id"]
    )
    entry = auto.adopt(record[0], updated["id"], {"detail": "本人が編集した内容"})
    assert entry["detail"] == "本人が編集した内容"
    assert entry["evidence"]["quotes"][0]["corrected_quote"] == "資料を確認します。"
    worker.step()
    assert life.get_entry(record[0], entry["id"])["detail"] == "本人が編集した内容"


def test_retained_evidence_alone_blocks_automatic_task(record, monkeypatch):
    monkeypatch.setattr(
        engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0], accepted=False)]
    )
    monkeypatch.setattr(conv, "request_insights", extracted)
    conv.extract_recording_insights(*record, SCHEMA, correct_first=True)
    worker = auto.AutomationWorker(record[0], SCHEMA, "unused", "unused")
    monkeypatch.setattr(worker, "_queue_recording", lambda _: None)
    worker.step()
    assert not life.snapshot(record[0])["entries"]
    assert auto.drafts(record[0])[0]["evidence"]["quotes"][0]["correction_uncertain"]


def test_manual_queue_preserves_candidate_state_and_has_bounded_capacity(record, monkeypatch):
    targets = []

    class Thread:
        def __init__(self, target, **kwargs):
            targets.append(target)

        def start(self):
            pass

    monkeypatch.setattr(correction.threading, "Thread", Thread)
    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])
    before = snapshot(record[0])
    assert correction.queue(*record, SCHEMA, "unused", "unused")
    assert not correction.queue(*record, SCHEMA, "unused", "unused")
    targets.pop()()
    assert snapshot(record[0]) == before
    assert conv.get_recording(*record)["correction"]["status"] == "completed"
    for index in range(10):
        raw = f"匿名メモ{index}".encode()
        item = conv.store_transcript(record[0], io.BytesIO(raw), len(raw), f"{index}.txt")
        correction.queue(record[0], item["id"], SCHEMA, "unused", "unused")
    with pytest.raises(ValueError, match="10件"):
        correction.queue(*record, SCHEMA, "unused", "unused")


@pytest.mark.parametrize(
    "headers,path,body,status",
    [
        (
            {"Host": "127.0.0.1:8787", "Content-Type": "application/json"},
            "/api/conversations/RECORD/corrections",
            {},
            202,
        ),
        (
            {"Host": "evil.example", "Content-Type": "application/json"},
            "/api/conversations/RECORD/corrections",
            {},
            403,
        ),
        (
            {
                "Host": "127.0.0.1:8787",
                "Content-Type": "application/json",
                "Origin": "https://evil.example",
            },
            "/api/conversations/RECORD/corrections",
            {},
            403,
        ),
        (
            {"Host": "127.0.0.1:8787", "Content-Type": "text/plain"},
            "/api/conversations/RECORD/corrections",
            {},
            415,
        ),
        (
            {"Host": "127.0.0.1:8787", "Content-Type": "application/json"},
            "/api/conversations/extra/RECORD/corrections",
            {},
            404,
        ),
        (
            {"Host": "127.0.0.1:8787", "Content-Type": "application/json"},
            "/api/conversations/RECORD/corrections",
            {"text": "do not send"},
            400,
        ),
    ],
)
def test_correction_route_source_type_path_and_body_guards(
    record, tmp_path, monkeypatch, headers, path, body, status
):
    from daily_reader.local_server import make_handler

    calls = []
    monkeypatch.setattr(correction, "queue", lambda *args: calls.append(args) or True)
    factory = make_handler(
        *[
            tmp_path / name
            for name in ["site", "articles", "read", "feedback", "assistant", "client", "token"]
        ],
        conversations_db=record[0],
    )
    handler = factory.func.__new__(factory.func)
    responses = []
    handler.path, handler.headers = path.replace("RECORD", record[1]), headers
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler._read_json = lambda **kw: body
    handler.do_POST()
    assert responses[-1][0] == status
    assert len(calls) == int(status == 202)


def test_expiry_edit_invalidates_used_identity_but_title_edit_does_not(record, monkeypatch):
    import json

    monkeypatch.setattr(engine, "run_correction_passes", lambda rows, *a, **kw: [result(rows[0])])
    entry = life.create_entry(
        record[0],
        {
            "kind": "profile",
            "person_id": "self",
            "title": "匿名の関心",
            "detail": "",
            "category": "interest",
        },
    )
    with conv._connect(record[0]) as connection:
        connection.execute(
            "UPDATE life_entries SET evidence=? WHERE id=?",
            (json.dumps({"type": "manual", "recording_id": record[1]}), entry["id"]),
        )
    assert run(record)
    with conv._connect(record[0]) as connection:
        connection.execute("UPDATE recordings SET correction_context_count=1")
    changed = life.update_entry(
        record[0], entry["id"], {"revision": entry["revision"], "title": "変更した関心"}
    )
    assert conv.get_recording(*record)["correction"]["status"] == "completed"
    life.update_entry(
        record[0],
        entry["id"],
        {"revision": changed["revision"], "expires_at": "2030-01-01T00:00:00Z"},
    )
    assert conv.get_recording(*record)["correction"]["status"] == "stale"


def test_context_deadline_reads_only_selected_recording_identity_proofs(record):
    import json

    with conv._connect(record[0]) as connection:
        for index, (source, expiry) in enumerate(
            [
                (record[1], "2030-01-02T00:00:00Z"),
                ("past", "2030-01-01T00:00:00Z"),
                ("unrelated", "2029-01-01T00:00:00Z"),
            ]
        ):
            connection.execute(
                "INSERT INTO life_entries VALUES(?,?,?,?,?,?,?,?,1)",
                (
                    str(index),
                    "profile",
                    "active",
                    json.dumps(
                        {"person_id": "self", "owner_confirmed": True, "expires_at": expiry}
                    ),
                    None,
                    json.dumps({"recording_id": source}),
                    "2026-01-01",
                    "2026-01-01",
                ),
            )
        assert (
            correction.context_deadline(connection, record[1], [{"recording_id": "past"}])
            == "2030-01-01T00:00:00+00:00"
        )
