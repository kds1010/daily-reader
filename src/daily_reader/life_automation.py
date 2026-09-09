"""Durable server-side completion of routine life workflows."""

from __future__ import annotations

import io
import json
import logging
import threading
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from daily_reader import conversations, device_context
from daily_reader.life_assistant import (
    connect,
    create_entry,
    get_entry,
    now_string,
    present,
    public_url,
    text_value,
    timestamp,
    validate_data,
)

LOGGER = logging.getLogger(__name__)
OPERATION_LOCK = threading.RLock()
DATE_FIELDS = (
    "due_at",
    "start_at",
    "end_at",
    "deadline_at",
    "prepare_at",
    "depart_at",
    "remind_at",
)
TEXT_FIELDS = {"source_url": 2000, "location": 200, "preparation": 1000, "constraints": 3000}
EDITABLE = {
    "title",
    "detail",
    "person_id",
    "category",
    "assignee",
    "expires_at",
    "timezone",
    *DATE_FIELDS,
    *TEXT_FIELDS,
}


def settings(database: Path) -> dict:
    with connect(database) as connection:
        row = dict(
            connection.execute("SELECT * FROM life_automation_settings WHERE id=1").fetchone()
        )
        counts = dict(
            connection.execute("SELECT status,count(*) FROM life_drafts GROUP BY status").fetchall()
        )
    return {
        "enabled": bool(row["enabled"]),
        "research_enabled": bool(row["research_enabled"]),
        "since": row["since"],
        "processed": counts.get("adopted", 0),
        "pending": counts.get("pending", 0),
    }


def update_settings(database: Path, payload: dict) -> dict:
    for key in ("enabled", "research_enabled"):
        if key in payload and type(payload[key]) is not bool:
            raise ValueError("自動化の設定が不正です")
    with OPERATION_LOCK, connect(database) as connection:
        for key in ("enabled", "research_enabled"):
            if key in payload:
                connection.execute(
                    f"UPDATE life_automation_settings SET {key}=? WHERE id=1", (int(payload[key]),)
                )
    return settings(database)


def drafts(database: Path) -> list[dict]:
    with connect(database) as connection:
        return [
            {
                "id": row["id"],
                "data": {
                    **json.loads(row["data"]),
                    "id": row["id"],
                    "status": "draft",
                    "revision": 0,
                    "evidence": json.loads(row["evidence"]),
                },
                "source_index": json.loads(row["data"]).get("source_index"),
                "evidence": json.loads(row["evidence"]),
                "reason": row["reason"],
            }
            for row in connection.execute(
                "SELECT * FROM life_drafts WHERE status='pending' ORDER BY created_at,id"
            )
        ]


def capture(database: Path, payload: dict) -> dict:
    content = text_value(payload, "text", 4000, True).encode()
    request_id = text_value(payload, "request_id", 100, True)
    if not settings(database)["enabled"]:
        raise ValueError("自動整理を有効にしてからメモを追加してください")
    now = now_string()
    return conversations.store_transcript(
        database,
        io.BytesIO(content),
        len(content),
        "メモ " + now[:10] + ".txt",
        now,
        memo_request_id=request_id,
    )


def _put_draft(
    database: Path, source_key: str, payload: dict, evidence: dict, reason: str, automatic: bool
) -> str:
    draft_id = uuid.uuid5(uuid.NAMESPACE_URL, "daymeld-life:" + source_key).hex
    with connect(database) as connection:
        connection.execute(
            "INSERT OR IGNORE INTO life_drafts VALUES(?,?,?,?,?,'pending',NULL,?,?)",
            (
                draft_id,
                source_key,
                json.dumps(payload, ensure_ascii=False),
                json.dumps(evidence, ensure_ascii=False),
                reason,
                int(automatic),
                now_string(),
            ),
        )
    return draft_id


