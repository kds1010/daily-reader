from __future__ import annotations

import sqlite3
import tomllib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

FINAL_STATES = {"completed", "blocked", "failed", "cancelled"}


def load_repositories(path: Path) -> dict[str, dict[str, str]]:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    repositories: dict[str, dict[str, str]] = {}
    for item in payload.get("repositories", []):
        name = item.get("name")
        label = item.get("label")
        repository_path = item.get("path")
        default_branch = item.get("default_branch", "main")
        if not all(isinstance(value, str) and value.strip() for value in (
            name,
            label,
            repository_path,
            default_branch,
        )):
            raise ValueError("invalid agent repository configuration")
        if name in repositories:
            raise ValueError(f"duplicate agent repository: {name}")
        resolved = (path.parent.parent / repository_path).resolve()
        if not (resolved / ".git").exists():
            raise ValueError(f"agent repository is not a Git checkout: {resolved}")
        repositories[name] = {
            "name": name,
            "label": label,
            "path": str(resolved),
            "default_branch": default_branch,
        }
    if not repositories:
        raise ValueError("at least one agent repository is required")
    return repositories


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS agent_jobs (
                id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                prompt TEXT NOT NULL,
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                thread_id TEXT,
                branch TEXT,
                worktree TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES agent_jobs(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS agent_jobs_status_created
                ON agent_jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS agent_events_job_id
                ON agent_events(job_id, id);
            """
        )
        connection.execute("PRAGMA foreign_keys = ON")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_job(
    path: Path,
    repositories: dict[str, dict[str, str]],
    payload: dict[str, object],
) -> dict[str, Any]:
    repository = payload.get("repository")
    prompt = payload.get("prompt")
    if repository not in repositories:
        raise ValueError("invalid repository")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
        raise ValueError("invalid prompt")
    now = _now()
    job = {
        "id": uuid.uuid4().hex,
        "repository": repository,
        "prompt": prompt.strip(),
        "status": "queued",
        "phase": "待機中",
        "created_at": now,
        "updated_at": now,
    }
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO agent_jobs (
                id, repository, prompt, status, phase, created_at, updated_at
            ) VALUES (
                :id, :repository, :prompt, :status, :phase, :created_at, :updated_at
            )""",
            job,
        )
        connection.execute(
            """INSERT INTO agent_events (job_id, created_at, kind, message)
            VALUES (?, ?, 'queued', 'タスクを受け付けました')""",
            (job["id"], now),
        )
    return job


def list_jobs(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT * FROM agent_jobs
            ORDER BY CASE status
                WHEN 'running' THEN 0 WHEN 'blocked' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,
                created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_job(path: Path, job_id: str) -> dict[str, Any] | None:
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT * FROM agent_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        events = connection.execute(
            """SELECT created_at, kind, message FROM agent_events
            WHERE job_id = ? ORDER BY id""",
            (job_id,),
        ).fetchall()
    result = dict(row)
    result["events"] = [dict(event) for event in events]
    return result


def request_cancel(path: Path, job_id: str) -> bool:
    initialize_database(path)
    now = _now()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status FROM agent_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row[0] in FINAL_STATES:
            return False
        if row[0] == "queued":
            connection.execute(
                """UPDATE agent_jobs SET status='cancelled', phase='キャンセル済み',
                cancel_requested=1, updated_at=?, finished_at=? WHERE id=?""",
                (now, now, job_id),
            )
        else:
            connection.execute(
                """UPDATE agent_jobs SET cancel_requested=1, phase='停止処理中',
                updated_at=? WHERE id=?""",
                (now, job_id),
            )
        connection.execute(
            """INSERT INTO agent_events (job_id, created_at, kind, message)
            VALUES (?, ?, 'cancel', '停止が要求されました')""",
            (job_id, now),
        )
    return True


def resume_job(path: Path, job_id: str, instruction: object) -> bool:
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 10_000:
        return False
    initialize_database(path)
    now = _now()
    with sqlite3.connect(path) as connection:
        row = connection.execute(
            "SELECT status FROM agent_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row[0] != "blocked":
            return False
        connection.execute(
            """UPDATE agent_jobs SET status='queued', phase='再開待ち',
            prompt=prompt || ?, cancel_requested=0, updated_at=? WHERE id=?""",
            (f"\n\nUser clarification:\n{instruction.strip()}", now, job_id),
        )
        connection.execute(
            """INSERT INTO agent_events (job_id, created_at, kind, message)
            VALUES (?, ?, 'user', ?)""",
            (job_id, now, instruction.strip()),
        )
    return True


def claim_next_job(path: Path) -> dict[str, Any] | None:
    initialize_database(path)
    now = _now()
    with sqlite3.connect(path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT * FROM agent_jobs WHERE status='queued'
            ORDER BY created_at LIMIT 1"""
        ).fetchone()
        if row is None:
            connection.commit()
            return None
        connection.execute(
            """UPDATE agent_jobs SET status='running', phase='作業環境を準備中',
            updated_at=? WHERE id=?""",
            (now, row["id"]),
        )
        connection.commit()
    return dict(row)


def update_job(path: Path, job_id: str, **fields: object) -> None:
    allowed = {
        "status",
        "phase",
        "summary",
        "thread_id",
        "branch",
        "worktree",
        "attempts",
        "cancel_requested",
        "finished_at",
    }
    values = {name: value for name, value in fields.items() if name in allowed}
    if not values:
        return
    values["updated_at"] = _now()
    assignments = ", ".join(f"{name} = ?" for name in values)
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            f"UPDATE agent_jobs SET {assignments} WHERE id = ?",  # noqa: S608
            (*values.values(), job_id),
        )


def append_event(path: Path, job_id: str, kind: str, message: str) -> None:
    initialize_database(path)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """INSERT INTO agent_events (job_id, created_at, kind, message)
            VALUES (?, ?, ?, ?)""",
            (job_id, _now(), kind, message[:20_000]),
        )
