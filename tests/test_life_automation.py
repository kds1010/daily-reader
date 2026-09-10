from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from daily_reader import conversation_correction_engine as correction_engine
from daily_reader import conversations as conv
from daily_reader import life_assistant as life
from daily_reader import life_automation as auto
from daily_reader.local_server import make_handler

SCHEMA = Path(__file__).resolve().parents[1] / "config/conversation-insight-schema.json"


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "db"
    auto.settings(path)
    return path


@pytest.fixture
def worker(db, monkeypatch):
    value = auto.AutomationWorker(db, SCHEMA, "unused", "unused")
    monkeypatch.setattr(value, "_queue_recording", lambda _: None)
    return value


def extracted(db, monkeypatch, items, *, verified=True, name="recording"):
    content = name.encode()
    record = conv.store_transcript(
        db,
        io.BytesIO(content),
        len(content),
        name + ".txt",
        "2026-09-07T10:00:00+09:00" if verified else None,
    )
    monkeypatch.setattr(
        conv,
        "request_insights",
        lambda **kw: [
            {
                "kind": "task",
                "title": "資料を確認する",
                "detail": "会話内の詳細",
                "assignee": "話者1",
                "due_date": None,
                "due_date_original": None,
                "certainty": "explicit",
                "evidence_utterance_ids": [kw["utterances"][0]["id"]],
                "life_data": {"intent": "committed", "time_basis": "absolute"},
                **item,
            }
            for item in items
        ],
    )
    monkeypatch.setattr(correction_engine, "run_correction_passes", lambda *a, **kw: [])
    conv.extract_recording_insights(db, record["id"], SCHEMA, correct_first=True)
    return record


def test_explicit_tasks_automatic_uncertain_prefilled_and_idempotent(db, worker, monkeypatch):
    extracted(db, monkeypatch, [{}, {"title": "検討する", "certainty": "ambiguous"}])
    worker.step()
    worker.step()
    saved = life.snapshot(db)["entries"]
    assert len(saved) == 1
    assert saved[0]["automatic"] is True
    assert saved[0]["evidence"]["confirmation"] == "automatic"
    assert "confirmed_at" not in saved[0]["evidence"]
    pending = auto.drafts(db)
    assert len(pending) == 1
    assert pending[0]["data"]["detail"] == "会話内の詳細"
    with ThreadPoolExecutor(4) as pool:
        adopted = list(pool.map(lambda _: auto.adopt(db, pending[0]["id"], {}), range(8)))
    assert len({x["id"] for x in adopted}) == 1
    assert len(life.snapshot(db)["entries"]) == 2


@pytest.mark.parametrize("basis,verified", [("recording_relative", False), ("unknown", True)])
def test_unknown_date_basis_does_not_silently_schedule(db, worker, monkeypatch, basis, verified):
    extracted(
        db,
        monkeypatch,
        [
            {
                "due_date": "2026-10-10",
                "life_data": {
                    "intent": "committed",
                    "time_basis": basis,
                    "due_at": "2026-10-10T10:00:00+09:00",
                },
            }
        ],
        verified=verified,
    )
    worker.step()
    assert life.snapshot(db)["entries"] == []
    draft = auto.drafts(db)[0]
    assert draft["data"]["due_at"] is None
    assert draft["data"]["remind_at"] is None
    assert "根拠" in draft["reason"]


def test_date_only_task_uses_documented_local_day_defaults(db, worker, monkeypatch):
    extracted(db, monkeypatch, [{"due_date": "2026-10-10"}])
    worker.step()
    entry = life.snapshot(db)["entries"][0]
    assert entry["due_at"] == "2026-10-10T14:59:00+00:00"
    assert entry["remind_at"] == "2026-10-10T00:00:00+00:00"


def test_event_requires_actual_commitment_and_end_time(db, worker, monkeypatch):
    extracted(
        db,
        monkeypatch,
        [
            {
                "kind": "event",
                "title": title,
                "life_data": {
                    "intent": intent,
                    "time_basis": "absolute",
                    "start_at": "2027-10-10T10:00:00+09:00",
                    "end_at": end,
                },
            }
            for title, intent, end in [
                ("終了不明", "committed", None),
                ("参加を検討", "considering", "2027-10-10T11:00:00+09:00"),
                ("参加する", "committed", "2027-10-10T11:00:00+09:00"),
            ]
        ],
    )
    worker.step()
    assert [e["title"] for e in life.snapshot(db)["entries"]] == ["参加する"]
    assert len(auto.drafts(db)) == 2
    missing = next(d for d in auto.drafts(db) if d["data"]["title"] == "終了不明")
    with pytest.raises(ValueError):
        auto.adopt(db, missing["id"], {})
    assert auto.adopt(db, missing["id"], {"end_at": "2027-10-10T11:00:00+09:00"})["kind"] == "event"


