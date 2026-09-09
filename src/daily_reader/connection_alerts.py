"""Persist fixed, actionable connection alerts without recording private inputs."""

from __future__ import annotations

import os
import sqlite3
import stat
import uuid
from contextlib import closing, contextmanager
from datetime import UTC, datetime
from pathlib import Path

DATABASE_NAME = "connection-alerts.sqlite3"
PROVIDERS = {"soundcore", "google_drive"}
SOUNDCORE_REASONS = {"sign_in_required", "verification_required", "drive_authorization_required"}
MESSAGES = {
    "soundcore": {
        "sign_in_required":
            "Soundcore Online Hubへのログインが必要です。Macで接続を確認してください。",
        "verification_required": "Soundcoreのログインで本人確認が必要です。Macで確認してください。",
        "drive_authorization_required":
            "SoundcoreからGoogle Driveへの同期で認証が必要です。Macで接続を確認してください。",
    },
    "google_drive": {
        "sign_in_required":
            "Google Driveの認証が必要です。MacでDriveの接続設定を確認してください。",
        "credential_required":
            "Google Driveの認証情報を利用できません。Macで接続設定を確認してください。",
        "permission_required": (
            "Google Driveへのアクセスが拒否されました。"
            "録音の共有・ダウンロード設定を確認してください。"
        ),
    },
}
TITLES = {"soundcore": "Soundcoreの接続確認", "google_drive": "Google Driveの接続確認"}


@contextmanager
def _connect(data_dir: Path):
    data_dir.mkdir(parents=True, exist_ok=True)
    database = data_dir / DATABASE_NAME
    descriptor = os.open(database, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        attributes = os.fstat(descriptor)
        if not stat.S_ISREG(attributes.st_mode) or attributes.st_uid != os.getuid():
            raise OSError("connection alert storage unavailable")
        os.fchmod(descriptor, 0o600)
    finally:
        os.close(descriptor)
    with closing(sqlite3.connect(database, timeout=5)) as connection, connection:
        connection.row_factory = sqlite3.Row
        connection.execute(
            "CREATE TABLE IF NOT EXISTS connection_health ("
            "provider TEXT PRIMARY KEY, state TEXT NOT NULL, alert_id TEXT, "
            "reason TEXT, occurred_at TEXT, updated_at TEXT NOT NULL)"
        )
        yield connection


def report(data_dir: Path, provider: str, state: str, reason: str | None = None) -> None:
    if provider not in PROVIDERS or state not in {
        "connected", "authentication_required", "unavailable",
    }:
        raise ValueError("invalid connection state")
    if state == "authentication_required":
        if reason not in MESSAGES[provider]:
            raise ValueError("invalid connection reason")
    elif reason is not None:
        raise ValueError("unexpected connection reason")
    now = datetime.now(UTC).isoformat()
    with _connect(data_dir) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT alert_id,reason,occurred_at FROM connection_health WHERE provider=?",
            (provider,),
        ).fetchone()
        alert_id, previous_reason, occurred_at = tuple(row) if row else (None, None, None)
        if state == "connected":
            alert_id, reason, occurred_at = None, None, None
        elif state == "authentication_required":
            if alert_id is None:
                alert_id, occurred_at = uuid.uuid4().hex, now
        else:
            reason = previous_reason  # A failed observation cannot prove recovery.
        connection.execute(
            "INSERT INTO connection_health VALUES(?,?,?,?,?,?) ON CONFLICT(provider) DO UPDATE SET "
            "state=excluded.state,alert_id=excluded.alert_id,reason=excluded.reason,"
            "occurred_at=excluded.occurred_at,updated_at=excluded.updated_at",
            (provider, state, alert_id, reason, occurred_at, now),
        )


def report_soundcore(data_dir: Path, payload: object) -> None:
    if not isinstance(payload, dict) or set(payload) - {"state", "reason"}:
        raise ValueError("invalid connection health payload")
    state, reason = payload.get("state"), payload.get("reason")
    if not isinstance(state, str) or (reason is not None and not isinstance(reason, str)):
        raise ValueError("invalid connection health payload")
    if state == "authentication_required":
        if reason not in SOUNDCORE_REASONS:
            raise ValueError("invalid connection reason")
    elif "reason" in payload:
        raise ValueError("unexpected connection reason")
    report(data_dir, "soundcore", state, reason)


def active_alerts(data_dir: Path) -> list[dict[str, str]]:
    database = data_dir / DATABASE_NAME
    try:
        attributes = database.lstat()
    except FileNotFoundError:
        return []
    if (
        not stat.S_ISREG(attributes.st_mode) or attributes.st_uid != os.getuid()
        or stat.S_IMODE(attributes.st_mode) != 0o600
    ):
        raise OSError("connection alert storage unavailable")
    with closing(sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True)) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT provider,alert_id,reason,occurred_at FROM connection_health "
            "WHERE alert_id IS NOT NULL ORDER BY occurred_at,provider"
        ).fetchall()
    return [
        {"id": row["alert_id"], "provider": row["provider"], "title": TITLES[row["provider"]],
         "message": MESSAGES[row["provider"]][row["reason"]], "occurred_at": row["occurred_at"]}
        for row in rows
    ]
