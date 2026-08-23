from __future__ import annotations

import sqlite3
import uuid
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from typing import Any

RECURRENCES = {"none", "daily", "weekdays", "weekly"}


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                due_date TEXT,
                priority INTEGER NOT NULL DEFAULT 2,
                recurrence TEXT NOT NULL DEFAULT 'none',
                created_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS routine_completions (
                task_id TEXT NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                completed_date TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                PRIMARY KEY (task_id, completed_date)
            );
            CREATE TABLE IF NOT EXISTS health_checkins (
                checkin_date TEXT PRIMARY KEY,
                sleep_minutes INTEGER,
                steps INTEGER,
                resting_heart_rate REAL,
                hrv_ms REAL,
                respiratory_rate REAL,
                fatigue INTEGER,
                mood INTEGER,
                note TEXT,
                source TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )
        connection.execute("PRAGMA foreign_keys = ON")


def _validate_date(value: object, *, required: bool = False) -> str | None:
    if (value is None or value == "") and not required:
        return None
    if not isinstance(value, str):
        raise ValueError("invalid date")
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as error:
        raise ValueError("invalid date") from error


def create_task(path: Path, payload: dict[str, object], now: datetime) -> dict[str, Any]:
    title = payload.get("title")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        raise ValueError("invalid title")
    due_date = _validate_date(payload.get("due_date"))
    recurrence = payload.get("recurrence", "none")
    if recurrence not in RECURRENCES:
        raise ValueError("invalid recurrence")
    priority = payload.get("priority", 2)
    if not isinstance(priority, int) or isinstance(priority, bool) or priority not in {1, 2, 3}:
        raise ValueError("invalid priority")
    task = {
        "id": uuid.uuid4().hex,
        "title": title.strip(),
        "due_date": due_date,
        "priority": priority,
        "recurrence": recurrence,
        "created_at": now.isoformat(),
    }
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO tasks (id, title, due_date, priority, recurrence, created_at)
            VALUES (:id, :title, :due_date, :priority, :recurrence, :created_at)""",
            task,
        )
    return task


def _routine_applies(task: dict[str, Any], day: date) -> bool:
    recurrence = task["recurrence"]
    start = date.fromisoformat(task["due_date"] or task["created_at"][:10])
    if day < start:
        return False
    if recurrence == "daily":
        return True
    if recurrence == "weekdays":
        return day.weekday() < 5
    if recurrence == "weekly":
        return start.weekday() == day.weekday()
    return False


def list_today(path: Path, day: date) -> dict[str, Any]:
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        task_rows = connection.execute(
            """SELECT * FROM tasks
            WHERE recurrence = 'none' AND completed_at IS NULL
              AND (due_date IS NULL OR due_date <= ?)
            ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END,
              due_date, priority, created_at""",
            (day.isoformat(),),
        ).fetchall()
        routine_rows = connection.execute(
            """SELECT tasks.*,
                CASE WHEN routine_completions.task_id IS NULL THEN 0 ELSE 1 END AS completed_today
            FROM tasks
            LEFT JOIN routine_completions
              ON routine_completions.task_id = tasks.id
             AND routine_completions.completed_date = ?
            WHERE tasks.recurrence != 'none' AND tasks.completed_at IS NULL
            ORDER BY tasks.priority, tasks.created_at""",
            (day.isoformat(),),
        ).fetchall()
        health_row = connection.execute(
            "SELECT * FROM health_checkins WHERE checkin_date = ?",
            (day.isoformat(),),
        ).fetchone()
    routines = [dict(row) for row in routine_rows]
    return {
        "date": day.isoformat(),
        "tasks": [dict(row) for row in task_rows],
        "routines": [task for task in routines if _routine_applies(task, day)],
        "health": dict(health_row) if health_row else None,
    }


def set_task_completion(
    path: Path, task_id: str, completed: bool, day: date, now: datetime
) -> bool:
    if not isinstance(task_id, str) or not task_id:
        return False
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        row = connection.execute(
            "SELECT recurrence FROM tasks WHERE id = ?", (task_id,)
        ).fetchone()
        if row is None:
            return False
        if row[0] == "none":
            cursor = connection.execute(
                "UPDATE tasks SET completed_at = ? WHERE id = ?",
                (now.isoformat() if completed else None, task_id),
            )
            return cursor.rowcount == 1
        if completed:
            connection.execute(
                """INSERT INTO routine_completions (task_id, completed_date, completed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(task_id, completed_date) DO UPDATE SET
                    completed_at = excluded.completed_at""",
                (task_id, day.isoformat(), now.isoformat()),
            )
        else:
            connection.execute(
                "DELETE FROM routine_completions WHERE task_id = ? AND completed_date = ?",
                (task_id, day.isoformat()),
            )
    return True


def delete_task(path: Path, task_id: str) -> bool:
    if not isinstance(task_id, str) or not task_id:
        return False
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        cursor = connection.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
    return cursor.rowcount == 1


def upsert_health_checkin(
    path: Path, payload: dict[str, object], now: datetime, source: str
) -> dict[str, Any]:
    checkin_date = _validate_date(payload.get("date"), required=True)
    fields: dict[str, type] = {
        "sleep_minutes": int,
        "steps": int,
        "resting_heart_rate": float,
        "hrv_ms": float,
        "respiratory_rate": float,
        "fatigue": int,
        "mood": int,
        "note": str,
    }
    values: dict[str, Any] = {"checkin_date": checkin_date}
    for name, expected in fields.items():
        value = payload.get(name)
        if value is None:
            values[name] = None
        elif expected is str:
            if not isinstance(value, str) or len(value) > 1000:
                raise ValueError(f"invalid {name}")
            values[name] = value.strip()
        elif not isinstance(value, (int, float)) or isinstance(value, bool):
            raise ValueError(f"invalid {name}")
        else:
            values[name] = expected(value)
    if values["sleep_minutes"] is not None and not 0 <= values["sleep_minutes"] <= 1440:
        raise ValueError("invalid sleep_minutes")
    if values["steps"] is not None and not 0 <= values["steps"] <= 200_000:
        raise ValueError("invalid steps")
    ranges = {
        "resting_heart_rate": (20, 300),
        "hrv_ms": (0, 1000),
        "respiratory_rate": (1, 100),
    }
    for name, (minimum, maximum) in ranges.items():
        if values[name] is not None and (
            not isfinite(values[name]) or not minimum <= values[name] <= maximum
        ):
            raise ValueError(f"invalid {name}")
    for name in ("fatigue", "mood"):
        if values[name] is not None and values[name] not in range(1, 6):
            raise ValueError(f"invalid {name}")
    values.update({"source": source, "updated_at": now.isoformat()})
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO health_checkins (
                checkin_date, sleep_minutes, steps, resting_heart_rate, hrv_ms,
                respiratory_rate, fatigue, mood, note, source, updated_at
            ) VALUES (
                :checkin_date, :sleep_minutes, :steps, :resting_heart_rate, :hrv_ms,
                :respiratory_rate, :fatigue, :mood, :note, :source, :updated_at
            )
            ON CONFLICT(checkin_date) DO UPDATE SET
                sleep_minutes=COALESCE(excluded.sleep_minutes, health_checkins.sleep_minutes),
                steps=COALESCE(excluded.steps, health_checkins.steps),
                resting_heart_rate=COALESCE(
                    excluded.resting_heart_rate, health_checkins.resting_heart_rate
                ),
                hrv_ms=COALESCE(excluded.hrv_ms, health_checkins.hrv_ms),
                respiratory_rate=COALESCE(
                    excluded.respiratory_rate, health_checkins.respiratory_rate
                ),
                fatigue=COALESCE(excluded.fatigue, health_checkins.fatigue),
                mood=COALESCE(excluded.mood, health_checkins.mood),
                note=COALESCE(excluded.note, health_checkins.note),
                source=excluded.source,
                updated_at=excluded.updated_at""",
            values,
        )
    return values
