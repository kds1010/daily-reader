import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from subprocess import CalledProcessError, CompletedProcess

from daily_reader.agent_jobs import create_job, get_job, hide_job, update_job
from daily_reader.agent_worker import (
    _codex_command,
    _continue_prompt,
    _default_branch_conflict_prompt,
    _deployment_prompt,
    _follow_up_prompt,
    _implementation_prompt,
    _initial_prompt,
    _parse_codex_events,
    build_parser,
    cleanup_expired_archives,
    resolve_schema_path,
    run_deployment_turn,
    sync_default_worktree,
)


def repositories(tmp_path: Path) -> dict[str, dict[str, str]]:
    repository = tmp_path / "repo"
    repository.mkdir()
    return {
        "repo": {
            "name": "repo",
            "path": str(repository),
            "default_branch": "main",
        }
    }


def test_agent_worker_defaults(monkeypatch) -> None:
    monkeypatch.setattr("sys.argv", ["daily-reader-agent-worker"])

    args = build_parser().parse_args()

    assert args.database == Path("data/agent.sqlite3")
    assert args.repositories == Path("config/agent-repositories.toml")
    assert args.schema == Path("config/agent-result-schema.json")
    assert args.poll_seconds == 5
    assert args.max_workers == 10
    assert not args.once


def test_agent_worker_accepts_configured_parallelism(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv", ["daily-reader-agent-worker", "--max-workers", "4"]
    )

    args = build_parser().parse_args()

    assert args.max_workers == 4


def test_expired_archive_removes_worktree_before_database_history(
    monkeypatch, tmp_path: Path
) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    job = create_job(
        database,
        configured,
        {"repository": "repo", "prompt": "Eventually discard this task"},
    )
    worktree = tmp_path / "worktree"
    update_job(
        database,
        job["id"],
        status="failed",
        branch="codex/web-task",
        worktree=str(worktree),
    )
    hide_job(database, job["id"])
    archived_at = datetime(2026, 8, 1, tzinfo=UTC)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agent_jobs SET hidden_at = ? WHERE id = ?",
            (archived_at.isoformat(), job["id"]),
        )
    cleaned = []
    monkeypatch.setattr(
        "daily_reader.agent_worker.cleanup_archived_worktree",
        lambda repository, branch, path: cleaned.append((repository, branch, path)),
    )

    assert cleanup_expired_archives(
        database, configured, now=archived_at + timedelta(days=7)
    ) == 1
    assert cleaned == [(configured["repo"], "codex/web-task", worktree)]
    assert get_job(database, job["id"]) is None


def test_expired_archive_is_retained_when_worktree_cleanup_fails(
    monkeypatch, tmp_path: Path
) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    job = create_job(
        database,
        configured,
        {"repository": "repo", "prompt": "Keep history if cleanup fails"},
    )
    update_job(
        database,
        job["id"],
        status="failed",
        branch="codex/web-task",
        worktree=str(tmp_path / "worktree"),
    )
    hide_job(database, job["id"])
    archived_at = datetime(2026, 8, 1, tzinfo=UTC)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agent_jobs SET hidden_at = ? WHERE id = ?",
            (archived_at.isoformat(), job["id"]),
        )

    def fail_cleanup(*_args) -> None:
        raise CalledProcessError(1, ["git", "worktree", "remove"])

    monkeypatch.setattr(
        "daily_reader.agent_worker.cleanup_archived_worktree", fail_cleanup
    )

    assert cleanup_expired_archives(
        database, configured, now=archived_at + timedelta(days=7)
    ) == 0
    assert get_job(database, job["id"]) is not None


def test_schema_path_is_resolved_before_using_external_worktree(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    schema = Path("config/agent-result-schema.json")

    assert resolve_schema_path(schema) == tmp_path / schema
    assert resolve_schema_path(schema).is_absolute()


def test_parse_codex_events_extracts_thread_and_messages() -> None:
    output = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "thread-1"}),
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "agent_message", "text": "Implemented the change"},
                }
            ),
            "not-json",
        ]
    )

    thread_id, messages = _parse_codex_events(output)

    assert thread_id == "thread-1"
    assert messages == ["Implemented the change"]


