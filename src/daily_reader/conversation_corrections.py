"""Versioned correction overlays; original audio, utterances, and evidence are never rewritten."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path

from daily_reader import conversation_vocabulary as vocabulary
from daily_reader.conversation_insights import ConversationInsightError

ACTIVE = {"queued", "correcting", "verifying"}
MAX_CHARACTERS = 120_000
REASONS = {
    "punctuation": "句読点・表記を整えました。",
    "grammar": "前後の文脈に照らして文法の乱れを補正しました。",
    "recognition": "文脈との整合性を確認した小さな誤記を補正しました。",
    "uncertain": "意味を確定できないため原文を保持しています。",
    "protected_change": "数字・否定・固有語などの変更を確定できず原文を保持しています。",
    "large_change": "原文からの変更が大きいため原文を保持しています。",
    "verification_rejected": "独立した文脈照合で確認できず原文を保持しています。",
    "long_utterance": "長い単独発言のため自動補正せず原文を保持しています。",
    "unsupported": "補正を裏付けられないため原文を保持しています。",
    "notation": "文脈で確認できる表記を整えました。",
    "uncertain_source": "原文の意味が不確かなため保持しています。",
    "protected_number": "数字・日時の変更を確定できず原文を保持しています。",
    "protected_meaning": "否定・約束などの意味の変更を避けて原文を保持しています。",
    "protected_name": "固有名詞を確定できず原文を保持しています。",
    "unsupported_change": "補正を裏付けられないため原文を保持しています。",
    "unsupported_term": "用語の補正を裏付けられないため原文を保持しています。",
    "verification_uncertain": "文脈照合でも不確かなため原文を保持しています。",
    "utterance_too_long": "長い単独発言のため自動補正せず原文を保持しています。",
}
RECORDING_COLUMNS = {
    "correction_status": "TEXT NOT NULL DEFAULT 'not_requested'",
    "correction_error": "TEXT",
    "correction_revision_id": "TEXT",
    "correction_run_id": "TEXT",
    "correction_input_hash": "TEXT",
    "correction_completed_at": "TEXT",
    "correction_attempted_at": "TEXT",
    "correction_attempts": "INTEGER NOT NULL DEFAULT 0",
    "correction_corrected_count": "INTEGER NOT NULL DEFAULT 0",
    "correction_retained_count": "INTEGER NOT NULL DEFAULT 0",
    "correction_flagged_count": "INTEGER NOT NULL DEFAULT 0",
    "correction_context_count": "INTEGER NOT NULL DEFAULT 0",
    "correction_requires_review": "INTEGER NOT NULL DEFAULT 0",
    "correction_valid_until": "TEXT",
    "correction_vocabulary_revision": "INTEGER NOT NULL DEFAULT 0",
}


def initialize(connection: sqlite3.Connection) -> None:
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS conversation_correction_runs (
            id TEXT PRIMARY KEY,recording_id TEXT NOT NULL REFERENCES recordings(id),
            input_hash TEXT NOT NULL,version TEXT NOT NULL,model TEXT NOT NULL,
            status TEXT NOT NULL,token TEXT NOT NULL,contexts TEXT NOT NULL,
            error TEXT,created_at TEXT NOT NULL,completed_at TEXT,
            UNIQUE(recording_id,input_hash,version,model)
        );
        CREATE TABLE IF NOT EXISTS conversation_corrections (
            revision_id TEXT NOT NULL REFERENCES conversation_correction_runs(id),
            utterance_id TEXT NOT NULL,original_text TEXT NOT NULL,proposed_text TEXT,
            corrected_text TEXT,status TEXT NOT NULL,reason TEXT NOT NULL,
            verification TEXT NOT NULL,context_ids TEXT NOT NULL,
            PRIMARY KEY(revision_id,utterance_id)
        );
    """)
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(conversation_correction_runs)")
    }
    for name, definition in {
        "vocabulary_revision": "INTEGER NOT NULL DEFAULT 0",
        "approved_terms": "TEXT NOT NULL DEFAULT '[]'",
    }.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE conversation_correction_runs ADD COLUMN {name} {definition}"
            )
    columns = {row[1] for row in connection.execute("PRAGMA table_info(recordings)")}
    for name, definition in RECORDING_COLUMNS.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE recordings ADD COLUMN {name} {definition}")
    columns = {
        row[1] for row in connection.execute("PRAGMA table_info(conversation_item_evidence)")
    }
    for name, definition in {
        "corrected_quote": "TEXT",
        "correction_revision_id": "TEXT",
        "correction_uncertain": "INTEGER NOT NULL DEFAULT 0",
    }.items():
        if name not in columns:
            connection.execute(
                f"ALTER TABLE conversation_item_evidence ADD COLUMN {name} {definition}"
            )


