from __future__ import annotations

import sqlite3
import tomllib
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

FINAL_STATES = {"completed", "blocked", "failed", "cancelled"}
LIVE_STATES = {"queued", "running", "blocked"}
JOB_MODES = {"execute", "requirements"}
ARCHIVE_RETENTION = timedelta(days=7)
DEFAULT_MODEL = "gpt-5.6-luna"
DEFAULT_REASONING_EFFORT = "low"

# The app-server catalog is supplied by the local server.  This fallback keeps
# older clients and an unavailable app-server compatible with the current route.
FALLBACK_MODEL_OPTIONS = [
    {
        "slug": DEFAULT_MODEL,
        "display_name": "GPT-5.6-Luna",
        "default_reasoning_effort": DEFAULT_REASONING_EFFORT,
        "supported_reasoning_efforts": ["low", "medium", "high", "xhigh", "max"],
    }
]


@contextmanager
def connect_database(
    path: Path, *, timeout: float = 5.0
) -> Iterator[sqlite3.Connection]:
    """Commit or roll back a transaction, then always release its file descriptors."""
    connection = sqlite3.connect(path, timeout=timeout)
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def load_repositories(path: Path) -> dict[str, dict[str, str]]:
    payload = tomllib.loads(path.read_text(encoding="utf-8"))
    repositories: dict[str, dict[str, str]] = {}
    for item in payload.get("repositories", []):
        name = item.get("name")
        label = item.get("label")
        repository_path = item.get("path")
        default_branch = item.get("default_branch", "main")
        deploy = item.get("deploy", True)
        if not all(isinstance(value, str) and value.strip() for value in (
            name,
            label,
            repository_path,
            default_branch,
        )):
            raise ValueError("invalid agent repository configuration")
        if not isinstance(deploy, bool):
            raise ValueError("invalid agent repository deploy configuration")
        if name in repositories:
            raise ValueError(f"duplicate agent repository: {name}")
        configured_path = Path(repository_path).expanduser()
        if not configured_path.is_absolute():
            configured_path = path.parent.parent / configured_path
        resolved = configured_path.resolve()
        if not (resolved / ".git").exists():
            raise ValueError(f"agent repository is not a Git checkout: {resolved}")
        repositories[name] = {
            "name": name,
            "label": label,
            "path": str(resolved),
            "default_branch": default_branch,
            "deploy": deploy,
        }
    if not repositories:
        raise ValueError("at least one agent repository is required")
    return repositories