def test_research_public_question_only_daily_cap_and_pause(db, worker, monkeypatch):
    extracted(
        db,
        monkeypatch,
        [
            {
                "kind": "research",
                "title": f"秘密の調査{i}",
                "life_data": {
                    "intent": "research_requested",
                    "time_basis": "absolute",
                    "constraints": "秘密の条件",
                    "public_query": f"公開の質問{i}",
                },
            }
            for i in range(4)
        ],
    )
    auto.update_settings(db, {"research_enabled": False})
    worker.step()
    assert not life.snapshot(db)["entries"]
    assert all("停止中" in d["reason"] for d in auto.drafts(db))
    auto.update_settings(db, {"research_enabled": True})
    worker.step()
    jobs = life.snapshot(db)["entries"]
    assert len(jobs) == 3
    assert all(
        e["title"].startswith("公開の質問") and not e["detail"] and not e["constraints"]
        for e in jobs
    )
    assert "3件" in auto.drafts(db)[0]["reason"]
    worker.step()
    assert len(life.snapshot(db)["entries"]) == 3


def test_profile_identity_once_per_recording_and_correction(db, worker, monkeypatch):
    profile = {"kind": "interest", "life_data": {"intent": "interest_only", "person_name": "話者1"}}
    extracted(db, monkeypatch, [{**profile, "title": "写真"}, {**profile, "title": "散歩"}])
    worker.step()
    pending = auto.drafts(db)
    assert len(pending) == 2
    first = auto.adopt(db, pending[0]["id"], {"person_id": "self"})
    worker.step()
    entries = life.snapshot(db)["entries"]
    assert len(entries) == 2 and all(e["person_id"] == "self" for e in entries)
    other = life.create_person(db, {"name": "同僚"})
    life.update_entry(db, first["id"], {"revision": 1, "person_id": other["id"]})
    assert all(e["person_id"] == other["id"] for e in life.snapshot(db)["entries"])
    inherited = next(e for e in life.snapshot(db)["entries"] if e["id"] != first["id"])
    life.update_entry(db, inherited["id"], {"revision": inherited["revision"], "person_id": "self"})
    life.update_entry(db, first["id"], {"revision": 2, "person_id": other["id"]})
    assert life.get_entry(db, inherited["id"])["person_id"] == "self"
    extracted(db, monkeypatch, [{**profile, "title": "音楽"}], name="another recording")
    worker.step()
    assert auto.drafts(db)[0]["data"]["person_id"] == ""


def test_event_children_and_research_followups_are_durable(db, worker):
    parent = life.create_entry(
        db,
        {
            "kind": "event",
            "title": "参加する",
            "timezone": "Asia/Tokyo",
            "start_at": "2027-10-10T10:00:00+09:00",
            "end_at": "2027-10-10T11:00:00+09:00",
            "preparation": "持ち物を用意",
            "prepare_at": "2027-10-09T10:00:00+09:00",
            "deadline_at": "2027-10-08T10:00:00+09:00",
        },
    )
    worker.step()
    worker.step()
    assert len(life.snapshot(db)["entries"]) == 3
    life.update_entry(db, parent["id"], {"revision": 1, "status": "registered"})
    children = [e for e in life.snapshot(db)["entries"] if e["kind"] == "task"]
    assert next(e for e in children if e["evidence"]["role"] == "deadline")["status"] == "completed"
    life.update_entry(db, parent["id"], {"revision": 2, "status": "cancelled"})
    assert not life.snapshot(db)["notifications"]
    research = life.create_entry(db, {"kind": "research", "title": "調べる"})
    with life.connect(db) as connection:
        data = json.loads(
            connection.execute(
                "SELECT data FROM life_entries WHERE id=?", (research["id"],)
            ).fetchone()[0]
        )
        data["result"] = {
            "actions": [{"kind": "research", "title": "次の調査", "detail": "提案"}],
            "sources": [{"title": "公式", "url": "https://example.org"}],
        }
        connection.execute(
            "UPDATE life_entries SET status='completed',data=? WHERE id=?",
            (json.dumps(data), research["id"]),
        )
    worker.step()
    assert len(auto.drafts(db)) == 1
    assert len([e for e in life.snapshot(db)["entries"] if e["kind"] == "research"]) == 1
    life.update_entry(db, research["id"], {"revision": 1, "status": "archived"})
    assert auto.adopt(db, auto.drafts(db)[0]["id"], {})["status"] == "queued"


