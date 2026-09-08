import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event

import pytest

from daily_reader.agent_jobs import (
    append_event,
    attach_to_job,
    claim_next_job,
    connect_database,
    create_job,
    delete_expired_archived_job,
    get_job,
    hide_job,
    list_archived_jobs,
    list_expired_archived_jobs,
    list_jobs,
    load_repositories,
    recover_interrupted_jobs,
    request_cancel,
    resume_job,
    take_pending_instructions,
    update_job,
)


class FakeConnection:
    def __init__(self) -> None:
        self.exited_with: type[BaseException] | None = None
        self.closed = False

    def __enter__(self):
        return self

    def __exit__(self, error_type, _error, _traceback) -> None:
        self.exited_with = error_type

    def close(self) -> None:
        self.closed = True


def test_connect_database_closes_after_success(monkeypatch, tmp_path: Path) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: connection)

    with connect_database(tmp_path / "agent.sqlite3") as opened:
        assert opened is connection

    assert connection.exited_with is None
    assert connection.closed


def test_connect_database_closes_after_error(monkeypatch, tmp_path: Path) -> None:
    connection = FakeConnection()
    monkeypatch.setattr(sqlite3, "connect", lambda *_args, **_kwargs: connection)

    with (
        pytest.raises(RuntimeError, match="failure"),
        connect_database(tmp_path / "agent.sqlite3"),
    ):
        raise RuntimeError("failure")

    assert connection.exited_with is RuntimeError
    assert connection.closed


