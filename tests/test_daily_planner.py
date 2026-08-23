from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from daily_reader.daily_planner import (
    create_task,
    delete_task,
    list_today,
    set_task_completion,
    upsert_health_checkin,
)
from daily_reader.local_server import build_parser, health_sync_authorized

NOW = datetime(2026, 8, 24, 8, 0, tzinfo=UTC)
TODAY = date(2026, 8, 24)


def test_tasks_and_daily_routines_have_separate_completion_behavior(tmp_path: Path) -> None:
    database = tmp_path / "planner.sqlite3"
    task = create_task(
        database,
        {"title": "書類を提出", "due_date": "2026-08-24", "priority": 1},
        NOW,
    )
    routine = create_task(
        database,
        {"title": "ストレッチ", "recurrence": "daily", "priority": 2},
        NOW,
    )

    today = list_today(database, TODAY)
    assert [item["id"] for item in today["tasks"]] == [task["id"]]
    assert [item["id"] for item in today["routines"]] == [routine["id"]]
    assert today["routines"][0]["completed_today"] == 0

    assert set_task_completion(database, routine["id"], True, TODAY, NOW)
    assert list_today(database, TODAY)["routines"][0]["completed_today"] == 1
    assert list_today(database, date(2026, 8, 25))["routines"][0]["completed_today"] == 0

    assert set_task_completion(database, task["id"], True, TODAY, NOW)
    assert list_today(database, TODAY)["tasks"] == []


def test_weekday_and_weekly_routines_follow_schedule(tmp_path: Path) -> None:
    database = tmp_path / "planner.sqlite3"
    create_task(database, {"title": "平日の習慣", "recurrence": "weekdays"}, NOW)
    create_task(
        database,
        {"title": "月曜の習慣", "recurrence": "weekly", "due_date": "2026-08-24"},
        NOW,
    )

    assert len(list_today(database, date(2026, 8, 24))["routines"]) == 2
    assert list_today(database, date(2026, 8, 29))["routines"] == []


def test_health_sources_merge_without_erasing_other_values(tmp_path: Path) -> None:
    database = tmp_path / "planner.sqlite3"
    upsert_health_checkin(
        database,
        {"date": "2026-08-24", "sleep_minutes": 412, "steps": 8432},
        NOW,
        "shortcut",
    )
    upsert_health_checkin(
        database,
        {"date": "2026-08-24", "fatigue": 2, "mood": 4, "note": "快調"},
        NOW,
        "manual",
    )

    health = list_today(database, TODAY)["health"]
    assert health["sleep_minutes"] == 412
    assert health["steps"] == 8432
    assert health["fatigue"] == 2
    assert health["mood"] == 4
    assert health["note"] == "快調"


def test_invalid_planner_input_is_rejected(tmp_path: Path) -> None:
    database = tmp_path / "planner.sqlite3"
    with pytest.raises(ValueError, match="invalid title"):
        create_task(database, {"title": "  "}, NOW)
    with pytest.raises(ValueError, match="invalid fatigue"):
        upsert_health_checkin(
            database, {"date": "2026-08-24", "fatigue": 6}, NOW, "manual"
        )


def test_task_can_be_deleted(tmp_path: Path) -> None:
    database = tmp_path / "planner.sqlite3"
    task = create_task(database, {"title": "削除対象"}, NOW)

    assert delete_task(database, task["id"])
    assert not delete_task(database, task["id"])


def test_planner_server_defaults_and_health_token(tmp_path: Path) -> None:
    args = build_parser().parse_args([])
    assert args.planner_db == Path("data/planner.sqlite3")
    assert args.health_sync_token == Path("secrets/health-sync-token.txt")

    token_path = tmp_path / "health-token.txt"
    assert not health_sync_authorized(token_path, "Bearer secret")
    token_path.write_text("secret\n", encoding="utf-8")
    assert health_sync_authorized(token_path, "Bearer secret")
    assert not health_sync_authorized(token_path, "Bearer wrong")
