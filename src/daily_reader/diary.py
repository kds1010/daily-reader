"""Private, deterministic daily drafts. No network, model, or shell access."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
from contextlib import closing, contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_reader.daily_planner import diary_records, initialize_database

ZONE = ZoneInfo("Asia/Tokyo")
VERSION = "local-diary-v1"
LOGGER = logging.getLogger(__name__)
MAX_BODY = 20_000


class DiaryConflict(ValueError):
    def __init__(self):
        super().__init__("日記が更新されています。入力を保持したまま最新内容を確認してください。")


class SourceUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("記録を取得できませんでした。保存済みの日記は保持しています。")


def clock() -> datetime:
    return datetime.now(UTC)


def parse_day(value: object) -> date:
    if not isinstance(value, str) or len(value) != 10:
        raise ValueError("日付はYYYY-MM-DDで指定してください")
    try:
        day = date.fromisoformat(value)
    except ValueError:
        raise ValueError("日付はYYYY-MM-DDで指定してください") from None
    if day.isoformat() != value or not 1900 <= day.year <= 2200:
        raise ValueError("日付が範囲外です")
    return day


def instant(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    return parsed if parsed.tzinfo else None


def encode(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@contextmanager
def connect(path: Path, now: datetime | None = None):
    initialize_database(path)
    with closing(sqlite3.connect(path, timeout=10)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS diary_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL, since TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS diary_entries (
                date TEXT PRIMARY KEY, timezone TEXT NOT NULL, generated_body TEXT NOT NULL,
                edited_body TEXT, source_snapshot TEXT NOT NULL, source_hash TEXT NOT NULL,
                generator_version TEXT NOT NULL, revision INTEGER NOT NULL,
                generated_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                deleted INTEGER NOT NULL DEFAULT 0, edited_sources TEXT
            );
            CREATE TABLE IF NOT EXISTS diary_generation_errors (
                date TEXT PRIMARY KEY, message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS diary_revisions (
                date TEXT NOT NULL, revision INTEGER NOT NULL, snapshot TEXT NOT NULL,
                PRIMARY KEY(date,revision)
            );
        """)
        connection.execute(
            "INSERT OR IGNORE INTO diary_settings VALUES(1,1,?)", ((now or clock()).isoformat(),)
        )
        connection.commit()
        with connection:
            yield connection


def settings(path: Path, now: datetime | None = None) -> dict:
    with connect(path, now) as connection:
        row = connection.execute("SELECT * FROM diary_settings WHERE id=1").fetchone()
    return {"enabled": bool(row["enabled"]), "since": row["since"], "timezone": str(ZONE)}


def update_settings(path: Path, payload: dict) -> dict:
    if type(payload.get("enabled")) is not bool:
        raise ValueError("自動生成の設定が不正です")
    with connect(path) as connection:
        connection.execute("UPDATE diary_settings SET enabled=? WHERE id=1", (payload["enabled"],))
    return settings(path)


def present(row) -> dict:
    entry = dict(row)
    entry["source_snapshot"] = json.loads(entry["source_snapshot"])
    entry["edited_sources"] = (
        json.loads(entry["edited_sources"]) if entry["edited_sources"] else None
    )
    entry["deleted"] = bool(entry["deleted"])
    entry["body"] = (
        entry["edited_body"] if entry["edited_body"] is not None else entry["generated_body"]
    )
    return entry


def get_entry(path: Path, day: date) -> dict:
    with connect(path) as connection:
        connection.execute("BEGIN")
        error = connection.execute(
            "SELECT message FROM diary_generation_errors WHERE date=?", (str(day),)
        ).fetchone()
        row = connection.execute("SELECT * FROM diary_entries WHERE date=?", (str(day),)).fetchone()
        history = [
            json.loads(r[0])
            for r in connection.execute(
                "SELECT snapshot FROM diary_revisions WHERE date=? ORDER BY revision DESC LIMIT 20",
                (str(day),),
            )
        ]
    return {
        "date": str(day),
        "timezone": str(ZONE),
        "generation_error": error[0] if error else None,
        "state": "missing" if row is None else "deleted" if row["deleted"] else "ready",
        "entry": present(row) if row else None,
        "history": history,
    }


def remember(connection, row) -> None:
    if row is None:
        return
    connection.execute(
        "INSERT INTO diary_revisions VALUES(?,?,?)",
        (row["date"], row["revision"], encode(present(row))),
    )
    connection.execute(
        "DELETE FROM diary_revisions WHERE date=? AND revision NOT IN "
        "(SELECT revision FROM diary_revisions WHERE date=? ORDER BY revision DESC LIMIT 20)",
        (row["date"], row["date"]),
    )


