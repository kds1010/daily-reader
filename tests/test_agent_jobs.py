from pathlib import Path

import pytest

from daily_reader.agent_jobs import (
    claim_next_job,
    create_job,
    get_job,
    list_jobs,
    load_repositories,
    request_cancel,
    resume_job,
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
    assert list_jobs(database)[0]["id"] == job["id"]
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


def test_agent_job_validates_repository_and_prompt(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)

    with pytest.raises(ValueError, match="repository"):
        create_job(database, configured, {"repository": "missing", "prompt": "x"})
    with pytest.raises(ValueError, match="prompt"):
        create_job(database, configured, {"repository": "repo", "prompt": ""})


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


def test_blocked_job_can_resume_with_user_instruction(tmp_path: Path) -> None:
    database = tmp_path / "agent.sqlite3"
    configured = repositories(tmp_path)
    job = create_job(database, configured, {"repository": "repo", "prompt": "Do it"})
    claim_next_job(database)
    update_job(database, job["id"], status="blocked", phase="判断待ち")

    assert resume_job(database, job["id"], "Use the compatible format")
    resumed = claim_next_job(database)
    assert resumed is not None
    assert "Use the compatible format" in resumed["prompt"]
    assert not resume_job(database, job["id"], "second response")