def test_initial_codex_command_uses_automatic_workspace_approval() -> None:
    command = _codex_command(
        Path("/tmp/worktree"),
        Path("schema.json"),
        "Complete the task",
        None,
        Path("result.json"),
    )

    assert "--approve-for-me" in command
    assert "--sandbox" not in command


def test_resumed_codex_command_can_switch_to_low_cost_model() -> None:
    command = _codex_command(
        Path("/tmp/worktree"),
        Path("schema.json"),
        "Implement the task",
        "thread-1",
        Path("result.json"),
        "gpt-5.6-luna",
        "low",
    )

    assert command[0:3] == ["codex", "exec", "resume"]
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="low"' in command


def test_resumed_codex_command_passes_ultra_for_supported_model() -> None:
    command = _codex_command(
        Path("/tmp/worktree"),
        Path("schema.json"),
        "Implement the task",
        "thread-1",
        Path("result.json"),
        "gpt-5.6-sol",
        "ultra",
    )

    assert command[command.index("--model") + 1] == "gpt-5.6-sol"
    assert 'model_reasoning_effort="ultra"' in command


def test_deployment_starts_fresh_automatic_approval_session(monkeypatch) -> None:
    calls = []

    def fake_run_codex_turn(worktree, schema, prompt, thread_id):
        calls.append((worktree, schema, prompt, thread_id))
        return "deployment-thread", {"state": "done"}, "deployed"

    monkeypatch.setattr(
        "daily_reader.agent_worker.run_codex_turn", fake_run_codex_turn
    )

    result = run_deployment_turn(Path("/tmp/worktree"), Path("schema.json"), "Deploy")

    assert calls == [(Path("/tmp/worktree"), Path("schema.json"), "Deploy", None)]
    assert result[0] == "deployment-thread"


def test_requirements_prompt_requires_discovery_before_implementation() -> None:
    prompt = _initial_prompt("Add a better workflow", "requirements")

    assert "do not change files" in prompt
    assert "return state=blocked" in prompt
    assert "After the user answers" in prompt


def test_execute_prompt_separates_planning_from_implementation() -> None:
    prompt = _initial_prompt("Add a better workflow")

    assert "Do not change files or commit in this turn" in prompt
    assert "Return state=continue" in prompt
    assert "lower-cost implementation model" in prompt

    implementation = _implementation_prompt(
        {"summary": "Update the worker", "next_action": "Edit and test"}
    )
    assert "Update the worker" in implementation
    assert "Edit and test" in implementation
    assert "Return state=done only after" in implementation


def test_execution_prompts_retry_xcode_failures_with_approval_escalation() -> None:
    implementation = _implementation_prompt(
        {"summary": "Update the app", "next_action": "Build it"}
    )
    continuation = _continue_prompt(
        {"summary": "Build failed", "next_action": "Retry it"}
    )
    deployment = _deployment_prompt("abc123")

    for prompt in (implementation, continuation, deployment):
        assert "CoreSimulatorService" in prompt
        assert "approval escalation mechanism" in prompt
        assert "Do not repeat an unchanged sandboxed command" in prompt


def test_deployment_prompt_requires_live_verification_before_done() -> None:
    prompt = _deployment_prompt("abc123")

    assert "integrated and pushed commit abc123" in prompt
    assert "Read all applicable AGENTS.md deployment instructions again" in prompt
    assert "Return state=done only after deployment and live verification succeed" in prompt
    assert "runtime changes remain undeployed or unverified" in prompt
    assert "already restarted onto this pushed commit" in prompt
    assert "do not restart it again" in prompt
    assert "launches duplicate Codex sessions against the same worktree" in prompt
    assert "publishing and verifying" in prompt
    assert "device installation is a separate user" in prompt
    assert "operation, return done after the distribution checks pass" in prompt
    assert "absence of a connected physical device" in prompt


