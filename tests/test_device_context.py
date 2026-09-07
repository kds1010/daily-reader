from __future__ import annotations

import copy
import io
import json
from datetime import UTC, datetime, timedelta

import pytest

from daily_reader import device_context as context
from daily_reader import life_assistant as life
from daily_reader import life_automation as automation
from daily_reader.conversations import (
    _connect,
    get_recording,
    store_location_events,
    store_transcript,
)
from daily_reader.local_server import make_handler


@pytest.fixture
def db(tmp_path):
    return tmp_path / "db"


def payload(now=None, device="iphone"):
    now = now or datetime.now(UTC)
    return {
        "device_id": device,
        "captured_at": now.isoformat(),
        "timezone": "Asia/Tokyo",
        "calendar": {
            "state": "available",
            "start_at": (now - timedelta(days=7)).isoformat(),
            "end_at": (now + timedelta(days=14)).isoformat(),
            "items": [],
        },
        "motion": {
            "state": "available",
            "start_at": (now - timedelta(days=7)).isoformat(),
            "end_at": now.isoformat(),
            "items": [],
        },
    }


def event(start, end, **extra):
    return {
        "id": "event",
        "title": "打ち合わせ",
        "location": "会議室",
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "busy": True,
        "all_day": False,
        "daymeld_entry_id": "",
        **extra,
    }


def test_ingest_retry_older_delivery_and_partial_failure(db):
    initial = payload()
    now = datetime.fromisoformat(initial["captured_at"])
    initial["calendar"]["items"] = [event(now, now + timedelta(hours=1))]
    assert context.ingest(db, initial)["accepted"]
    assert not context.ingest(db, initial)["accepted"]
    later = payload(now + timedelta(seconds=1))
    later["calendar"] = {"state": "unavailable"}
    context.ingest(db, later)
    with _connect(db) as conn:
        assert len(context.context_at(conn, now.isoformat())["calendar"]) == 1
    assert not context.overview(db, [], now=now)["calendar_ready"]
    assert not context.ingest(db, initial)["accepted"]


@pytest.mark.parametrize("state", ["disabled", "denied"])
def test_revocation_clears_only_that_device(db, state):
    initial = payload()
    now = datetime.fromisoformat(initial["captured_at"])
    initial["calendar"]["items"] = [event(now, now + timedelta(hours=1))]
    context.ingest(db, initial)
    other = copy.deepcopy(initial)
    other["device_id"] = "second"
    context.ingest(db, other)
    later = payload(now + timedelta(seconds=1))
    later["calendar"] = {"state": state}
    context.ingest(db, later)
    with _connect(db) as conn:
        assert [r[0] for r in conn.execute("SELECT device_id FROM device_calendar_events")] == [
            "second"
        ]


def test_deleted_calendar_events_are_removed_in_covered_window(db):
    initial = payload()
    now = datetime.fromisoformat(initial["captured_at"])
    initial["calendar"]["items"] = [event(now, now + timedelta(hours=1))]
    context.ingest(db, initial)
    context.ingest(db, payload(now + timedelta(seconds=1)))
    with _connect(db) as conn:
        assert not context.context_at(conn, now.isoformat())["calendar"]


def test_recording_and_audio_time_links_include_evidence_not_attendance(db):
    initial = payload()
    now = datetime.fromisoformat(initial["captured_at"])
    target = now - timedelta(minutes=10)
    initial["calendar"]["items"] = [event(target, now)]
    initial["motion"]["items"] = [
        {
            "start_at": target.isoformat(),
            "end_at": now.isoformat(),
            "activity": "walking",
            "confidence": "high",
        }
    ]
    context.ingest(db, initial)
    text = "会話のテスト".encode()
    record = store_transcript(db, io.BytesIO(text), len(text), "test.txt", target.isoformat())
    link = get_recording(db, record["id"])["location_contexts"][0]
    assert link["state"] == "no_nearby_gps"
    assert link["device_context"]["calendar"][0]["title"] == "打ち合わせ"
    assert link["device_context"]["motion"][0]["activity"] == "walking"
    assert link["device_context"]["basis"] == "time_overlap_not_attendance"
    with _connect(db) as conn:
        assert not context.context_at(conn, now.isoformat())["calendar"]  # half-open
        assert context.context_at(conn, None)["calendar"] == []


def test_uncertain_motion_never_becomes_confirmed_activity(db):
    initial = payload()
    now = datetime.fromisoformat(initial["captured_at"])
    initial["motion"]["items"] = [
        {
            "start_at": (now - timedelta(minutes=2)).isoformat(),
            "end_at": now.isoformat(),
            "activity": "automotive",
            "confidence": "low",
        }
    ]
    context.ingest(db, initial)
    with _connect(db) as conn:
        assert context.context_at(conn, (now - timedelta(minutes=1)).isoformat())["motion"] == []


def test_calendar_conflict_blocks_automatic_event_and_ignores_own_export(db):
    initial = payload()
    now = datetime.fromisoformat(initial["captured_at"])
    start, end = now + timedelta(days=1), now + timedelta(days=1, hours=1)
    initial["calendar"]["items"] = [event(start, end)]
    context.ingest(db, initial)
    item = {
        "kind": "event",
        "title": "別の用事",
        "detail": "",
        "id": "item",
        "recording_id": "record",
        "evidence": [],
        "certainty": "explicit",
        "recorded_at_verified": True,
        "life_data": {
            "intent": "committed",
            "time_basis": "absolute",
            "start_at": start.isoformat(),
            "end_at": end.isoformat(),
        },
    }
    _, _, reason, auto = automation._candidate(db, item)
    assert not auto and "カレンダー" in reason
    changed = copy.deepcopy(initial)
    changed["captured_at"] = (now + timedelta(seconds=1)).isoformat()
    changed["calendar"]["items"][0]["daymeld_entry_id"] = "mine"
    context.ingest(db, changed)
    assert context.event_conflicts(db, item["life_data"], "mine") == []