def inputs(connection, recording_id, version):
    from daily_reader.conversations import _raw_insight_input
    from daily_reader.correction_context import reference_context

    recorded_at, rows = _raw_insight_input(connection, recording_id)
    rows = _user_overlay(rows, vocabulary.feedback_for_recording(connection, recording_id))
    terms = vocabulary.terms_snapshot(connection, rows)
    contexts = reference_context(connection, recording_id, rows)
    deadline = context_deadline(connection, recording_id, contexts)
    contexts = [{**entry, "valid_until": deadline} for entry in contexts]
    input_hash = hashlib.sha256(
        json.dumps(
            [
                recorded_at,
                rows,
                contexts,
                version,
                terms,
                vocabulary.feedback_revisions(connection, recording_id),
            ],
            ensure_ascii=False,
            sort_keys=True,
        ).encode()
    ).hexdigest()
    return rows, contexts, input_hash


def context_deadline(connection, recording_id, contexts):
    """Conservative local expiry for identity proofs; never send profile bodies."""
    if not contexts:
        return None
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='life_entries'"
    ).fetchone():
        return None
    sources = {recording_id, *(entry["recording_id"] for entry in contexts)}
    now = datetime.now(UTC)
    dates = []
    for row in connection.execute(
        "SELECT json_extract(data,'$.expires_at') FROM life_entries "
        "WHERE kind='profile' AND status='active' "
        "AND json_extract(data,'$.person_id')='self' "
        "AND json_extract(data,'$.owner_confirmed')=1 "
        "AND COALESCE(json_extract(data,'$.automatic'),0)=0 "
        f"AND json_extract(evidence,'$.recording_id') IN ({','.join('?' for _ in sources)})",
        tuple(sources),
    ):
        try:
            expiry = datetime.fromisoformat(row[0])
            if expiry.tzinfo and expiry > now:
                dates.append(expiry.astimezone(UTC))
        except (ValueError, TypeError):
            continue
    return min(dates).isoformat() if dates else None


def expired(recording):
    value = recording["correction_valid_until"]
    return bool(value and datetime.fromisoformat(value) <= datetime.now(UTC))


def _owns(connection, recording_id, run_id, token):
    return bool(
        connection.execute(
            "SELECT 1 FROM recordings r JOIN conversation_correction_runs c "
            "ON c.id=r.correction_run_id "
            "WHERE r.id=? AND c.id=? AND c.token=? "
            "AND r.correction_status IN ('correcting','verifying')",
            (recording_id, run_id, token),
        ).fetchone()
    )