def test_default_branch_conflict_prompt_requires_rebase_verification() -> None:
    prompt = _default_branch_conflict_prompt("main")

    assert "rebase onto origin/main" in prompt
    assert "without discarding either" in prompt
    assert "local commits or unrelated upstream changes" in prompt
    assert "rerun the relevant" in prompt
    assert "verification. Leave the checkout clean" in prompt
    assert "Do not push" in prompt


def test_sync_default_worktree_rebases_when_fast_forward_fails(
    monkeypatch, tmp_path: Path
) -> None:
    calls = []

    def fake_run_command(command, cwd, *, check=True):
        calls.append((command, cwd, check))
        if command[:3] == ["git", "rev-parse", "origin/main"]:
            return CompletedProcess(command, 0, "remote-head\n", "")
        if command[:3] == ["git", "branch", "--show-current"]:
            return CompletedProcess(command, 0, "main\n", "")
        if command[:3] == ["git", "merge", "--ff-only"]:
            return CompletedProcess(command, 128, "", "not a fast-forward")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("daily_reader.agent_worker.run_command", fake_run_command)
    repository = {"path": str(tmp_path), "default_branch": "main"}

    assert sync_default_worktree(repository, "task-commit") == "rebased"
    assert (["git", "rebase", "origin/main"], tmp_path, False) in calls


def test_sync_default_worktree_reports_rebase_conflict(
    monkeypatch, tmp_path: Path
) -> None:
    def fake_run_command(command, cwd, *, check=True):
        if command[:3] == ["git", "rev-parse", "origin/main"]:
            return CompletedProcess(command, 0, "remote-head\n", "")
        if command[:3] == ["git", "branch", "--show-current"]:
            return CompletedProcess(command, 0, "main\n", "")
        if command[:2] == ["git", "rebase"]:
            return CompletedProcess(command, 1, "", "conflict")
        if command[:3] == ["git", "merge", "--ff-only"]:
            return CompletedProcess(command, 128, "", "not a fast-forward")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("daily_reader.agent_worker.run_command", fake_run_command)
    repository = {"path": str(tmp_path), "default_branch": "main"}

    assert sync_default_worktree(repository, "task-commit") == "conflict"


def test_sync_default_worktree_skips_dirty_checkout(
    monkeypatch, tmp_path: Path
) -> None:
    calls = []

    def fake_run_command(command, cwd, *, check=True):
        calls.append(command)
        if command[:3] == ["git", "branch", "--show-current"]:
            return CompletedProcess(command, 0, "main\n", "")
        if command[:3] == ["git", "rev-parse", "origin/main"]:
            return CompletedProcess(command, 0, "remote-head\n", "")
        if command[:3] == ["git", "status", "--porcelain"]:
            return CompletedProcess(command, 0, " M generated.json\n", "")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("daily_reader.agent_worker.run_command", fake_run_command)
    repository = {"path": str(tmp_path), "default_branch": "main"}

    assert sync_default_worktree(repository, "task-commit") == "dirty"
    assert not any(command[:2] == ["git", "merge"] for command in calls)
    assert not any(command[:2] == ["git", "rebase"] for command in calls)


def test_follow_up_prompt_is_read_only_and_uses_completion_context() -> None:
    prompt = _follow_up_prompt(
        "Add task summaries",
        "Added a completion summary and verified the UI",
        "Previous answer",
        ["Which checks ran?"],
    )

    assert "Add task summaries" in prompt
    assert "Added a completion summary and verified the UI" in prompt
    assert "Previous answer" in prompt
    assert "Which checks ran?" in prompt
    assert "read-only confirmation conversation" in prompt
    assert "do not edit files" in prompt


def busy_error():
    error = sqlite3.OperationalError("database is locked")
    error.sqlite_errorcode = sqlite3.SQLITE_BUSY
    return error


