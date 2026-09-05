from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import BinaryIO

from daily_reader.conversation_insights import (
    DEFAULT_INSIGHT_MODEL,
    PROMPT_VERSION,
    ConversationInsightError,
    chunk_utterances,
    load_api_key,
    request_insights,
)

MINIMUM_FREE_BYTES = 5 * 1024**3
MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_TRANSCRIPT_BYTES = 10 * 1024**2
ANALYSIS_LOCK = threading.Lock()
INSIGHT_ANALYSIS_LOCK = threading.Lock()
INSIGHT_KINDS = {"task", "follow_up", "decision", "idea", "friction"}
ACTIONABLE_INSIGHT_KINDS = {"task", "follow_up"}
INSIGHT_CERTAINTIES = {"explicit", "inferred", "ambiguous"}
INSIGHT_REVIEW_ACTIONS = {"keep": "kept", "dismiss": "dismissed"}


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS recordings (
                id TEXT PRIMARY KEY, filename TEXT NOT NULL, audio_path TEXT NOT NULL,
                sha256 TEXT NOT NULL UNIQUE, byte_size INTEGER NOT NULL,
                status TEXT NOT NULL, error TEXT, created_at TEXT NOT NULL,
                analyzed_at TEXT, recorded_at TEXT,
                location_latitude REAL, location_longitude REAL,
                location_accuracy REAL, location_timestamp TEXT, location_time_delta REAL,
                source_type TEXT NOT NULL DEFAULT 'audio', transcript_text TEXT,
                insight_status TEXT NOT NULL DEFAULT 'not_requested', insight_error TEXT,
                insight_analyzed_at TEXT
            );
            CREATE TABLE IF NOT EXISTS speakers (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id),
                label TEXT NOT NULL, display_name TEXT,
                UNIQUE(recording_id, label)
            );
            CREATE TABLE IF NOT EXISTS utterances (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id),
                speaker_id TEXT REFERENCES speakers(id), start_seconds REAL NOT NULL,
                end_seconds REAL NOT NULL, text TEXT NOT NULL, confidence REAL,
                context TEXT NOT NULL, topic TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS conversation_topics (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id),
                name TEXT NOT NULL, context TEXT NOT NULL, summary TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS task_proposals (
                id TEXT PRIMARY KEY, recording_id TEXT NOT NULL REFERENCES recordings(id),
                utterance_id TEXT REFERENCES utterances(id), title TEXT NOT NULL,
                instruction TEXT NOT NULL DEFAULT '', due_date TEXT, assignee TEXT,
                status TEXT NOT NULL DEFAULT 'awaiting_review', approved_target TEXT,
                approved_item_id TEXT, created_at TEXT NOT NULL, approved_at TEXT
            );
            CREATE TABLE IF NOT EXISTS conversation_analysis_runs (
                id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES recordings(id),
                input_hash TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                provider TEXT NOT NULL,
                model TEXT NOT NULL,
                status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT,
                UNIQUE(recording_id, input_hash, extractor_version, model)
            );
            CREATE TABLE IF NOT EXISTS conversation_items (
                id TEXT PRIMARY KEY,
                recording_id TEXT NOT NULL REFERENCES recordings(id),
                analysis_run_id TEXT REFERENCES conversation_analysis_runs(id),
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                detail TEXT NOT NULL DEFAULT '',
                assignee TEXT,
                due_date TEXT,
                due_date_original TEXT,
                certainty TEXT NOT NULL DEFAULT 'explicit',
                status TEXT NOT NULL DEFAULT 'awaiting_review',
                approved_target TEXT,
                approved_item_id TEXT,
                source TEXT NOT NULL,
                extractor_version TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                reviewed_at TEXT,
                UNIQUE(recording_id, extractor_version, fingerprint)
            );
            CREATE TABLE IF NOT EXISTS conversation_item_evidence (
                item_id TEXT NOT NULL REFERENCES conversation_items(id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                utterance_id TEXT REFERENCES utterances(id) ON DELETE SET NULL,
                quote TEXT NOT NULL,
                speaker TEXT,
                start_seconds REAL,
                end_seconds REAL,
                PRIMARY KEY(item_id, position)
            );
            CREATE INDEX IF NOT EXISTS conversation_items_status_created
                ON conversation_items(status, created_at);
            CREATE INDEX IF NOT EXISTS conversation_items_recording
                ON conversation_items(recording_id, created_at);
            CREATE TABLE IF NOT EXISTS location_events (
                id TEXT PRIMARY KEY, timestamp TEXT NOT NULL,
                latitude REAL NOT NULL, longitude REAL NOT NULL,
                horizontal_accuracy REAL NOT NULL, is_approximate INTEGER NOT NULL DEFAULT 0,
                UNIQUE(timestamp, latitude, longitude)
            );
            """
        )
        columns = {row[1] for row in connection.execute("PRAGMA table_info(recordings)")}
        for name, definition in {
            "recorded_at": "TEXT",
            "location_latitude": "REAL",
            "location_longitude": "REAL",
            "location_accuracy": "REAL",
            "location_timestamp": "TEXT",
            "location_time_delta": "REAL",
            "source_type": "TEXT NOT NULL DEFAULT 'audio'",
            "transcript_text": "TEXT",
            "insight_status": "TEXT NOT NULL DEFAULT 'not_requested'",
            "insight_error": "TEXT",
            "insight_analyzed_at": "TEXT",
        }.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE recordings ADD COLUMN {name} {definition}")
        connection.execute(
            """INSERT OR IGNORE INTO conversation_items
            (id, recording_id, kind, title, detail, assignee, due_date, certainty, status,
             approved_target, approved_item_id, source, extractor_version, fingerprint,
             created_at, updated_at, reviewed_at)
            SELECT id, recording_id, 'task', title, instruction, assignee, due_date, 'explicit',
                   status, approved_target, approved_item_id, 'rule', 'rule-v1',
                   'legacy:' || id, created_at, COALESCE(approved_at, created_at), approved_at
            FROM task_proposals"""
        )
        connection.execute(
            """INSERT OR IGNORE INTO conversation_item_evidence
            (item_id, position, utterance_id, quote, speaker, start_seconds, end_seconds)
            SELECT proposals.id, 0, utterances.id, utterances.text,
                   COALESCE(speakers.display_name, speakers.label),
                   utterances.start_seconds, utterances.end_seconds
            FROM task_proposals AS proposals
            JOIN utterances ON utterances.id = proposals.utterance_id
            LEFT JOIN speakers ON speakers.id = utterances.speaker_id"""
        )


def _connect(path: Path) -> sqlite3.Connection:
    initialize_database(path)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def store_upload(
    database: Path,
    audio_directory: Path,
    source: BinaryIO,
    length: int,
    filename: str,
    recorded_at: str | None = None,
) -> dict[str, object]:
    if not 0 < length <= MAX_UPLOAD_BYTES:
        raise ValueError("invalid audio size")
    if Path(filename).suffix.lower() != ".mp3":
        raise ValueError("only MP3 audio is supported")
    free = shutil.disk_usage(
        audio_directory.parent if audio_directory.parent.exists() else Path(".")
    ).free
    if free - length < MINIMUM_FREE_BYTES:
        raise OSError("録音を保存すると空き容量が5 GiB未満になります")
    audio_directory.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256()
    recording_id = uuid.uuid4().hex
    destination = audio_directory / recording_id / "original.mp3"
    destination.parent.mkdir()
    temporary = destination.with_suffix(".tmp")
    remaining = length
    try:
        with temporary.open("wb") as output:
            while remaining:
                chunk = source.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("incomplete audio upload")
                output.write(chunk)
                digest.update(chunk)
                remaining -= len(chunk)
        checksum = digest.hexdigest()
        with _connect(database) as connection:
            existing = connection.execute(
                "SELECT * FROM recordings WHERE sha256 = ?", (checksum,)
            ).fetchone()
            if existing:
                shutil.rmtree(destination.parent)
                return dict(existing)
            temporary.replace(destination)
            now = datetime.now(UTC).isoformat()
            connection.execute(
                """INSERT INTO recordings
                (id, filename, audio_path, sha256, byte_size, status, created_at, recorded_at)
                VALUES (?, ?, ?, ?, ?, 'queued', ?, ?)""",
                (
                    recording_id,
                    Path(filename).name[:240],
                    str(destination),
                    checksum,
                    length,
                    now,
                    recorded_at,
                ),
            )
        return get_recording(database, recording_id)
    except Exception:
        temporary.unlink(missing_ok=True)
        if destination.parent.exists() and not any(destination.parent.iterdir()):
            destination.parent.rmdir()
        raise


def store_transcript(
    database: Path,
    source: BinaryIO,
    length: int,
    filename: str,
    recorded_at: str | None = None,
) -> dict[str, object]:
    if not 0 < length <= MAX_TRANSCRIPT_BYTES:
        raise ValueError("invalid transcript size")
    if Path(filename).suffix.lower() != ".txt":
        raise ValueError("only TXT transcripts are supported")
    content = source.read(length)
    if len(content) != length:
        raise ValueError("incomplete transcript upload")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError as error:
        raise ValueError("transcript must be UTF-8 text") from error
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        raise ValueError("transcript is empty")

    checksum = hashlib.sha256(b"transcript\0" + content).hexdigest()
    now = datetime.now(UTC).isoformat()
    recording_id = uuid.uuid4().hex
    with _connect(database) as connection:
        existing = connection.execute(
            "SELECT id FROM recordings WHERE sha256 = ?", (checksum,)
        ).fetchone()
        if existing:
            recording_id = str(existing["id"])
        else:
            connection.execute(
                """INSERT INTO recordings
                (id, filename, audio_path, sha256, byte_size, status, created_at, analyzed_at,
                 recorded_at, source_type, transcript_text)
                VALUES (?, ?, '', ?, ?, 'completed', ?, ?, ?, 'transcript', ?)""",
                (
                    recording_id,
                    Path(filename).name[:240],
                    checksum,
                    length,
                    now,
                    now,
                    recorded_at,
                    text,
                ),
            )
            transcript = [
                (float(index), float(index + 1), line, None, "話者1")
                for index, line in enumerate(lines)
            ]
            _replace_analysis_results(connection, recording_id, transcript, now)
    return get_recording(database, recording_id)


def list_recordings(database: Path) -> list[dict[str, object]]:
    with _connect(database) as connection:
        return [
            dict(row)
            for row in connection.execute(
                "SELECT id, filename, byte_size, status, error, created_at, analyzed_at, "
                "recorded_at, location_latitude, location_longitude, location_accuracy, "
                "location_timestamp, location_time_delta, source_type, insight_status, "
                "insight_error, insight_analyzed_at, "
                "(SELECT COUNT(*) FROM conversation_items WHERE recording_id=recordings.id "
                "AND status='awaiting_review') AS insight_item_count "
                "FROM recordings ORDER BY created_at DESC"
            )
        ]


def get_recording(database: Path, recording_id: str) -> dict[str, object]:
    with _connect(database) as connection:
        row = connection.execute(
            "SELECT * FROM recordings WHERE id = ?", (recording_id,)
        ).fetchone()
        if row is None:
            raise KeyError(recording_id)
        result = dict(row)
        result.pop("audio_path", None)
        result.pop("transcript_text", None)
        result["speakers"] = [
            dict(item)
            for item in connection.execute(
                "SELECT id, label, display_name FROM speakers "
                "WHERE recording_id = ? ORDER BY label",
                (recording_id,),
            )
        ]
        result["utterances"] = [
            dict(item)
            for item in connection.execute(
                """SELECT utterances.*, COALESCE(speakers.display_name, speakers.label) AS speaker
            FROM utterances LEFT JOIN speakers ON speakers.id = utterances.speaker_id
            WHERE utterances.recording_id = ? ORDER BY start_seconds""",
                (recording_id,),
            )
        ]
        result["topics"] = [
            dict(item)
            for item in connection.execute(
                "SELECT id, name, context, summary FROM conversation_topics WHERE recording_id = ?",
                (recording_id,),
            )
        ]
        result["task_proposals"] = [
            dict(item)
            for item in connection.execute(
                "SELECT * FROM task_proposals WHERE recording_id = ? ORDER BY created_at",
                (recording_id,),
            )
        ]
        result["insight_items"] = _items_for_query(
            connection,
            "WHERE items.recording_id = ?",
            (recording_id,),
        )
        return result


def _items_for_query(
    connection: sqlite3.Connection,
    where: str,
    parameters: tuple[object, ...],
) -> list[dict[str, object]]:
    rows = connection.execute(
        f"""SELECT items.*, recordings.filename AS recording_filename,
        recordings.recorded_at, recordings.source_type AS recording_source_type
        FROM conversation_items AS items
        JOIN recordings ON recordings.id = items.recording_id
        {where}
        ORDER BY items.created_at DESC, items.id""",
        parameters,
    ).fetchall()
    results: list[dict[str, object]] = []
    for row in rows:
        item = dict(row)
        item["evidence"] = [
            dict(evidence)
            for evidence in connection.execute(
                """SELECT position, utterance_id, quote, speaker, start_seconds, end_seconds
                FROM conversation_item_evidence WHERE item_id=? ORDER BY position""",
                (row["id"],),
            )
        ]
        results.append(item)
    return results


def list_insight_items(database: Path, status: str = "awaiting_review") -> list[dict[str, object]]:
    if status not in {"awaiting_review", "kept", "dismissed", "approved", "superseded"}:
        raise ValueError("invalid conversation item status")
    with _connect(database) as connection:
        return _items_for_query(connection, "WHERE items.status = ?", (status,))


def get_insight_item(database: Path, item_id: str) -> dict[str, object]:
    with _connect(database) as connection:
        items = _items_for_query(connection, "WHERE items.id = ?", (item_id,))
    if not items:
        raise KeyError(item_id)
    return items[0]


def _optional_text(value: object, maximum: int, field: str) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str) or len(value.strip()) > maximum:
        raise ValueError(f"invalid {field}")
    return value.strip() or None


def _editable_item_values(item: dict[str, object], payload: dict[str, object]) -> dict[str, object]:
    title = payload.get("title", item["title"])
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        raise ValueError("invalid title")
    detail = payload.get("detail", item["detail"])
    if not isinstance(detail, str) or len(detail.strip()) > 4_000:
        raise ValueError("invalid detail")
    due_date = _optional_text(payload.get("due_date", item["due_date"]), 10, "due date")
    if due_date is not None:
        try:
            due_date = date.fromisoformat(due_date).isoformat()
        except ValueError as error:
            raise ValueError("invalid due date") from error
    return {
        "title": title.strip(),
        "detail": detail.strip(),
        "assignee": _optional_text(payload.get("assignee", item["assignee"]), 100, "assignee"),
        "due_date": due_date,
    }


def prepare_insight_item(
    database: Path, item_id: str, payload: dict[str, object]
) -> dict[str, object]:
    item = get_insight_item(database, item_id)
    if item["status"] != "awaiting_review":
        raise ValueError("conversation item is not awaiting review")
    values = _editable_item_values(item, payload)
    with _connect(database) as connection:
        connection.execute(
            """UPDATE conversation_items SET title=?, detail=?, assignee=?, due_date=?,
            updated_at=? WHERE id=? AND status='awaiting_review'""",
            (
                values["title"],
                values["detail"],
                values["assignee"],
                values["due_date"],
                datetime.now(UTC).isoformat(),
                item_id,
            ),
        )
    return get_insight_item(database, item_id)


def review_insight_item(
    database: Path, item_id: str, payload: dict[str, object]
) -> dict[str, object]:
    action = payload.get("action")
    if action not in INSIGHT_REVIEW_ACTIONS:
        raise ValueError("invalid review action")
    prepare_insight_item(database, item_id, payload)
    now = datetime.now(UTC).isoformat()
    with _connect(database) as connection:
        cursor = connection.execute(
            """UPDATE conversation_items SET status=?, reviewed_at=?, updated_at=?
            WHERE id=? AND status='awaiting_review'""",
            (INSIGHT_REVIEW_ACTIONS[str(action)], now, now, item_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("conversation item is not awaiting review")
    return get_insight_item(database, item_id)


def mark_insight_item_approved(database: Path, item_id: str, target: str, created_id: str) -> None:
    if target not in {"agent", "planner"}:
        raise ValueError("invalid approval target")
    now = datetime.now(UTC).isoformat()
    with _connect(database) as connection:
        cursor = connection.execute(
            """UPDATE conversation_items SET status='approved', approved_target=?,
            approved_item_id=?, reviewed_at=?, updated_at=?
            WHERE id=? AND status='awaiting_review'""",
            (target, created_id, now, now, item_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("conversation item is not awaiting review")


def store_location_events(database: Path, events: list[dict[str, object]]) -> int:
    if len(events) > 500:
        raise ValueError("位置イベントが多すぎます")
    with _connect(database) as connection:
        for event in events:
            timestamp = str(event["timestamp"])
            latitude, longitude = float(event["latitude"]), float(event["longitude"])
            accuracy = float(event["horizontal_accuracy"])
            if (
                not -90 <= latitude <= 90
                or not -180 <= longitude <= 180
                or not 0 <= accuracy <= 100000
            ):
                raise ValueError("invalid location event")
            connection.execute(
                """INSERT OR IGNORE INTO location_events
                (id,timestamp,latitude,longitude,horizontal_accuracy,is_approximate)
                VALUES(?,?,?,?,?,?)""",
                (
                    uuid.uuid4().hex,
                    timestamp,
                    latitude,
                    longitude,
                    accuracy,
                    int(bool(event.get("is_approximate", False))),
                ),
            )
    return len(events)


def match_recording_location(
    database: Path, recording_id: str, max_delta_seconds: float = 7200
) -> dict[str, object] | None:
    with _connect(database) as connection:
        row = connection.execute(
            "SELECT recorded_at, created_at FROM recordings WHERE id=?", (recording_id,)
        ).fetchone()
        if row is None:
            raise KeyError(recording_id)
        target = row[0] or row[1]
        candidate = connection.execute(
            """SELECT *, ABS(strftime('%s', timestamp)-strftime('%s', ?)) AS delta
            FROM location_events ORDER BY delta LIMIT 1""",
            (target,),
        ).fetchone()
        if candidate is None or candidate["delta"] > max_delta_seconds:
            return None
        connection.execute(
            """UPDATE recordings SET location_latitude=?, location_longitude=?,
            location_accuracy=?, location_timestamp=?, location_time_delta=? WHERE id=?""",
            (
                candidate["latitude"],
                candidate["longitude"],
                candidate["horizontal_accuracy"],
                candidate["timestamp"],
                candidate["delta"],
                recording_id,
            ),
        )
        return dict(candidate)


def update_speaker(database: Path, speaker_id: str, display_name: str) -> None:
    if not display_name.strip() or len(display_name) > 100:
        raise ValueError("invalid speaker name")
    with _connect(database) as connection:
        cursor = connection.execute(
            "UPDATE speakers SET display_name = ? WHERE id = ?", (display_name.strip(), speaker_id)
        )
        if cursor.rowcount != 1:
            raise KeyError(speaker_id)


def proposal(database: Path, proposal_id: str) -> dict[str, object]:
    with _connect(database) as connection:
        row = connection.execute(
            "SELECT * FROM task_proposals WHERE id = ?", (proposal_id,)
        ).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return dict(row)


def mark_proposal_approved(database: Path, proposal_id: str, target: str, item_id: str) -> None:
    if target not in {"agent", "planner"}:
        raise ValueError("invalid approval target")
    with _connect(database) as connection:
        cursor = connection.execute(
            """UPDATE task_proposals SET status='approved', approved_target=?, approved_item_id=?,
            approved_at=? WHERE id=? AND status='awaiting_review'""",
            (target, item_id, datetime.now(UTC).isoformat(), proposal_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("task proposal is not awaiting review")
        now = datetime.now(UTC).isoformat()
        connection.execute(
            """UPDATE conversation_items SET status='approved', approved_target=?,
            approved_item_id=?, reviewed_at=?, updated_at=? WHERE id=?""",
            (target, item_id, now, now, proposal_id),
        )


def _speaker_for(start: float, end: float, turns: list[tuple[float, float, str]]) -> str:
    overlaps = (
        (max(0.0, min(end, turn_end) - max(start, turn_start)), label)
        for turn_start, turn_end, label in turns
    )
    overlap, label = max(overlaps, default=(0.0, "話者1"))
    return label if overlap > 0 else "話者1"


def _classify(text: str) -> tuple[str, str]:
    categories = {
        "予定・調整": ("予定", "日程", "いつ", "予約", "会議"),
        "仕事・プロジェクト": ("仕事", "実装", "顧客", "資料", "プロジェクト"),
        "意思決定": ("決め", "方針", "選ぶ", "合意", "結論"),
        "依頼・タスク": ("お願い", "しておいて", "必要", "対応", "確認して"),
    }
    for topic, words in categories.items():
        if any(word in text for word in words):
            return ("会話", topic)
    return ("会話", "その他")


def _task_title(text: str) -> str | None:
    if not re.search(
        r"(しておいて|お願いします|必要|対応して|確認して|やっておく|しなければ)", text
    ):
        return None
    return text.strip()[:200]


def _replace_analysis_results(
    connection: sqlite3.Connection,
    recording_id: str,
    transcript: list[tuple[float, float, str, float | None, str]],
    created_at: str,
) -> None:
    connection.execute(
        """UPDATE conversation_items SET status='superseded', updated_at=?
        WHERE recording_id=? AND status='awaiting_review'""",
        (created_at, recording_id),
    )
    connection.execute(
        """UPDATE recordings SET insight_status='not_requested', insight_error=NULL,
        insight_analyzed_at=NULL WHERE id=?""",
        (recording_id,),
    )
    connection.execute("DELETE FROM task_proposals WHERE recording_id=?", (recording_id,))
    connection.execute("DELETE FROM conversation_topics WHERE recording_id=?", (recording_id,))
    connection.execute("DELETE FROM utterances WHERE recording_id=?", (recording_id,))
    connection.execute("DELETE FROM speakers WHERE recording_id=?", (recording_id,))
    labels = list(dict.fromkeys(label for _, _, _, _, label in transcript))
    speaker_ids: dict[str, str] = {}
    for label in labels:
        speaker_ids[label] = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO speakers(id, recording_id, label) VALUES(?,?,?)",
            (speaker_ids[label], recording_id, label),
        )
    topics: dict[str, list[str]] = {}
    for start, end, text, confidence, label in transcript:
        context, topic = _classify(text)
        utterance_id = uuid.uuid4().hex
        connection.execute(
            """INSERT INTO utterances
            (id,recording_id,speaker_id,start_seconds,end_seconds,text,confidence,context,topic)
            VALUES(?,?,?,?,?,?,?,?,?)""",
            (
                utterance_id,
                recording_id,
                speaker_ids[label],
                start,
                end,
                text,
                confidence,
                context,
                topic,
            ),
        )
        topics.setdefault(topic, []).append(text)
        if title := _task_title(text):
            connection.execute(
                """INSERT INTO task_proposals
                (id,recording_id,utterance_id,title,created_at) VALUES(?,?,?,?,?)""",
                (uuid.uuid4().hex, recording_id, utterance_id, title, created_at),
            )
    for topic, texts in topics.items():
        connection.execute(
            "INSERT INTO conversation_topics VALUES(?,?,?,?,?)",
            (uuid.uuid4().hex, recording_id, topic, "会話", " ".join(texts)[:1000]),
        )


def analyze_recording(database: Path, recording_id: str, token_file: Path) -> None:
    with ANALYSIS_LOCK:
        _analyze_recording(database, recording_id, token_file)


def _analyze_recording(database: Path, recording_id: str, token_file: Path) -> None:
    try:
        with _connect(database) as connection:
            row = connection.execute(
                "SELECT audio_path, source_type FROM recordings WHERE id=?", (recording_id,)
            ).fetchone()
            if row is None:
                return
            if row["source_type"] != "audio":
                return
            connection.execute(
                "UPDATE recordings SET status='analyzing', error=NULL WHERE id=?", (recording_id,)
            )
            audio_path = Path(row["audio_path"])
        token = token_file.read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("Hugging Faceトークンが設定されていません")
        import torch
        from faster_whisper import WhisperModel
        from pyannote.audio import Pipeline
        from scipy.io import wavfile

        with tempfile.TemporaryDirectory(prefix="daymeld-audio-") as temporary:
            wav = Path(temporary) / "analysis.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(audio_path),
                    "-ar",
                    "16000",
                    "-ac",
                    "1",
                    str(wav),
                ],
                check=True,
            )
            diarizer = Pipeline.from_pretrained(
                "pyannote/speaker-diarization-community-1", token=token
            )
            sample_rate, waveform = wavfile.read(wav)
            waveform = waveform.astype("float32") / 32768.0
            diarization = diarizer(
                {
                    "waveform": torch.from_numpy(waveform).unsqueeze(0),
                    "sample_rate": sample_rate,
                }
            )
            annotation = getattr(diarization, "speaker_diarization", diarization)
            raw_turns = list(annotation.itertracks(yield_label=True))
            raw_labels = list(dict.fromkeys(label for _, _, label in raw_turns))
            label_names = {label: f"話者{index + 1}" for index, label in enumerate(raw_labels)}
            turns = [
                (float(turn.start), float(turn.end), label_names[label])
                for turn, _, label in raw_turns
            ]
            model = WhisperModel(
                os.environ.get("DAYMELD_WHISPER_MODEL", "small"), device="cpu", compute_type="int8"
            )
            segments, _ = model.transcribe(str(wav), language="ja", vad_filter=True)
            raw_transcript = [
                (float(s.start), float(s.end), s.text.strip(), float(s.avg_logprob))
                for s in segments
            ]
        transcript = [
            (start, end, text, confidence, _speaker_for(start, end, turns))
            for start, end, text, confidence in raw_transcript
        ]
        now = datetime.now(UTC).isoformat()
        with _connect(database) as connection:
            _replace_analysis_results(connection, recording_id, transcript, now)
            connection.execute(
                "UPDATE recordings SET status='completed', analyzed_at=? WHERE id=?",
                (now, recording_id),
            )
    except Exception as error:
        with _connect(database) as connection:
            connection.execute(
                "UPDATE recordings SET status='failed', error=? WHERE id=?",
                (str(error)[:500], recording_id),
            )


def start_analysis(database: Path, recording_id: str, token_file: Path) -> None:
    threading.Thread(
        target=analyze_recording, args=(database, recording_id, token_file), daemon=True
    ).start()


def _insight_input(
    connection: sqlite3.Connection, recording_id: str
) -> tuple[str, list[dict[str, object]]]:
    recording = connection.execute(
        "SELECT recorded_at, created_at FROM recordings WHERE id=?", (recording_id,)
    ).fetchone()
    if recording is None:
        raise KeyError(recording_id)
    utterances = [
        dict(row)
        for row in connection.execute(
            """SELECT utterances.id, utterances.start_seconds, utterances.end_seconds,
            utterances.text, COALESCE(speakers.display_name, speakers.label) AS speaker
            FROM utterances LEFT JOIN speakers ON speakers.id=utterances.speaker_id
            WHERE utterances.recording_id=? ORDER BY utterances.start_seconds""",
            (recording_id,),
        )
    ]
    return str(recording["recorded_at"] or recording["created_at"]), utterances


def queue_insight_extraction(
    database: Path,
    recording_id: str,
    api_key_file: Path,
    schema_path: Path,
    model: str = DEFAULT_INSIGHT_MODEL,
) -> bool:
    with _connect(database) as connection:
        recording = connection.execute(
            "SELECT status, insight_status FROM recordings WHERE id=?", (recording_id,)
        ).fetchone()
        if recording is None:
            raise KeyError(recording_id)
        if recording["status"] != "completed":
            raise ValueError("文字起こしの完了後にLLMで整理できます")
        if recording["insight_status"] in {"queued", "extracting"}:
            return False
        if not connection.execute(
            "SELECT 1 FROM utterances WHERE recording_id=? LIMIT 1", (recording_id,)
        ).fetchone():
            raise ValueError("整理できる発話がありません")
        connection.execute(
            """UPDATE recordings SET insight_status='queued', insight_error=NULL
            WHERE id=?""",
            (recording_id,),
        )
    threading.Thread(
        target=extract_recording_insights,
        args=(database, recording_id, api_key_file, schema_path, model),
        daemon=True,
    ).start()
    return True


def _validate_extracted_item(
    raw: dict[str, object],
    utterances: dict[str, dict[str, object]],
) -> tuple[dict[str, object], list[dict[str, object]], str]:
    kind = raw.get("kind")
    certainty = raw.get("certainty")
    if kind not in INSIGHT_KINDS or certainty not in INSIGHT_CERTAINTIES:
        raise ConversationInsightError("LLMの候補種別または確実性が不正です")
    title = raw.get("title")
    detail = raw.get("detail")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 200:
        raise ConversationInsightError("LLMの候補タイトルが不正です")
    if not isinstance(detail, str) or len(detail.strip()) > 4_000:
        raise ConversationInsightError("LLMの候補詳細が不正です")
    assignee = _optional_text(raw.get("assignee"), 100, "assignee")
    due_date = _optional_text(raw.get("due_date"), 10, "due date")
    if due_date is not None:
        try:
            due_date = date.fromisoformat(due_date).isoformat()
        except ValueError as error:
            raise ConversationInsightError("LLMの期限が正しい日付ではありません") from error
    due_date_original = _optional_text(raw.get("due_date_original"), 100, "due date source")
    evidence_ids = raw.get("evidence_utterance_ids")
    if (
        not isinstance(evidence_ids, list)
        or not 1 <= len(evidence_ids) <= 8
        or not all(isinstance(value, str) and value in utterances for value in evidence_ids)
        or len(set(evidence_ids)) != len(evidence_ids)
    ):
        raise ConversationInsightError("LLMの根拠発話が不正です")
    evidence = [utterances[str(value)] for value in evidence_ids]
    normalized_title = re.sub(r"\s+", "", title).casefold()
    fingerprint = hashlib.sha256(
        json.dumps(
            [kind, normalized_title, sorted(evidence_ids)],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    return (
        {
            "kind": kind,
            "title": title.strip(),
            "detail": detail.strip(),
            "assignee": assignee,
            "due_date": due_date,
            "due_date_original": due_date_original,
            "certainty": certainty,
        },
        evidence,
        fingerprint,
    )


def _store_extracted_items(
    database: Path,
    recording_id: str,
    run_id: str,
    raw_items: list[dict[str, object]],
    utterance_rows: list[dict[str, object]],
    completed_at: str,
) -> None:
    utterances = {str(row["id"]): row for row in utterance_rows}
    validated: dict[str, tuple[dict[str, object], list[dict[str, object]]]] = {}
    for raw in raw_items:
        values, evidence, fingerprint = _validate_extracted_item(raw, utterances)
        validated[fingerprint] = (values, evidence)
    with _connect(database) as connection:
        connection.execute(
            """UPDATE conversation_items SET status='superseded', updated_at=?
            WHERE recording_id=? AND status='awaiting_review'""",
            (completed_at, recording_id),
        )
        for fingerprint, (values, evidence) in validated.items():
            item_id = uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"daymeld:{recording_id}:{PROMPT_VERSION}:{fingerprint}",
            ).hex
            existing = connection.execute(
                "SELECT status FROM conversation_items WHERE id=?", (item_id,)
            ).fetchone()
            if existing is None:
                connection.execute(
                    """INSERT INTO conversation_items
                    (id, recording_id, analysis_run_id, kind, title, detail, assignee,
                     due_date, due_date_original, certainty, status, source, extractor_version,
                     fingerprint, created_at, updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,'awaiting_review','openai',?,?,?,?)""",
                    (
                        item_id,
                        recording_id,
                        run_id,
                        values["kind"],
                        values["title"],
                        values["detail"],
                        values["assignee"],
                        values["due_date"],
                        values["due_date_original"],
                        values["certainty"],
                        PROMPT_VERSION,
                        fingerprint,
                        completed_at,
                        completed_at,
                    ),
                )
            elif existing["status"] in {"awaiting_review", "superseded"}:
                connection.execute(
                    """UPDATE conversation_items SET analysis_run_id=?, kind=?, title=?,
                    detail=?, assignee=?, due_date=?, due_date_original=?, certainty=?,
                    status='awaiting_review', updated_at=? WHERE id=?""",
                    (
                        run_id,
                        values["kind"],
                        values["title"],
                        values["detail"],
                        values["assignee"],
                        values["due_date"],
                        values["due_date_original"],
                        values["certainty"],
                        completed_at,
                        item_id,
                    ),
                )
            else:
                continue
            connection.execute(
                "DELETE FROM conversation_item_evidence WHERE item_id=?", (item_id,)
            )
            for position, row in enumerate(evidence):
                connection.execute(
                    """INSERT INTO conversation_item_evidence
                    (item_id, position, utterance_id, quote, speaker, start_seconds, end_seconds)
                    VALUES(?,?,?,?,?,?,?)""",
                    (
                        item_id,
                        position,
                        row["id"],
                        row["text"],
                        row["speaker"],
                        row["start_seconds"],
                        row["end_seconds"],
                    ),
                )
        connection.execute(
            """UPDATE conversation_analysis_runs SET status='completed', error=NULL,
            completed_at=? WHERE id=?""",
            (completed_at, run_id),
        )
        connection.execute(
            """UPDATE recordings SET insight_status='completed', insight_error=NULL,
            insight_analyzed_at=? WHERE id=?""",
            (completed_at, recording_id),
        )


def extract_recording_insights(
    database: Path,
    recording_id: str,
    api_key_file: Path,
    schema_path: Path,
    model: str = DEFAULT_INSIGHT_MODEL,
) -> None:
    with INSIGHT_ANALYSIS_LOCK:
        run_id: str | None = None
        try:
            with _connect(database) as connection:
                connection.execute(
                    """UPDATE recordings SET insight_status='extracting', insight_error=NULL
                    WHERE id=?""",
                    (recording_id,),
                )
                recorded_at, utterances = _insight_input(connection, recording_id)
            input_hash = hashlib.sha256(
                json.dumps(
                    [recorded_at, utterances], ensure_ascii=False, sort_keys=True
                ).encode()
            ).hexdigest()
            with _connect(database) as connection:
                existing = connection.execute(
                    """SELECT id, status, completed_at FROM conversation_analysis_runs
                    WHERE recording_id=? AND input_hash=? AND extractor_version=? AND model=?""",
                    (recording_id, input_hash, PROMPT_VERSION, model),
                ).fetchone()
                if existing is not None and existing["status"] == "completed":
                    connection.execute(
                        """UPDATE recordings SET insight_status='completed', insight_error=NULL,
                        insight_analyzed_at=? WHERE id=?""",
                        (existing["completed_at"], recording_id),
                    )
                    return
                run_id = str(existing["id"]) if existing is not None else uuid.uuid4().hex
                now = datetime.now(UTC).isoformat()
                if existing is None:
                    connection.execute(
                        """INSERT INTO conversation_analysis_runs
                        (id, recording_id, input_hash, extractor_version, provider, model,
                         status, created_at) VALUES(?,?,?,?,?,?,'extracting',?)""",
                        (
                            run_id,
                            recording_id,
                            input_hash,
                            PROMPT_VERSION,
                            "openai",
                            model,
                            now,
                        ),
                    )
                else:
                    connection.execute(
                        """UPDATE conversation_analysis_runs SET status='extracting',
                        error=NULL, completed_at=NULL, created_at=? WHERE id=?""",
                        (now, run_id),
                    )
            api_key = load_api_key(api_key_file)
            try:
                schema = json.loads(schema_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as error:
                raise ConversationInsightError("会話整理の出力スキーマを読み取れません") from error
            raw_items: list[dict[str, object]] = []
            for chunk in chunk_utterances(utterances):
                raw_items.extend(
                    request_insights(
                        api_key=api_key,
                        model=model,
                        schema=schema,
                        recorded_at=recorded_at,
                        timezone="Asia/Tokyo",
                        utterances=chunk,
                    )
                )
            _store_extracted_items(
                database,
                recording_id,
                run_id,
                raw_items,
                utterances,
                datetime.now(UTC).isoformat(),
            )
        except Exception as error:
            message = str(error)[:500] or "LLMによる整理に失敗しました"
            with _connect(database) as connection:
                if run_id is not None:
                    connection.execute(
                        """UPDATE conversation_analysis_runs SET status='failed', error=?
                        WHERE id=?""",
                        (message, run_id),
                    )
                connection.execute(
                    """UPDATE recordings SET insight_status='failed', insight_error=?
                    WHERE id=?""",
                    (message, recording_id),
                )