def initialize_database(path: Path, *, timeout: float = 5.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with connect_database(path, timeout=timeout) as connection:
        connection.executescript(
            """
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS agent_jobs (
                id TEXT PRIMARY KEY,
                repository TEXT NOT NULL,
                prompt TEXT NOT NULL,
                mode TEXT NOT NULL DEFAULT 'execute',
                status TEXT NOT NULL,
                phase TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                completion_summary TEXT NOT NULL DEFAULT '',
                thread_id TEXT,
                branch TEXT,
                worktree TEXT,
                attempts INTEGER NOT NULL DEFAULT 0,
                cancel_requested INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                hidden_at TEXT,
                follow_up INTEGER NOT NULL DEFAULT 0,
                model TEXT NOT NULL DEFAULT 'gpt-5.6-luna',
                reasoning_effort TEXT NOT NULL DEFAULT 'low',
                finished_at TEXT
            );
            CREATE TABLE IF NOT EXISTS agent_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES agent_jobs(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL,
                message TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_instructions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL REFERENCES agent_jobs(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                instruction TEXT NOT NULL,
                delivered_at TEXT
            );
            CREATE INDEX IF NOT EXISTS agent_jobs_status_created
                ON agent_jobs(status, created_at);
            CREATE INDEX IF NOT EXISTS agent_events_job_id
                ON agent_events(job_id, id);
            CREATE INDEX IF NOT EXISTS agent_instructions_pending
                ON agent_instructions(job_id, delivered_at, id);
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(agent_jobs)")
        }
        if "mode" not in columns:
            connection.execute(
                "ALTER TABLE agent_jobs ADD COLUMN mode TEXT NOT NULL DEFAULT 'execute'"
            )
        if "hidden_at" not in columns:
            connection.execute("ALTER TABLE agent_jobs ADD COLUMN hidden_at TEXT")
        if "follow_up" not in columns:
            connection.execute(
                "ALTER TABLE agent_jobs ADD COLUMN follow_up INTEGER NOT NULL DEFAULT 0"
            )
        if "completion_summary" not in columns:
            connection.execute(
                "ALTER TABLE agent_jobs ADD COLUMN completion_summary TEXT NOT NULL DEFAULT ''"
            )
            connection.execute(
                """UPDATE agent_jobs SET completion_summary = summary
                WHERE status = 'completed' AND completion_summary = '' AND summary != ''"""
            )
        if "model" not in columns:
            connection.execute(
                "ALTER TABLE agent_jobs ADD COLUMN model TEXT NOT NULL DEFAULT 'gpt-5.6-luna'"
            )
        if "reasoning_effort" not in columns:
            connection.execute(
                "ALTER TABLE agent_jobs ADD COLUMN reasoning_effort TEXT NOT NULL DEFAULT 'low'"
            )
        connection.execute("PRAGMA foreign_keys = ON")


def _now() -> str:
    return datetime.now(UTC).isoformat()


def create_job(
    path: Path,
    repositories: dict[str, dict[str, str]],
    payload: dict[str, object],
    model_options: list[dict[str, object]] | None = None,
) -> dict[str, Any]:
    repository = payload.get("repository")
    prompt = payload.get("prompt")
    mode = payload.get("mode", "execute")
    model = payload.get("model", DEFAULT_MODEL)
    reasoning_effort = payload.get("reasoning_effort", DEFAULT_REASONING_EFFORT)
    if repository not in repositories:
        raise ValueError("invalid repository")
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 20_000:
        raise ValueError("invalid prompt")
    if mode not in JOB_MODES:
        raise ValueError("invalid mode")
    if not isinstance(model, str) or not isinstance(reasoning_effort, str):
        raise ValueError("invalid model")
    options = model_options or FALLBACK_MODEL_OPTIONS
    option = next((item for item in options if item.get("slug") == model), None)
    if option is None or reasoning_effort not in option.get("supported_reasoning_efforts", []):
        raise ValueError("invalid model or reasoning effort")
    now = _now()
    job = {
        "id": uuid.uuid4().hex,
        "repository": repository,
        "prompt": prompt.strip(),
        "mode": mode,
        "model": model,
        "reasoning_effort": reasoning_effort,
        "status": "queued",
        "phase": "待機中",
        "created_at": now,
        "updated_at": now,
    }
    initialize_database(path)
    with connect_database(path) as connection:
        connection.execute(
            """INSERT INTO agent_jobs (
                id, repository, prompt, mode, model, reasoning_effort,
                status, phase, created_at, updated_at
            ) VALUES (
                :id, :repository, :prompt, :mode, :model, :reasoning_effort,
                :status, :phase, :created_at, :updated_at
            )""",
            job,
        )
        connection.execute(
            """INSERT INTO agent_events (job_id, created_at, kind, message)
            VALUES (?, ?, 'queued', ?)""",
            (
                job["id"],
                now,
                "要件の深掘りを受け付けました"
                if mode == "requirements"
                else "タスクを受け付けました",
            ),
        )
    return job


def list_jobs(path: Path, limit: int = 50) -> list[dict[str, Any]]:
    initialize_database(path)
    with connect_database(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT * FROM agent_jobs WHERE hidden_at IS NULL
            ORDER BY CASE status
                WHEN 'running' THEN 0 WHEN 'blocked' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,
                created_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        jobs = [dict(row) for row in rows]
        for job in jobs:
            if job["status"] not in LIVE_STATES:
                continue
            events = connection.execute(
                """SELECT created_at, kind, message FROM agent_events
                WHERE job_id = ? ORDER BY id DESC LIMIT 3""",
                (job["id"],),
            ).fetchall()
            job["recent_events"] = [dict(event) for event in reversed(events)]
    return jobs


def list_archived_jobs(
    path: Path, limit: int = 50, now: datetime | None = None
) -> list[dict[str, Any]]:
    """List archived jobs that are still within the seven-day retention period."""
    initialize_database(path)
    cutoff = (now or datetime.now(UTC)) - ARCHIVE_RETENTION
    with connect_database(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """SELECT * FROM agent_jobs WHERE hidden_at > ?
            ORDER BY hidden_at DESC LIMIT ?""",
            (cutoff.isoformat(), limit),
        ).fetchall()
        jobs = [dict(row) for row in rows]
        for job in jobs:
            # Archived cards only show metadata. Full history remains available
            # through get_job() when a conversation is opened.
            if job["status"] not in LIVE_STATES:
                continue
            events = connection.execute(
                """SELECT created_at, kind, message FROM agent_events
                WHERE job_id = ? ORDER BY id DESC LIMIT 3""",
                (job["id"],),
            ).fetchall()
            job["recent_events"] = [dict(event) for event in reversed(events)]
    return jobs


def list_expired_archived_jobs(
    path: Path, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Return dormant final-state archives whose resources may now be removed."""
    initialize_database(path)
    cutoff = (now or datetime.now(UTC)) - ARCHIVE_RETENTION
    final_states = sorted(FINAL_STATES)
    placeholders = ", ".join("?" for _ in final_states)
    with connect_database(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            f"""SELECT * FROM agent_jobs
            WHERE hidden_at <= ? AND status IN ({placeholders})
            ORDER BY hidden_at""",  # noqa: S608
            (cutoff.isoformat(), *final_states),
        ).fetchall()
    return [dict(row) for row in rows]


def delete_expired_archived_job(
    path: Path,
    job_id: str,
    hidden_at: str,
    *,
    before_delete: Callable[[], None] | None = None,
) -> bool:
    """Delete an unchanged archive, cleaning external resources in the same lock."""
    initialize_database(path)
    final_states = sorted(FINAL_STATES)
    placeholders = ", ".join("?" for _ in final_states)
    with connect_database(path, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            f"""SELECT id FROM agent_jobs
            WHERE id = ? AND hidden_at = ? AND status IN ({placeholders})""",  # noqa: S608
            (job_id, hidden_at, *final_states),
        ).fetchone()
        if row is None:
            connection.commit()
            return False
        if before_delete is not None:
            before_delete()
        connection.execute("DELETE FROM agent_events WHERE job_id = ?", (job_id,))
        connection.execute("DELETE FROM agent_instructions WHERE job_id = ?", (job_id,))
        connection.execute("DELETE FROM agent_jobs WHERE id = ?", (job_id,))
    return True


def get_job(path: Path, job_id: str) -> dict[str, Any] | None:
    initialize_database(path)
    with connect_database(path) as connection:
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
    with connect_database(path) as connection:
        row = connection.execute(
            "SELECT status FROM agent_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row[0] in FINAL_STATES:
            return False
        if row[0] == "queued":
            connection.execute(
                """UPDATE agent_jobs SET status='cancelled', phase='キャンセル済み',
                cancel_requested=1, updated_at=?, hidden_at=NULL, finished_at=? WHERE id=?""",
                (now, now, job_id),
            )
        else:
            connection.execute(
                """UPDATE agent_jobs SET cancel_requested=1, phase='停止処理中',
                updated_at=?, hidden_at=NULL WHERE id=?""",
                (now, job_id),
            )
        connection.execute(
            """INSERT INTO agent_events (job_id, created_at, kind, message)
            VALUES (?, ?, 'cancel', '停止が要求されました')""",
            (job_id, now),
        )
    return True


def hide_job(path: Path, job_id: str) -> bool:
    """Archive a job until its next update or the retention period expires."""
    if not isinstance(job_id, str) or not job_id:
        return False
    # The worker can legitimately hold a write transaction while updating the same
    # job. Match its 30-second lock wait instead of dropping the HTTP connection
    # after the default five seconds.
    initialize_database(path, timeout=30)
    with connect_database(path, timeout=30) as connection:
        cursor = connection.execute(
            "UPDATE agent_jobs SET hidden_at = ? WHERE id = ?",
            (_now(), job_id),
        )
    return cursor.rowcount == 1


def attach_to_job(path: Path, job_id: str, instruction: object) -> bool:
    if not isinstance(instruction, str) or not instruction.strip() or len(instruction) > 10_000:
        return False
    initialize_database(path)
    now = _now()
    with connect_database(path) as connection:
        row = connection.execute(
            "SELECT status, worktree, follow_up FROM agent_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or row[0] == "cancelled":
            return False
        if row[0] == "failed" and not row[1]:
            return False
        completed_follow_up = row[0] == "completed" or bool(row[2])
        next_status = (
            "queued" if row[0] in {"blocked", "failed", "completed"} else row[0]
        )
        next_phase = None
        if next_status == "queued" and row[0] != "queued":
            next_phase = "確認待ち" if completed_follow_up else "再開待ち"
        connection.execute(
            """UPDATE agent_jobs SET status=?, phase=COALESCE(?, phase),
            cancel_requested=0, follow_up=?, finished_at=NULL, updated_at=?, hidden_at=NULL
            WHERE id=?""",
            (next_status, next_phase, completed_follow_up, now, job_id),
        )
        connection.execute(
            """INSERT INTO agent_instructions (job_id, created_at, instruction)
            VALUES (?, ?, ?)""",
            (job_id, now, instruction.strip()),
        )
        connection.execute(
            """INSERT INTO agent_events (job_id, created_at, kind, message)
            VALUES (?, ?, 'user', ?)""",
            (job_id, now, instruction.strip()),
        )
    return True


def resume_job(path: Path, job_id: str, instruction: object) -> bool:
    """Backward-compatible name for attaching a user instruction to a job."""
    return attach_to_job(path, job_id, instruction)


def take_pending_instructions(path: Path, job_id: str) -> list[str]:
    initialize_database(path)
    now = _now()
    with connect_database(path, timeout=30) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """SELECT id, instruction FROM agent_instructions
            WHERE job_id = ? AND delivered_at IS NULL ORDER BY id""",
            (job_id,),
        ).fetchall()
        if rows:
            connection.executemany(
                "UPDATE agent_instructions SET delivered_at = ? WHERE id = ?",
                ((now, row["id"]) for row in rows),
            )
        connection.commit()
    return [row["instruction"] for row in rows]


def claim_next_job(path: Path) -> dict[str, Any] | None:
    initialize_database(path)
    now = _now()
    with connect_database(path, timeout=30) as connection:
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
            updated_at=?, hidden_at=NULL WHERE id=?""",
            (now, row["id"]),
        )
        connection.commit()
    return dict(row)


def recover_interrupted_jobs(path: Path) -> int:
    """Requeue jobs left running by a previous worker process."""
    initialize_database(path)
    now = _now()
    message = "Agentワーカーの再起動により処理が中断されたため、自動で再試行します。"
    with connect_database(path, timeout=30) as connection:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            "SELECT id FROM agent_jobs WHERE status='running'"
        ).fetchall()
        if not rows:
            connection.commit()
            return 0
        job_ids = [row[0] for row in rows]
        connection.executemany(
            """UPDATE agent_jobs SET status='queued', phase='ワーカー再起動のため再試行待ち',
            summary=?, updated_at=?, hidden_at=NULL, finished_at=NULL WHERE id=?""",
            ((message, now, job_id) for job_id in job_ids),
        )
        connection.executemany(
            """INSERT INTO agent_events (job_id, created_at, kind, message)
            VALUES (?, ?, 'retrying', ?)""",
            ((job_id, now, message) for job_id in job_ids),
        )
        connection.commit()
    return len(job_ids)


def update_job(path: Path, job_id: str, **fields: object) -> None:
    allowed = {
        "status",
        "phase",
        "summary",
        "completion_summary",
        "thread_id",
        "branch",
        "worktree",
        "attempts",
        "cancel_requested",
        "follow_up",
        "finished_at",
    }
    values = {name: value for name, value in fields.items() if name in allowed}
    if not values:
        return
    values["updated_at"] = _now()
    values["hidden_at"] = None
    assignments = ", ".join(f"{name} = ?" for name in values)
    initialize_database(path)
    with connect_database(path) as connection:
        connection.execute(
            f"UPDATE agent_jobs SET {assignments} WHERE id = ?",  # noqa: S608
            (*values.values(), job_id),
        )


def append_event(path: Path, job_id: str, kind: str, message: str) -> None:
    initialize_database(path)
    now = _now()
    with connect_database(path) as connection:
        connection.execute(
            """INSERT INTO agent_events (job_id, created_at, kind, message)
            VALUES (?, ?, ?, ?)""",
            (job_id, now, kind, message[:20_000]),
        )
        connection.execute(
            "UPDATE agent_jobs SET updated_at = ?, hidden_at = NULL WHERE id = ?",
            (now, job_id),
        )