def test_archive_cleanup_is_nonblocking_and_backs_off(monkeypatch, tmp_path):
    import daily_reader.agent_worker as worker

    database = tmp_path / "agent.sqlite3"
    job = {"id": "old", "repository": "repo", "hidden_at": "2020-01-01"}
    clock = [100.0]
    calls = []
    scans = []
    monkeypatch.setattr(worker, "monotonic", lambda: clock[0])
    monkeypatch.setattr(worker, "list_expired_archived_jobs",
                        lambda *_args, **_kwargs: scans.append(True) or [job])

    def fail(*_args, **_kwargs):
        calls.append(clock[0])
        raise OSError("residual files retained")

    monkeypatch.setattr(worker, "delete_expired_archived_job", fail)
    configured = repositories(tmp_path)
    with worker.ARCHIVE_CLEANUP_LOCK:
        assert worker.cleanup_expired_archives(database, configured) == 0
    assert not scans
    for value in [100, 101, 160, 220, 280]:
        clock[0] = value
        worker.cleanup_expired_archives(database, configured)
    assert calls == [100, 160, 280]
    assert len(scans) == 4


def test_unregistered_archive_residual_files_are_retained(monkeypatch, tmp_path):
    import pytest

    from daily_reader.agent_worker import cleanup_archived_worktree

    worktree = tmp_path / "unregistered"
    worktree.mkdir()
    source = worktree / "UserFile.swift"
    source.write_text("retained implementation")
    calls = []

    def run(command, cwd):
        calls.append(command)
        return CompletedProcess(command, 0, f"worktree {tmp_path}\0branch refs/heads/main\0\0", "")

    monkeypatch.setattr("daily_reader.agent_worker.run_command", run)
    with pytest.raises(OSError, match="registration missing; residual files retained"):
        cleanup_archived_worktree({"path": str(tmp_path)}, "codex/old", worktree)
    assert source.read_text() == "retained implementation"
    assert calls == [["git", "worktree", "list", "--porcelain", "-z"]]


def test_queue_poll_survives_database_busy(monkeypatch, tmp_path):
    import pytest

    import daily_reader.agent_worker as worker

    args = build_parser().parse_args([])
    args.database = tmp_path / "agent.sqlite3"
    seen = []
    sleeps = []

    class StopLoop(Exception):
        pass

    def claim(_db):
        seen.append(True)
        if len(seen) == 1:
            raise busy_error()
        raise StopLoop

    monkeypatch.setattr(worker, "cleanup_expired_archives", lambda *_args: 0)
    monkeypatch.setattr(worker, "claim_next_job", claim)
    monkeypatch.setattr(worker, "sleep", sleeps.append)
    with pytest.raises(StopLoop):
        worker.run_worker(args, {})
    assert len(seen) == 2
    assert sleeps == [args.poll_seconds]


def test_worker_retries_failure_persistence_without_reexecuting_task(monkeypatch, tmp_path):
    import pytest

    import daily_reader.agent_worker as worker

    args = build_parser().parse_args([])
    args.database = tmp_path / "agent.sqlite3"
    executed = []
    persisted = []
    claims = []

    class StopLoop(Exception):
        pass

    def claim(_db):
        claims.append(True)
        if len(claims) > 1:
            raise StopLoop
        return {"id": "job"}

    def execute(*_args):
        executed.append(True)
        raise worker.PendingJobFailure("job", "original failure", "/retained/worktree")

    def persist(*args):
        persisted.append(args)
        if len(persisted) == 1:
            raise busy_error()

    monkeypatch.setattr(worker, "cleanup_expired_archives", lambda *_args: 0)
    monkeypatch.setattr(worker, "claim_next_job", claim)
    monkeypatch.setattr(worker, "execute_job", execute)
    monkeypatch.setattr(worker, "record_job_failure", persist)
    monkeypatch.setattr(worker, "sleep", lambda _seconds: None)
    with pytest.raises(StopLoop):
        worker.run_worker(args, {})
    assert len(executed) == 1
    assert persisted == [(args.database, "job", "original failure", "/retained/worktree")] * 2