def test_cutoff_pause_and_retry_limits_survive_restart(db, monkeypatch):
    content = b"new recording"
    old = conv.store_transcript(db, io.BytesIO(content), len(content), "old.txt")
    with life.connect(db) as connection:
        connection.execute(
            "UPDATE recordings SET created_at='2000-01-01T00:00:00Z' WHERE id=?", (old["id"],)
        )
    new = auto.capture(db, {"text": "新しいメモ", "request_id": "new"})
    calls = []
    monkeypatch.setattr(conv, "queue_insight_extraction", lambda *args: calls.append(args[1]))
    worker = auto.AutomationWorker(db, SCHEMA, "unused", "unused")
    cutoff = auto.settings(db)["since"]
    auto.update_settings(db, {"enabled": False})
    worker.step()
    assert calls == []
    auto.update_settings(db, {"enabled": True})
    for _ in range(5):
        worker.step()
        worker.step()  # immediate iteration respects backoff
        with life.connect(db) as connection:
            connection.execute(
                "UPDATE life_automation_recordings SET attempted_at='2000-01-01T00:00:00Z'"
            )
        worker = auto.AutomationWorker(db, SCHEMA, "unused", "unused")
    assert calls == [new["id"]] * 3
    assert auto.settings(db)["since"] == cutoff


def test_old_extracted_items_are_not_automatically_adopted(db, worker, monkeypatch):
    record = extracted(db, monkeypatch, [{}])
    with life.connect(db) as connection:
        connection.execute(
            "UPDATE recordings SET created_at='2000-01-01T00:00:00Z' WHERE id=?", (record["id"],)
        )
    worker.step()
    assert life.snapshot(db)["entries"] == []
    assert auto.drafts(db) == []


def test_memo_retry_preserves_recording_but_identical_new_note_is_distinct(db):
    payload = {"text": "明日やる", "request_id": "a"}
    with ThreadPoolExecutor(4) as pool:
        records = list(pool.map(lambda _: auto.capture(db, payload), range(6)))
    assert len({r["id"] for r in records}) == 1
    assert records[0]["recorded_at_verified"]
    with pytest.raises(ValueError):
        auto.capture(db, {**payload, "text": "変更後"})
    assert auto.capture(db, {**payload, "request_id": "b"})["id"] != records[0]["id"]


def test_dismissed_draft_does_not_return_after_reanalysis(db, worker, monkeypatch):
    record = extracted(db, monkeypatch, [{"certainty": "ambiguous"}])
    worker.step()
    draft = auto.drafts(db)[0]
    auto.dismiss(db, draft["id"])
    monkeypatch.setattr(conv, "PROMPT_VERSION", "another-version")
    conv.extract_recording_insights(db, record["id"], SCHEMA)
    worker.step()
    assert auto.drafts(db) == []
    with pytest.raises(ValueError):
        auto.adopt(db, draft["id"], {})