def adopt(database: Path, draft_id: str, changes: dict, *, automatic: bool = False) -> dict:
    with OPERATION_LOCK:
        with connect(database) as connection:
            row = connection.execute("SELECT * FROM life_drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise KeyError(draft_id)
            if row["status"] == "adopted":
                return get_entry(database, row["entry_id"])
            if row["status"] != "pending":
                raise ValueError("この候補は取り下げられています")
            payload, evidence = json.loads(row["data"]), json.loads(row["evidence"])
        payload.update({key: value for key, value in changes.items() if key in EDITABLE})
        entry = create_entry(database, payload, automatic=automatic)
        with connect(database) as connection:
            connection.execute(
                "UPDATE life_drafts SET status='adopted',entry_id=? WHERE id=?",
                (entry["id"], draft_id),
            )
            if item_id := evidence.get("item_id"):
                connection.execute(
                    "UPDATE conversation_items SET status='approved',approved_target='life',"
                    "approved_item_id=?,updated_at=? WHERE id=? "
                    "AND status IN ('awaiting_review','kept')",
                    (entry["id"], now_string(), item_id),
                )
        return entry


def dismiss(database: Path, draft_id: str) -> None:
    with OPERATION_LOCK, connect(database) as connection:
        row = connection.execute("SELECT * FROM life_drafts WHERE id=?", (draft_id,)).fetchone()
        if not row or row["status"] != "pending":
            raise ValueError("候補を再読み込みしてください")
        connection.execute("UPDATE life_drafts SET status='dismissed' WHERE id=?", (draft_id,))
        evidence = json.loads(row["evidence"])
        if evidence.get("item_id"):
            connection.execute(
                "UPDATE conversation_items SET status='dismissed',updated_at=? "
                "WHERE id=? AND status='awaiting_review'",
                (now_string(), evidence["item_id"]),
            )


def _candidate(database: Path, item: dict) -> tuple[dict, dict, str, bool]:
    kind = {
        "task": "task",
        "follow_up": "task",
        "research": "research",
        "event": "event",
        "interest": "profile",
        "preference": "profile",
    }[item["kind"]]
    raw = item.get("life_data") or {}
    values = {
        "kind": kind,
        "title": item["title"],
        "detail": item["detail"],
        "source_type": "conversation",
        "source_id": item["id"],
        "timezone": "Asia/Tokyo",
    }
    errors = []
    zone = raw.get("timezone") or "Asia/Tokyo"
    try:
        ZoneInfo(zone)
        values["timezone"] = zone
    except (ValueError, TypeError, KeyError):
        errors.append("タイムゾーンを確認してください")
    for key in DATE_FIELDS:
        try:
            values[key] = timestamp(raw.get(key))
        except ValueError:
            values[key] = None
            errors.append("日時を確認してください")
    ungrounded = raw.get("time_basis") not in {"absolute", "recording_relative"} or (
        raw.get("time_basis") == "recording_relative" and not item.get("recorded_at_verified")
    )
    if ungrounded:
        has_dates = any(values.get(key) for key in DATE_FIELDS) or item.get("due_date")
        for key in DATE_FIELDS:
            values[key] = None
        if has_dates:
            errors.append("日時の根拠が不明なため、日付を確認してください")
    for key, limit in TEXT_FIELDS.items():
        try:
            values[key] = text_value({key: raw.get(key) or ""}, key, limit)
            if key == "source_url":
                values[key] = public_url(values[key])
        except ValueError:
            values[key] = ""
            errors.append("場所・出典・条件を確認してください")
    if (
        kind in {"task", "research"}
        and not values.get("due_at")
        and item.get("due_date")
        and not ungrounded
    ):
        day = date.fromisoformat(item["due_date"])
        values["due_at"] = (
            datetime.combine(day, time(23, 59), ZoneInfo(values["timezone"]))
            .astimezone(UTC)
            .isoformat()
        )
        if kind == "task":
            values["remind_at"] = (
                datetime.combine(day, time(9), ZoneInfo(values["timezone"]))
                .astimezone(UTC)
                .isoformat()
            )
    if kind == "task":
        values["assignee"] = item.get("assignee") or ""
        if values.get("due_at") and not values.get("remind_at"):
            values["remind_at"] = values["due_at"]
    evidence = {
        "type": "conversation",
        "recording_id": item["recording_id"],
        "item_id": item["id"],
        "quotes": item["evidence"],
    }
    automatic = item["certainty"] == "explicit"
    intent = raw.get("intent")
    if kind == "research":
        public_query = raw.get("public_query")
        automatic &= (
            intent == "research_requested"
            and isinstance(public_query, str)
            and 1 <= len(public_query.strip()) <= 200
        )
        if automatic:
            # Only the model's sanitized standalone question reaches public web search.
            values["title"], values["detail"], values["constraints"], values["source_url"] = (
                public_query.strip(),
                "",
                "",
                "",
            )
        else:
            errors.append("公開Webへ送る調査内容を確認してください")
    elif kind == "event":
        automatic &= intent == "committed"
        if not values["start_at"] or not values["end_at"]:
            errors.append("開始・終了の不明な日時だけ補ってください")
        if values["start_at"] and not values["remind_at"]:
            values["remind_at"] = (
                datetime.fromisoformat(values["start_at"]) - timedelta(minutes=30)
            ).isoformat()
        if values["start_at"] and values["preparation"] and not values["prepare_at"]:
            values["prepare_at"] = (
                datetime.fromisoformat(values["start_at"]) - timedelta(days=1)
            ).isoformat()
        if intent != "committed":
            errors.append("参加する予定か確認してください")
    elif kind == "profile":
        subject = raw.get("person_name") or item.get("assignee") or ""
        subject = subject if isinstance(subject, str) else ""
        evidence["subject"] = subject
        with connect(database) as connection:
            owner = connection.execute(
                "SELECT person_id FROM life_speaker_people WHERE recording_id=? AND speaker=?",
                (item["recording_id"], subject),
            ).fetchone()
        values["person_id"] = owner[0] if owner else ""
        category = raw.get("category")
        values["category"] = (
            category
            if category in ("interest", "goal", "preference", "fact")
            else "preference"
            if item["kind"] == "preference"
            else "interest"
        )
        if not owner:
            errors.append("誰の情報かを一度選んでください。同じ録音の同じ対象者へ引き継ぎます")
        automatic &= intent == "interest_only" and owner is not None
    else:
        automatic &= intent == "committed"
    if item["certainty"] != "explicit":
        errors.append("推定・曖昧な候補です。内容を確認してください")
    if not automatic and not errors:
        errors.append("この内容で追加してよいか確認してください")
    if (
        kind == "event"
        and values["start_at"]
        and values["end_at"]
        and device_context.event_conflicts(database, values)
    ):
        errors.append("iPhoneのカレンダーに同時刻の予定があります。重複・時間を確認してください")
    if kind == "event" and values["start_at"] and values["start_at"] < now_string():
        errors.append("過去の予定です。日付を確認してください")
    try:
        with connect(database) as connection:
            validate_data(connection, kind, values)
    except (ValueError, TypeError) as exc:
        errors.append(str(exc))
    return values, evidence, "\n".join(dict.fromkeys(errors)), automatic and not errors


class AutomationWorker:
    def __init__(self, database: Path, schema: Path, codex: str, model: str):
        self.database, self.schema, self.codex, self.model = (
            database,
            schema.resolve(),
            codex,
            model,
        )
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        settings(self.database)  # Persist the adoption cutoff before serving new uploads.
        self.thread = threading.Thread(target=self.run, name="daymeld-life-automation", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                self.step()
            except Exception:
                LOGGER.exception("Life automation iteration failed")
            self.stopped.wait(5)

    def _queue_recording(self, policy: dict) -> None:
        with connect(self.database) as connection:
            if connection.execute(
                "SELECT 1 FROM recordings WHERE insight_status IN ('queued','extracting') LIMIT 1"
            ).fetchone():
                return
            rows = connection.execute(
                """SELECT r.*,COALESCE(a.attempts,0) AS attempts,a.attempted_at,
                (SELECT sum(length(text)) FROM utterances WHERE recording_id=r.id) AS characters
                FROM recordings r LEFT JOIN life_automation_recordings a ON a.recording_id=r.id
                WHERE r.status='completed' AND julianday(r.created_at)>=julianday(?)
                AND r.insight_status IN ('not_requested','failed')
                AND r.overview_status NOT IN ('queued','extracting')
                AND r.transcription_needs_review=0
                AND COALESCE(a.attempts,0)<3 ORDER BY r.created_at LIMIT 20""",
                (policy["since"],),
            ).fetchall()
            selected = next(
                (
                    r
                    for r in rows
                    if not r["attempted_at"]
                    or datetime.fromisoformat(r["attempted_at"])
                    + timedelta(seconds=30 * 2 ** r["attempts"])
                    < datetime.now(UTC)
                ),
                None,
            )
            if selected is None:
                return
            if (selected["characters"] or 0) > 60000:
                connection.execute(
                    "INSERT OR REPLACE INTO life_automation_recordings VALUES(?,3,?)",
                    (selected["id"], now_string()),
                )
                connection.execute(
                    "UPDATE recordings SET insight_status='failed',insight_error=? WHERE id=?",
                    (
                        "長い録音のため自動整理の上限を超えました。録音詳細から手動で開始できます。",
                        selected["id"],
                    ),
                )
                return
            connection.execute(
                "INSERT OR REPLACE INTO life_automation_recordings VALUES(?,?,?)",
                (selected["id"], selected["attempts"] + 1, now_string()),
            )
        conversations.queue_insight_extraction(
            self.database, selected["id"], self.schema, self.codex, self.model
        )

    def step(self) -> None:
        with OPERATION_LOCK:
            policy = settings(self.database)
            if not policy["enabled"]:
                return
            self._queue_recording(policy)
            for item in conversations.list_insight_items(self.database):
                if datetime.fromisoformat(item["recording_created_at"]) < datetime.fromisoformat(
                    policy["since"]
                ):
                    continue
                if item["source"] != "codex" or item["kind"] not in {
                    "task",
                    "follow_up",
                    "research",
                    "event",
                    "interest",
                    "preference",
                }:
                    continue
                payload, evidence, reason, automatic = _candidate(self.database, item)
                if payload["kind"] == "research" and not policy["research_enabled"]:
                    reason = "自動調査を停止中です。内容を確認して個別に開始できます。"
                if item["transcription_needs_review"]:
                    automatic = False
                    reason = "\n".join(filter(None, [
                        reason,
                        "再解析後の候補です。追加済みの用事・調査との重複を確認してください。",
                    ]))
                draft_id = _put_draft(
                    self.database,
                    f"conversation:{item['recording_id']}:{item['fingerprint']}",
                    payload,
                    evidence,
                    reason,
                    automatic,
                )
                if automatic and (payload["kind"] != "research" or policy["research_enabled"]):
                    with connect(self.database) as connection:
                        # Reuse the confirmed subject within this recording only.
                        connection.execute(
                            "UPDATE life_drafts SET data=?,reason='' "
                            "WHERE id=? AND status='pending'",
                            (json.dumps(payload, ensure_ascii=False), draft_id),
                        )
                    self._adopt_automatic(draft_id)
            self._continue_entries()

    def _adopt_automatic(self, draft_id: str) -> None:
        try:
            with connect(self.database) as connection:
                row = connection.execute(
                    "SELECT * FROM life_drafts WHERE id=?", (draft_id,)
                ).fetchone()
                if (
                    row
                    and row["status"] == "pending"
                    and json.loads(row["data"])["kind"] == "research"
                ):
                    midnight = datetime.combine(
                        datetime.now(ZoneInfo("Asia/Tokyo")).date(), time(), ZoneInfo("Asia/Tokyo")
                    ).isoformat()
                    count = connection.execute(
                        "SELECT count(*) FROM life_entries WHERE kind='research' "
                        "AND json_extract(data,'$.automatic')=1 "
                        "AND julianday(created_at)>=julianday(?)",
                        (midnight,),
                    ).fetchone()[0]
                    if count >= 3:
                        raise ValueError(
                            "本日の自動調査3件に達しました。明日自動再開するか、確認して開始できます。"
                        )
            adopt(self.database, draft_id, {}, automatic=True)
        except (ValueError, KeyError) as error:
            with connect(self.database) as connection:
                connection.execute(
                    "UPDATE life_drafts SET reason=? WHERE id=? AND status='pending'",
                    (str(error), draft_id),
                )

    def _continue_entries(self) -> None:
        with connect(self.database) as connection:
            entries = [
                present(row)
                for row in connection.execute(
                    "SELECT * FROM life_entries WHERE kind IN ('event','research')"
                )
            ]
        for entry in entries:
            if (
                entry["kind"] == "research"
                and entry["status"] == "completed"
                and entry.get("result")
            ):
                for index, action in enumerate(entry["result"].get("actions", [])):
                    payload = {
                        "kind": action["kind"],
                        "title": action["title"],
                        "detail": action["detail"],
                        "source_type": "research",
                        "source_id": entry["id"],
                        "source_index": index,
                        "source_url": (entry["result"].get("sources") or [{}])[0].get("url", ""),
                        "timezone": "Asia/Tokyo",
                    }
                    _put_draft(
                        self.database,
                        f"research:{entry['id']}:{index}",
                        payload,
                        {
                            "type": "research",
                            "entry_id": entry["id"],
                            "sources": entry["result"].get("sources", []),
                        },
                        "調査が提案した次の行動です。追加するものだけ選んでください。",
                        False,
                    )
            if entry["kind"] == "event" and entry["status"] in {"planned", "registered"}:
                for role, field, label in (
                    ("prepare", "prepare_at", "準備"),
                    ("deadline", "deadline_at", "申し込み"),
                ):
                    if (
                        role == "prepare"
                        and not entry.get("preparation")
                        or role == "deadline"
                        and (not entry.get(field) or entry["status"] == "registered")
                    ):
                        continue
                    payload = {
                        "kind": "task",
                        "title": f"{label}: {entry['title']}"[:200],
                        "detail": entry.get("preparation") or entry["detail"],
                        "due_at": entry.get(field) or entry.get("start_at"),
                        "remind_at": entry.get(field),
                        "source_type": "event",
                        "source_id": entry["id"],
                        "source_role": role,
                        "source_url": entry.get("source_url", ""),
                    }
                    draft_id = _put_draft(
                        self.database,
                        f"event:{entry['id']}:{role}",
                        payload,
                        {"type": "event", "entry_id": entry["id"]},
                        "",
                        True,
                    )
                    self._adopt_automatic(draft_id)