def test_busy_progress_is_flushed_without_abandoning_codex(monkeypatch, tmp_path):
    import daily_reader.agent_worker as worker
    from daily_reader.agent_jobs import append_event

    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    job = create_job(database, configured, {"repository": "repo", "prompt": "Work"})
    update_job(database, job["id"], status="running", worktree=str(worktree), branch="codex/test")
    job = get_job(database, job["id"])
    attempts = []

    def save(db, job_id, kind, message):
        if message == "progress one":
            attempts.append(True)
            if len(attempts) == 1:
                raise busy_error()
        append_event(db, job_id, kind, message)

    def run(*_args, **kwargs):
        for message in ["progress one", "progress two"]:
            kwargs["on_event"]({"type": "item.completed",
                                "item": {"type": "agent_message", "text": message}})
        return "thread", {"state": "blocked", "human_input_required": True,
                          "summary": "Needs input", "next_action": "Input"}, ""

    monkeypatch.setattr(worker, "append_event", save)
    monkeypatch.setattr(worker, "run_codex_turn", run)
    monkeypatch.setattr(worker, "monotonic", lambda: 100.0)
    worker.execute_job(database, configured, tmp_path / "schema", tmp_path, job)
    stored = get_job(database, job["id"])
    assert stored["status"] == "blocked"
    assert len(attempts) == 2
    assert [event["message"] for event in stored["events"]
            if event["message"].startswith("progress ")] == ["progress one", "progress two"]