def test_http_capture_settings_and_draft_routes(tmp_path, db, worker, monkeypatch):
    extracted(db, monkeypatch, [{"certainty": "ambiguous"}])
    worker.step()
    draft = auto.drafts(db)[0]
    articles = tmp_path / "articles.json"
    articles.write_text('{"articles": []}')
    factory = make_handler(
        *[
            tmp_path / p
            for p in ["site", "articles.json", "read", "feedback", "assistant", "client", "token"]
        ],
        conversations_db=db,
    )
    handler = factory.func.__new__(factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    for path, payload, status in [
        ("/api/life/capture", {"text": "買い物する", "request_id": "api"}, 200),
        ("/api/life/automation", {"enabled": "bad"}, 400),
        (f"/api/life/drafts/{draft['id']}/adopt", {}, 200),
        (f"/api/life/drafts/{draft['id']}/adopt", {}, 200),
        (f"/api/life/drafts/{draft['id']}/dismiss", {}, 400),
    ]:
        handler.path = path
        handler._read_json = lambda payload=payload, **_: payload
        handler.do_POST()
        assert responses[-1][0] == status
    handler.path = "/api/life"
    handler.do_GET()
    assert responses[-1][1]["automation"]["enabled"] is True
    assert responses[-1][1]["drafts"] == []


def test_automatic_child_rechecks_parent_inside_transaction(db):
    parent = life.create_entry(
        db,
        {
            "kind": "event",
            "title": "予定",
            "timezone": "Asia/Tokyo",
            "start_at": "2027-10-10T10:00:00+09:00",
            "end_at": "2027-10-10T11:00:00+09:00",
            "prepare_at": "2027-10-09T10:00:00+09:00",
        },
    )
    payload = {
        "kind": "task",
        "title": "古い名前",
        "source_type": "event",
        "source_id": parent["id"],
        "source_role": "prepare",
        "due_at": "2027-10-01T10:00:00+09:00",
    }
    child = life.create_entry(db, payload, automatic=True)
    assert child["due_at"] == parent["prepare_at"]
    life.update_entry(db, parent["id"], {"revision": 1, "status": "cancelled"})
    with pytest.raises(ValueError, match="中止"):
        life.create_entry(db, {**payload, "source_role": "deadline"}, automatic=True)


def test_auto_queue_waits_for_running_extraction_and_caps_long_input(db, monkeypatch):
    first = auto.capture(db, {"text": "処理中", "request_id": "first"})
    second = auto.capture(db, {"text": "待機", "request_id": "second"})
    with life.connect(db) as connection:
        connection.execute(
            "UPDATE recordings SET insight_status='extracting' WHERE id=?", (first["id"],)
        )
        connection.execute(
            "UPDATE utterances SET text=? WHERE recording_id=?", ("長" * 60001, second["id"])
        )
    calls = []
    monkeypatch.setattr(conv, "queue_insight_extraction", lambda *a: calls.append(a[1]))
    worker = auto.AutomationWorker(db, SCHEMA, "unused", "unused")
    worker.step()
    assert calls == []
    with life.connect(db) as connection:
        connection.execute(
            "UPDATE recordings SET insight_status='completed' WHERE id=?", (first["id"],)
        )
    worker.step()
    result = conv.get_recording(db, second["id"])
    assert result["insight_status"] == "failed"
    assert "上限" in result["insight_error"]
    assert calls == []


def test_duplicate_conversion_preserves_confirmed_profile_owner(db, worker, monkeypatch):
    extracted(
        db,
        monkeypatch,
        [
            {
                "kind": "interest",
                "life_data": {"intent": "interest_only", "person_name": "話者1"},
            }
        ],
    )
    worker.step()
    draft = auto.drafts(db)[0]
    saved = life.create_entry(db, {**draft["data"], "person_id": "self"})
    other = life.create_person(db, {"name": "別の人物"})
    repeated = auto.adopt(db, draft["id"], {"person_id": other["id"]})
    assert repeated["id"] == saved["id"]
    assert repeated["person_id"] == "self"
    with life.connect(db) as connection:
        mapped = connection.execute("SELECT person_id FROM life_speaker_people").fetchone()[0]
    assert mapped == "self"


def test_audio_reanalysis_keeps_adopted_entries_and_requires_review(db, worker, monkeypatch):
    from daily_reader.conversation_transcription import Transcription

    record = extracted(db, monkeypatch, [{}, {"title": "検討する", "certainty": "ambiguous"}])
    worker.step()
    saved = life.snapshot(db)["entries"]
    pending = auto.drafts(db)
    assert len(saved) == len(pending) == 1
    with life.connect(db) as connection:
        connection.execute("UPDATE recordings SET source_type='audio'")
        connection.execute(
            "INSERT INTO life_speaker_people VALUES(?,?,?)", (record["id"], "話者1", "self")
        )
    monkeypatch.setattr(
        conv,
        "transcribe_audio",
        lambda *_, **__: Transcription(
            [(0, 2, "資料を確認します", -0.1, "話者1")], {"warnings": [], "model": "test"}
        ),
    )
    conv.analyze_recording(db, record["id"], Path("/unused/token"))
    assert auto.drafts(db) == []
    with pytest.raises(ValueError):
        auto.adopt(db, pending[0]["id"], {})
    with life.connect(db) as connection:
        assert connection.execute("SELECT count(*) FROM life_speaker_people").fetchone()[0] == 0
    conv.extract_recording_insights(db, record["id"], SCHEMA, correct_first=True)
    worker.step()
    worker.step()
    assert [entry["id"] for entry in life.snapshot(db)["entries"]] == [saved[0]["id"]]
    assert len(auto.drafts(db)) == 2
    assert all("再解析後" in draft["reason"] for draft in auto.drafts(db))


def test_reanalysis_is_not_automatically_sent_to_codex(db, monkeypatch):
    record = conv.store_transcript(db, io.BytesIO(b"text"), 4, "test.txt")
    with life.connect(db) as connection:
        connection.execute("UPDATE recordings SET transcription_needs_review=1")
    queued = []
    monkeypatch.setattr(conv, "queue_insight_extraction", lambda *args: queued.append(args))
    value = auto.AutomationWorker(db, SCHEMA, "unused", "unused")
    value._queue_recording(auto.settings(db))
    assert queued == []
    assert conv.get_recording(db, record["id"])["insight_status"] == "not_requested"