def validate_results(results, rows, contexts):
    if not isinstance(results, list) or len(results) > len(rows):
        raise ConversationInsightError("補正結果の件数が不正です")
    originals = {row["id"]: row for row in rows}
    known_contexts = {row["id"]: row for row in contexts}
    validated, seen = [], set()
    for result in results:
        if not isinstance(result, dict):
            raise ConversationInsightError("補正結果の形式が不正です")
        utterance_id = result.get("utterance_id")
        if (
            not isinstance(utterance_id, str)
            or utterance_id not in originals
            or utterance_id in seen
        ):
            raise ConversationInsightError("補正結果の発言IDが不正です")
        seen.add(utterance_id)
        original = originals[utterance_id]
        if original.get("user_corrected"):
            raise ConversationInsightError("本人が訂正した発言を自動補正できません")
        if result.get("original_text") != original["text"]:
            raise ConversationInsightError("補正結果の原文が一致しません")
        status, verification = result.get("status"), result.get("verification")
        if (
            status not in {"accepted", "retained"}
            or result.get("reason") not in REASONS
            or verification not in {"verified", "rejected", "uncertain", "not_needed"}
        ):
            raise ConversationInsightError("補正結果の検証状態が不正です")
        proposed, corrected = result.get("proposed_text"), result.get("corrected_text")
        if proposed is not None and (not isinstance(proposed, str) or len(proposed) > 8_000):
            raise ConversationInsightError("補正案の本文が不正です")
        if status == "accepted":
            if (
                verification != "verified"
                or not isinstance(corrected, str)
                or not corrected.strip()
                or len(corrected) > 8_000
                or corrected != proposed
            ):
                raise ConversationInsightError("未検証の補正案は採用できません")
        elif corrected is not None:
            raise ConversationInsightError("保留した補正案を本文へ使用できません")
        context_ids = result.get("context_ids")
        if (
            not isinstance(context_ids, list)
            or len(context_ids) > 8
            or not all(isinstance(value, str) and value in known_contexts for value in context_ids)
            or len(set(context_ids)) != len(context_ids)
        ):
            raise ConversationInsightError("補正結果の参照文脈が不正です")
        if any(
            original["speaker"] not in known_contexts[value]["target_speakers"]
            for value in context_ids
        ):
            raise ConversationInsightError("補正対象と本人の文脈が一致しません")
        validated.append(
            {
                key: result[key]
                for key in (
                    "utterance_id",
                    "original_text",
                    "proposed_text",
                    "corrected_text",
                    "status",
                    "reason",
                    "verification",
                    "context_ids",
                )
            }
        )
    return validated


