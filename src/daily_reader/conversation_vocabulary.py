"""User-confirmed vocabulary and reversible transcript feedback, stored only on this Mac."""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
import uuid
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

MAX_TERMS = 100
MAX_TERM_LENGTH = 80
MAX_ALIASES = 5


class VocabularyConflict(ValueError):
    """The displayed revision is no longer current, or the recording is busy."""


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS conversation_vocabulary_state (
            singleton INTEGER PRIMARY KEY CHECK(singleton=1), revision INTEGER NOT NULL
        );
        INSERT OR IGNORE INTO conversation_vocabulary_state VALUES(1,0);
        CREATE TABLE IF NOT EXISTS conversation_vocabulary_terms (
            id TEXT PRIMARY KEY, revision INTEGER NOT NULL, canonical TEXT NOT NULL,
            normalized TEXT NOT NULL, reading TEXT NOT NULL, aliases TEXT NOT NULL,
            enabled INTEGER NOT NULL, deleted INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE UNIQUE INDEX IF NOT EXISTS conversation_vocabulary_unique_term
            ON conversation_vocabulary_terms(normalized) WHERE deleted=0;
        CREATE TABLE IF NOT EXISTS conversation_vocabulary_history (
            term_id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(term_id,revision)
        );
        CREATE TABLE IF NOT EXISTS conversation_user_corrections (
            id TEXT NOT NULL UNIQUE, utterance_id TEXT PRIMARY KEY,
            recording_id TEXT NOT NULL REFERENCES recordings(id), revision INTEGER NOT NULL,
            original_text TEXT NOT NULL, corrected_text TEXT, active INTEGER NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_user_correction_history (
            id TEXT NOT NULL, revision INTEGER NOT NULL, data TEXT NOT NULL,
            PRIMARY KEY(id,revision)
        );
    """)


def _connect(database: Path):
    from daily_reader.conversations import _connect as connect

    connection = connect(database)
    initialize(connection)
    return connection


def vocabulary_revision(connection) -> int:
    row = connection.execute(
        "SELECT revision FROM conversation_vocabulary_state WHERE singleton=1"
    ).fetchone()
    return int(row[0])


def _normalized(value: str) -> str:
    return unicodedata.normalize("NFKC", value).casefold()


def _term(row) -> dict:
    return {
        "id": row["id"],
        "revision": row["revision"],
        "canonical": row["canonical"],
        "reading": row["reading"],
        "aliases": json.loads(row["aliases"]),
        "enabled": bool(row["enabled"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def list_terms(database: Path) -> dict:
    with closing(_connect(database)) as connection, connection:
        return {
            "revision": vocabulary_revision(connection),
            "terms": [
                _term(row)
                for row in connection.execute(
                    "SELECT * FROM conversation_vocabulary_terms WHERE deleted=0 "
                    "ORDER BY updated_at DESC,id"
                )
            ],
            "max_terms": MAX_TERMS,
        }


def terms_snapshot(connection, rows=None) -> dict:
    """Only explicit enabled terms; never promote inferred/model-produced corrections."""
    terms = [
        _term(row)
        for row in connection.execute(
            "SELECT * FROM conversation_vocabulary_terms WHERE enabled=1 AND deleted=0 "
            "ORDER BY updated_at DESC,id LIMIT ?",
            (MAX_TERMS,),
        )
    ]
    if rows is not None:
        text = _normalized("\n".join(row["text"] for row in rows))

        def relevant(term):
            for value in [term["canonical"], term["reading"], *term["aliases"]]:
                needle = _normalized(value)
                if not needle:
                    continue
                # English acronyms must not match fragments of a longer English word.
                left = r"(?<![a-z0-9])" if needle[0].isascii() and needle[0].isalnum() else ""
                right = r"(?![a-z0-9])" if needle[-1].isascii() and needle[-1].isalnum() else ""
                if re.search(left + re.escape(needle) + right, text):
                    return True
            return False

        terms = [term for term in terms if relevant(term)]
    return {"revision": vocabulary_revision(connection), "terms": terms}


def _word(value, name, *, optional=False):
    if not isinstance(value, str):
        raise ValueError(f"{name}は文字列で入力してください")
    value = unicodedata.normalize("NFC", value.strip())
    if (not optional and not value) or len(value) > MAX_TERM_LENGTH:
        raise ValueError(f"{name}は1〜{MAX_TERM_LENGTH}文字で入力してください")
    if any(unicodedata.category(char).startswith("C") for char in value):
        raise ValueError(f"{name}には改行や制御文字を使えません")
    return value


def _validated_term(payload, *, update=False):
    allowed = {"canonical", "reading", "aliases", "enabled"} | ({"revision"} if update else set())
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("用語の入力形式が不正です")
    canonical = _word(payload.get("canonical"), "正しい表記")
    reading = _word(payload.get("reading", ""), "読み", optional=True)
    aliases = payload.get("aliases", [])
    if not isinstance(aliases, list) or len(aliases) > MAX_ALIASES:
        raise ValueError(f"誤表記は{MAX_ALIASES}件まで登録できます")
    aliases = [_word(value, "誤表記") for value in aliases]
    aliases = list(dict.fromkeys(aliases))
    enabled = payload.get("enabled", True)
    if not isinstance(enabled, bool):
        raise ValueError("用語の有効・無効の形式が不正です")
    return canonical, reading, aliases, enabled


def _revision(value):
    if type(value) is not int or value < 0:
        raise ValueError("編集元の版が不正です。画面を更新してください")
    return value


def _save_term(connection, payload, term_id=None):
    canonical, reading, aliases, enabled = _validated_term(payload, update=term_id is not None)
    previous = None
    if term_id is not None:
        previous = connection.execute(
            "SELECT * FROM conversation_vocabulary_terms WHERE id=? AND deleted=0", (term_id,)
        ).fetchone()
        if previous is None:
            raise KeyError("term")
        if _revision(payload.get("revision")) != previous["revision"]:
            raise VocabularyConflict("用語が別の操作で更新されました。再取得してください")
    duplicate = connection.execute(
        "SELECT id FROM conversation_vocabulary_terms WHERE normalized=? AND deleted=0",
        (_normalized(canonical),),
    ).fetchone()
    if duplicate and duplicate["id"] != term_id:
        raise VocabularyConflict("同じ表記の用語が登録済みです。辞書から編集してください")
    if (
        previous is None
        and connection.execute(
            "SELECT COUNT(*) FROM conversation_vocabulary_terms WHERE deleted=0"
        ).fetchone()[0]
        >= MAX_TERMS
    ):
        raise ValueError(f"用語は{MAX_TERMS}件まで登録できます")
    term_id = term_id or uuid.uuid4().hex
    now = datetime.now(UTC).isoformat()
    connection.execute(
        "UPDATE conversation_vocabulary_state SET revision=revision+1 WHERE singleton=1"
    )
    revision = vocabulary_revision(connection)
    connection.execute(
        """INSERT INTO conversation_vocabulary_terms
        (id,revision,canonical,normalized,reading,aliases,enabled,created_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
        revision=excluded.revision,canonical=excluded.canonical,normalized=excluded.normalized,
        reading=excluded.reading,aliases=excluded.aliases,enabled=excluded.enabled,
        updated_at=excluded.updated_at""",
        (
            term_id,
            revision,
            canonical,
            _normalized(canonical),
            reading,
            json.dumps(aliases, ensure_ascii=False),
            int(enabled),
            previous["created_at"] if previous else now,
            now,
        ),
    )
    term = _term(
        connection.execute(
            "SELECT * FROM conversation_vocabulary_terms WHERE id=?", (term_id,)
        ).fetchone()
    )
    connection.execute(
        "INSERT INTO conversation_vocabulary_history VALUES(?,?,?)",
        (term_id, revision, json.dumps(term, ensure_ascii=False)),
    )
    return term


def save_term(database: Path, payload, term_id=None) -> dict:
    with closing(_connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        return _save_term(connection, payload, term_id)


def delete_term(database: Path, term_id: str, payload) -> None:
    if not isinstance(payload, dict) or set(payload) != {"revision"}:
        raise ValueError("削除の入力形式が不正です")
    expected = _revision(payload.get("revision"))
    with closing(_connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM conversation_vocabulary_terms WHERE id=? AND deleted=0", (term_id,)
        ).fetchone()
        if row is None:
            raise KeyError("term")
        if expected != row["revision"]:
            raise VocabularyConflict("用語が別の操作で更新されました。再取得してください")
        connection.execute(
            "UPDATE conversation_vocabulary_state SET revision=revision+1 WHERE singleton=1"
        )
        revision = vocabulary_revision(connection)
        now = datetime.now(UTC).isoformat()
        connection.execute(
            "UPDATE conversation_vocabulary_terms SET revision=?,deleted=1,enabled=0,updated_at=? "
            "WHERE id=?",
            (revision, now, term_id),
        )
        connection.execute(
            "INSERT INTO conversation_vocabulary_history VALUES(?,?,?)",
            (
                term_id,
                revision,
                json.dumps(
                    {
                        **_term(row),
                        "revision": revision,
                        "deleted": True,
                        "enabled": False,
                        "updated_at": now,
                    },
                    ensure_ascii=False,
                ),
            ),
        )


def _feedback(row) -> dict:
    return {
        key: row[key]
        for key in (
            "id",
            "revision",
            "utterance_id",
            "recording_id",
            "original_text",
            "corrected_text",
            "created_at",
            "updated_at",
        )
    }


def feedback_for_recording(connection, recording_id) -> dict:
    return {
        row["utterance_id"]: _feedback(row)
        for row in connection.execute(
            "SELECT f.* FROM conversation_user_corrections f JOIN utterances u "
            "ON u.id=f.utterance_id AND u.recording_id=f.recording_id AND u.text=f.original_text "
            "WHERE f.recording_id=? AND f.active=1",
            (recording_id,),
        )
    }


def feedback_revisions(connection, recording_id) -> dict:
    return {
        row["utterance_id"]: row["revision"]
        for row in connection.execute(
            "SELECT f.utterance_id,f.revision FROM conversation_user_corrections f "
            "JOIN utterances u "
            "ON u.id=f.utterance_id AND u.recording_id=f.recording_id AND u.text=f.original_text "
            "WHERE f.recording_id=?",
            (recording_id,),
        )
    }


def save_feedback(database: Path, recording_id: str, utterance_id: str, payload) -> dict:
    from daily_reader import conversation_corrections
    from daily_reader.life_automation import OPERATION_LOCK

    allowed = {
        "expected_original_text",
        "expected_feedback_revision",
        "corrected_text",
        "reset",
        "term",
    }
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError("訂正の入力形式が不正です")
    expected = _revision(payload.get("expected_feedback_revision"))
    reset = payload.get("reset", False)
    if not isinstance(reset, bool):
        raise ValueError("取り消しの入力形式が不正です")
    if reset and ("term" in payload or "corrected_text" in payload):
        raise ValueError("訂正の取り消しと用語登録は同時にできません")
    corrected = None if reset else payload.get("corrected_text")
    if not reset:
        if not isinstance(corrected, str) or not corrected.strip() or len(corrected) > 8000:
            raise ValueError("訂正文は1〜8000文字で入力してください")
        if any(unicodedata.category(c) == "Cc" and c not in "\n\t" for c in corrected):
            raise ValueError("訂正文に不正な制御文字があります")
        corrected = corrected.strip()
    with OPERATION_LOCK, closing(_connect(database)) as connection, connection:
        connection.execute("BEGIN IMMEDIATE")
        recording = connection.execute(
            "SELECT * FROM recordings WHERE id=?", (recording_id,)
        ).fetchone()
        utterance = connection.execute(
            "SELECT text FROM utterances WHERE id=? AND recording_id=?",
            (utterance_id, recording_id),
        ).fetchone()
        if recording is None or utterance is None:
            raise KeyError("utterance")
        if any(
            recording[key]
            in {"queued", "analyzing", "extracting", "generating", "correcting", "verifying"}
            for key in ("status", "insight_status", "overview_status", "correction_status")
        ):
            raise VocabularyConflict("この会話を処理中です。処理完了後に訂正してください")
        if payload.get("expected_original_text") != utterance["text"]:
            raise VocabularyConflict("文字起こしが更新されました。再取得してください")
        previous = connection.execute(
            "SELECT * FROM conversation_user_corrections WHERE utterance_id=?", (utterance_id,)
        ).fetchone()
        if expected != (previous["revision"] if previous else 0):
            raise VocabularyConflict("この発言は別の操作で訂正されました。再取得してください")
        if reset and (previous is None or not previous["active"]):
            raise VocabularyConflict("取り消せる訂正がありません。再取得してください")
        if not reset and corrected == utterance["text"]:
            raise ValueError("原文へ戻す場合は「訂正を取り消す」を選んでください")
        term = _save_term(connection, payload["term"]) if "term" in payload else None
        now = datetime.now(UTC).isoformat()
        feedback_id = previous["id"] if previous else uuid.uuid4().hex
        revision = expected + 1
        connection.execute(
            """INSERT INTO conversation_user_corrections
            (id,utterance_id,recording_id,revision,original_text,corrected_text,active,
             created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)
            ON CONFLICT(utterance_id) DO UPDATE SET revision=excluded.revision,
            corrected_text=excluded.corrected_text,active=excluded.active,updated_at=excluded.updated_at
            """,
            (
                feedback_id,
                utterance_id,
                recording_id,
                revision,
                utterance["text"],
                corrected,
                int(not reset),
                previous["created_at"] if previous else now,
                now,
            ),
        )
        feedback = _feedback(
            connection.execute(
                "SELECT * FROM conversation_user_corrections WHERE utterance_id=?", (utterance_id,)
            ).fetchone()
        )
        connection.execute(
            "INSERT INTO conversation_user_correction_history VALUES(?,?,?)",
            (
                feedback_id,
                revision,
                json.dumps(
                    {**feedback, "active": not reset, "vocabulary_term": term}, ensure_ascii=False
                ),
            ),
        )
        conversation_corrections.invalidate(connection, recording_id)
        connection.execute(
            "UPDATE recordings SET correction_status='stale',correction_requires_review=1,"
            "transcription_needs_review=1 WHERE id=?",
            (recording_id,),
        )
        connection.execute(
            "UPDATE conversation_overviews SET stale=1 WHERE recording_id=?", (recording_id,)
        )
        return {
            "feedback": None if reset else feedback,
            "feedback_revision": revision,
            "vocabulary_term": term,
        }