def check_revision(row, revision: object) -> None:
    if type(revision) is not int or revision < 0:
        raise ValueError("改訂番号が不正です")
    if revision != (row["revision"] if row else 0):
        raise DiaryConflict()


def save(path: Path, payload: dict, *, delete: bool = False) -> dict:
    day = parse_day(payload.get("date"))
    body = payload.get("body")
    if not delete and (not isinstance(body, str) or len(body) > MAX_BODY):
        raise ValueError("日記は20000文字以内で入力してください")
    with connect(path) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM diary_entries WHERE date=?", (str(day),)).fetchone()
        check_revision(row, payload.get("revision"))
        if row is None or row["deleted"]:
            raise ValueError("下書きを作成してから保存してください")
        if delete:
            # Delete diary copies and retain a tombstone to prevent automatic resurrection.
            connection.execute("DELETE FROM diary_revisions WHERE date=?", (str(day),))
            connection.execute("DELETE FROM diary_generation_errors WHERE date=?", (str(day),))
            connection.execute(
                "UPDATE diary_entries SET deleted=1,generated_body='',edited_body=NULL,"
                "source_snapshot=?,edited_sources=NULL,source_hash='',revision=revision+1,"
                "updated_at=? WHERE date=?",
                (encode({"items": [], "warnings": []}), clock().isoformat(), str(day)),
            )
        else:
            remember(connection, row)
            connection.execute(
                "UPDATE diary_entries SET edited_body=?,edited_sources=source_snapshot,"
                "revision=revision+1,updated_at=? WHERE date=?",
                (body, clock().isoformat(), str(day)),
            )
    return get_entry(path, day)


def _item(kind: str, identity: str, title: str, at: str | None, detail: str = "") -> dict:
    # Plain text only. Keep the source identity and exact observation time for inspection.
    return {"kind": kind, "id": identity, "title": title, "at": at, "detail": detail}


def _conversation_sources(connection, start: datetime, end: datetime) -> list[dict]:
    items = []
    for row in connection.execute(
        "SELECT r.id,r.recorded_at,t.id AS topic_id,t.name,t.summary FROM recordings r "
        "LEFT JOIN conversation_topics t ON t.recording_id=r.id "
        "WHERE r.recorded_at_verified=1 AND r.status='completed' ORDER BY r.id,t.id"
    ):
        at = instant(row["recorded_at"])
        if at and start <= at < end:
            identity = (
                row["id"] + ":" + hashlib.sha256((row["name"] or "").encode()).hexdigest()[:16]
            )
            items.append(
                _item(
                    "conversation",
                    identity,
                    row["name"] or "話題未整理の会話",
                    row["recorded_at"],
                    row["summary"] or "録音の記録があります。",
                )
            )
    return items


def _life_sources(connection, start: datetime, end: datetime) -> tuple[list[dict], set[str]]:
    items, event_ids = [], set()
    for row in connection.execute(
        "SELECT * FROM life_entries WHERE kind IN ('task','event') ORDER BY id"
    ):
        data = json.loads(row["data"])
        if row["status"] == "completed":
            # changed_at on a revision is when that old state was replaced, not when it began.
            revisions = connection.execute(
                "SELECT status,changed_at FROM life_revisions WHERE entry_id=? ORDER BY revision",
                (row["id"],),
            ).fetchall()
            states = [r["status"] for r in revisions] + [row["status"]]
            transitions = [
                r["changed_at"]
                for index, r in enumerate(revisions)
                if r["status"] != "completed" and states[index + 1] == "completed"
            ]
            completion = transitions[-1] if transitions else data.get("completed_at")
            at = instant(completion)
            if at and start <= at < end:
                items.append(
                    _item(
                        "life_completed",
                        row["id"],
                        data["title"],
                        completion,
                        "暮らしの項目で完了と記録されています。",
                    )
                )
        if row["kind"] == "event":
            # Linked calendar copies of cancelled/completed events must not reappear independently.
            event_ids.add(row["id"])
            a, b = instant(data.get("start_at")), instant(data.get("end_at"))
            if row["status"] != "cancelled" and a and b and a < end and b > start:
                items.append(
                    _item(
                        "planned",
                        row["id"],
                        data["title"],
                        data["start_at"],
                        "暮らしに登録された予定です。参加実績を示すものではありません。",
                    )
                )
    return items, event_ids


