import io
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

import pytest

from daily_reader import diary, life_assistant
from daily_reader.conversations import store_transcript
from daily_reader.daily_planner import (
    create_task,
    delete_task,
    set_task_completion,
    upsert_health_checkin,
)

NOW = datetime.fromisoformat("2026-09-08T12:00:00+09:00")
DAY = NOW.date()


@pytest.fixture
def stores(tmp_path, monkeypatch):
    monkeypatch.setattr(diary, "clock", lambda: NOW)
    planner, conversations = tmp_path / "planner.sqlite3", tmp_path / "conversations.sqlite3"
    diary.settings(planner, NOW)
    with life_assistant.connect(conversations):
        pass
    return planner, conversations


def create_draft(stores, day=DAY, revision=0, now=NOW):
    return diary.generate(*stores, day, revision=revision, now=now)


def test_read_does_not_generate_and_empty_is_not_an_inactive_day(stores):
    assert diary.get_entry(stores[0], DAY)["state"] == "missing"
    result = create_draft(stores)
    assert "材料となる記録がまだありません" in result["entry"]["body"]
    assert "カレンダーは未取得です。" in result["entry"]["source_snapshot"]["warnings"]
    assert create_draft(stores, revision=1) == result


def test_completed_tasks_use_aware_half_open_boundaries_and_day_keys(stores):
    planner, _ = stores
    for title, at in [
        ("前日", "2026-09-07T14:59:59.999999Z"),
        ("当日開始", "2026-09-07T15:00:00Z"),
        ("当日末尾", "2026-09-08T14:59:59.999999Z"),
        ("翌日", "2026-09-08T15:00:00Z"),
    ]:
        task = create_task(planner, {"title": title}, NOW)
        set_task_completion(planner, task["id"], True, DAY, datetime.fromisoformat(at))
    create_task(planner, {"title": "未完了"}, NOW)
    routine = create_task(planner, {"title": "日課", "recurrence": "daily"}, NOW)
    set_task_completion(planner, routine["id"], True, DAY, NOW - timedelta(days=1))
    upsert_health_checkin(
        planner, {"date": str(DAY), "steps": 0, "note": "疲れを感じた"}, NOW, "manual"
    )
    result = create_draft(stores)
    body = result["entry"]["body"]
    assert all(
        value in body for value in ["当日開始", "当日末尾", "日課", "歩数: 0歩", "疲れを感じた"]
    )
    assert all(value not in body for value in ["前日", "翌日", "未完了", "睡眠: 0", "気分: 0"])


def test_conversation_requires_verified_recording_date_and_stable_topics(stores):
    _, db = stores
    for filename in ["2026-09-08 10:00:00_文字起こし.txt", "日時不明.txt"]:
        raw = "資料について話しました。".encode()
        store_transcript(db, io.BytesIO(raw), len(raw), filename)
        # Use unique input so deduplication does not hide the unverified case.
        with sqlite3.connect(db) as connection:
            connection.execute("UPDATE recordings SET sha256=id")
    with sqlite3.connect(db) as connection:
        unknown = connection.execute(
            "SELECT id FROM recordings WHERE recorded_at_verified=0"
        ).fetchone()[0]
        connection.execute(
            "UPDATE recordings SET recorded_at=? WHERE id=?", (NOW.isoformat(), unknown)
        )
    result = create_draft(stores)
    sources = result["entry"]["source_snapshot"]["items"]
    assert len(sources) == 1
    assert sources[0]["kind"] == "conversation"
    assert "話者は未確認" in result["entry"]["body"]
    with sqlite3.connect(db) as connection:
        connection.execute("UPDATE conversation_topics SET id='new-' || id")
    assert create_draft(stores, revision=1) == result


def test_life_completion_survives_edit_but_not_reopen(stores, monkeypatch):
    _, db = stores
    at = ["2026-09-07T00:00:00Z"]
    monkeypatch.setattr(life_assistant, "now_string", lambda: at[0])
    task = life_assistant.create_entry(db, {"kind": "task", "title": "本を返す"})
    at[0] = "2026-09-08T00:00:00Z"
    task = life_assistant.update_entry(db, task["id"], {"revision": 1, "status": "completed"})
    at[0] = "2026-09-09T00:00:00Z"
    task = life_assistant.update_entry(db, task["id"], {"revision": 2, "title": "図書館へ本を返す"})
    assert "completed_at" not in task
    result = create_draft(stores)
    assert "図書館へ本を返す" in result["entry"]["body"]
    assert result["entry"]["source_snapshot"]["items"][0]["at"] == "2026-09-08T00:00:00Z"
    monkeypatch.setattr(life_assistant, "now_string", lambda: "2026-09-09T01:00:00Z")
    life_assistant.update_entry(db, task["id"], {"revision": 3, "status": "open"})
    assert "図書館へ本を返す" not in create_draft(stores, revision=1)["entry"]["generated_body"]


