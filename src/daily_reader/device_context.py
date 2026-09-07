"""Local iPhone context; calendar occupancy is never proof of attendance."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_reader.conversations import _connect

STATES = {"available", "disabled", "denied", "unavailable"}
ACTIVITIES = {"stationary", "walking", "running", "cycling", "automotive", "unknown"}


def _text(value, limit=200):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError("端末情報の文字列が不正です")
    return value


def _date(value):
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or not 1900 <= parsed.year <= 2200:
            raise ValueError
        return parsed.astimezone(UTC)
    except (ValueError, TypeError) as exc:
        raise ValueError("端末情報の日時が不正です") from exc


def _boolean(value):
    if type(value) is not bool:
        raise ValueError("端末情報のフラグが不正です")
    return value


def ingest(database: Path, payload: dict) -> dict:
    device = _text(payload.get("device_id"), 100)
    if not device:
        raise ValueError("端末IDが必要です")
    captured = _date(payload.get("captured_at"))
    now = datetime.now(UTC)
    if not now - timedelta(days=8) <= captured <= now + timedelta(minutes=5):
        raise ValueError("端末の時計または同期日時を確認してください")
    zone = _text(payload.get("timezone"), 100)
    try:
        ZoneInfo(zone)
    except (ValueError, KeyError) as exc:
        raise ValueError("タイムゾーンが不正です") from exc
    clean = {}
    for kind, limit, days in [("calendar", 1000, 32), ("motion", 3000, 7)]:
        source = payload.get(kind)
        if not isinstance(source, dict) or source.get("state") not in STATES:
            raise ValueError("取得状態が不正です")
        state = source["state"]
        items = []
        if state == "available":
            start, end = _date(source.get("start_at")), _date(source.get("end_at"))
            if not timedelta(0) < end - start <= timedelta(days=days):
                raise ValueError("取得期間が不正です")
            if kind == "motion" and end > captured:
                raise ValueError("未来の移動履歴は保存できません")
            if start < captured - timedelta(days=8) or end > captured + timedelta(days=25):
                raise ValueError("取得期間が同期日時から離れすぎています")
            raw = source.get("items")
            if not isinstance(raw, list) or len(raw) > limit:
                raise ValueError("取得件数が上限を超えています")
            ids = set()
            for item in raw:
                if not isinstance(item, dict):
                    raise ValueError("取得内容が不正です")
                a, b = _date(item.get("start_at")), _date(item.get("end_at"))
                if a >= b:
                    raise ValueError(f"取得した項目の期間が不正です: {kind} empty_or_reversed")
                if b <= start or a >= end:
                    raise ValueError(f"取得した項目の期間が不正です: {kind} outside_window")
                if kind == "calendar":
                    event_id = _text(item.get("id"), 200)
                    if not event_id or event_id in ids:
                        raise ValueError("予定IDが重複しています")
                    ids.add(event_id)
                    items.append(
                        {
                            "id": event_id,
                            "start_at": a.isoformat(),
                            "end_at": b.isoformat(),
                            "title": _text(item.get("title")),
                            "location": _text(item.get("location", ""), 300),
                            "busy": _boolean(item.get("busy")),
                            "all_day": _boolean(item.get("all_day")),
                            "daymeld_entry_id": _text(item.get("daymeld_entry_id", ""), 100),
                        }
                    )
                else:
                    activity = item.get("activity")
                    confidence = item.get("confidence")
                    if activity not in ACTIVITIES or confidence not in {"low", "medium", "high"}:
                        raise ValueError("移動状態が不正です")
                    a, b = max(a, start), min(b, end)
                    items.append(
                        {
                            "start_at": a.isoformat(),
                            "end_at": b.isoformat(),
                            "activity": activity,
                            "confidence": confidence,
                        }
                    )
            clean[kind] = {
                "state": state,
                "start_at": start.isoformat(),
                "end_at": end.isoformat(),
                "items": items,
            }
        else:
            clean[kind] = {"state": state}
    with _connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM device_context_devices WHERE id=?", (device,)
        ).fetchone()
        old = json.loads(row["data"]) if row else {}
        if row and _date(row["captured_at"]) >= captured:
            return {"accepted": False, "reason": "older_snapshot"}
        for kind in ("calendar", "motion"):
            section = clean[kind]
            table = "device_calendar_events" if kind == "calendar" else "device_motion_intervals"
            if section["state"] in {"disabled", "denied"}:
                connection.execute(f"DELETE FROM {table} WHERE device_id=?", (device,))
            elif section["state"] == "available":
                connection.execute(
                    f"DELETE FROM {table} WHERE device_id=? AND "
                    "julianday(start_at)<julianday(?) AND julianday(end_at)>julianday(?)",
                    (device, section["end_at"], section["start_at"]),
                )
                for item in section["items"]:
                    identity = item.get("id") or item["start_at"]
                    connection.execute(
                        f"INSERT OR REPLACE INTO {table} VALUES(?,?,?,?,?)",
                        (
                            device,
                            identity,
                            item["start_at"],
                            item["end_at"],
                            json.dumps(item, ensure_ascii=False),
                        ),
                    )
                section["updated_at"] = captured.isoformat()
            else:
                # A transient API failure is not an empty calendar or a permission revocation.
                section["updated_at"] = old.get(kind, {}).get("updated_at")
                section["start_at"] = old.get(kind, {}).get("start_at")
                section["end_at"] = old.get(kind, {}).get("end_at")
            section.pop("items", None)
        metadata = {"timezone": zone, **clean}
        connection.execute(
            "INSERT OR REPLACE INTO device_context_devices VALUES(?,?,?)",
            (device, captured.isoformat(), json.dumps(metadata)),
        )
        cutoff = (now - timedelta(days=90)).isoformat()
        for table in ("device_calendar_events", "device_motion_intervals"):
            connection.execute(
                f"DELETE FROM {table} WHERE julianday(end_at)<julianday(?)", (cutoff,)
            )
    return {"accepted": True}


def context_at(connection, target: str | None) -> dict:
    result = {"calendar": [], "motion": [], "basis": "time_overlap_not_attendance"}
    if not target:
        return result
    for kind, table in [
        ("calendar", "device_calendar_events"),
        ("motion", "device_motion_intervals"),
    ]:
        rows = connection.execute(
            f"SELECT data FROM {table} WHERE "
            "julianday(start_at)<=julianday(?) AND julianday(end_at)>julianday(?)",
            (target, target),
        ).fetchall()
        values = [json.loads(row[0]) for row in rows]
        if kind == "motion":
            values = [
                v
                for v in values
                if v["confidence"] in {"medium", "high"} and v["activity"] != "unknown"
            ]
        unique = {json.dumps(v, sort_keys=True): v for v in values}
        if kind == "calendar":
            unique = {(v["title"], v["start_at"], v["end_at"]): v for v in unique.values()}
        result[kind] = list(unique.values())[:10]
    return result


def _calendars(connection, now: datetime) -> tuple[list[dict], list[dict]]:
    devices = []
    fresh = []
    for row in connection.execute("SELECT * FROM device_context_devices ORDER BY captured_at DESC"):
        data = json.loads(row["data"])
        section = data["calendar"]
        is_fresh = (
            section["state"] == "available"
            and bool(section.get("updated_at"))
            and (now - _date(section["updated_at"]) <= timedelta(hours=24))
        )
        devices.append(
            {"id": row["id"], "captured_at": row["captured_at"], **data, "calendar_fresh": is_fresh}
        )
        if is_fresh:
            fresh.append(row["id"])
    events = []
    for device in fresh:
        events.extend(
            json.loads(row[0])
            for row in connection.execute(
                "SELECT data FROM device_calendar_events WHERE device_id=?", (device,)
            )
        )
    unique = {}
    for event in events:
        unique.setdefault((event["title"], event["start_at"], event["end_at"]), event)
    return devices, list(unique.values())


def event_conflicts(database: Path, payload: dict, entry_id: str = "") -> list[dict]:
    if not payload.get("start_at") or not payload.get("end_at"):
        return []
    start, end = _date(payload["start_at"]), _date(payload["end_at"])
    with _connect(database) as connection:
        _, events = _calendars(connection, datetime.now(UTC))

    def normalize(text):
        return re.sub(r"\s+", "", text).casefold()

    return [
        {
            **e,
            "exact_match": normalize(e["title"]) == normalize(payload.get("title", ""))
            and _date(e["start_at"]) == start
            and _date(e["end_at"]) == end,
        }
        for e in events
        if e["busy"]
        and (not entry_id or e["daymeld_entry_id"] != entry_id)
        and _date(e["start_at"]) < end
        and _date(e["end_at"]) > start
    ]


def overview(database: Path, entries: list[dict], *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    with _connect(database) as connection:
        devices, events = _calendars(connection, now)
    zone = ZoneInfo(devices[0]["timezone"]) if devices else ZoneInfo("Asia/Tokyo")
    start = max(now, datetime.combine(now.astimezone(zone).date(), time(8), zone))
    end = datetime.combine(now.astimezone(zone).date(), time(21), zone)
    covered = any(
        d["calendar_fresh"]
        and _date(d["calendar"]["start_at"]) <= start
        and _date(d["calendar"]["end_at"]) >= end
        for d in devices
    )
    active_events = [
        e for e in entries if e["kind"] == "event" and e["status"] in {"planned", "registered"}
    ]
    busy = [(_date(e["start_at"]), _date(e["end_at"])) for e in events if e["busy"]]
    busy.extend((_date(e["start_at"]), _date(e["end_at"])) for e in active_events)
    windows = []
    cursor = start
    for a, b in sorted(busy) + [(end, end)]:
        if b <= cursor or a >= end and cursor >= end:
            continue
        if min(a, end) - cursor >= timedelta(minutes=20):
            windows.append((cursor, min(a, end)))
        cursor = max(cursor, b)
        if cursor >= end:
            break
    tasks = sorted(
        (e for e in entries if e["kind"] == "task" and e["status"] == "open"),
        key=lambda e: (e.get("due_at") or "9999", e["id"]),
    )
    suggestions = []
    if covered:
        for a, b in windows:
            while b - a >= timedelta(minutes=20) and tasks and len(suggestions) < 3:
                eligible = next(
                    (
                        i
                        for i, t in enumerate(tasks)
                        if not t.get("due_at")
                        or _date(t["due_at"]) < now
                        or _date(t["due_at"]) >= a + timedelta(minutes=20)
                    ),
                    None,
                )
                if eligible is None:
                    break
                task = tasks.pop(eligible)
                # No duration estimate: offer an editable 20-minute starting block.
                suggestions.append(
                    {
                        "task_id": task["id"],
                        "title": task["title"],
                        "start_at": a.isoformat(),
                        "end_at": (a + timedelta(minutes=20)).isoformat(),
                    }
                )
                a += timedelta(minutes=20)
    conflicts = []
    for entry in active_events:
        for event in events:
            if (
                event["busy"]
                and event["daymeld_entry_id"] != entry["id"]
                and (
                    _date(event["start_at"]) < _date(entry["end_at"])
                    and _date(event["end_at"]) > _date(entry["start_at"])
                )
            ):
                conflicts.append(
                    {
                        "entry_id": entry["id"],
                        "title": entry["title"],
                        "calendar_title": event["title"],
                    }
                )
    return {
        "devices": devices,
        "calendar_ready": covered,
        "agenda": sorted(
            (e for e in events if _date(e["end_at"]) > now), key=lambda e: e["start_at"]
        )[:5],
        "suggestions": suggestions,
        "conflicts": conflicts[:20],
        "timezone": str(zone),
    }