def _calendar_sources(connection, start, end, now, event_ids) -> tuple[list[dict], list[str]]:
    warnings, items, seen = [], [], set()
    devices = connection.execute(
        "SELECT * FROM device_context_devices ORDER BY captured_at DESC,id"
    ).fetchall()
    if not devices:
        warnings.append("カレンダーは未取得です。")
    labels = {"disabled": "停止中", "denied": "許可されていません", "unavailable": "取得失敗"}
    for device in devices:
        section = json.loads(device["data"])["calendar"]
        state = section["state"]
        if state != "available":
            warnings.append("カレンダー: " + labels.get(state, "未取得") + "。")
            if state != "unavailable":
                continue
        updated = instant(section.get("updated_at"))
        stale = not updated or not timedelta(0) <= now - updated <= timedelta(hours=24)
        if stale:
            warnings.append("カレンダーの同期が24時間以上前です。保存済みの予定を表示しています。")
        a, b = instant(section.get("start_at")), instant(section.get("end_at"))
        if not a or not b or a > start or b < end:
            warnings.append("カレンダーの取得範囲がこの日の全体をカバーしていません。")
        for row in connection.execute(
            "SELECT * FROM device_calendar_events WHERE device_id=? ORDER BY start_at,id",
            (device["id"],),
        ):
            data = json.loads(row["data"])
            a, b = instant(row["start_at"]), instant(row["end_at"])
            if not a or not b or a >= end or b <= start:
                continue
            link = data.get("daymeld_entry_id")
            if link and link in event_ids:
                continue
            key = (link,) if link else (data["title"], a.isoformat(), b.isoformat())
            if key in seen:
                continue
            seen.add(key)
            items.append(
                _item(
                    "planned",
                    f"{device['id']}:{row['id']}",
                    data["title"],
                    row["start_at"],
                    "カレンダーの予定です。参加実績は未確認です。"
                    + ("取得失敗のため前回保存分です。" if state == "unavailable" else "")
                    + ("同期が古い記録です。" if stale else ""),
                )
            )
    return items, sorted(set(warnings))


def collect(planner: Path, conversations: Path, day: date, now: datetime) -> dict:
    start = datetime.combine(day, time(), ZONE)
    end = start + timedelta(days=1)
    try:
        records = diary_records(planner, day, start, end)
        items = [
            _item("task_completed", t["id"], t["title"], t["completed_at"])
            for t in records["tasks"]
        ]
        items += [
            _item("routine_completed", t["id"], t["title"], t["completed_at"])
            for t in records["routines"]
        ]
        health = records["health"]
        if health:
            labels = {
                "sleep_minutes": ("睡眠", "分"),
                "steps": ("歩数", "歩"),
                "fatigue": ("疲労度", "/5"),
                "mood": ("気分", "/5"),
                "resting_heart_rate": ("安静時心拍数", "拍/分"),
                "hrv_ms": ("心拍変動", "ms"),
                "respiratory_rate": ("呼吸数", "回/分"),
            }
            for key, (label, unit) in labels.items():
                if health[key] is not None:
                    items.append(
                        _item(
                            "health",
                            key,
                            f"{label}: {health[key]}{unit}",
                            health["updated_at"],
                            "保存された日次集計・本人の入力です。",
                        )
                    )
            if health["note"]:
                items.append(
                    _item("health_note", "note", "体調メモ", health["updated_at"], health["note"])
                )
        warnings = []
        # Missing input stores are a collection failure, not evidence of an uneventful day.
        with closing(
            sqlite3.connect(f"{conversations.resolve().as_uri()}?mode=ro", uri=True, timeout=10)
        ) as connection:
            connection.row_factory = sqlite3.Row
            connection.execute("BEGIN")
            items += _conversation_sources(connection, start, end)
            life, event_ids = _life_sources(connection, start, end)
            calendar, warnings = _calendar_sources(connection, start, end, now, event_ids)
            items += life + calendar
        items.sort(key=lambda item: (item["kind"], item["at"] or "", item["id"]))
        return {"items": items, "warnings": warnings}
    except (sqlite3.Error, OSError, ValueError, KeyError, TypeError):
        raise SourceUnavailable() from None