def calendar_fixture(db, *, state="available", link="", stale=False):
    event = {
        "title": "読書会",
        "start_at": "2026-09-07T14:00:00Z",
        "end_at": "2026-09-07T16:00:00Z",
        "daymeld_entry_id": link,
    }
    metadata = {
        "calendar": {
            "state": state,
            "updated_at": (NOW - timedelta(days=2) if stale else NOW).isoformat(),
            "start_at": "2026-09-07T00:00:00Z",
            "end_at": "2026-09-10T00:00:00Z",
        }
    }
    with sqlite3.connect(db) as connection:
        for device in ["phone-a", "phone-b"]:
            connection.execute(
                "INSERT OR REPLACE INTO device_context_devices VALUES(?,?,?)",
                (device, NOW.isoformat(), diary.encode(metadata)),
            )
            connection.execute(
                "INSERT OR REPLACE INTO device_calendar_events VALUES(?,?,?,?,?)",
                (device, "event", event["start_at"], event["end_at"], diary.encode(event)),
            )


def test_calendar_cross_day_dedup_stale_and_failure_are_not_attendance(stores):
    calendar_fixture(stores[1], stale=True)
    result = create_draft(stores)
    assert len(result["entry"]["source_snapshot"]["items"]) == 1
    assert "参加実績は未確認" in result["entry"]["body"]
    assert "24時間" in "".join(result["entry"]["source_snapshot"]["warnings"])
    calendar_fixture(stores[1], state="unavailable")
    result = create_draft(stores, revision=1)
    assert "読書会" in result["entry"]["body"]
    assert "取得失敗" in "".join(result["entry"]["source_snapshot"]["warnings"])
    calendar_fixture(stores[1], state="denied")
    result = create_draft(stores, revision=2)
    assert not result["entry"]["source_snapshot"]["items"]


def test_calendar_link_to_life_event_is_deduplicated_even_when_cancelled(stores):
    event = life_assistant.create_entry(
        stores[1],
        {
            "kind": "event",
            "title": "読書会",
            "start_at": "2026-09-07T14:00:00Z",
            "end_at": "2026-09-07T16:00:00Z",
            "timezone": "Asia/Tokyo",
        },
    )
    calendar_fixture(stores[1], link=event["id"])
    assert len(create_draft(stores)["entry"]["source_snapshot"]["items"]) == 1
    life_assistant.update_entry(stores[1], event["id"], {"revision": 1, "status": "cancelled"})
    assert not create_draft(stores, revision=1)["entry"]["source_snapshot"]["items"]


def test_edit_preserved_on_delayed_sync_and_deleted_source_in_history(stores):
    planner, _ = stores
    task = create_task(planner, {"title": "本を返す"}, NOW)
    set_task_completion(planner, task["id"], True, DAY, NOW)
    create_draft(stores)
    saved = diary.save(planner, {"date": str(DAY), "revision": 1, "body": "楽しい一日でした。"})
    assert saved["entry"]["revision"] == 2
    upsert_health_checkin(planner, {"date": str(DAY), "steps": 42}, NOW, "shortcut")
    delete_task(planner, task["id"])
    result = diary.generate(*stores, DAY, automatic=True, now=NOW)
    assert result["entry"]["body"] == "楽しい一日でした。"
    assert "歩数: 42歩" in result["entry"]["generated_body"]
    assert result["entry"]["edited_sources"]["items"][0]["title"] == "本を返す"
    assert result["history"][0]["source_snapshot"]["items"][0]["title"] == "本を返す"
    with pytest.raises(diary.DiaryConflict):
        diary.save(planner, {"date": str(DAY), "revision": 2, "body": "古い端末"})
    with pytest.raises(diary.DiaryConflict):
        diary.save(planner, {"date": str(DAY), "revision": 2}, delete=True)
    upsert_health_checkin(
        planner, {"date": str(DAY), "steps": 42}, NOW + timedelta(minutes=30), "shortcut"
    )
    assert create_draft(stores, revision=3) == result


def test_deletion_clears_copies_prevents_automatic_resurrection_and_checks_revision(stores):
    planner, _ = stores
    create_draft(stores)
    diary.save(planner, {"date": str(DAY), "revision": 1, "body": "個人の日記"})
    removed = diary.save(planner, {"date": str(DAY), "revision": 2}, delete=True)
    assert removed["state"] == "deleted"
    assert removed["history"] == []
    assert removed["entry"]["body"] == ""
    assert removed["entry"]["edited_sources"] is None
    assert diary.generate(*stores, DAY, automatic=True, now=NOW) == removed
    assert create_draft(stores, revision=3)["state"] == "ready"


