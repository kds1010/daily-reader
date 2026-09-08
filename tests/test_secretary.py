import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from daily_reader import device_context, life_assistant, secretary

NOW = datetime(2026, 9, 8, 3, tzinfo=UTC)


def task(identity="task", **changes):
    return {
        "id": identity,
        "kind": "task",
        "status": "open",
        "title": "確認する",
        "due_at": "2026-09-08T10:00:00+09:00",
        "revision": 1,
        "evidence": {},
        **changes,
    }


def life(*entries, **changes):
    return {
        "entries": list(entries),
        "drafts": [],
        "news": [],
        "automation": {"enabled": True},
        **changes,
    }


def get(tmp_path, data, now=NOW):
    return secretary.snapshot(
        tmp_path / "life.db", data, tmp_path / "planner.db", tmp_path / "email.db", {}, now=now
    )


def test_ranking_midnight_and_distinct_source_ids():
    pending = {"id": "draft", "data": task("draft"), "reason": "担当の確認"}
    items = secretary.cards(
        life(
            task("one"),
            task("two"),
            task("tomorrow", due_at="2026-09-09T00:00:00+09:00"),
            drafts=[pending],
        ),
        {},
        [],
        NOW,
    )
    assert [i["id"] for i in items] == ["life:one", "life:two", "draft:draft", "life:tomorrow"]
    assert [i["urgent"] for i in items] == [True, True, True, False]
    assert items[2]["certainty"] == "unconfirmed"
    assert all("version" in item for item in items)


def test_event_steps_suppress_completed_registered_and_cancelled():
    event = {
        "id": "event",
        "kind": "event",
        "status": "planned",
        "revision": 1,
        "title": "参加",
        "start_at": "2026-09-09T09:00:00+09:00",
        "end_at": "2026-09-09T10:00:00+09:00",
        "prepare_at": "2026-09-08T09:00:00+09:00",
        "deadline_at": "2026-09-08T09:00:00+09:00",
    }
    prepare = task("prepare", evidence={"type": "event", "entry_id": "event", "role": "prepare"})
    data = life(event, prepare)
    ids = [i["id"] for i in secretary.cards(data, {}, [], NOW)]
    assert "life:prepare" in ids and "life:event:prepare" not in ids
    for state in ("completed", "cancelled"):
        prepare["status"] = state
        ids = [i["id"] for i in secretary.cards(data, {}, [], NOW)]
        assert "life:prepare" not in ids and "life:event:prepare" not in ids
    event["status"] = "registered"
    assert all(i["id"] != "life:event:deadline" for i in secretary.cards(data, {}, [], NOW))
    event["status"] = "cancelled"
    prepare["status"] = "open"
    assert secretary.cards(data, {}, [], NOW) == []


def test_card_review_replay_version_and_next_day(tmp_path):
    data = life(task())
    initial = get(tmp_path, data)
    card = initial["items"][0]
    payload = {"card_id": card["id"], "version": card["version"], "status": "reviewed"}
    secretary.save_card(tmp_path / "life.db", payload, initial, now=NOW)
    secretary.save_card(tmp_path / "life.db", payload, initial, now=NOW)
    current = get(tmp_path, data)
    assert current["top_ids"] == []
    assert current["urgent_count"] == current["deferred_urgent_count"] == 1
    assert data["entries"][0]["status"] == "open"
    assert get(tmp_path, data, NOW + timedelta(days=1))["top_ids"] == [card["id"]]
    data["entries"][0]["title"] = "修正後"
    changed = get(tmp_path, data)
    assert changed["top_ids"] == [card["id"]]
    with pytest.raises(ValueError, match="更新"):
        secretary.save_card(tmp_path / "life.db", payload, changed, now=NOW)


def test_snooze_expiry_and_validation(tmp_path):
    data = life(task())
    initial = get(tmp_path, data)
    card = initial["items"][0]
    payload = {
        "card_id": card["id"],
        "version": card["version"],
        "status": "snoozed",
        "until_at": (NOW + timedelta(hours=1)).isoformat(),
    }
    secretary.save_card(tmp_path / "life.db", payload, initial, now=NOW)
    assert not get(tmp_path, data)["top_ids"]
    assert get(tmp_path, data, NOW + timedelta(hours=1))["top_ids"]
    for bad in (None, NOW.isoformat(), (NOW + timedelta(days=8)).isoformat()):
        with pytest.raises(ValueError):
            secretary.save_card(
                tmp_path / "life.db", {**payload, "until_at": bad}, initial, now=NOW
            )