def test_archive_cleanup_does_not_block_other_jobs(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    archived = create_job(database, configured, {"repository": "repo", "prompt": "Old job"})
    active = create_job(database, configured, {"repository": "repo", "prompt": "Live job"})
    update_job(database, archived["id"], status="failed")
    hide_job(database, archived["id"])
    hidden_at = get_job(database, archived["id"])["hidden_at"]
    entered = Event()
    release = Event()

    def slow_cleanup():
        entered.set()
        assert release.wait(15)

    with ThreadPoolExecutor(max_workers=2) as pool:
        cleanup = pool.submit(
            delete_expired_archived_job, database, archived["id"], hidden_at,
            before_delete=slow_cleanup,
        )
        try:
            assert entered.wait(5)
            write = pool.submit(append_event, database, active["id"], "progress", "Working")
            write.result(timeout=7)
            assert get_job(database, active["id"])["events"][-1]["message"] == "Working"
        finally:
            release.set()
        assert cleanup.result(timeout=5)


def repositories(tmp_path: Path) -> dict[str, dict[str, str]]:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    config = tmp_path / "config" / "agent-repositories.toml"
    config.parent.mkdir()
    config.write_text(
        """[[repositories]]
name = "repo"
label = "Repository"
path = "repo"
default_branch = "main"
""",
        encoding="utf-8",
    )
    return load_repositories(config)


def test_agent_job_lifecycle(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    job = create_job(
        database,
        configured,
        {"repository": "repo", "prompt": "Implement the requested change"},
    )

    assert job["model"] == "gpt-6-astra"
    assert job["reasoning_effort"] == "low"
    assert job["status"] == "queued"
    listed = list_jobs(database)[0]
    assert listed["id"] == job["id"]
    assert listed["recent_events"] == [
        {
            "created_at": job["created_at"],
            "kind": "queued",
            "message": "タスクを受け付けました",
        }
    ]
    claimed = claim_next_job(database)
    assert claimed is not None
    assert claimed["id"] == job["id"]
    assert claim_next_job(database) is None

    update_job(database, job["id"], phase="Codex実行中", attempts=1)
    stored = get_job(database, job["id"])
    assert stored is not None
    assert stored["status"] == "running"
    assert stored["phase"] == "Codex実行中"
    assert stored["attempts"] == 1
    assert stored["events"][0]["kind"] == "queued"


def test_running_jobs_are_requeued_after_worker_restart(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    job = create_job(
        database,
        repositories(tmp_path),
        {"repository": "repo", "prompt": "Resume this task"},
    )
    claim_next_job(database)
    update_job(
        database,
        job["id"],
        phase="Codex実行中",
        worktree=str(tmp_path / "worktree"),
    )
    hide_job(database, job["id"])

    assert recover_interrupted_jobs(database) == 1
    assert recover_interrupted_jobs(database) == 0

    stored = get_job(database, job["id"])
    assert stored is not None
    assert stored["status"] == "queued"
    assert stored["phase"] == "ワーカー再起動のため再試行待ち"
    assert stored["finished_at"] is None
    assert stored["hidden_at"] is None
    assert stored["events"][-1]["kind"] == "retrying"
    assert "自動で再試行します" in stored["summary"]

    retried = claim_next_job(database)
    assert retried is not None
    assert retried["id"] == job["id"]
    assert retried["worktree"] == str(tmp_path / "worktree")


def test_hidden_job_returns_to_list_after_an_update(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    job = create_job(
        database,
        repositories(tmp_path),
        {"repository": "repo", "prompt": "Hide this task"},
    )

    assert hide_job(database, job["id"])
    assert list_jobs(database) == []
    assert [item["id"] for item in list_archived_jobs(database)] == [job["id"]]
    assert get_job(database, job["id"]) is not None

    update_job(database, job["id"], phase="更新されました")

    assert [item["id"] for item in list_jobs(database)] == [job["id"]]

    assert hide_job(database, job["id"])
    append_event(database, job["id"], "progress", "新しい進捗")

    assert [item["id"] for item in list_jobs(database)] == [job["id"]]


def test_hide_job_rejects_unknown_job(tmp_path: Path) -> None:
    assert not hide_job(tmp_path / "agent.sqlite3", "missing")


def test_archived_job_is_deleted_after_seven_days(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    job = create_job(
        database,
        repositories(tmp_path),
        {"repository": "repo", "prompt": "Delete this archive later"},
    )
    assert hide_job(database, job["id"])
    archived_at = datetime(2026, 8, 1, tzinfo=UTC)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agent_jobs SET hidden_at = ? WHERE id = ?",
            (archived_at.isoformat(), job["id"]),
        )
        connection.execute(
            """INSERT INTO agent_instructions (job_id, created_at, instruction)
            VALUES (?, ?, 'pending')""",
            (job["id"], archived_at.isoformat()),
        )

    assert list_archived_jobs(database, now=archived_at + timedelta(days=6))
    assert list_archived_jobs(database, now=archived_at + timedelta(days=7)) == []
    assert list_expired_archived_jobs(
        database, now=archived_at + timedelta(days=7)
    ) == []
    assert get_job(database, job["id"]) is not None

    update_job(database, job["id"], status="failed")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agent_jobs SET hidden_at = ? WHERE id = ?",
            (archived_at.isoformat(), job["id"]),
        )
    expired = list_expired_archived_jobs(
        database, now=archived_at + timedelta(days=7)
    )
    assert [item["id"] for item in expired] == [job["id"]]
    assert delete_expired_archived_job(
        database, job["id"], archived_at.isoformat()
    )
    assert get_job(database, job["id"]) is None
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM agent_events").fetchone()[0] == 0
        assert connection.execute("SELECT count(*) FROM agent_instructions").fetchone()[0] == 0


def test_agent_job_validates_repository_and_prompt(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)

    with pytest.raises(ValueError, match="repository"):
        create_job(database, configured, {"repository": "missing", "prompt": "x"})
    with pytest.raises(ValueError, match="prompt"):
        create_job(database, configured, {"repository": "repo", "prompt": ""})
    with pytest.raises(ValueError, match="mode"):
        create_job(
            database,
            configured,
            {"repository": "repo", "prompt": "x", "mode": "unknown"},
        )


def test_agent_job_validates_effort_against_selected_model(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    model_options = [
        {
            "slug": "gpt-5.6-sol",
            "supported_reasoning_efforts": ["low", "max", "ultra"],
        },
        {
            "slug": "gpt-5.6-luna",
            "supported_reasoning_efforts": ["low", "max"],
        },
    ]

    job = create_job(
        database,
        configured,
        {
            "repository": "repo",
            "prompt": "Use the strongest supported mode",
            "model": "gpt-5.6-sol",
            "reasoning_effort": "ultra",
        },
        model_options=model_options,
    )

    assert job["model"] == "gpt-5.6-sol"
    assert job["reasoning_effort"] == "ultra"
    with pytest.raises(ValueError, match="model or reasoning effort"):
        create_job(
            database,
            configured,
            {
                "repository": "repo",
                "prompt": "Use an unsupported mode",
                "model": "gpt-5.6-luna",
                "reasoning_effort": "ultra",
            },
            model_options=model_options,
        )


def test_requirements_job_persists_mode_and_distinct_event(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)

    job = create_job(
        database,
        configured,
        {"repository": "repo", "prompt": "Build it", "mode": "requirements"},
    )

    stored = get_job(database, job["id"])
    assert stored is not None
    assert stored["mode"] == "requirements"
    assert stored["events"][0]["message"] == "要件の深掘りを受け付けました"


def test_existing_agent_database_is_migrated_with_execute_mode(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE agent_jobs (
            id TEXT PRIMARY KEY, repository TEXT NOT NULL, prompt TEXT NOT NULL,
            status TEXT NOT NULL, phase TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
            thread_id TEXT, branch TEXT, worktree TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, finished_at TEXT
            )"""
        )

    create_job(
        database,
        repositories(tmp_path),
        {"repository": "repo", "prompt": "New task"},
    )

    assert list_jobs(database)[0]["mode"] == "execute"
    assert list_jobs(database)[0]["completion_summary"] == ""


def test_existing_completed_job_backfills_completion_summary(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """CREATE TABLE agent_jobs (
            id TEXT PRIMARY KEY, repository TEXT NOT NULL, prompt TEXT NOT NULL,
            status TEXT NOT NULL, phase TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '',
            thread_id TEXT, branch TEXT, worktree TEXT, attempts INTEGER NOT NULL DEFAULT 0,
            cancel_requested INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL, finished_at TEXT
            )"""
        )
        connection.execute(
            """INSERT INTO agent_jobs
            (id, repository, prompt, status, phase, summary, created_at, updated_at)
            VALUES ('job', 'repo', 'Task', 'completed', '完了', 'Original', 'a', 'b')"""
        )

    from daily_reader.agent_jobs import initialize_database

    initialize_database(database)
    stored = get_job(database, "job")
    assert stored is not None
    assert stored["completion_summary"] == "Original"


def test_queued_job_can_be_cancelled(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    job = create_job(database, configured, {"repository": "repo", "prompt": "Do it"})

    assert request_cancel(database, job["id"])
    stored = get_job(database, job["id"])
    assert stored is not None
    assert stored["status"] == "cancelled"
    assert stored["cancel_requested"] == 1
    assert claim_next_job(database) is None


def test_repository_configuration_rejects_duplicate_names(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    config = tmp_path / "config" / "agent-repositories.toml"
    config.parent.mkdir()
    config.write_text(
        """[[repositories]]
name = "repo"
label = "One"
path = "repo"
[[repositories]]
name = "repo"
label = "Two"
path = "repo"
""",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="duplicate"):
        load_repositories(config)


def test_repository_configuration_supports_multiple_checkouts_in_home(
    tmp_path: Path, monkeypatch
) -> None:
    home = tmp_path / "home"
    first = home / "repos" / "first"
    second = home / "repos" / "second"
    for repository in (first, second):
        repository.mkdir(parents=True)
        (repository / ".git").mkdir()
    monkeypatch.setenv("HOME", str(home))
    config = tmp_path / "config" / "agent-repositories.toml"
    config.parent.mkdir()
    config.write_text(
        """[[repositories]]
name = "first"
label = "First"
path = "~/repos/first"
[[repositories]]
name = "second"
label = "Second"
path = "~/repos/second"
""",
        encoding="utf-8",
    )

    configured = load_repositories(config)

    assert list(configured) == ["first", "second"]
    assert configured["first"]["path"] == str(first)
    assert configured["second"]["path"] == str(second)
    assert configured["first"]["deploy"] is True


def test_repository_configuration_can_skip_deployment(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    (repository / ".git").mkdir()
    config = tmp_path / "config" / "agent-repositories.toml"
    config.parent.mkdir()
    config.write_text(
        """[[repositories]]
name = "repo"
label = "Repository"
path = "repo"
deploy = false
""",
        encoding="utf-8",
    )

    assert load_repositories(config)["repo"]["deploy"] is False


def test_blocked_job_can_resume_with_user_instruction(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    job = create_job(database, configured, {"repository": "repo", "prompt": "Do it"})
    claim_next_job(database)
    update_job(database, job["id"], status="blocked", phase="判断待ち")

    assert resume_job(database, job["id"], "Use the compatible format")
    resumed = claim_next_job(database)
    assert resumed is not None
    assert take_pending_instructions(database, job["id"]) == [
        "Use the compatible format"
    ]
    update_job(database, job["id"], status="completed")
    assert resume_job(database, job["id"], "What changed?")
    follow_up = claim_next_job(database)
    assert follow_up is not None
    assert follow_up["follow_up"] == 1
    assert take_pending_instructions(database, job["id"]) == ["What changed?"]


def test_completed_job_follow_up_preserves_summary(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    job = create_job(
        database,
        repositories(tmp_path),
        {"repository": "repo", "prompt": "Do it"},
    )
    claim_next_job(database)
    update_job(
        database,
        job["id"],
        status="completed",
        phase="完了",
        summary="Implemented and verified the feature",
    )

    assert attach_to_job(database, job["id"], "How was it verified?")

    stored = get_job(database, job["id"])
    assert stored is not None
    assert stored["status"] == "queued"
    assert stored["phase"] == "確認待ち"
    assert stored["summary"] == "Implemented and verified the feature"
    assert stored["follow_up"] == 1
    assert stored["events"][-1]["kind"] == "user"


def test_running_job_accepts_attached_messages_in_order(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    job = create_job(database, configured, {"repository": "repo", "prompt": "Do it"})
    claim_next_job(database)

    assert attach_to_job(database, job["id"], "First addition")
    assert attach_to_job(database, job["id"], "Second addition")
    assert take_pending_instructions(database, job["id"]) == [
        "First addition",
        "Second addition",
    ]
    assert take_pending_instructions(database, job["id"]) == []
    stored = get_job(database, job["id"])
    assert stored is not None
    assert stored["status"] == "running"
    assert [event["message"] for event in stored["events"] if event["kind"] == "user"] == [
        "First addition",
        "Second addition",
    ]
    assert [event["message"] for event in list_jobs(database)[0]["recent_events"]] == [
        "タスクを受け付けました",
        "First addition",
        "Second addition",
    ]


def test_final_jobs_omit_recent_events_from_list_payload(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    job = create_job(database, configured, {"repository": "repo", "prompt": "Do it"})
    claim_next_job(database)
    update_job(database, job["id"], status="completed", phase="完了", summary="Done")

    listed = list_jobs(database)[0]

    assert "recent_events" not in listed


def expired_job(database: Path, configured: dict) -> dict:
    job = create_job(database, configured, {"repository": "repo", "prompt": "Old archive"})
    update_job(database, job["id"], status="failed", worktree="/retained/worktree")
    with connect_database(database) as connection:
        connection.execute(
            "UPDATE agent_jobs SET hidden_at=? WHERE id=?", ("2020-01-01T00:00:00+00:00", job["id"])
        )
    return get_job(database, job["id"])


@pytest.mark.parametrize("mutation", [
    lambda db, job: attach_to_job(db, job, "resume"),
    lambda db, job: resume_job(db, job, "resume"),
    lambda db, job: hide_job(db, job),
    lambda db, job: request_cancel(db, job),
    lambda db, job: update_job(db, job, status="running"),
    lambda db, job: append_event(db, job, "progress", "late event"),
    lambda db, job: take_pending_instructions(db, job),
])
def test_partial_cleanup_blocks_mutation_but_can_be_retried(tmp_path: Path, mutation) -> None:
    from daily_reader.agent_jobs import ArchiveCleanupInProgress

    database = tmp_path / "agent.sqlite3"
    job = expired_job(database, repositories(tmp_path))

    def partial_cleanup():
        raise OSError("branch cleanup failed after worktree removal")

    with pytest.raises(OSError, match="branch cleanup failed"):
        delete_expired_archived_job(database, job["id"], job["hidden_at"],
                                    before_delete=partial_cleanup)
    assert get_job(database, job["id"])["cleanup_pending"] == 1
    with pytest.raises(ArchiveCleanupInProgress):
        mutation(database, job["id"])
    assert get_job(database, job["id"])["status"] == "failed"
    assert delete_expired_archived_job(database, job["id"], job["hidden_at"])
    append_event(database, job["id"], "progress", "after deletion")
    with connect_database(database) as connection:
        assert connection.execute("SELECT count(*) FROM agent_events").fetchone()[0] == 0


def test_resumed_archive_cannot_be_deleted_from_stale_snapshot(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    job = expired_job(database, repositories(tmp_path))
    assert attach_to_job(database, job["id"], "resume")
    cleaned = []
    assert not delete_expired_archived_job(database, job["id"], job["hidden_at"],
                                          before_delete=lambda: cleaned.append(True))
    assert not cleaned
    assert claim_next_job(database)["id"] == job["id"]


def test_process_exit_releases_cleanup_lock_and_preserves_deletion_intent(tmp_path: Path) -> None:
    import subprocess
    import sys

    database = tmp_path / "agent.sqlite3"
    job = expired_job(database, repositories(tmp_path))
    code = """
import os, sys
from pathlib import Path
from daily_reader.agent_jobs import delete_expired_archived_job
delete_expired_archived_job(Path(sys.argv[1]), sys.argv[2], sys.argv[3],
                           before_delete=lambda: os._exit(9))
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(database), job["id"], job["hidden_at"]],
        timeout=30, check=False,
    )
    assert result.returncode == 9
    assert get_job(database, job["id"])["cleanup_pending"] == 1
    # A clock adjustment must not postpone an already committed deletion.
    pending = list_expired_archived_jobs(database, now=datetime(2019, 1, 1, tzinfo=UTC))
    assert pending[0]["id"] == job["id"]
    assert delete_expired_archived_job(database, job["id"], job["hidden_at"])


def test_cleanup_lock_excludes_another_process(tmp_path: Path) -> None:
    import subprocess
    import sys

    database = tmp_path / "agent.sqlite3"
    job = expired_job(database, repositories(tmp_path))

    def competing_cleanup():
        code = """
import sys
from pathlib import Path
from daily_reader.agent_jobs import delete_expired_archived_job
assert not delete_expired_archived_job(Path(sys.argv[1]), sys.argv[2], sys.argv[3],
                                      before_delete=lambda: sys.exit(99))
"""
        subprocess.run([sys.executable, "-c", code, str(database), job["id"], job["hidden_at"]],
                       timeout=30, check=True)

    assert delete_expired_archived_job(database, job["id"], job["hidden_at"],
                                      before_delete=competing_cleanup)


def test_busy_event_write_retries_without_duplicate_or_open_connection(monkeypatch, tmp_path):
    database = tmp_path / "agent.sqlite3"
    job = create_job(database, repositories(tmp_path), {"repository": "repo", "prompt": "Live"})
    real_connect = sqlite3.connect
    failures = []
    opened = []

    class Connection(sqlite3.Connection):
        closed = False

        def execute(self, sql, *args, **kwargs):
            if sql.startswith("UPDATE agent_jobs SET updated_at") and not failures:
                failures.append(True)
                error = sqlite3.OperationalError("database is locked")
                error.sqlite_errorcode = sqlite3.SQLITE_BUSY
                raise error
            return super().execute(sql, *args, **kwargs)

        def close(self):
            self.closed = True
            super().close()

    def connect(*args, **kwargs):
        connection = real_connect(*args, **kwargs, factory=Connection)
        opened.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    append_event(database, job["id"], "progress", "one event")
    assert failures == [True]
    events = get_job(database, job["id"])["events"]
    assert sum(event["message"] == "one event" for event in events) == 1
    assert all(connection.closed for connection in opened)


def test_real_write_lock_is_retried_after_release(monkeypatch, tmp_path):
    database = tmp_path / "agent.sqlite3"
    job = create_job(database, repositories(tmp_path), {"repository": "repo", "prompt": "Live"})
    real_connect = sqlite3.connect
    holder = real_connect(database)
    holder.execute("BEGIN IMMEDIATE")
    releases = []

    def release(_seconds):
        releases.append(True)
        holder.rollback()

    def connect(*args, **kwargs):
        kwargs["timeout"] = 0.01
        return real_connect(*args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr("daily_reader.agent_jobs.sleep", release)
    try:
        assert hide_job(database, job["id"])
        assert releases == [True]
    finally:
        holder.close()
    assert get_job(database, job["id"])["hidden_at"]


@pytest.mark.parametrize(("code", "attempts"), [(sqlite3.SQLITE_BUSY, 3),
                                                 (sqlite3.SQLITE_LOCKED, 3),
                                                 (sqlite3.SQLITE_BUSY_SNAPSHOT, 3),
                                                 (sqlite3.SQLITE_IOERR, 1),
                                                 (sqlite3.SQLITE_ERROR, 1)])
def test_database_retry_is_bounded_and_only_for_contention(monkeypatch, code, attempts):
    from daily_reader.agent_jobs import retry_database_busy

    calls = []
    monkeypatch.setattr("daily_reader.agent_jobs.sleep", lambda _seconds: None)

    @retry_database_busy
    def operation():
        calls.append(True)
        error = sqlite3.OperationalError("failure")
        error.sqlite_errorcode = code
        raise error

    with pytest.raises(sqlite3.OperationalError):
        operation()
    assert len(calls) == attempts


def test_ten_workers_claim_and_record_each_job_once(tmp_path):
    from threading import Barrier

    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    jobs = [create_job(database, configured, {"repository": "repo", "prompt": str(i)})
            for i in range(20)]
    ready = Barrier(10)

    def work(_number):
        ready.wait(timeout=30)
        claimed = []
        while job := claim_next_job(database):
            claimed.append(job["id"])
            append_event(database, job["id"], "progress", "claimed once")
            update_job(database, job["id"], status="completed")
        return claimed

    with ThreadPoolExecutor(max_workers=10) as pool:
        claimed = [job_id for group in pool.map(work, range(10)) for job_id in group]
    assert sorted(claimed) == sorted(job["id"] for job in jobs)
    for job in jobs:
        stored = get_job(database, job["id"])
        assert stored["status"] == "completed"
        assert sum(event["kind"] == "progress" for event in stored["events"]) == 1


def test_legacy_database_migration_is_safe_for_simultaneous_startup(monkeypatch, tmp_path):
    from threading import Barrier

    database = tmp_path / "agent.sqlite3"
    job = create_job(database, repositories(tmp_path), {"repository": "repo", "prompt": "Kept"})
    with connect_database(database) as connection:
        connection.execute("ALTER TABLE agent_jobs DROP COLUMN cleanup_pending")
    real_connect = sqlite3.connect
    inspected = Barrier(2)

    class Connection(sqlite3.Connection):
        inspected_columns = False

        def execute(self, sql, *args, **kwargs):
            cursor = super().execute(sql, *args, **kwargs)
            if sql == "PRAGMA table_info(agent_jobs)" and not self.inspected_columns:
                self.inspected_columns = True
                columns = cursor.fetchall()
                inspected.wait(timeout=15)
                return iter(columns)
            return cursor

    monkeypatch.setattr(sqlite3, "connect",
                        lambda *args, **kwargs: real_connect(*args, **kwargs, factory=Connection))
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _number: get_job(database, job["id"]), range(2)))
    assert all(row["cleanup_pending"] == 0 and row["prompt"] == "Kept" for row in jobs)


def test_failure_state_and_events_roll_back_together(monkeypatch, tmp_path):
    from daily_reader.agent_jobs import record_job_failure

    database = tmp_path / "agent.sqlite3"
    job = create_job(database, repositories(tmp_path), {"repository": "repo", "prompt": "Live"})
    update_job(database, job["id"], status="running")
    real_connect = sqlite3.connect

    class Connection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if "'preserved'" in sql:
                error = sqlite3.OperationalError("disk failure")
                error.sqlite_errorcode = sqlite3.SQLITE_IOERR
                raise error
            return super().execute(sql, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(sqlite3, "connect",
                      lambda *args, **kwargs: real_connect(*args, **kwargs, factory=Connection))
        with pytest.raises(sqlite3.OperationalError, match="disk failure"):
            record_job_failure(database, job["id"], "original error", "/retained/worktree")
    stored = get_job(database, job["id"])
    assert stored["status"] == "running"
    assert not any(event["kind"] == "failed" for event in stored["events"])
    record_job_failure(database, job["id"], "original error", "/retained/worktree")
    stored = get_job(database, job["id"])
    assert stored["status"] == "failed"
    assert stored["summary"] == "original error"
    assert [event["kind"] for event in stored["events"]][-2:] == ["failed", "preserved"]
