"""Local, evidence-backed interests, research, tasks, and timed events."""

from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from daily_reader.conversations import get_insight_item, initialize_database

KINDS = {"profile", "research", "event", "task"}
STATES = {
    "profile": {"active", "archived"},
    "research": {"queued", "running", "completed", "failed", "cancelled", "archived"},
    "event": {"planned", "registered", "completed", "cancelled"},
    "task": {"open", "completed", "cancelled"},
}


def now_string() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@contextmanager
def connect(database: Path):
    initialize_database(database)
    connection = sqlite3.connect(database, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS life_automation_settings (
                id INTEGER PRIMARY KEY CHECK(id=1), enabled INTEGER NOT NULL,
                research_enabled INTEGER NOT NULL, since TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS life_automation_recordings (
                recording_id TEXT PRIMARY KEY, attempts INTEGER NOT NULL,
                attempted_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS life_drafts (
                id TEXT PRIMARY KEY, source_key TEXT NOT NULL UNIQUE, data TEXT NOT NULL,
                evidence TEXT NOT NULL, reason TEXT NOT NULL, status TEXT NOT NULL,
                entry_id TEXT, automatic INTEGER NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS life_speaker_people (
                recording_id TEXT NOT NULL, speaker TEXT NOT NULL, person_id TEXT NOT NULL,
                PRIMARY KEY(recording_id,speaker)
            );
            CREATE TABLE IF NOT EXISTS life_people (
                id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS life_entries (
                id TEXT PRIMARY KEY, kind TEXT NOT NULL, status TEXT NOT NULL,
                data TEXT NOT NULL, source_key TEXT UNIQUE, evidence TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL, revision INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS life_entries_kind_status ON life_entries(kind,status);
            CREATE TABLE IF NOT EXISTS life_revisions (
                entry_id TEXT NOT NULL, revision INTEGER NOT NULL,
                data TEXT NOT NULL, status TEXT NOT NULL, changed_at TEXT NOT NULL,
                PRIMARY KEY(entry_id,revision)
            );
            CREATE TABLE IF NOT EXISTS life_changes (
                id INTEGER PRIMARY KEY, entry_id TEXT NOT NULL,
                action TEXT NOT NULL, created_at TEXT NOT NULL
            );
        """)
        connection.execute(
            "INSERT OR IGNORE INTO life_people VALUES('self','自分',?)", (now_string(),)
        )
        connection.execute(
            "INSERT OR IGNORE INTO life_automation_settings VALUES(1,1,1,?)",
            (datetime.now(UTC).isoformat(),),
        )
        connection.commit()
        with connection:
            yield connection
    finally:
        connection.close()


def text_value(payload: dict, key: str, maximum: int, required: bool = False) -> str:
    value = payload.get(key, "")
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise ValueError(f"{key}が不正です")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{key}を入力してください")
    return value


def timestamp(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("日時が不正です")
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None or not 1900 <= parsed.year <= 2200:
            raise ValueError
        return parsed.astimezone(UTC).isoformat(timespec="seconds")
    except ValueError as error:
        raise ValueError("タイムゾーン付きの日時を指定してください") from error


def public_url(value: object) -> str:
    if value is None or value == "":
        return ""
    if not isinstance(value, str) or len(value) > 2000:
        raise ValueError("URLが不正です")
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("http/httpsのURLを指定してください")
    return value


def present(row) -> dict:
    return {
        **json.loads(row["data"]),
        "id": row["id"],
        "kind": row["kind"],
        "status": row["status"],
        "evidence": json.loads(row["evidence"]),
        "revision": row["revision"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_entry(database: Path, entry_id: str) -> dict:
    with connect(database) as connection:
        row = connection.execute("SELECT * FROM life_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(entry_id)
        entry = present(row)
        entry["history"] = [
            dict(item)
            for item in connection.execute(
                "SELECT revision,data,status,changed_at FROM life_revisions "
                "WHERE entry_id=? ORDER BY revision DESC LIMIT 20",
                (entry_id,),
            )
        ]
        return entry


def validate_data(connection, kind: str, payload: dict) -> dict:
    data = {
        "title": text_value(payload, "title", 200, True),
        "detail": text_value(payload, "detail", 4000),
    }
    data["source_url"] = public_url(payload.get("source_url"))
    if kind == "profile":
        person_id = text_value(payload, "person_id", 100, True)
        if not connection.execute("SELECT 1 FROM life_people WHERE id=?", (person_id,)).fetchone():
            raise ValueError("誰の情報か選択してください")
        category = payload.get("category", "interest")
        if not isinstance(category, str) or category not in {
            "interest",
            "goal",
            "preference",
            "fact",
        }:
            raise ValueError("プロフィールの種別が不正です")
        data.update(
            person_id=person_id, category=category, expires_at=timestamp(payload.get("expires_at"))
        )
    if kind in {"research", "task"}:
        data["due_at"] = timestamp(payload.get("due_at"))
    if kind == "research":
        data["constraints"] = text_value(payload, "constraints", 3000)
    if kind == "task":
        data["assignee"] = text_value(payload, "assignee", 100)
        data["remind_at"] = timestamp(payload.get("remind_at"))
    if kind == "event":
        for key in ("start_at", "end_at", "deadline_at", "prepare_at", "depart_at", "remind_at"):
            data[key] = timestamp(payload.get(key))
        if not data["start_at"] or not data["end_at"] or data["end_at"] <= data["start_at"]:
            raise ValueError("開始・終了日時を確認してください")
        if any(
            data[key] and data[key] > data["start_at"]
            for key in ("deadline_at", "prepare_at", "depart_at", "remind_at")
        ):
            raise ValueError("締切・準備・出発・通知は開始以前を指定してください")
        zone = text_value(payload, "timezone", 100, True)
        try:
            ZoneInfo(zone)
        except (ZoneInfoNotFoundError, ValueError) as error:
            raise ValueError("タイムゾーンが不正です") from error
        data.update(
            timezone=zone,
            location=text_value(payload, "location", 200),
            preparation=text_value(payload, "preparation", 1000),
        )
    return data


def _source(database: Path, payload: dict) -> tuple[str | None, dict]:
    source_type = payload.get("source_type", "manual")
    source_id = payload.get("source_id", "")
    if source_type == "manual":
        return None, {"type": "manual", "confirmed_at": now_string()}
    if source_type == "conversation":
        item = get_insight_item(database, text_value(payload, "source_id", 100, True))
        subject = item.get("life_data", {}).get("person_name") or item.get("assignee") or ""
        return f"conversation:{item['id']}", {
            "type": "conversation",
            "item_id": item["id"],
            "recording_id": item["recording_id"],
            "certainty": item["certainty"],
            "subject": subject if isinstance(subject, str) else "",
            "quotes": item["evidence"],
            "confirmed_at": now_string(),
        }
    if source_type == "event":
        source = get_entry(database, text_value(payload, "source_id", 100, True))
        role = payload.get("source_role")
        if source["kind"] != "event" or role not in {"prepare", "deadline", "research"}:
            raise ValueError("予定の関連付けが不正です")
        return f"event:{source_id}:{role}", {
            "type": "event",
            "entry_id": source_id,
            "role": role,
            "title": source["title"],
            "confirmed_at": now_string(),
        }
    if source_type == "research":
        source = get_entry(database, text_value(payload, "source_id", 100, True))
        index = payload.get("source_index")
        if (
            source["kind"] != "research"
            or source["status"] not in {"completed", "archived"}
            or type(index) is not int
        ):
            raise ValueError("調査結果が不正です")
        actions = source.get("result", {}).get("actions", [])
        if not 0 <= index < len(actions):
            raise ValueError("調査の次の行動が見つかりません")
        return f"research:{source_id}:{index}", {
            "type": "research",
            "entry_id": source_id,
            "action": actions[index],
            "sources": source["result"].get("sources", []),
            "confirmed_at": now_string(),
        }
    raise ValueError("出典が不正です")


def create_entry(database: Path, payload: dict, *, automatic: bool = False) -> dict:
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("種別が不正です")
    source_key, evidence = _source(database, payload)
    if automatic:
        evidence["generated_at"] = evidence.pop("confirmed_at", now_string())
        evidence["confirmation"] = "automatic"
    source_key = f"{source_key}:{kind}" if source_key else None
    # Idempotency covers retries of explicit manual requests as well as source conversion.
    request_id = text_value(payload, "request_id", 100)
    if not source_key and request_id:
        source_key = f"manual:{request_id}:{kind}"
    with connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        if source_key:
            row = connection.execute(
                "SELECT * FROM life_entries WHERE source_key=?", (source_key,)
            ).fetchone()
            if row:
                return present(row)
        if payload.get("source_type") == "event":
            parent = connection.execute(
                "SELECT * FROM life_entries WHERE id=?", (payload.get("source_id"),)
            ).fetchone()
            role = payload.get("source_role")
            if not parent or parent["status"] not in {"planned", "registered"}:
                raise ValueError("元の予定が終了・中止されています")
            if role == "deadline" and parent["status"] == "registered":
                raise ValueError("この予定は申込済みです")
            if automatic and kind == "task" and role in {"prepare", "deadline"}:
                current = present(parent)
                field = "prepare_at" if role == "prepare" else "deadline_at"
                payload = {
                    **payload,
                    "title": (("準備: " if role == "prepare" else "申し込み: ") + current["title"])[
                        :200
                    ],
                    "detail": current.get("preparation") or current["detail"],
                    "due_at": current.get(field) or current.get("start_at"),
                    "remind_at": current.get(field),
                    "source_url": current.get("source_url", ""),
                }
        data = validate_data(connection, kind, payload)
        if automatic:
            data["automatic"] = True
        if kind == "profile":
            data["owner_confirmed"] = not automatic
            if not automatic:
                _confirm_owner(connection, evidence, data["person_id"])
        if (
            kind == "research"
            and connection.execute(
                "SELECT count(*) FROM life_entries WHERE kind='research' "
                "AND status IN ('queued','running')"
            ).fetchone()[0]
            >= 10
        ):
            raise ValueError("調査待ちが10件あります。完了後に追加してください")
        state = {"profile": "active", "research": "queued", "event": "planned", "task": "open"}[
            kind
        ]
        entry_id, now = uuid.uuid4().hex, now_string()
        connection.execute(
            "INSERT INTO life_entries VALUES(?,?,?,?,?,?,?,?,1)",
            (
                entry_id,
                kind,
                state,
                json.dumps(data, ensure_ascii=False),
                source_key,
                json.dumps(evidence, ensure_ascii=False),
                now,
                now,
            ),
        )
        connection.execute(
            "INSERT INTO life_changes(entry_id,action,created_at) VALUES(?,'created',?)",
            (entry_id, now),
        )
    return get_entry(database, entry_id)


def update_entry(database: Path, entry_id: str, payload: dict) -> dict:
    with connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM life_entries WHERE id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(entry_id)
        if type(payload.get("revision")) is not int or payload["revision"] != row["revision"]:
            raise ValueError("他の端末で更新されています。再読み込みしてください")
        old = present(row)
        state = payload.get("status", row["status"])
        if not isinstance(state, str) or state not in STATES[row["kind"]]:
            raise ValueError("状態が不正です")
        if row["kind"] == "research":
            permitted = {
                "queued": {"cancelled"},
                "running": {"cancelled"},
                "failed": {"queued", "archived"},
                "cancelled": {"queued", "archived"},
                "completed": {"archived"},
                "archived": set(),
            }
            feedback = payload.get("feedback")
            recording_feedback = row["status"] == state == "completed" and feedback is not None
            if state not in permitted[row["status"]] and not recording_feedback:
                raise ValueError("この調査は変更できません")
            data = json.loads(row["data"])
            if recording_feedback:
                if not isinstance(feedback, dict) or type(feedback.get("useful")) is not bool:
                    raise ValueError("役立ち評価が不正です")
                minutes = feedback.get("saved_minutes")
                if minutes is not None and (type(minutes) is not int or not 0 <= minutes <= 600):
                    raise ValueError("節約時間は0〜600分で入力してください")
                data["feedback"] = {
                    "useful": feedback["useful"],
                    "saved_minutes": minutes,
                    "reported_at": now_string(),
                }
            if state == "queued":
                if (
                    connection.execute(
                        "SELECT count(*) FROM life_entries WHERE kind='research' "
                        "AND status IN ('queued','running')"
                    ).fetchone()[0]
                    >= 10
                ):
                    raise ValueError("調査待ちが10件あります。完了後に再試行してください")
                data.pop("error", None)
        else:
            data = validate_data(connection, row["kind"], {**old, **payload})
            if old.get("automatic"):
                data["automatic"] = True
            if row["kind"] == "profile":
                data["owner_confirmed"] = old.get("owner_confirmed", True)
                if "person_id" in payload:
                    data["owner_confirmed"] = True
                    _confirm_owner(connection, old["evidence"], data["person_id"], entry_id)
        now = now_string()
        if row["kind"] in {"event", "task"}:
            connection.execute(
                "INSERT INTO life_revisions VALUES(?,?,?,?,?)",
                (
                    entry_id,
                    row["revision"],
                    row["data"],
                    row["status"],
                    now,
                ),
            )
        if row["kind"] == "event":
            _update_event_tasks(connection, old, data, state, now)
        if state == "completed" and old["status"] != "completed":
            data["completed_at"] = now
        connection.execute(
            "UPDATE life_entries SET data=?,status=?,updated_at=?,revision=revision+1 WHERE id=?",
            (
                json.dumps(data, ensure_ascii=False),
                state,
                now,
                entry_id,
            ),
        )
        connection.execute(
            "INSERT INTO life_changes(entry_id,action,created_at) VALUES(?,?,?)",
            (entry_id, f"{old['status']}->{state}", now),
        )
    return get_entry(database, entry_id)


def _confirm_owner(connection, evidence: dict, person_id: str, exclude_id: str = "") -> None:
    """A correction affects only inherited identities for this recording and subject."""
    recording_id, subject = evidence.get("recording_id"), evidence.get("subject")
    if not recording_id or not subject:
        return
    connection.execute(
        "INSERT OR REPLACE INTO life_speaker_people VALUES(?,?,?)",
        (recording_id, subject, person_id),
    )
    for row in connection.execute(
        "SELECT * FROM life_entries WHERE kind='profile' AND id<>?", (exclude_id,)
    ).fetchall():
        data, source = json.loads(row["data"]), json.loads(row["evidence"])
        if (
            not data.get("automatic")
            or data.get("owner_confirmed", True)
            or source.get("recording_id") != recording_id
            or source.get("subject") != subject
            or data.get("person_id") == person_id
        ):
            continue
        data["person_id"] = person_id
        connection.execute(
            "UPDATE life_entries SET data=?,updated_at=?,revision=revision+1 WHERE id=?",
            (json.dumps(data, ensure_ascii=False), now_string(), row["id"]),
        )


def _update_event_tasks(connection, old: dict, data: dict, state: str, now: str) -> None:
    """Move untouched child deadlines with their event; preserve explicit task overrides."""
    for row in connection.execute(
        "SELECT * FROM life_entries WHERE kind='task' AND status='open'"
    ).fetchall():
        evidence = json.loads(row["evidence"])
        if evidence.get("type") != "event" or evidence.get("entry_id") != old["id"]:
            continue
        field = {"prepare": "prepare_at", "deadline": "deadline_at"}.get(evidence.get("role"))
        if not field:
            continue
        child = json.loads(row["data"])
        for key in ("due_at", "remind_at"):
            if child.get(key) == old.get(field):
                child[key] = data.get(field)
        next_state = "cancelled" if state == "cancelled" else row["status"]
        if state == "registered" and field == "deadline_at":
            next_state = "completed"
            child["completed_at"] = now
        if child == json.loads(row["data"]) and next_state == row["status"]:
            continue
        connection.execute(
            "INSERT INTO life_revisions VALUES(?,?,?,?,?)",
            (
                row["id"],
                row["revision"],
                row["data"],
                row["status"],
                now,
            ),
        )
        connection.execute(
            "UPDATE life_entries SET data=?,status=?,revision=revision+1,updated_at=? WHERE id=?",
            (json.dumps(child, ensure_ascii=False), next_state, now, row["id"]),
        )
        connection.execute(
            "INSERT INTO life_changes(entry_id,action,created_at) VALUES(?,?,?)",
            (row["id"], "event_updated", now),
        )


def delete_profile(database: Path, entry_id: str, revision: int) -> None:
    with connect(database) as connection:
        cursor = connection.execute(
            "DELETE FROM life_entries WHERE id=? AND kind='profile' AND revision=?",
            (entry_id, revision),
        )
        if cursor.rowcount != 1:
            raise ValueError("プロフィールを再読み込みしてください")


def create_person(database: Path, payload: dict) -> dict:
    name = text_value(payload, "name", 100, True)
    person_id = uuid.uuid4().hex
    with connect(database) as connection:
        connection.execute("INSERT INTO life_people VALUES(?,?,?)", (person_id, name, now_string()))
    return {"id": person_id, "name": name}


def snapshot(database: Path, articles: list[dict] | None = None) -> dict:
    with connect(database) as connection:
        entries = [
            present(row)
            for row in connection.execute("SELECT * FROM life_entries ORDER BY created_at DESC,id")
        ]
        people = [
            dict(row)
            for row in connection.execute("SELECT id,name FROM life_people ORDER BY created_at,id")
        ]
    now = now_string()
    interests = [
        e
        for e in entries
        if e["kind"] == "profile"
        and e["status"] == "active"
        and e["person_id"] == "self"
        and e["category"] in {"interest", "goal"}
        and (not e["expires_at"] or e["expires_at"] > now)
    ]
    # Profiles stay local: rank existing, region-filtered news without another LLM call.
    ranked = []
    for article in articles or []:
        content = (str(article.get("title", "")) + " " + str(article.get("summary", ""))).casefold()
        matched = [e["title"] for e in interests if e["title"].casefold() in content]
        ranked.append((len(matched), article, matched))
    ranked.sort(key=lambda x: (x[0], str(x[1].get("published_at", ""))), reverse=True)
    news = [
        {
            "id": str(a.get("id", a.get("url", ""))),
            "title": str(a.get("title", "")),
            "url": str(a.get("url", "")),
            "reason": "関心: " + "・".join(m) if m else "最近の更新",
        }
        for _, a, m in ranked[:3]
    ]
    notices = []
    by_id = {entry["id"]: entry for entry in entries}
    role_fields = {"prepare": "prepare_at", "deadline": "deadline_at"}
    completed_steps = {
        (entry["evidence"].get("entry_id"), role_fields.get(entry["evidence"].get("role")))
        for entry in entries
        if entry["kind"] == "task"
        and entry["status"] == "completed"
        and entry["evidence"].get("type") == "event"
    }
    for entry in entries:
        if entry["status"] in {"cancelled", "completed", "archived"}:
            continue
        fields = [("remind_at", "用事")] if entry["kind"] == "task" else []
        if entry["kind"] == "event":
            fields = [
                ("deadline_at", "申し込み期限"),
                ("prepare_at", "準備"),
                ("depart_at", "出発"),
                ("remind_at", "予定"),
            ]
        for key, label in fields:
            if entry["kind"] == "event" and (
                (entry["id"], key) in completed_steps
                or key == "deadline_at"
                and entry["status"] == "registered"
            ):
                continue
            evidence = entry["evidence"]
            parent = by_id.get(evidence.get("entry_id"), {})
            parent_field = role_fields.get(evidence.get("role"))
            if (
                entry["kind"] == "task"
                and evidence.get("type") == "event"
                and parent.get("status") in {"planned", "registered"}
                and parent_field
                and entry.get(key) == parent.get(parent_field)
            ):
                # The event already schedules this exact preparation/deadline reminder.
                continue
            if entry.get(key):
                notices.append(
                    {
                        "id": f"life.{entry['id']}.{key}",
                        "entry_id": entry["id"],
                        "title": f"{label}: {entry['title']}",
                        "at": entry[key],
                    }
                )
    notices.sort(key=lambda n: n["at"])
    return {
        "entries": entries,
        "people": people,
        "news": news,
        "news_remaining": max(0, len(ranked) - len(news)),
        "notifications": notices,
        "generated_at": now,
    }


def search_conversations(database: Path, query: str) -> list[dict]:
    query = query.strip()
    if not 1 <= len(query) <= 100:
        return []
    # Date, filename, confirmed speaker name, and text are explicit search dimensions.
    pattern = "%" + query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
    with connect(database) as connection:
        rows = connection.execute(
            """SELECT u.id,u.recording_id,u.text,u.start_seconds,
            r.filename,r.recorded_at,r.recorded_at_verified,
            COALESCE(s.display_name,s.label) AS speaker,
            l.state AS location_state,g.latitude,g.longitude
            FROM utterances u JOIN recordings r ON r.id=u.recording_id
            LEFT JOIN speakers s ON s.id=u.speaker_id
            LEFT JOIN conversation_location_links l ON l.utterance_id=u.id
            LEFT JOIN location_events g ON g.id=l.location_event_id
            WHERE u.text LIKE ? ESCAPE '\\' OR r.filename LIKE ? ESCAPE '\\'
            OR (r.recorded_at_verified=1 AND r.recorded_at LIKE ? ESCAPE '\\')
            OR s.display_name LIKE ? ESCAPE '\\'
            ORDER BY r.created_at DESC,u.start_seconds LIMIT 50""",
            (pattern,) * 4,
        ).fetchall()
        return [dict(row) for row in rows]