def render(snapshot: dict) -> str:
    lines = []
    groups = [
        ("task_completed", "完了と記録したタスク"),
        ("routine_completed", "完了と記録したルーティン"),
        ("life_completed", "暮らしの完了記録"),
        ("health", "体調・健康の記録"),
        ("health_note", "残した体調メモ"),
        ("conversation", "記録された会話の話題（話者は未確認）"),
        ("planned", "登録されていた予定（参加実績は未確認）"),
    ]
    for kind, label in groups:
        values = [i for i in snapshot["items"] if i["kind"] == kind]
        if not values:
            continue
        lines.append(label + "：")
        for item in values[:20]:
            title = item["title"][:500]
            detail = item["detail"][:500] if kind in {"conversation", "health_note"} else ""
            lines.append("・" + title + (" — " + detail if detail else ""))
        if len(values) > 20:
            lines.append(f"ほか{len(values) - 20}件（根拠一覧で確認できます）。")
        lines.append("")
    if not lines:
        return "この日は日記の材料となる記録がまだありません。出来事や気持ちを追記できます。"
    body = "\n".join(lines).strip()
    return (
        body
        if len(body) <= MAX_BODY
        else body[: MAX_BODY - 40] + "\n（続きは根拠一覧で確認できます。）"
    )


def generate(
    planner: Path,
    conversations: Path,
    day: date,
    *,
    revision: object = None,
    automatic: bool = False,
    now: datetime | None = None,
) -> dict:
    now = now or clock()
    if day > now.astimezone(ZONE).date():
        raise ValueError("未来の日記は生成できません")
    policy = settings(planner, now)
    current = get_entry(planner, day)["entry"]
    if automatic and (not policy["enabled"] or (current and current["deleted"])):
        return get_entry(planner, day)
    if not automatic:
        check_revision(current, revision)
    try:
        snapshot = collect(planner, conversations, day, now)
    except SourceUnavailable:
        with connect(planner) as connection:
            connection.execute(
                "INSERT OR REPLACE INTO diary_generation_errors VALUES(?,?)",
                (str(day), str(SourceUnavailable())),
            )
        raise
    # Health sync refreshes updated_at even for identical values. Do not invalidate an edit
    # merely because that timestamp changed; retain the actual snapshot used by this version.
    stable = {
        **snapshot,
        "items": [
            {**item, "at": None} if item["kind"] in {"health", "health_note"} else item
            for item in snapshot["items"]
        ],
    }
    digest = hashlib.sha256(encode([VERSION, str(day), str(ZONE), stable]).encode()).hexdigest()
    with connect(planner, now) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM diary_entries WHERE date=?", (str(day),)).fetchone()
        skip = False
        if automatic:
            enabled = connection.execute(
                "SELECT enabled FROM diary_settings WHERE id=1"
            ).fetchone()[0]
            skip = not enabled or bool(row and row["deleted"])
            # Do not commit an observation made against a superseded base.
            skip |= (row["revision"] if row else 0) != (current["revision"] if current else 0)
        else:
            check_revision(row, revision)
        skip |= bool(row and row["source_hash"] == digest and not row["deleted"])
        connection.execute("DELETE FROM diary_generation_errors WHERE date=?", (str(day),))
        if not skip:
            remember(connection, row)
            stamp = now.isoformat()
            connection.execute(
                "INSERT INTO diary_entries VALUES(?,?,?,NULL,?,?,?,1,?,?,0,NULL) "
                "ON CONFLICT(date) DO UPDATE SET generated_body=excluded.generated_body,"
                "source_snapshot=excluded.source_snapshot,source_hash=excluded.source_hash,"
                "generator_version=excluded.generator_version,revision=diary_entries.revision+1,"
                "generated_at=excluded.generated_at,updated_at=excluded.updated_at,deleted=0",
                (
                    str(day),
                    str(ZONE),
                    render(snapshot),
                    encode(snapshot),
                    digest,
                    VERSION,
                    stamp,
                    stamp,
                ),
            )
    return get_entry(planner, day)


class DiaryWorker:
    def __init__(self, planner: Path, conversations: Path):
        self.planner, self.conversations = planner, conversations
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self):
        # Initialize inside run(), so a Planner failure cannot prevent HTTP startup.
        self.thread = threading.Thread(target=self.run, name="daymeld-diary", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopped.set()

    def run(self):
        while not self.stopped.is_set():
            try:
                self.step()
            except Exception:
                # Never include exception strings, tracebacks, or personal source data.
                LOGGER.warning("Diary generation failed; saved entries retained")
            self.stopped.wait(300)

    def step(self, now: datetime | None = None):
        now = now or clock()
        policy = settings(self.planner, now)
        if not policy["enabled"]:
            return
        today = now.astimezone(ZONE).date()
        since = datetime.fromisoformat(policy["since"]).astimezone(ZONE).date()
        for offset in range(8):
            if self.stopped.is_set():
                break
            day = today - timedelta(days=offset)
            if day >= since:
                generate(self.planner, self.conversations, day, automatic=True, now=now)