def ensure_corrected(database: Path, recording_id: str, schema_path: Path, codex: str, model: str):
    """Run under the shared generation lock; publish only a fully verified overlay."""
    from daily_reader.conversation_correction_engine import VERSION, run_correction_passes
    from daily_reader.conversations import _connect
    from daily_reader.life_automation import OPERATION_LOCK

    run_id, token = None, uuid.uuid4().hex
    try:
        with OPERATION_LOCK, _connect(database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            recording = connection.execute(
                "SELECT * FROM recordings WHERE id=?", (recording_id,)
            ).fetchone()
            if recording is None or recording["status"] != "completed":
                raise ConversationInsightError("文字起こし完了後に補正できます")
            rows, contexts, input_hash = inputs(connection, recording_id, VERSION)
            terms = vocabulary.terms_snapshot(connection, rows)
            if not rows or sum(len(row["text"]) for row in rows) > MAX_CHARACTERS:
                raise ConversationInsightError("補正できる文字起こしの量を超えています")
            existing = connection.execute(
                "SELECT * FROM conversation_correction_runs WHERE recording_id=? AND input_hash=? "
                "AND version=? AND model=?",
                (recording_id, input_hash, VERSION, model),
            ).fetchone()
            if existing and existing["status"] == "completed":
                _publish(
                    connection,
                    recording_id,
                    existing["id"],
                    len(rows),
                    len(contexts),
                    existing["completed_at"],
                )
                return True
            run_id = existing["id"] if existing else uuid.uuid4().hex
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """INSERT INTO conversation_correction_runs
                (id,recording_id,input_hash,version,model,status,token,contexts,created_at,
                 vocabulary_revision,approved_terms)
                VALUES(?,?,?,?,?,'correcting',?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                status='correcting',token=excluded.token,error=NULL,completed_at=NULL""",
                (
                    run_id,
                    recording_id,
                    input_hash,
                    VERSION,
                    model,
                    token,
                    json.dumps(contexts, ensure_ascii=False),
                    now,
                    terms["revision"],
                    json.dumps(terms["terms"], ensure_ascii=False),
                ),
            )
            connection.execute(
                """UPDATE recordings SET correction_status='correcting',correction_error=NULL,
                correction_run_id=?,correction_input_hash=?,correction_attempts=correction_attempts+1,
                correction_attempted_at=?,correction_requires_review=MAX(correction_requires_review,
                  EXISTS(SELECT 1 FROM conversation_items WHERE recording_id=recordings.id
                  AND source='codex'))
                WHERE id=?""",
                (run_id, input_hash, now, recording_id),
            )

        def on_stage(stage):
            if stage not in {"correcting", "verifying"}:
                raise ConversationInsightError("補正の処理段階が不正です")
            with _connect(database) as connection:
                connection.execute("BEGIN IMMEDIATE")
                if not _owns(connection, recording_id, run_id, token):
                    raise ConversationInsightError("補正対象が更新されたため処理を止めました")
                connection.execute(
                    "UPDATE recordings SET correction_status=? WHERE id=?",
                    (stage, recording_id),
                )

        results = run_correction_passes(
            rows,
            contexts,
            schema_directory=schema_path.parent,
            codex_command=codex,
            model=model,
            on_stage=on_stage,
            usage_task_id=recording_id,
            approved_terms=terms["terms"],
        )
        results = validate_results(results, rows, contexts)
        with OPERATION_LOCK, _connect(database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if inputs(connection, recording_id, VERSION)[2] != input_hash or not _owns(
                connection, recording_id, run_id, token
            ):
                raise ConversationInsightError(
                    "補正中に原文または本人の文脈が変わりました。再試行してください"
                )
            for entry in results:
                connection.execute(
                    """INSERT OR REPLACE INTO conversation_corrections
                    (revision_id,utterance_id,original_text,proposed_text,corrected_text,status,reason,
                     verification,context_ids) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        run_id,
                        entry["utterance_id"],
                        entry["original_text"],
                        entry["proposed_text"],
                        entry["corrected_text"],
                        entry["status"],
                        entry["reason"],
                        entry["verification"],
                        json.dumps(entry["context_ids"]),
                    ),
                )
            completed_at = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE conversation_correction_runs SET status='completed',error=NULL,"
                "completed_at=? "
                "WHERE id=? AND token=?",
                (completed_at, run_id, token),
            )
            _publish(connection, recording_id, run_id, len(rows), len(contexts), completed_at)
            connection.execute(
                "UPDATE conversation_overviews SET stale=1 WHERE recording_id=?", (recording_id,)
            )
        return True
    except Exception as error:
        message = (
            str(error)
            if isinstance(error, ConversationInsightError)
            else "文字起こしの補正に失敗しました。再試行してください。"
        )
        with _connect(database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            if run_id and not _owns(connection, recording_id, run_id, token):
                return False
            if run_id:
                connection.execute(
                    "UPDATE conversation_correction_runs SET status='failed',error=? "
                    "WHERE id=? AND token=?",
                    (message, run_id, token),
                )
            connection.execute(
                "UPDATE recordings SET correction_status='failed',correction_error=? WHERE id=?",
                (message, recording_id),
            )
        return False


def _publish(connection, recording_id, run_id, total, context_count, completed_at):
    run = connection.execute(
        "SELECT contexts,vocabulary_revision FROM conversation_correction_runs WHERE id=?",
        (run_id,),
    ).fetchone()
    context_rows = json.loads(run["contexts"])
    deadlines = [row["valid_until"] for row in context_rows if row.get("valid_until")]
    counts = dict(
        connection.execute(
            "SELECT status,COUNT(*) FROM conversation_corrections WHERE revision_id=? "
            "GROUP BY status",
            (run_id,),
        )
    )
    connection.execute(
        """UPDATE recordings SET correction_status='completed',correction_error=NULL,
        correction_revision_id=?,correction_run_id=?,correction_completed_at=?,correction_flagged_count=?,
        correction_corrected_count=?,correction_retained_count=?,correction_context_count=?,
        correction_valid_until=?,
        correction_vocabulary_revision=?,
        correction_input_hash=(SELECT input_hash FROM conversation_correction_runs WHERE id=?)
        WHERE id=?""",
        (
            run_id,
            run_id,
            completed_at,
            counts.get("retained", 0),
            counts.get("accepted", 0),
            total - counts.get("accepted", 0),
            context_count,
            min(deadlines) if deadlines else None,
            run["vocabulary_revision"],
            run_id,
            recording_id,
        ),
    )


def invalidate(connection, recording_id, *, context_only=False):
    connection.execute(
        "UPDATE recordings SET correction_status='stale',correction_run_id=NULL WHERE id=? "
        "AND correction_status<>'not_requested' "
        "AND (?=0 OR correction_context_count>0 "
        "OR correction_status IN ('queued','correcting','verifying'))",
        (recording_id, int(context_only)),
    )
    connection.execute(
        "UPDATE conversation_overviews SET stale=1 WHERE recording_id=? "
        "AND EXISTS(SELECT 1 FROM recordings WHERE id=recording_id AND correction_status='stale')",
        (recording_id,),
    )
    # Stored reference provenance is local-only and has no dependency on mutable names.
    dependents = connection.execute(
        "SELECT DISTINCT r.id FROM recordings r JOIN conversation_correction_runs c "
        "ON c.id=COALESCE(r.correction_run_id,r.correction_revision_id),json_each(c.contexts) x "
        "WHERE json_extract(x.value,'$.recording_id')=?",
        (recording_id,),
    ).fetchall()
    for row in dependents:
        connection.execute(
            "UPDATE recordings SET correction_status='stale',correction_run_id=NULL WHERE id=?",
            (row[0],),
        )
        connection.execute(
            "UPDATE conversation_overviews SET stale=1 WHERE recording_id=?", (row[0],)
        )


def queue(database, recording_id, schema_path, codex, model):
    from daily_reader.conversations import INSIGHT_ANALYSIS_LOCK, _connect
    from daily_reader.life_automation import OPERATION_LOCK

    with OPERATION_LOCK, _connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        recording = connection.execute(
            "SELECT * FROM recordings WHERE id=?", (recording_id,)
        ).fetchone()
        if recording is None:
            raise KeyError(recording_id)
        if recording["status"] != "completed":
            raise ValueError("文字起こしの完了後に補正できます")
        if recording["correction_status"] in ACTIVE or any(
            recording[key] in {"queued", "extracting"}
            for key in ("insight_status", "overview_status")
        ):
            return False
        if not connection.execute(
            "SELECT 1 FROM utterances WHERE recording_id=? LIMIT 1", (recording_id,)
        ).fetchone():
            raise ValueError("補正できる発話がありません")
        if (
            connection.execute(
                "SELECT COUNT(*) FROM recordings WHERE correction_status "
                "IN ('queued','correcting','verifying')"
            ).fetchone()[0]
            >= 10
        ):
            raise ValueError("補正待ちが10件あります。完了後に再試行してください")
        connection.execute(
            "UPDATE recordings SET correction_status='queued',correction_error=NULL WHERE id=?",
            (recording_id,),
        )

    def run():
        with INSIGHT_ANALYSIS_LOCK:
            ensure_corrected(database, recording_id, schema_path, codex, model)

    threading.Thread(target=run, name="daymeld-transcript-correction", daemon=True).start()
    return True


def automatic_allowed(connection, evidence):
    if evidence.get("type") != "conversation":
        return True
    recording = connection.execute(
        "SELECT * FROM recordings WHERE id=?", (evidence.get("recording_id"),)
    ).fetchone()
    if (
        not recording
        or recording["correction_status"] != "completed"
        or recording["correction_requires_review"]
        or expired(recording)
        or vocabulary_changed(connection, recording)
    ):
        return False
    quotes = evidence.get("quotes", [])
    return bool(quotes) and all(
        row.get("correction_revision_id") == recording["correction_revision_id"]
        and not row.get("correction_uncertain")
        for row in quotes
    )


def vocabulary_changed(connection, recording):
    recording = dict(recording)
    if recording.get("correction_vocabulary_revision", 0) == vocabulary.vocabulary_revision(
        connection
    ):
        return False
    run_id = recording.get("correction_revision_id")
    if not run_id:
        return False
    run = connection.execute(
        "SELECT approved_terms FROM conversation_correction_runs WHERE id=?", (run_id,)
    ).fetchone()
    if not run:
        return True
    previous = json.loads(run["approved_terms"])
    if not previous:
        # Adding a dictionary cannot invalidate an older, independently grounded
        # correction that never used it. The next explicit run still has a new hash.
        return False
    current = {
        term["id"]: term["revision"] for term in vocabulary.terms_snapshot(connection)["terms"]
    }
    return any(current.get(term["id"]) != term["revision"] for term in previous)


def _user_overlay(rows, feedback):
    output = []
    for row in rows:
        entry = feedback.get(row["id"])
        raw = row.get("raw_text", row["text"])
        if entry and entry["original_text"] == raw:
            output.append(
                {
                    **row,
                    "raw_text": raw,
                    "text": entry["corrected_text"],
                    "user_corrected": True,
                    "correction_revision_id": f"user:{entry['id']}:{entry['revision']}",
                    "correction_uncertain": False,
                }
            )
        else:
            output.append(row)
    return output


def apply_overlay(connection, recording_id, rows):
    feedback = vocabulary.feedback_for_recording(connection, recording_id)
    recording = connection.execute(
        "SELECT correction_status,correction_revision_id,correction_valid_until,"
        "correction_vocabulary_revision "
        "FROM recordings WHERE id=?",
        (recording_id,),
    ).fetchone()
    if (
        recording["correction_status"] != "completed"
        or not recording["correction_revision_id"]
        or expired(recording)
        or vocabulary_changed(connection, recording)
    ):
        return _user_overlay(rows, feedback)
    entries = {
        row["utterance_id"]: row
        for row in connection.execute(
            "SELECT * FROM conversation_corrections WHERE revision_id=?",
            (recording["correction_revision_id"],),
        )
    }
    output = []
    for row in rows:
        entry = entries.get(row["id"])
        if entry and entry["original_text"] != row["text"]:
            return _user_overlay(rows, feedback)
        output.append(
            {
                **row,
                "raw_text": row["text"],
                "text": entry["corrected_text"]
                if entry and entry["status"] == "accepted"
                else row["text"],
                "correction_revision_id": recording["correction_revision_id"],
                "correction_uncertain": bool(entry and entry["status"] == "retained"),
            }
        )
    return _user_overlay(output, feedback)


def describe(connection, recording, *, full=False):
    run_id, status = (
        recording.get("correction_revision_id"),
        recording.get("correction_status", "not_requested"),
    )
    if recording.get("correction_valid_until") and expired(recording):
        status = "stale"
    if run_id and status not in ACTIVE and vocabulary_changed(connection, recording):
        status = "stale"
    result = {
        "status": status,
        "revision_id": run_id,
        "corrected_count": recording.get("correction_corrected_count", 0),
        "retained_count": recording.get("correction_retained_count", 0),
        "flagged_count": recording.get("correction_flagged_count", 0),
        "context_count": recording.get("correction_context_count", 0),
        "error": recording.get("correction_error"),
        "completed_at": recording.get("correction_completed_at"),
        "vocabulary_revision": recording.get("correction_vocabulary_revision", 0),
        "automatic_blocked": status != "completed"
        or bool(recording.get("correction_requires_review")),
        "context_message": "本人として確認された関連する過去の文脈がありません",
    }
    if result["context_count"]:
        result["context_message"] = "本人として確認された関連する過去の文脈を語彙の参考にしました"
    if not full:
        return result
    result.update(items=[], contexts=[], approved_terms=[])
    if not run_id or status == "stale":
        return result
    run = connection.execute(
        "SELECT * FROM conversation_correction_runs WHERE id=?", (run_id,)
    ).fetchone()
    if not run or run["input_hash"] != recording.get("correction_input_hash"):
        return result
    entries = [
        dict(row)
        for row in connection.execute(
            "SELECT * FROM conversation_corrections WHERE revision_id=? ORDER BY utterance_id",
            (run_id,),
        )
    ]
    for entry in entries:
        entry["context_ids"] = json.loads(entry["context_ids"])
        entry["reason"] = REASONS[entry["reason"]]
    result["items"] = entries
    result["approved_terms"] = json.loads(run["approved_terms"])
    result["contexts"] = [
        {
            key: entry[key]
            for key in (
                "id",
                "source_type",
                "source_id",
                "title",
                "recording_id",
                "recorded_at",
            )
        }
        for entry in json.loads(run["contexts"])
    ]
    return result