def test_codex_callback_failure_kills_children_even_when_parent_exited(monkeypatch, tmp_path):
    import os
    import signal
    import subprocess
    import sys
    from contextlib import suppress
    from time import monotonic, sleep

    import pytest

    import daily_reader.agent_worker as worker

    pid_file = tmp_path / "child.pid"
    # The child explicitly ignores TERM; the parent exits before the callback
    # raises, so merely polling/terminating the parent cannot clean this up.
    child_code = (
        "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
        "print('ready', flush=True); time.sleep(60)"
    )
    code = """
import json, subprocess, sys
from pathlib import Path
child = subprocess.Popen([sys.executable, '-u', '-c', sys.argv[2]], stdout=subprocess.PIPE)
assert child.stdout.readline() == b'ready\\n'
Path(sys.argv[1]).write_text(str(child.pid))
print(json.dumps({'type': 'thread.started'}), flush=True)
"""
    command = [sys.executable, "-u", "-c", code, str(pid_file), child_code]
    monkeypatch.setattr(worker, "_codex_command", lambda *_args: command)
    real_popen = subprocess.Popen
    processes = []

    def popen(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(worker.subprocess, "Popen", popen)

    def fail(_event):
        processes[0].wait(timeout=10)
        raise RuntimeError("callback failed")

    try:
        with pytest.raises(RuntimeError, match="callback failed"):
            worker.run_codex_turn(tmp_path, tmp_path / "schema", "prompt", None, on_event=fail)
        child_pid = int(pid_file.read_text())
        deadline = monotonic() + 5
        while True:
            status = subprocess.run(["ps", "-p", str(child_pid), "-o", "state="],
                                    capture_output=True, text=True, check=False).stdout.strip()
            if not status or status.startswith("Z"):
                break
            assert monotonic() < deadline, f"Codex child still running: {status}"
            sleep(0.01)
        assert processes[0].returncode == 0
        assert processes[0].stdout.closed
    finally:
        if pid_file.exists():
            with suppress(ProcessLookupError):
                os.kill(int(pid_file.read_text()), signal.SIGKILL)


def test_execute_job_preserves_original_failure_when_database_stays_busy(monkeypatch, tmp_path):
    import pytest

    import daily_reader.agent_worker as worker

    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    job = create_job(database, configured, {"repository": "repo", "prompt": "Work"})

    def prepare(*_args):
        raise RuntimeError("original preparation error")

    def persist(*_args):
        raise busy_error()

    monkeypatch.setattr(worker, "prepare_worktree", prepare)
    monkeypatch.setattr(worker, "record_job_failure", persist)
    with pytest.raises(worker.PendingJobFailure) as raised:
        worker.execute_job(database, configured, tmp_path / "schema", tmp_path, job)
    assert raised.value.job_id == job["id"]
    assert raised.value.summary == "original preparation error"
    assert raised.value.worktree is None


def test_registered_archive_cleanup_removes_only_its_worktree_and_branch(tmp_path):
    import subprocess

    from daily_reader.agent_worker import cleanup_archived_worktree

    repository = tmp_path / "repo"
    repository.mkdir()

    def git(*args):
        return subprocess.run(
            ["git", "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
             "-c", "commit.gpgsign=false", "-c", "core.hooksPath=/dev/null", *args],
            cwd=repository, check=True, capture_output=True, text=True,
        ).stdout

    git("init", "-b", "main")
    git("commit", "--allow-empty", "-m", "fixture")
    worktree = tmp_path / "expired"
    git("worktree", "add", "-b", "codex/expired", str(worktree))
    cleanup_archived_worktree({"path": str(repository)}, "codex/expired", worktree)
    assert not worktree.exists()
    assert "codex/expired" not in git("branch", "--list")
    assert "refs/heads/main" in git("worktree", "list", "--porcelain")
    # A restart after successful resource deletion can finish the DB deletion.
    cleanup_archived_worktree({"path": str(repository)}, "codex/expired", worktree)


def test_archive_branch_failure_can_finish_after_worktree_was_removed(monkeypatch, tmp_path):
    import pytest

    import daily_reader.agent_worker as worker

    worktree = tmp_path / "expired"
    worktree.mkdir()
    calls = []
    branch_failures = []

    def run(command, cwd, *, check=True):
        calls.append(command)
        if command[1:3] == ["worktree", "list"]:
            return CompletedProcess(command, 0, f"worktree {worktree}\0\0", "")
        if command[1:3] == ["worktree", "remove"]:
            worktree.rmdir()
        if command[1:3] == ["branch", "-D"] and not branch_failures:
            branch_failures.append(True)
            raise CalledProcessError(128, command, stderr="branch temporarily locked")
        return CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(worker, "run_command", run)
    with pytest.raises(CalledProcessError):
        worker.cleanup_archived_worktree({"path": str(tmp_path)}, "codex/old", worktree)
    assert not worktree.exists()
    worker.cleanup_archived_worktree({"path": str(tmp_path)}, "codex/old", worktree)
    assert sum(command[1:3] == ["worktree", "remove"] for command in calls) == 1
    assert calls[-1] == ["git", "branch", "-D", "codex/old"]


def test_shutdown_rejects_new_codex_and_git_commands(monkeypatch, tmp_path):
    from threading import Event

    import pytest

    import daily_reader.agent_worker as worker

    stopping = Event()
    stopping.set()
    monkeypatch.setattr(worker, "WORKER_STOPPING", stopping)
    with pytest.raises(worker.WorkerStopping):
        worker.run_codex_turn(tmp_path, tmp_path / "schema", "prompt", None)
    with pytest.raises(worker.WorkerStopping):
        worker.run_command(["git", "status"], tmp_path)
    assert not worker.CODEX_PROCESSES


def test_shutdown_during_cleanup_does_not_claim_another_job(monkeypatch, tmp_path):
    from threading import Event

    import daily_reader.agent_worker as worker

    stopping = Event()
    monkeypatch.setattr(worker, "WORKER_STOPPING", stopping)
    monkeypatch.setattr(worker, "cleanup_expired_archives", lambda *_args: stopping.set())
    claims = []
    monkeypatch.setattr(worker, "claim_next_job", lambda *_args: claims.append(True))
    worker.run_worker(build_parser().parse_args([]), {})
    assert not claims


def test_worker_sigterm_covers_process_registration_race_and_preserves_job(tmp_path):
    import os
    import signal
    import subprocess
    import sys
    from contextlib import suppress
    from time import monotonic, sleep

    from daily_reader.agent_jobs import recover_interrupted_jobs

    database = tmp_path / "agent.sqlite3"
    pids_file = tmp_path / "pids.json"
    code = r'''
import json, os, signal, subprocess, sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import daily_reader.agent_worker as worker
from daily_reader.agent_jobs import create_job, get_job, update_job

root = Path(sys.argv[1])
database = root / 'agent.sqlite3'
worktree = root / 'worktree'
worktree.mkdir()
configured = {'repo': {'path': str(root), 'default_branch': 'main'}}
job = create_job(database, configured, {'repository': 'repo', 'prompt': 'fixture'})
update_job(database, job['id'], status='running', worktree=str(worktree), branch='codex/test')
job = get_job(database, job['id'])
child_code = "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); " \
             "print('ready', flush=True); time.sleep(120)"
codex_code = """
import json, os, signal, subprocess, sys, time
from pathlib import Path
signal.signal(signal.SIGTERM, signal.SIG_IGN)
child = subprocess.Popen([sys.executable, '-u', '-c', sys.argv[2]], stdout=subprocess.PIPE)
assert child.stdout.readline().strip() == b'ready'
marker = Path(sys.argv[1])
temporary = marker.with_suffix('.tmp')
temporary.write_text(json.dumps([os.getpid(), child.pid]))
temporary.replace(marker)
print(json.dumps({'type': 'thread.started'}), flush=True)
time.sleep(120)
"""
# Leave the child alive in the Popen/registry gap until the shutdown flag is set.
real_popen = subprocess.Popen
def delayed_popen(*args, **kwargs):
    process = real_popen(*args, **kwargs)
    assert worker.WORKER_STOPPING.wait(90)
    return process
worker.subprocess.Popen = delayed_popen
worker._codex_command = lambda *_args: [sys.executable, '-u', '-c', codex_code,
                                       str(root / 'pids.json'), child_code]
signal.signal(signal.SIGTERM, worker.stop_worker)
with ThreadPoolExecutor(max_workers=1) as pool:
    pool.submit(worker.execute_job, database, configured, root / 'schema', root, job).result(90)
assert worker.WORKER_STOPPING.is_set()
assert not worker.CODEX_PROCESSES
assert get_job(database, job['id'])['status'] == 'running'
'''
    service = subprocess.Popen([sys.executable, "-u", "-c", code, str(tmp_path)],
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    pids = []
    try:
        deadline = monotonic() + 60
        while not pids_file.exists():
            if service.poll() is not None:
                stdout, stderr = service.communicate()
                raise AssertionError(f"fixture worker exited early: {stdout} {stderr}")
            assert monotonic() < deadline, "fixture Codex did not start"
            sleep(0.01)
        pids = json.loads(pids_file.read_text())
        service.send_signal(signal.SIGTERM)
        stdout, stderr = service.communicate(timeout=60)
        assert service.returncode == 0, (stdout, stderr)
        for pid in pids:
            deadline = monotonic() + 5
            while True:
                state = subprocess.run(["ps", "-p", str(pid), "-o", "state="],
                                       check=False, capture_output=True, text=True)
                assert not state.stderr, state.stderr
                if not state.stdout.strip() or state.stdout.strip().startswith("Z"):
                    break
                assert monotonic() < deadline, "fixture Codex process survived worker shutdown"
                sleep(0.01)
        assert recover_interrupted_jobs(database) == 1
    finally:
        if service.poll() is None:
            service.kill()
            service.wait(timeout=5)
        if pids:
            with suppress(ProcessLookupError):
                os.killpg(pids[0], signal.SIGKILL)
        if service.stdout is not None:
            service.stdout.close()
        if service.stderr is not None:
            service.stderr.close()
