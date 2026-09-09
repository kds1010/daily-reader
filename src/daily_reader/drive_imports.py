"""Receive connector-downloaded Soundcore originals without accepting credentials or URLs."""

from __future__ import annotations

import fcntl
import json
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO
from zoneinfo import ZoneInfo

from daily_reader import conversations

LOGGER = logging.getLogger(__name__)
MAX_PENDING = 10
MAX_ATTEMPTS = 3
FAILURE = "Drive音声を保存できませんでした。接続・音声形式・空き容量を確認してください。"
ANALYSIS_FAILURE = "原音は保存済みですが、解析を開始できませんでした。再試行してください。"
CONFLICT = "同じDriveファイルの内容または版が変わっています。保存済みの録音は保持しました。"
FIELDS = {
    "file_id", "parent_folder_id", "parent_folder_name", "file_name", "mime_type",
    "size", "md5_checksum", "modified_time", "version",
}
MIMES = {"audio/ogg": ".ogg", "audio/mpeg": ".mp3", "audio/mp3": ".mp3"}


class ImportConflict(ValueError):
    def __init__(self, message: str = CONFLICT, *, code: str = "source_conflict"):
        super().__init__(message)
        self.code = code


class ImportQueueFull(ValueError):
    pass


@contextmanager
def _connect(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(database, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    database.chmod(0o600)
    with closing(conversations._connect(database)) as connection, connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS drive_imports (
                id TEXT PRIMARY KEY, file_id TEXT NOT NULL UNIQUE,
                metadata TEXT NOT NULL, conflict_metadata TEXT,
                status TEXT NOT NULL, recording_id TEXT REFERENCES recordings(id),
                analysis_needed INTEGER NOT NULL DEFAULT 0,
                upload_attempts INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                next_attempt_at TEXT NOT NULL, error TEXT,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        yield connection


def _text(value: object, limit: int) -> str:
    if (
        not isinstance(value, str) or not value or len(value) > limit
        or any(ord(char) < 32 for char in value)
        or "://" in value
    ):
        raise ValueError("invalid Drive metadata")
    value.encode("utf-8")
    return value


def validate_metadata(payload: object) -> dict:
    if not isinstance(payload, dict) or set(payload) - FIELDS:
        raise ValueError("invalid Drive metadata")
    result = {}
    for key in ("file_id", "parent_folder_id"):
        value = _text(payload.get(key), 256)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
            raise ValueError("invalid Drive identifier")
        result[key] = value
    for key in ("file_name", "parent_folder_name"):
        result[key] = _text(payload.get(key), 240)
    mime = payload.get("mime_type")
    if not isinstance(mime, str) or mime not in MIMES:
        raise ValueError("unsupported Drive audio")
    if Path(result["file_name"]).suffix.lower() != MIMES[mime]:
        raise ValueError("inconsistent Drive audio type")
    result["mime_type"] = mime
    size = payload.get("size")
    if type(size) is not int or not 0 < size <= conversations.MAX_UPLOAD_BYTES:
        raise ValueError("invalid Drive audio size")
    result["size"] = size
    modified = _text(payload.get("modified_time"), 64)
    stamp = datetime.fromisoformat(modified)
    if stamp.tzinfo is None:
        raise ValueError("Drive modified time must include timezone")
    result["modified_time"] = stamp.astimezone(UTC).isoformat()
    checksum = payload.get("md5_checksum")
    if checksum is not None:
        if not isinstance(checksum, str) or not re.fullmatch(r"[0-9a-fA-F]{32}", checksum):
            raise ValueError("invalid Drive checksum")
        result["md5_checksum"] = checksum.lower()
    version = payload.get("version")
    if version is not None:
        if not isinstance(version, str) or not re.fullmatch(r"[0-9]{1,32}", version):
            raise ValueError("invalid Drive version")
        result["version"] = version
    return result


def _same_content(old: dict, new: dict) -> bool:
    if old["size"] != new["size"] or MIMES[old["mime_type"]] != MIMES[new["mime_type"]]:
        return False
    if old.get("md5_checksum") and new.get("md5_checksum"):
        return old["md5_checksum"] == new["md5_checksum"]
    return (
        old.get("version") == new.get("version")
        and old["modified_time"] == new["modified_time"]
    )


def _public(row) -> dict:
    result = {key: row[key] for key in (
        "id", "file_id", "status", "recording_id", "upload_attempts", "attempts",
        "error", "created_at", "updated_at",
    )}
    result["needs_audio"] = row["recording_id"] is None and row["status"] == "pending"
    return result


def _has_capacity(connection) -> None:
    if connection.execute(
        "SELECT COUNT(*) FROM drive_imports WHERE status IN ('pending','receiving','saved')"
    ).fetchone()[0] >= MAX_PENDING:
        raise ImportQueueFull("Drive取り込み待ちは10件までです。完了後に追加してください。")


def enqueue(database: Path, payload: object) -> dict:
    metadata = validate_metadata(payload)
    now = datetime.now(UTC).isoformat()
    conflict = False
    with _connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM drive_imports WHERE file_id=?", (metadata["file_id"],)
        ).fetchone()
        if row:
            if not _same_content(json.loads(row["metadata"]), metadata):
                connection.execute(
                    "UPDATE drive_imports SET status='conflict',conflict_metadata=?,error=?,"
                    "updated_at=? WHERE id=?",
                    (json.dumps(metadata, ensure_ascii=False), CONFLICT, now, row["id"]),
                )
                conflict = True
            elif row["status"] == "conflict":
                conflict = True
            result = _public(row)
        else:
            _has_capacity(connection)
            job_id = uuid.uuid4().hex
            connection.execute(
                "INSERT INTO drive_imports "
                "(id,file_id,metadata,status,next_attempt_at,created_at,updated_at) "
                "VALUES(?,?,?,'pending',?,?,?)",
                (job_id, metadata["file_id"], json.dumps(metadata, ensure_ascii=False),
                 now, now, now),
            )
            result = _public(connection.execute(
                "SELECT * FROM drive_imports WHERE id=?", (job_id,)
            ).fetchone())
    if conflict:
        raise ImportConflict(CONFLICT)
    return result


def get_import(database: Path, job_id: str) -> dict:
    with _connect(database) as connection:
        row = connection.execute("SELECT * FROM drive_imports WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        return _public(row)


def list_imports(database: Path, *, limit: int = 100, offset: int = 0) -> dict:
    if not 1 <= limit <= 100 or not 0 <= offset <= 1_000_000:
        raise ValueError("invalid import pagination")
    with _connect(database) as connection:
        rows = connection.execute(
            "SELECT * FROM drive_imports ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        count = connection.execute("SELECT COUNT(*) FROM drive_imports").fetchone()[0]
        return {"items": [_public(row) for row in rows], "total": count,
                "next_offset": offset + len(rows) if offset + len(rows) < count else None}


def _recorded_at(folder_name: str) -> str | None:
    if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", folder_name):
        return None
    try:
        stamp = datetime.strptime(folder_name, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    return stamp.replace(tzinfo=ZoneInfo("Asia/Tokyo")).isoformat()


def _validate_audio(path: Path, extension: str) -> None:
    with path.open("rb") as stream:
        prefix = stream.read(12)
    if extension == ".ogg":
        valid = prefix.startswith(b"OggS")
    else:
        valid = prefix.startswith(b"ID3") or (
            len(prefix) >= 2 and prefix[0] == 255 and prefix[1] & 224 == 224
        )
    if not valid:
        raise ValueError("invalid audio signature")
    executable = shutil.which("ffprobe")
    if not executable:
        raise OSError("audio verification unavailable")
    result = subprocess.run(
        [executable, "-v", "error", "-protocol_whitelist", "file,pipe", "-show_entries",
         "format=format_name,duration:stream=codec_type,codec_name", "-of", "json", str(path)],
        capture_output=True, timeout=30, check=False,
    )
    try:
        data = json.loads(result.stdout)
        duration = float(data["format"]["duration"])
        formats = data["format"]["format_name"].split(",")
        valid = (
            result.returncode == 0 and math.isfinite(duration) and duration > 0
            and extension.removeprefix(".") in formats
            and any(stream.get("codec_type") == "audio" for stream in data["streams"])
        )
    except (ValueError, TypeError, KeyError):
        valid = False
    if not valid:
        raise ValueError("invalid audio contents")


@contextmanager
def _operation_lock(database: Path, kind: str):
    database.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(str(database) + f"-drive-{kind}.lock", os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ImportConflict(
                "別のDrive取り込みを処理中です。完了後に再試行してください。", code="busy",
            ) from None
        yield
    finally:
        os.close(descriptor)


def receive_audio(
    database: Path, audio_directory: Path, job_id: str, source: BinaryIO, length: int,
) -> dict:
    with _operation_lock(database, "upload"):
        with _connect(database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM drive_imports WHERE id=?", (job_id,)
            ).fetchone()
            if row is None:
                raise KeyError(job_id)
            if row["status"] in {"conflict", "failed", "receiving"}:
                raise ImportConflict(
                    row["error"] or "取り込みは再試行待ちまたは処理中です。",
                    code="source_conflict" if row["status"] == "conflict" else "busy",
                )
            metadata = json.loads(row["metadata"])
            if type(length) is not int or length != metadata["size"]:
                raise ValueError("audio length differs from Drive metadata")
            if row["recording_id"]:
                return _public(row)
            connection.execute(
                "UPDATE drive_imports SET status='receiving',upload_attempts=upload_attempts+1,"
                "error=NULL,updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), job_id),
            )

        def checkpoint(connection, recording_id: str, created: bool) -> None:
            updated = connection.execute(
                "UPDATE drive_imports SET recording_id=?,analysis_needed=?,status='saved',"
                "error=NULL,updated_at=? WHERE id=? AND status='receiving'",
                (recording_id, int(created), datetime.now(UTC).isoformat(), job_id),
            )
            if updated.rowcount != 1:
                raise ImportConflict(CONFLICT)

        try:
            extension = MIMES[metadata["mime_type"]]
            display_name = metadata["parent_folder_name"].replace("/", "_").replace("\\", "_")
            conversations._store_audio(
                database, audio_directory, source, length,
                display_name[:240 - len(extension)] + extension,
                _recorded_at(metadata["parent_folder_name"]),
                cloud_metadata={"provider": "soundcore_google_drive", "schema_version": 1,
                                **metadata},
                imported_date_source="soundcore_drive_folder_name", import_checkpoint=checkpoint,
                audio_validator=lambda path: _validate_audio(path, extension),
                expected_md5=metadata.get("md5_checksum"), storage_id=job_id,
            )
        except Exception:
            with _connect(database) as connection:
                connection.execute(
                    "UPDATE drive_imports SET status=CASE WHEN upload_attempts>=? THEN 'failed' "
                    "ELSE 'pending' END,error=?,updated_at=? WHERE id=? AND status='receiving'",
                    (MAX_ATTEMPTS, FAILURE, datetime.now(UTC).isoformat(), job_id),
                )
            raise
    return get_import(database, job_id)


def retry(database: Path, job_id: str) -> dict:
    with _connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute("SELECT * FROM drive_imports WHERE id=?", (job_id,)).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row["status"] == "conflict":
            raise ImportConflict(CONFLICT)
        if row["status"] == "failed":
            _has_capacity(connection)
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE drive_imports SET status=?,attempts=0,upload_attempts=0,error=NULL,"
                "next_attempt_at=?,updated_at=? WHERE id=?",
                ("saved" if row["recording_id"] else "pending", now, now, job_id),
            )
    return get_import(database, job_id)


def recover(database: Path, audio_directory: Path) -> None:
    # Run before accepting HTTP requests. Only remove this queue's uncommitted copies.
    with _operation_lock(database, "upload"):
        with _connect(database) as connection:
            rows = connection.execute(
                "SELECT id FROM drive_imports WHERE status='receiving' AND recording_id IS NULL"
            ).fetchall()
        for row in rows:
            folder = audio_directory / row["id"]
            if folder.is_dir() and not folder.is_symlink():
                for name in ("original.tmp", "original.mp3", "original.ogg"):
                    (folder / name).unlink(missing_ok=True)
                if not any(folder.iterdir()):
                    folder.rmdir()
        with _connect(database) as connection:
            connection.execute(
                "UPDATE drive_imports SET status=CASE WHEN upload_attempts>=? THEN 'failed' "
                "ELSE 'pending' END,error=? WHERE status='receiving' AND recording_id IS NULL",
                (MAX_ATTEMPTS, "再起動で音声転送が中断されました。原音を再送してください。"),
            )
            connection.execute(
                "UPDATE drive_imports SET status=CASE WHEN attempts>=? THEN 'failed' "
                "ELSE 'saved' END,error=?,next_attempt_at=? WHERE analysis_needed=1 "
                "AND status IN ('saved','completed') AND recording_id IN "
                "(SELECT id FROM recordings WHERE status='failed' AND error=?)",
                (MAX_ATTEMPTS, ANALYSIS_FAILURE, datetime.now(UTC).isoformat(),
                 "サーバー再起動により処理が中断されました。再試行してください。"),
            )


class DriveImportWorker:
    def __init__(self, database: Path, audio_directory: Path, token_file: Path):
        self.database, self.audio_directory, self.token_file = (
            database, audio_directory, token_file
        )
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None

    def start(self) -> None:
        recover(self.database, self.audio_directory)
        self.thread = threading.Thread(target=self.run, name="daymeld-drive-import", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                self.step()
            except Exception:
                LOGGER.warning("Drive import queue unavailable; saved originals retained")
            self.stopped.wait(5)

    def step(self, now: datetime | None = None) -> bool:
        if self.stopped.is_set():
            return False
        now = now or datetime.now(UTC)
        with _operation_lock(self.database, "worker"):
            with _connect(self.database) as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute(
                    "SELECT * FROM drive_imports WHERE status='saved' AND next_attempt_at<=? "
                    "ORDER BY created_at,id LIMIT 1", (now.isoformat(),),
                ).fetchone()
                if row is None:
                    return False
                job = dict(row)
                connection.execute(
                    "UPDATE drive_imports SET attempts=attempts+1,updated_at=? WHERE id=?",
                    (now.isoformat(), job["id"]),
                )
            try:
                if job["analysis_needed"]:
                    recording = conversations.get_recording(self.database, job["recording_id"])
                    if recording["status"] not in {"analyzing", "completed"}:
                        conversations.start_analysis(
                            self.database, job["recording_id"], self.token_file, initial_only=True,
                        )
                with _connect(self.database) as connection:
                    connection.execute(
                        "UPDATE drive_imports SET status='completed',error=NULL,updated_at=? "
                        "WHERE id=? AND status='saved'", (now.isoformat(), job["id"]),
                    )
            except Exception:
                with _connect(self.database) as connection:
                    connection.execute(
                        "UPDATE drive_imports SET status=CASE WHEN attempts>=? THEN 'failed' "
                        "ELSE 'saved' END,error=?,next_attempt_at=?,updated_at=? "
                        "WHERE id=? AND status='saved'",
                        (MAX_ATTEMPTS, ANALYSIS_FAILURE,
                         (now + timedelta(seconds=30 * 2 ** (job["attempts"] + 1))).isoformat(),
                         now.isoformat(), job["id"]),
                    )
            return True
