"""Durable, bounded Soundcore imports; capability URLs never enter responses or logs."""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from daily_reader import conversations
from daily_reader.soundcore_cloud import SoundcoreCloudError, download_share, validate_share_url

LOGGER = logging.getLogger(__name__)
MAX_PENDING = 10
MAX_ATTEMPTS = 3
FAILURE = "共有録音を取り込めませんでした。共有期限・接続・空き容量を確認してください。"


class ImportQueueFull(ValueError):
    pass


@contextmanager
def _connect(database: Path):
    database.parent.mkdir(parents=True, exist_ok=True)
    # The URL is a capability, so set file permissions before writing any rows.
    descriptor = os.open(database, os.O_CREAT | os.O_RDWR, 0o600)
    os.close(descriptor)
    database.chmod(0o600)
    with closing(conversations._connect(database)) as connection, connection:
        connection.execute(
            """CREATE TABLE IF NOT EXISTS soundcore_imports (
                id TEXT PRIMARY KEY, url TEXT NOT NULL, url_hash TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL, recording_id TEXT REFERENCES recordings(id),
                analysis_needed INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT NOT NULL,
                error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )"""
        )
        yield connection


def _public(row) -> dict:
    return {
        key: row[key] for key in (
            "id", "status", "recording_id", "attempts", "error", "created_at", "updated_at"
        )
    }


def _has_capacity(connection) -> None:
    count = connection.execute(
        "SELECT COUNT(*) FROM soundcore_imports WHERE status IN ('queued','downloading','saved')"
    ).fetchone()[0]
    if count >= MAX_PENDING:
        raise ImportQueueFull("取り込み待ちは10件までです。完了後に追加してください。")


def enqueue(database: Path, url: object) -> dict:
    canonical = validate_share_url(url)
    digest = hashlib.sha256(canonical.encode()).hexdigest()
    now = datetime.now(UTC).isoformat()
    with _connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM soundcore_imports WHERE url_hash=?", (digest,)
        ).fetchone()
        if row:
            return _public(row)
        _has_capacity(connection)
        job_id = uuid.uuid4().hex
        connection.execute(
            "INSERT INTO soundcore_imports "
            "(id,url,url_hash,status,next_attempt_at,created_at,updated_at) "
            "VALUES(?,?,?,'queued',?,?,?)",
            (job_id, canonical, digest, now, now, now),
        )
        return _public(connection.execute(
            "SELECT * FROM soundcore_imports WHERE id=?", (job_id,)
        ).fetchone())


def list_imports(database: Path) -> list[dict]:
    with _connect(database) as connection:
        return [_public(row) for row in connection.execute(
            "SELECT * FROM soundcore_imports ORDER BY created_at DESC,id DESC LIMIT 100"
        )]


def retry(database: Path, job_id: str) -> dict:
    with _connect(database) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT * FROM soundcore_imports WHERE id=?", (job_id,)
        ).fetchone()
        if row is None:
            raise KeyError(job_id)
        if row["status"] == "failed":
            _has_capacity(connection)
            now = datetime.now(UTC).isoformat()
            connection.execute(
                "UPDATE soundcore_imports SET status=?,attempts=0,error=NULL,"
                "next_attempt_at=?,updated_at=? WHERE id=?",
                ("saved" if row["recording_id"] else "queued", now, now, job_id),
            )
            row = connection.execute(
                "SELECT * FROM soundcore_imports WHERE id=?", (job_id,)
            ).fetchone()
        return _public(row)


def recover(database: Path) -> None:
    with _connect(database) as connection:
        connection.execute(
            "UPDATE soundcore_imports SET status=CASE WHEN attempts>=? THEN 'failed' "
            "ELSE 'queued' END,error=? WHERE status='downloading'",
            (MAX_ATTEMPTS, "サーバー再起動により取得が中断されました。"),
        )


class SoundcoreImportWorker:
    def __init__(self, database: Path, audio_directory: Path, token_file: Path):
        self.database, self.audio_directory, self.token_file = (
            database, audio_directory, token_file
        )
        self.stopped = threading.Event()
        self.thread: threading.Thread | None = None
        self.operation = threading.Lock()

    def start(self) -> None:
        recover(self.database)
        self.thread = threading.Thread(target=self.run, name="daymeld-soundcore", daemon=True)
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                self.step()
            except Exception:
                LOGGER.warning("Soundcore import queue unavailable; pending imports retained")
            self.stopped.wait(5)

    def step(self, now: datetime | None = None) -> bool:
        with self.operation:
            return self._step(now or datetime.now(UTC))

    def _step(self, now: datetime) -> bool:
        if self.stopped.is_set():
            return False
        with _connect(self.database) as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM soundcore_imports WHERE status IN ('queued','saved') "
                "AND next_attempt_at<=? ORDER BY created_at,id LIMIT 1", (now.isoformat(),)
            ).fetchone()
            if row is None:
                return False
            job = dict(row)
            connection.execute(
                "UPDATE soundcore_imports SET status=?,attempts=attempts+1,updated_at=? "
                "WHERE id=?",
                ("saved" if job["recording_id"] else "downloading", now.isoformat(), job["id"]),
            )
        try:
            if not job["recording_id"]:
                # Downloading and validation hold no database transaction. Temporary
                # cloud data is private and is removed even when validation fails.
                with tempfile.TemporaryDirectory(prefix="daymeld-soundcore-") as temporary:
                    audio = download_share(job["url"], Path(temporary))
                    if self.stopped.is_set():
                        return True
                    with audio.path.open("rb") as source:
                        conversations.store_cloud_audio(
                            self.database, self.audio_directory, source,
                            audio.path.stat().st_size, audio.filename, audio.recorded_at,
                            audio.metadata, job["id"],
                        )
            if self.stopped.is_set():
                return True
            with _connect(self.database) as connection:
                job = dict(connection.execute(
                    "SELECT * FROM soundcore_imports WHERE id=?", (job["id"],)
                ).fetchone())
            if job["analysis_needed"]:
                recording = conversations.get_recording(self.database, job["recording_id"])
                # A crash after start_analysis may already have started or completed
                # recognition. Never repeat it or disturb existing candidates.
                if recording["status"] not in {"analyzing", "completed"}:
                    conversations.start_analysis(
                        self.database, job["recording_id"], self.token_file
                    )
            with _connect(self.database) as connection:
                connection.execute(
                    "UPDATE soundcore_imports SET status='completed',error=NULL,"
                    "updated_at=? WHERE id=?", (datetime.now(UTC).isoformat(), job["id"]),
                )
        except Exception as error:
            # Fetchers, ffmpeg and filesystem exceptions can contain signed URLs,
            # transcripts or user paths. Persist only this static error category.
            with _connect(self.database) as connection:
                row = connection.execute(
                    "SELECT * FROM soundcore_imports WHERE id=?", (job["id"],)
                ).fetchone()
                cloud_error = isinstance(error, SoundcoreCloudError)
                failed = row["attempts"] >= MAX_ATTEMPTS or (cloud_error and not error.retryable)
                message = (
                    SoundcoreCloudError.MESSAGES.get(error.code, FAILURE)
                    if cloud_error else FAILURE
                )
                status = "failed" if failed else ("saved" if row["recording_id"] else "queued")
                connection.execute(
                    "UPDATE soundcore_imports SET status=?,error=?,next_attempt_at=?,"
                    "updated_at=? WHERE id=?",
                    (
                        status, message,
                        (now + timedelta(seconds=30 * 2 ** row["attempts"])).isoformat(),
                        now.isoformat(), job["id"],
                    ),
                )
        return True
