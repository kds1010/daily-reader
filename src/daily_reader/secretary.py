"""A local, deterministic brief. Reading it never completes its source work."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from daily_reader import daily_planner, email_assistant
from daily_reader.life_assistant import connect, text_value, timestamp

FIELDS = ("browsing_minutes", "management_minutes", "forgotten_count")


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def _date(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
        return parsed if parsed and parsed.tzinfo else None
    except ValueError:
        return None


def timezone_name(life: dict) -> str:
    value = life.get("device_context", {}).get("timezone", "Asia/Tokyo")
    try:
        return str(ZoneInfo(value))
    except (ValueError, TypeError, ZoneInfoNotFoundError):
        return "Asia/Tokyo"


def _source(
    name: str, label: str, state: str, updated: str | None = None, detail: str = ""
) -> dict:
    return {
        "id": name,
        "label": label,
        "state": state,
        "last_success_at": updated,
        "detail": detail,
    }


def _fresh(value: str | None, now: datetime, hours: int) -> str:
    updated = _date(value)
    if updated is None:
        return "missing"
    return "available" if timedelta(0) <= now - updated <= timedelta(hours=hours) else "stale"


def inputs(
    life: dict, planner_db: Path, email_db: Path, news: dict, now: datetime
) -> tuple[dict, list[dict], list[dict]]:
    """Isolate optional stores; no OAuth refresh, Gmail request, or LLM call."""
    sources = []
    planner, emails = {}, []
    try:
        planner = daily_planner.list_today(
            planner_db, now.astimezone(ZoneInfo(timezone_name(life))).date()
        )
        sources.append(_source("planner", "通常タスク", "available", detail="Mac内の保存済み項目"))
    except (OSError, ValueError, sqlite3.Error):
        sources.append(_source("planner", "通常タスク", "failed"))
    try:
        sync = email_assistant.get_gmail_sync_state(email_db) or {}
        status = email_assistant.get_gmail_sync_status(email_db) or {}
        updated = sync.get("completed_at")
        state = _fresh(updated, now, 1)
        if status.get("authorization_required"):
            state = "authorization_required"
        elif status.get("last_error"):
            state = "failed"
        emails = [
            e
            for e in email_assistant.list_unread_threads(email_db, now)
            if e["importance"] in {"high", "medium"}
        ]
        sources.append(
            _source(
                "email",
                "重要な未読メール",
                state,
                updated,
                "保存済みの未読のみ。期限は本文からの推定です",
            )
        )
    except (OSError, ValueError, sqlite3.Error):
        sources.append(_source("email", "重要な未読メール", "failed"))
    sources.append(
        _source(
            "news",
            "関心のある更新",
            news.get("state", "missing"),
            news.get("updated_at"),
            "既存の地域制約を通ったハイライト",
        )
    )
    sources.append(
        _source(
            "life",
            "会話・暮らしの用事",
            "available",
            detail="保存済みの用事・調査・関心。未録音の約束は含みません",
        )
    )
    sources.append(
        _source(
            "automation",
            "会話の自動整理",
            "available" if life.get("automation", {}).get("enabled") else "stopped",
            detail="新規録音・一文メモを対象にします",
        )
    )
    devices = life.get("device_context", {}).get("devices", [])
    if not devices:
        sources.append(
            _source(
                "calendar",
                "カレンダー",
                "failed" if life.get("device_context", {}).get("failed") else "missing",
            )
        )
    for device in devices:
        section = device.get("calendar", {})
        state = section.get("state", "missing")
        state = {
            "disabled": "stopped",
            "denied": "authorization_required",
            "not_determined": "missing",
            "unavailable": "failed",
        }.get(state, state)
        if state == "available":
            state = _fresh(section.get("updated_at"), now, 24)
        sources.append(
            _source(
                "calendar." + device["id"],
                "カレンダー",
                state,
                section.get("updated_at"),
                "取得期間内の予定のみ。空き枠は参加・実行可能性の保証ではありません",
            )
        )
    return planner, emails, sources


def cards(life: dict, planner: dict, emails: list[dict], now: datetime) -> list[dict]:
    zone = ZoneInfo(timezone_name(life))
    day = now.astimezone(zone).date()
    end = datetime.combine(day + timedelta(days=1), time(), zone)
    result = {}

    def add(
        source: str,
        source_id: str,
        title: str,
        reason: str,
        due: str | None,
        certainty: str,
        rank: int,
        content: object,
        *,
        stage: str = "",
        url: str = "",
    ):
        moment = _date(due)
        urgent = moment is not None and moment < end
        card_id = f"{source}:{source_id}" + (f":{stage}" if stage else "")
        item = {
            "id": card_id,
            "source_type": source,
            "source_id": source_id,
            "title": title,
            "reason": reason,
            "due_at": due,
            "certainty": certainty,
            "urgent": urgent,
            "rank": rank,
            "url": url,
        }
        # A new day must not leave today's deadlines hidden by yesterday's review.
        item["content_version"] = _hash(
            [{k: v for k, v in item.items() if k not in {"urgent", "rank"}}, content]
        )
        item["version"] = _hash([item["content_version"], day.isoformat() if urgent else None])
        result[card_id] = item

    entries = life.get("entries", [])
    by_id = {e["id"]: e for e in entries}
    steps = {
        (e.get("evidence", {}).get("entry_id"), e.get("evidence", {}).get("role")): e
        for e in entries
        if e["kind"] == "task" and e.get("evidence", {}).get("type") == "event"
    }
    for e in entries:
        kind, state = e["kind"], e["status"]
        if kind == "event" and state in {"planned", "registered"}:
            if (_date(e.get("end_at")) or now) <= now:
                continue
            for field, role, label in (
                ("deadline_at", "deadline", "申し込み"),
                ("prepare_at", "prepare", "準備"),
                ("depart_at", "depart", "出発"),
                ("start_at", "start", "約束"),
            ):
                due = e.get(field)
                if not due or role == "deadline" and state == "registered":
                    continue
                if (e["id"], role) in steps:
                    # The explicit child is authoritative, including its completion/cancellation.
                    continue
                urgent = (_date(due) or end) < end
                add(
                    "life",
                    e["id"],
                    f"{label}: {e['title']}",
                    "保存した予定の日時",
                    due,
                    "recorded",
                    0 if urgent else 5,
                    [e.get("revision"), field],
                    stage=role,
                )
        elif kind == "task" and state == "open":
            parent = by_id.get(e.get("evidence", {}).get("entry_id"), {})
            if e.get("evidence", {}).get("type") == "event" and (
                parent.get("status") in {"cancelled", "completed"}
                or parent.get("status") == "registered"
                and e["evidence"].get("role") == "deadline"
            ):
                continue
            due = e.get("due_at")
            add(
                "life",
                e["id"],
                e["title"],
                "保存した用事。詳細から元の根拠を確認できます",
                due,
                "recorded" if due else "unknown",
                0 if (_date(due) or end) < end else 5,
                e.get("revision"),
            )
        elif kind == "research" and state in {"completed", "failed"}:
            add(
                "life",
                e["id"],
                e["title"],
                "調査結果を確認" if state == "completed" else "調査の再試行を確認",
                e.get("due_at"),
                "recorded" if e.get("due_at") else "unknown",
                3,
                [state, e.get("checked_at"), e.get("result"), e.get("error")],
            )
    for e in life.get("secretary_calendar", []):
        if e.get("daymeld_entry_id") in by_id:
            continue
        due = e["start_at"]
        add(
            "calendar",
            e["source_id"],
            e["title"],
            "カレンダー上の予定。参加の確認ではありません",
            due,
            "date_only" if e.get("all_day") else "recorded",
            0 if (_date(due) or end) < end else 5,
            [e["start_at"], e["end_at"], e.get("location"), e.get("busy")],
        )
    for draft in life.get("drafts", []):
        e = draft["data"]
        due = e.get("due_at") or e.get("deadline_at") or e.get("start_at")
        add(
            "draft",
            draft["id"],
            e["title"],
            draft["reason"],
            due,
            "unconfirmed",
            1 if due else 5,
            e,
        )
    for e in planner.get("tasks", []) + planner.get("routines", []):
        if e.get("completed_today") or e.get("completed_at"):
            continue
        routine = e.get("recurrence", "none") != "none"
        due_day = day.isoformat() if routine else e.get("due_date")
        due = (
            datetime.combine(date.fromisoformat(due_day), time(23, 59), zone).isoformat()
            if due_day
            else None
        )
        add(
            "planner",
            e["id"],
            e["title"],
            "今日のルーティン" if routine else "通常タスク",
            due,
            "date_only" if due else "unknown",
            0 if due else 5,
            e,
        )
    for e in emails:
        due_day = e.get("due_date")
        due = (
            datetime.combine(date.fromisoformat(due_day), time(23, 59), zone).isoformat()
            if due_day
            else None
        )
        add(
            "email",
            e["thread_id"],
            e["subject"],
            e["required_action"] + "（保存済み未読メール）",
            due,
            "estimated" if due else "unknown",
            2,
            [e["latest_message_id"], e["status"], e["importance"], e["reason"]],
            url=e["gmail_url"],
        )
    for e in life.get("news", []):
        add("news", e["id"], e["title"], e["reason"], None, "unknown", 4, e, url=e["url"])
    return sorted(
        result.values(),
        key=lambda c: (c["rank"], _date(c["due_at"]) or datetime.max.replace(tzinfo=UTC), c["id"]),
    )


def _sum(rows: list[dict], field: str) -> dict:
    values = [r[field] for r in rows if r.get(field) is not None]
    return {"count": len(values), "total": sum(values) if values else None}


def review(connection, life: dict, now: datetime) -> dict:
    zone = timezone_name(life)
    today = now.astimezone(ZoneInfo(zone)).date()
    start = today - timedelta(days=today.weekday())
    rows = [
        dict(r)
        for r in connection.execute(
            "SELECT * FROM secretary_days WHERE timezone=? AND day>=? AND day<=? ORDER BY day",
            (zone, start.isoformat(), today.isoformat()),
        )
    ]
    feedback = [
        e["feedback"]
        for e in life.get("entries", [])
        if e["kind"] == "research"
        and e.get("feedback")
        and _date(e["feedback"].get("reported_at"))
        and start <= _date(e["feedback"]["reported_at"]).astimezone(ZoneInfo(zone)).date() <= today
    ]
    return {
        "start_date": start.isoformat(),
        "end_date": today.isoformat(),
        "timezone": zone,
        "recorded_days": len(rows),
        **{field: _sum(rows, field) for field in FIELDS},
        "research_evaluations": len(feedback),
        "research_useful": sum(f.get("useful") is True for f in feedback),
        "saved_minutes": _sum(feedback, "saved_minutes"),
        "today": next((r for r in rows if r["day"] == today.isoformat()), None),
    }


def snapshot(
    database: Path,
    life: dict,
    planner_db: Path,
    email_db: Path,
    news: dict,
    *,
    now: datetime | None = None,
) -> dict:
    now = now or datetime.now(UTC)
    planner, emails, sources = inputs(life, planner_db, email_db, news, now)
    calendar = []
    try:
        with connect(database) as connection:
            for device in life.get("device_context", {}).get("devices", []):
                section = device.get("calendar", {})
                if (
                    section.get("state") != "available"
                    or _fresh(section.get("updated_at"), now, 24) != "available"
                ):
                    continue
                for row in connection.execute(
                    "SELECT data FROM device_calendar_events WHERE device_id=? "
                    "AND julianday(end_at)>julianday(?) "
                    "AND julianday(start_at)<julianday(?) AND julianday(end_at)>julianday(?)",
                    (device["id"], now.isoformat(), section["end_at"], section["start_at"]),
                ):
                    event = json.loads(row[0])
                    event["source_id"] = device["id"] + "." + event["id"]
                    calendar.append(event)
    except (OSError, ValueError, sqlite3.Error):
        for source in sources:
            if source["id"].startswith("calendar"):
                source["state"] = "failed"
    life = {**life, "secretary_calendar": calendar}
    items = cards(life, planner, emails, now)
    with connect(database) as connection:
        states = {
            (r["card_id"], r["content_version"]): dict(r)
            for r in connection.execute("SELECT * FROM secretary_cards")
        }
        weekly = review(connection, life, now)
    for item in items:
        state = states.get((item["id"], item["content_version"]), {})
        status = state.get("status", "pending")
        if status == "reviewed" and state.get("version") != item["version"]:
            status = "pending"
        until = _date(state.get("until_at"))
        if status == "snoozed" and (until is None or until <= now):
            status = "pending"
        item.update(status=status, until_at=state.get("until_at"))
    pending = [i for i in items if i["status"] == "pending"]
    return {
        "items": items,
        "top_ids": [i["id"] for i in pending[:3]],
        "remaining_count": max(0, len(pending) - 3),
        "urgent_count": sum(i["urgent"] for i in items),
        "deferred_urgent_count": sum(i["urgent"] and i["status"] != "pending" for i in items),
        "sources": sources,
        "weekly": weekly,
        "timezone": timezone_name(life),
        "generated_at": now.isoformat(),
    }


def save_card(database: Path, payload: dict, current: dict, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(UTC)
    card_id = text_value(payload, "card_id", 512, True)
    version = text_value(payload, "version", 64, True)
    card = next(
        (c for c in current["items"] if c["id"] == card_id and c["version"] == version), None
    )
    if card is None:
        raise ValueError("内容が更新されています。再読み込みしてください")
    status = payload.get("status")
    if status not in ("pending", "reviewed", "snoozed"):
        raise ValueError("確認状態が不正です")
    until = timestamp(payload.get("until_at")) if status == "snoozed" else None
    if status == "snoozed" and (not until or not now < _date(until) <= now + timedelta(days=7)):
        raise ValueError("保留は現在から7日以内を指定してください")
    with connect(database) as connection:
        connection.execute(
            "INSERT INTO secretary_cards VALUES(?,?,?,?,?,?) "
            "ON CONFLICT(card_id,content_version) DO UPDATE SET version=excluded.version,"
            "status=excluded.status,"
            "until_at=excluded.until_at,updated_at=excluded.updated_at",
            (card_id, version, card["content_version"], status, until, now.isoformat()),
        )
    return {"ok": True}


def save_day(database: Path, payload: dict) -> dict:
    request_id = text_value(payload, "request_id", 100, True)
    day = text_value(payload, "day", 10, True)
    if date.fromisoformat(day).isoformat() != day:
        raise ValueError("日付が不正です")
    zone = text_value(payload, "timezone", 100, True)
    try:
        ZoneInfo(zone)
    except (ZoneInfoNotFoundError, ValueError) as error:
        raise ValueError("タイムゾーンが不正です") from error
    revision = payload.get("revision", 0)
    if type(revision) is not int or revision < 0:
        raise ValueError("改訂番号が不正です")
    values = {f: payload.get(f) for f in FIELDS}
    for field, value in values.items():
        if value is not None and (
            type(value) is not int
            or not 0 <= value <= (100 if field == "forgotten_count" else 1440)
        ):
            raise ValueError("分数は0〜1440、忘れ件数は0〜100で入力してください")
    fingerprint = _hash([day, zone, revision, values])
    with connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        previous = connection.execute(
            "SELECT * FROM secretary_requests WHERE request_id=?", (request_id,)
        ).fetchone()
        if previous:
            if previous["fingerprint"] != fingerprint:
                raise ValueError("同じ送信IDで内容が変わっています")
            return json.loads(previous["response"])
        old = connection.execute(
            "SELECT revision FROM secretary_days WHERE day=? AND timezone=?", (day, zone)
        ).fetchone()
        if revision != (old[0] if old else 0):
            raise ValueError("他の端末で更新されています。再読み込みしてください")
        connection.execute(
            "INSERT INTO secretary_days VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(day,timezone) DO UPDATE SET "
            "browsing_minutes=excluded.browsing_minutes,"
            "management_minutes=excluded.management_minutes,"
            "forgotten_count=excluded.forgotten_count,"
            "revision=excluded.revision,updated_at=excluded.updated_at",
            (day, zone, *values.values(), revision + 1, datetime.now(UTC).isoformat()),
        )
        response = {"ok": True, "revision": revision + 1}
        connection.execute(
            "INSERT INTO secretary_requests VALUES(?,?,?)",
            (request_id, fingerprint, json.dumps(response)),
        )
    return response