def test_free_and_touching_events_do_not_conflict(db):
    initial = payload()
    now = datetime.fromisoformat(initial["captured_at"])
    initial["calendar"]["items"] = [
        event(now, now + timedelta(hours=1), busy=False),
        event(now + timedelta(hours=1), now + timedelta(hours=2), id="next"),
    ]
    context.ingest(db, initial)
    assert (
        context.event_conflicts(
            db, {"start_at": now.isoformat(), "end_at": (now + timedelta(hours=1)).isoformat()}
        )
        == []
    )


def test_windows_consider_overlap_local_day_and_deadline(db):
    # Today in Tokyo, 09:00, without assuming the test runner's local timezone.
    now = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    initial = payload(now)
    initial["calendar"]["items"] = [
        event(now, now + timedelta(hours=1)),
        event(now + timedelta(minutes=30), now + timedelta(hours=2), id="overlap"),
    ]
    context.ingest(db, initial)
    too_soon = life.create_entry(
        db,
        {
            "kind": "task",
            "title": "先に期限が来る",
            "due_at": (now + timedelta(minutes=30)).isoformat(),
        },
    )
    suitable = life.create_entry(db, {"kind": "task", "title": "資料の確認"})
    value = context.overview(db, life.snapshot(db)["entries"], now=now)
    assert value["calendar_ready"]
    assert value["suggestions"][0]["task_id"] == suitable["id"]
    assert value["suggestions"][0]["start_at"] == (now + timedelta(hours=2)).isoformat()
    assert all(s["task_id"] != too_soon["id"] for s in value["suggestions"])
    assert (
        context.overview(db, life.snapshot(db)["entries"], now=now + timedelta(days=2))[
            "suggestions"
        ]
        == []
    )


@pytest.mark.parametrize(
    "bad",
    [
        {"timezone": "invalid"},
        {"captured_at": "2026-09-01T10:00:00"},
        {"calendar": {"state": "available", "start_at": "bad", "end_at": "bad", "items": []}},
        {"motion": {"state": "unexpected"}},
    ],
)
def test_invalid_payload_is_atomic(db, bad):
    initial = payload()
    context.ingest(db, initial)
    with pytest.raises(ValueError):
        context.ingest(db, {**initial, **bad})
    with _connect(db) as conn:
        assert (
            conn.execute("SELECT captured_at FROM device_context_devices").fetchone()[0]
            == initial["captured_at"]
        )


def test_different_devices_do_not_duplicate_agenda(db):
    initial = payload()
    now = datetime.fromisoformat(initial["captured_at"])
    initial["calendar"]["items"] = [event(now + timedelta(hours=1), now + timedelta(hours=2))]
    context.ingest(db, initial)
    other = copy.deepcopy(initial)
    other["device_id"] = "second"
    other["calendar"]["items"][0]["id"] = "another-id"
    context.ingest(db, other)
    assert len(context.overview(db, [], now=now)["agenda"]) == 1


def test_gps_displacement_and_simulation_are_not_used_as_places(db):
    now = datetime.now(UTC)
    sample = {
        "timestamp": (now - timedelta(minutes=4)).isoformat(),
        "latitude": 35.5,
        "longitude": 139.5,
        "horizontal_accuracy": 20,
        "is_approximate": False,
        "speed_mps": 20,
        "speed_accuracy_mps": 1,
    }
    store_location_events(db, [sample])
    record = store_transcript(db, io.BytesIO(b"test"), 4, "test.txt", now.isoformat())
    assert get_recording(db, record["id"])["location_contexts"][0]["location"] is None
    store_location_events(db, [{**sample, "timestamp": now.isoformat(), "is_simulated": True}])
    assert get_recording(db, record["id"])["location_contexts"][0]["location"] is None
    store_location_events(db, [{**sample, "timestamp": (now + timedelta(seconds=1)).isoformat()}])
    result = get_recording(db, record["id"])["location_contexts"][0]
    assert result["state"] == "matched_estimate"
    assert result["location"]["speed_mps"] == 20


def test_http_device_snapshot_and_life_context(tmp_path, db):
    (tmp_path / "articles.json").write_text('{"articles": []}')
    factory = make_handler(
        *[
            tmp_path / p
            for p in ["site", "articles.json", "read", "feedback", "assistant", "client", "token"]
        ],
        conversations_db=db,
    )
    handler = factory.func.__new__(factory.func)
    responses = []
    handler._send_json = lambda status, value: responses.append((status, value))
    handler.path = "/api/device-context/sync"
    handler._read_json = lambda **_: payload()
    handler.do_POST()
    assert responses[-1][0] == 200
    handler.path = "/api/life"
    handler.do_GET()
    assert responses[-1][1]["device_context"]["devices"][0]["timezone"] == "Asia/Tokyo"
    # Context remains outside the public research/extraction request data.
    assert "calendar" not in json.dumps(automation.settings(db))