def test_routines_reappear_on_next_local_day():
    planner = {
        "routines": [
            {"id": "routine", "title": "確認", "recurrence": "daily", "completed_today": 0}
        ]
    }
    one = secretary.cards(life(), planner, [], NOW)[0]
    two = secretary.cards(life(), planner, [], NOW + timedelta(days=1))[0]
    assert one["version"] != two["version"]
    planner["routines"][0]["completed_today"] = 1
    assert not secretary.cards(life(), planner, [], NOW)


def test_email_resync_does_not_undo_review_but_new_message_does():
    email = {
        "thread_id": "thread",
        "subject": "確認",
        "required_action": "返信する",
        "latest_message_id": "one",
        "status": "open",
        "importance": "high",
        "reason": "重要",
        "gmail_url": "https://mail.google.com/",
        "due_date": "2026-09-08",
    }
    one = secretary.cards(life(), {}, [email], NOW)[0]
    email["updated_at"] = (NOW + timedelta(minutes=15)).isoformat()
    two = secretary.cards(life(), {}, [email], NOW)[0]
    assert one["version"] == two["version"] and one["certainty"] == "estimated"
    email["latest_message_id"] = "two"
    assert secretary.cards(life(), {}, [email], NOW)[0]["version"] != one["version"]


def test_source_failures_are_isolated_and_timestamps_are_real(tmp_path, monkeypatch):
    def fail(*_args):
        raise sqlite3.OperationalError("fixture failure")

    monkeypatch.setattr(secretary.daily_planner, "list_today", fail)
    monkeypatch.setattr(
        secretary.email_assistant,
        "get_gmail_sync_state",
        lambda _: {"completed_at": "2026-09-01T00:00:00Z"},
    )
    monkeypatch.setattr(
        secretary.email_assistant,
        "get_gmail_sync_status",
        lambda _: {"authorization_required": True},
    )
    monkeypatch.setattr(secretary.email_assistant, "list_unread_threads", lambda *_: [])
    value = get(tmp_path, life(task()))
    sources = {s["id"]: s for s in value["sources"]}
    assert sources["planner"]["state"] == "failed"
    assert sources["email"]["state"] == "authorization_required"
    assert sources["email"]["last_success_at"] == "2026-09-01T00:00:00Z"
    assert sources["life"]["last_success_at"] is None
    assert value["top_ids"] == ["life:task"]
    assert secretary._fresh("2026-09-01T00:00:00Z", NOW, 24) == "stale"
    assert secretary._fresh((NOW + timedelta(seconds=1)).isoformat(), NOW, 24) == "stale"


def test_calendar_all_ids_and_freshness(tmp_path, monkeypatch):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW

    monkeypatch.setattr(device_context, "datetime", FixedDateTime)
    db = tmp_path / "life.db"
    calendar = {
        "state": "available",
        "start_at": "2026-09-08T00:00:00+09:00",
        "end_at": "2026-09-10T00:00:00+09:00",
        "items": [
            {
                "id": str(i),
                "title": "同じ名前",
                "start_at": "2026-09-08T14:00:00+09:00",
                "end_at": "2026-09-08T15:00:00+09:00",
                "busy": True,
                "all_day": False,
            }
            for i in range(7)
        ],
    }
    device_context.ingest(
        db,
        {
            "device_id": "phone",
            "captured_at": NOW.isoformat(),
            "timezone": "Asia/Tokyo",
            "calendar": calendar,
            "motion": {"state": "disabled"},
        },
    )
    context = device_context.overview(db, [], now=NOW)
    value = get(tmp_path, life(device_context=context))
    assert len(value["items"]) == 7
    assert value["remaining_count"] == 4 and value["urgent_count"] == 7
    assert get(tmp_path, life(device_context=context), NOW + timedelta(days=2))["items"] == []
    context["devices"][0]["calendar"]["state"] = "unavailable"
    value = get(tmp_path, life(device_context=context))
    assert not value["items"]
    assert next(s for s in value["sources"] if s["id"] == "calendar.phone")["state"] == "failed"


