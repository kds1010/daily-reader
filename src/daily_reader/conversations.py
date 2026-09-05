from __future__ import annotations

import hashlib
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

MINIMUM_FREE_BYTES = 5 * 1024**3
MAX_UPLOAD_BYTES = 2 * 1024**3
MAX_TRANSCRIPT_BYTES = 10 * 1024**2
ANALYSIS_LOCK = threading.Lock()


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
                source_type TEXT NOT NULL DEFAULT 'audio', transcript_text TEXT
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
        }.items():
            if name not in columns:
                connection.execute(f"ALTER TABLE recordings ADD COLUMN {name} {definition}")


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
                "location_timestamp, location_time_delta, source_type "
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
        return result


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
