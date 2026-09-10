"""Bounded, local provenance for explicitly confirmed self-speech correction hints.

Call inside the caller's input-snapshot transaction. Returned provenance stays local;
the sending layer must explicitly allow only ``id`` and ``text``. These historical
quotes do not establish a current identity, preference, fact, or commitment.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from datetime import UTC, datetime

MAX_CANDIDATES = 200
MAX_CONTEXTS = 8
MAX_TEXT = 200
_TABLES = {
    "recordings",
    "speakers",
    "utterances",
    "life_people",
    "life_speaker_people",
    "life_entries",
    "conversation_items",
    "conversation_item_evidence",
}
_WORDS = re.compile(
    r"[A-Za-zＡ-Ｚａ-ｚ][A-Za-z0-9Ａ-Ｚａ-ｚ０-９_+#.-]+"
    r"|[ァ-ヺｦ-ﾟー]{2,}|[\u3400-\u4dbf\u4e00-\u9fff]{2,}"
)
_COMMON = {
    "the",
    "this",
    "that",
    "with",
    "from",
    "have",
    "will",
    "for",
    "and",
    "you",
    "are",
    "今日",
    "昨日",
    "明日",
    "今回",
    "次回",
    "来週",
    "先週",
    "自分",
    "本人",
    "予定",
    "確認",
    "相談",
    "お願い",
    "本当",
    "日時",
    "必要",
    "大丈夫",
}


def _rows(connection: sqlite3.Connection, sql: str, values: tuple = ()) -> list[dict]:
    cursor = connection.execute(sql, values)
    names = [column[0] for column in cursor.description]
    return [dict(zip(names, row, strict=True)) for row in cursor]


def _object(value: str) -> dict:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _date(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(UTC) if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def _word(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold().strip(".-")


def _terms(text: str) -> set[str]:
    return {_word(match[0]) for match in _WORDS.finditer(text) if len(match[0]) <= 40} - _COMMON


def _user_corrected_ids(connection, recording_ids):
    if (
        not recording_ids
        or not connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='conversation_user_corrections'"
        ).fetchone()
    ):
        return set()
    marks = ",".join("?" for _ in recording_ids)
    return {
        row[0]
        for row in connection.execute(
            "SELECT f.utterance_id FROM conversation_user_corrections f JOIN utterances u "
            "ON u.id=f.utterance_id AND u.recording_id=f.recording_id AND u.text=f.original_text "
            f"WHERE f.active=1 AND f.recording_id IN ({marks})",
            tuple(sorted(recording_ids)),
        )
    }


def _confirmed_speakers(connection: sqlite3.Connection, recording_ids: set[str]) -> dict:
    """The legacy mapping stores a subject name, so require live speaker evidence."""
    if not recording_ids:
        return {}
    corrected_ids = _user_corrected_ids(connection, recording_ids)
    placeholders = ",".join("?" for _ in recording_ids)
    profiles = _rows(
        connection,
        f"""
        SELECT s.id AS speaker_id, s.recording_id,
               COALESCE(s.display_name,s.label) AS label, e.data, e.evidence
        FROM speakers s
        JOIN life_speaker_people m ON m.recording_id=s.recording_id
            AND m.speaker=COALESCE(s.display_name,s.label) AND m.person_id='self'
        JOIN life_people p ON p.id=m.person_id
        JOIN life_entries e ON e.kind='profile' AND e.status='active'
            AND json_extract(CASE WHEN json_valid(e.evidence) THEN e.evidence ELSE '{{}}' END,
                             '$.recording_id')=s.recording_id
            AND json_extract(CASE WHEN json_valid(e.evidence) THEN e.evidence ELSE '{{}}' END,
                             '$.subject')=COALESCE(s.display_name,s.label)
        WHERE s.recording_id IN ({placeholders})
          AND NOT EXISTS (
              SELECT 1 FROM speakers duplicate WHERE duplicate.recording_id=s.recording_id
                AND duplicate.id<>s.id
                AND COALESCE(duplicate.display_name,duplicate.label)
                    =COALESCE(s.display_name,s.label)
          )
        ORDER BY e.updated_at DESC,e.id LIMIT ?
        """,
        (*sorted(recording_ids), MAX_CANDIDATES + 1),
    )
    # Truncating identity evidence could conceal a contradictory explicit owner.
    if len(profiles) > MAX_CANDIDATES:
        return {}
    confirmed, conflicts = {}, set()
    for row in profiles:
        data, evidence = _object(row["data"]), _object(row["evidence"])
        key = (row["recording_id"], row["label"])
        if data.get("owner_confirmed") is not True:
            continue
        if data.get("person_id") != "self":
            conflicts.add(key)
            continue
        if data.get("automatic") or evidence.get("confirmation") not in (None, "manual"):
            continue
        expires = data.get("expires_at")
        if expires and ((_date(expires) or datetime.min.replace(tzinfo=UTC)) <= datetime.now(UTC)):
            continue
        if evidence.get("type") != "conversation" or _date(evidence.get("confirmed_at")) is None:
            continue
        if not isinstance(evidence.get("item_id"), str) or not evidence["item_id"]:
            continue
        quotes = evidence.get("quotes")
        if not isinstance(quotes, list) or not 1 <= len(quotes) <= 8:
            continue
        if any(not isinstance(quote, dict) for quote in quotes):
            continue
        ids = [quote.get("utterance_id") for quote in quotes]
        if any(not isinstance(value, str) or not value for value in ids) or len(set(ids)) != len(
            ids
        ):
            continue
        if any(value in corrected_ids for value in ids):
            # A saved identity decision is historical evidence, not permission to
            # reinterpret its edited source text as a fresh self-identification.
            continue
        item = connection.execute(
            "SELECT 1 FROM conversation_items WHERE id=? AND recording_id=? "
            "AND status IN ('awaiting_review','kept','approved') AND certainty='explicit'",
            (evidence.get("item_id"), row["recording_id"]),
        ).fetchone()
        if not item:
            continue
        marks = ",".join("?" for _ in ids)
        live = {
            quote["id"]: quote
            for quote in _rows(
                connection,
                f"SELECT u.id,u.text,u.speaker_id FROM utterances u "
                "JOIN conversation_item_evidence e ON e.utterance_id=u.id AND e.quote=u.text "
                "AND e.speaker=? AND e.item_id=? "
                f"WHERE u.recording_id=? AND u.id IN ({marks})",
                (row["label"], evidence["item_id"], row["recording_id"], *ids),
            )
        }
        if all(
            quote["utterance_id"] in live
            and live[quote["utterance_id"]]["speaker_id"] == row["speaker_id"]
            and live[quote["utterance_id"]]["text"] == quote.get("quote")
            and quote.get("speaker") == row["label"]
            for quote in quotes
        ):
            confirmed[key] = row["speaker_id"]
    return {key: value for key, value in confirmed.items() if key not in conflicts}


def reference_context(
    connection: sqlite3.Connection, recording_id: str, utterances: list[dict]
) -> list[dict]:
    """Return relevant earlier self-quotes; never initialize or modify the database."""
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'",
        )
    }
    if not tables >= _TABLES:
        return []
    target = connection.execute(
        "SELECT recorded_at FROM recordings WHERE id=? AND recorded_at_verified=1 "
        "AND status='completed'",
        (recording_id,),
    ).fetchone()
    target_date = _date(target[0]) if target else None
    if target_date is None:
        return []
    speakers = _confirmed_speakers(connection, {recording_id})
    if not speakers:
        return []
    selected = {
        row["id"]: row
        for row in utterances[:MAX_CANDIDATES]
        if isinstance(row.get("id"), str) and isinstance(row.get("text"), str)
    }
    if not selected:
        return []
    marks = ",".join("?" for _ in selected)
    live = _rows(
        connection,
        f"SELECT id,text,speaker_id FROM utterances WHERE recording_id=? AND id IN ({marks})",
        (recording_id, *selected),
    )
    labels = {speaker_id: label for (_, label), speaker_id in speakers.items()}
    corrected_ids = _user_corrected_ids(connection, {recording_id})
    target_terms: dict[str, set[str]] = {}
    for row in live:
        if row["id"] in corrected_ids:
            continue
        label = labels.get(row["speaker_id"])
        supplied = selected[row["id"]]
        if label and supplied.get("speaker") == label and supplied["text"] == row["text"]:
            target_terms.setdefault(label, set()).update(_terms(row["text"]))
    target_terms = {label: terms for label, terms in target_terms.items() if terms}
    if not target_terms:
        return []
    candidates = _rows(
        connection,
        """
        SELECT DISTINCT u.id, u.text, u.speaker_id, u.recording_id, r.recorded_at,
                        COALESCE(s.display_name,s.label) AS speaker
        FROM conversation_item_evidence e
        JOIN conversation_items i ON i.id=e.item_id AND i.status IN ('kept','approved')
            AND i.certainty='explicit'
        JOIN utterances u ON u.id=e.utterance_id AND u.recording_id=i.recording_id
            AND u.text=e.quote
        JOIN speakers s ON s.id=u.speaker_id AND s.recording_id=u.recording_id
            AND COALESCE(s.display_name,s.label)=e.speaker
        JOIN life_speaker_people m ON m.recording_id=s.recording_id
            AND m.speaker=COALESCE(s.display_name,s.label) AND m.person_id='self'
        JOIN recordings r ON r.id=u.recording_id AND r.recorded_at_verified=1
            AND r.status='completed'
        WHERE r.id<>? AND julianday(r.recorded_at)<julianday(?)
        ORDER BY julianday(r.recorded_at) DESC,u.recording_id,u.id LIMIT ?
        """,
        (recording_id, target_date.isoformat(), MAX_CANDIDATES),
    )
    previous_speakers = _confirmed_speakers(
        connection,
        {row["recording_id"] for row in candidates},
    )
    corrected_ids = _user_corrected_ids(connection, {row["recording_id"] for row in candidates})
    all_terms = set().union(*target_terms.values())
    result = []
    for row in candidates:
        if row["id"] in corrected_ids:
            continue
        recorded_at = _date(row["recorded_at"])
        if recorded_at is None or recorded_at >= target_date:
            continue
        if previous_speakers.get((row["recording_id"], row["speaker"])) != row["speaker_id"]:
            continue
        match = next(
            (match for match in _WORDS.finditer(row["text"]) if _word(match[0]) in all_terms), None
        )
        if match is None:
            continue
        start = max(0, match.start() - 60)
        text = row["text"][start : start + MAX_TEXT]
        snippet_terms = _terms(text)
        result.append(
            {
                "id": f"c{len(result) + 1:03d}",
                "source_type": "confirmed_self_utterance",
                "source_id": row["id"],
                "recording_id": row["recording_id"],
                "recorded_at": row["recorded_at"],
                "title": "過去の確認済み本人発言",
                "text": text,
                "target_speakers": sorted(
                    label for label, terms in target_terms.items() if terms & snippet_terms
                ),
            }
        )
        if len(result) == MAX_CONTEXTS:
            break
    return result