def test_worker_cutoff_window_stop_and_restart_without_external_calls(stores, monkeypatch):
    import socket
    import subprocess

    def forbidden(*args, **kwargs):
        pytest.fail("Diary attempted an external operation")

    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    worker = diary.DiaryWorker(*stores)
    worker.step(NOW)
    assert diary.get_entry(stores[0], DAY - timedelta(days=1))["state"] == "missing"
    first = diary.get_entry(stores[0], DAY)
    diary.DiaryWorker(*stores).step(NOW)
    assert diary.get_entry(stores[0], DAY) == first
    diary.update_settings(stores[0], {"enabled": False})
    worker.step(NOW + timedelta(days=1))
    assert diary.get_entry(stores[0], DAY + timedelta(days=1))["state"] == "missing"
    diary.update_settings(stores[0], {"enabled": True})
    worker.step(NOW + timedelta(days=10))
    assert diary.get_entry(stores[0], DAY + timedelta(days=2))["state"] == "missing"
    assert diary.get_entry(stores[0], DAY + timedelta(days=3))["state"] == "ready"
    worker.stop()
    worker.step(NOW + timedelta(days=11))
    assert diary.get_entry(stores[0], DAY + timedelta(days=11))["state"] == "missing"


def test_source_failure_keeps_saved_draft_and_user_text(stores):
    create_draft(stores)
    saved = diary.save(stores[0], {"date": str(DAY), "revision": 1, "body": "保持する文章"})
    stores[1].write_bytes(b"broken database")
    with pytest.raises(diary.SourceUnavailable):
        create_draft(stores, revision=2)
    failed = diary.get_entry(stores[0], DAY)
    assert failed["entry"] == saved["entry"]
    assert failed["history"] == saved["history"]
    assert failed["generation_error"] == str(diary.SourceUnavailable())


def test_competing_save_during_collection_is_not_overwritten(stores, monkeypatch):
    create_draft(stores)
    original = diary.collect

    def collect(*args):
        snapshot = original(*args)
        diary.save(stores[0], {"date": str(DAY), "revision": 1, "body": "別端末の保存"})
        return snapshot

    monkeypatch.setattr(diary, "collect", collect)
    result = diary.generate(*stores, DAY, automatic=True, now=NOW)
    assert result["entry"]["body"] == "別端末の保存"
    assert result["entry"]["revision"] == 2


def test_concurrent_first_generation_creates_one_revision(stores):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(lambda _: diary.generate(*stores, DAY, automatic=True, now=NOW), range(2))
        )
    assert {r["entry"]["revision"] for r in results} == {1}
    assert diary.get_entry(stores[0], DAY)["history"] == []


@pytest.mark.parametrize("value", [None, "", "20260908", "2026-02-30", "2026-9-08", True, 1])
def test_invalid_dates(value):
    with pytest.raises(ValueError):
        diary.parse_day(value)


def test_invalid_revision_body_settings_and_future(stores):
    create_draft(stores)
    for revision in [None, True, -1, "1"]:
        with pytest.raises(ValueError):
            diary.save(stores[0], {"date": str(DAY), "revision": revision, "body": "文章"})
    for body in [None, 12, "あ" * 20001]:
        with pytest.raises(ValueError):
            diary.save(stores[0], {"date": str(DAY), "revision": 1, "body": body})
    with pytest.raises(ValueError):
        diary.update_settings(stores[0], {"enabled": 1})
    with pytest.raises(ValueError):
        create_draft(stores, day=date(2027, 1, 1))


def test_worker_start_does_not_initialize_planner_on_http_thread(stores, monkeypatch):
    import threading

    attempted = threading.Event()
    main_thread = threading.current_thread()

    def failing_settings(*args):
        assert threading.current_thread() is not main_thread
        attempted.set()
        raise sqlite3.OperationalError("private database detail")

    monkeypatch.setattr(diary, "settings", failing_settings)
    worker = diary.DiaryWorker(*stores)
    worker.start()
    try:
        assert attempted.wait(10)
    finally:
        worker.stop()
        worker.thread.join(timeout=10)
    assert not worker.thread.is_alive()


def test_source_failure_status_clears_after_recovery(stores, monkeypatch):
    original = diary.collect

    def failure(*args):
        raise diary.SourceUnavailable()

    create_draft(stores)
    monkeypatch.setattr(diary, "collect", failure)
    with pytest.raises(diary.SourceUnavailable):
        create_draft(stores, revision=1)
    assert diary.get_entry(stores[0], DAY)["generation_error"]
    monkeypatch.setattr(diary, "collect", original)
    recovered = create_draft(stores, revision=1)
    assert recovered["generation_error"] is None
    assert recovered["entry"]["revision"] == 1
