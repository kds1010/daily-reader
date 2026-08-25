import sqlite3
from pathlib import Path

import pytest

from daily_reader.agent_jobs import (
    append_event,
    attach_to_job,
    claim_next_job,
    create_job,
    get_job,
    hide_job,
    list_jobs,
    load_repositories,
    request_cancel,
    resume_job,
    take_pending_instructions,
    update_job,
)


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


def test_hidden_job_returns_to_list_after_an_update(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    job = create_job(
        database,
        repositories(tmp_path),
        {"repository": "repo", "prompt": "Hide this task"},
    )

    assert hide_job(database, job["id"])
    assert list_jobs(database) == []
    assert get_job(database, job["id"]) is not None

    update_job(database, job["id"], phase="更新されました")

    assert [item["id"] for item in list_jobs(database)] == [job["id"]]

    assert hide_job(database, job["id"])
    append_event(database, job["id"], "progress", "新しい進捗")

    assert [item["id"] for item in list_jobs(database)] == [job["id"]]


def test_hide_job_rejects_unknown_job(tmp_path: Path) -> None:
    assert not hide_job(tmp_path / "agent.sqlite3", "missing")


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
    assert not resume_job(database, job["id"], "too late")


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