def test_daily_idempotency_conflicts_and_nulls(tmp_path):
    db = tmp_path / "life.db"
    payload = {
        "request_id": "request-one",
        "day": "2026-09-08",
        "timezone": "Asia/Tokyo",
        "revision": 0,
        "browsing_minutes": 0,
        "management_minutes": None,
        "forgotten_count": 1,
    }
    assert secretary.save_day(db, payload) == {"ok": True, "revision": 1}
    assert secretary.save_day(db, payload)["revision"] == 1
    second = {**payload, "request_id": "two", "revision": 1, "browsing_minutes": 10}
    secretary.save_day(db, second)
    assert secretary.save_day(db, payload)["revision"] == 1  # lost reply replay, no rollback
    with pytest.raises(ValueError):
        secretary.save_day(db, {**payload, "browsing_minutes": 50})
    with pytest.raises(ValueError):
        secretary.save_day(db, {**payload, "request_id": "stale"})
    weekly = get(tmp_path, life())["weekly"]
    assert weekly["browsing_minutes"] == {"count": 1, "total": 10}
    assert weekly["management_minutes"] == {"count": 0, "total": None}
    assert weekly["today"]["revision"] == 2
    assert weekly["forgotten_count"]["total"] == 1


@pytest.mark.parametrize(
    "change",
    [
        {"browsing_minutes": True},
        {"browsing_minutes": -1},
        {"browsing_minutes": 1441},
        {"management_minutes": "2"},
        {"forgotten_count": 101},
        {"revision": True},
        {"timezone": "Invalid/Zone"},
        {"day": "2026-02-30"},
        {"request_id": ""},
    ],
)
def test_invalid_daily_input(tmp_path, change):
    with pytest.raises(ValueError):
        secretary.save_day(
            tmp_path / "life.db",
            {"request_id": "r", "day": "2026-09-08", "timezone": "Asia/Tokyo", **change},
        )


def test_weekly_uses_reported_at_and_keeps_estimate_separate(tmp_path):
    db = tmp_path / "life.db"
    for day, minutes in (("2026-09-06", 100), ("2026-09-07", 0), ("2026-09-08", None)):
        secretary.save_day(
            db,
            {"request_id": day, "day": day, "timezone": "Asia/Tokyo", "browsing_minutes": minutes},
        )
    data = life(
        {
            "id": "research",
            "kind": "research",
            "status": "completed",
            "title": "調査",
            "feedback": {"useful": True, "saved_minutes": 20, "reported_at": NOW.isoformat()},
        }
    )
    weekly = get(tmp_path, data)["weekly"]
    assert weekly["recorded_days"] == 2
    assert weekly["browsing_minutes"] == {"count": 1, "total": 0}
    assert weekly["saved_minutes"] == {"count": 1, "total": 20}
    assert weekly["research_evaluations"] == weekly["research_useful"] == 1
    assert get(tmp_path, data, NOW + timedelta(days=7))["weekly"]["saved_minutes"]["total"] is None


def test_existing_database_is_preserved(tmp_path):
    db = tmp_path / "life.db"
    entry = life_assistant.create_entry(db, {"kind": "task", "title": "既存の用事"})
    secretary.save_day(db, {"request_id": "r", "day": "2026-09-08", "timezone": "Asia/Tokyo"})
    assert life_assistant.get_entry(db, entry["id"])["title"] == "既存の用事"
    with life_assistant.connect(db) as connection:
        assert (
            json.loads(connection.execute("SELECT data FROM life_entries").fetchone()[0])["title"]
            == "既存の用事"
        )


def test_snooze_survives_midnight_but_not_content_change(tmp_path):
    before = datetime.fromisoformat("2026-09-08T23:40:00+09:00")
    data = life(task(due_at="2026-09-09T10:00:00+09:00"))
    current = get(tmp_path, data, before)
    card = current["items"][0]
    secretary.save_card(
        tmp_path / "life.db",
        {
            "card_id": card["id"],
            "version": card["version"],
            "status": "snoozed",
            "until_at": (before + timedelta(hours=1)).isoformat(),
        },
        current,
        now=before,
    )
    assert not get(tmp_path, data, before + timedelta(minutes=30))["top_ids"]
    assert get(tmp_path, data, before + timedelta(hours=1))["top_ids"]
    data["entries"][0]["title"] = "変更された用事"
    assert get(tmp_path, data, before + timedelta(minutes=30))["top_ids"]
