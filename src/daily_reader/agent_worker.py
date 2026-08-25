from __future__ import annotations

import argparse
import json
import logging
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from time import sleep
from typing import Any

from daily_reader.agent_jobs import (
    append_event,
    claim_next_job,
    get_job,
    load_repositories,
    update_job,
)

LOGGER = logging.getLogger(__name__)
MAX_TURNS = 8


def run_command(
    command: list[str], cwd: Path, *, check: bool = True
) -> subprocess.CompletedProcess[str]:
    LOGGER.info("Running %s in %s", command, cwd)
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def prepare_worktree(
    repository: dict[str, str], job_id: str, worktree_root: Path
) -> tuple[str, Path]:
    repository_path = Path(repository["path"])
    branch = f"codex/web-{job_id[:12]}"
    worktree = (worktree_root / job_id).resolve()
    run_command(["git", "fetch", "origin"], repository_path)
    run_command(
        [
            "git",
            "worktree",
            "add",
            "-b",
            branch,
            str(worktree),
            f"origin/{repository['default_branch']}",
        ],
        repository_path,
    )
    return branch, worktree


def _parse_codex_events(output: str) -> tuple[str | None, list[str]]:
    thread_id = None
    messages: list[str] = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("type") == "thread.started":
            thread_id = event.get("thread_id")
        item = event.get("item", {})
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            messages.append(item.get("text", ""))
    return thread_id, messages


def run_codex_turn(
    worktree: Path,
    schema: Path,
    prompt: str,
    thread_id: str | None,
) -> tuple[str | None, dict[str, Any], str]:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as output_file:
        output_path = Path(output_file.name)
    try:
        if thread_id:
            command = [
                "codex",
                "exec",
                "resume",
                "--json",
                "--output-schema",
                str(schema),
                "-o",
                str(output_path),
                thread_id,
                prompt,
            ]
        else:
            command = [
                "codex",
                "exec",
                "--approve-for-me",
                "--sandbox",
                "workspace-write",
                "--json",
                "--output-schema",
                str(schema),
                "-o",
                str(output_path),
                "-C",
                str(worktree),
                prompt,
            ]
        result = run_command(command, worktree, check=False)
        parsed_thread_id, messages = _parse_codex_events(result.stdout)
        if result.returncode != 0:
            error = result.stderr.strip() or "Codex exited without an error message"
            raise RuntimeError(error[-4000:])
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        return parsed_thread_id or thread_id, payload, "\n".join(messages)
    finally:
        output_path.unlink(missing_ok=True)


def _initial_prompt(task: str) -> str:
    return f"""Complete the following coding task autonomously.

Task:
{task}

You are already running in a dedicated task worktree and branch. Do not create another
worktree. Inspect all applicable AGENTS.md files before changing files. Implement the task,
run the relevant verification, and commit only task-scoped changes. Do not merge, push, remove
the worktree, or delete the branch; the external supervisor performs those steps.

Continue working without asking the user unless progress is impossible without credentials,
an irreversible high-risk decision, or a materially ambiguous product decision that cannot be
resolved from the repository. Return state=done only after the implementation is committed and
verification succeeds. Return state=continue when another autonomous turn can make progress.
"""


def _continue_prompt(result: dict[str, Any]) -> str:
    return f"""Continue the task autonomously. The previous turn reported:

Summary: {result.get('summary', '')}
Next action: {result.get('next_action', '')}

Inspect the current repository state and keep working until the original task is committed and
verified. Do not ask the user unless the configured high-risk blocker criteria are met.
"""


def push_and_cleanup_worktree(
    repository: dict[str, str], branch: str, worktree: Path
) -> str | None:
    repository_path = Path(repository["path"])
    status = run_command(["git", "status", "--porcelain"], worktree).stdout.strip()
    if status:
        raise RuntimeError("Codex left uncommitted changes in the task worktree")
    run_command(
        ["git", "diff", "--check", f"origin/{repository['default_branch']}...HEAD"],
        worktree,
    )
    head = run_command(["git", "rev-parse", "HEAD"], worktree).stdout.strip()
    pushed = run_command(
        ["git", "push", "origin", f"HEAD:{repository['default_branch']}"],
        worktree,
        check=False,
    )
    if pushed.returncode != 0:
        return None
    run_command(["git", "worktree", "remove", str(worktree)], repository_path)
    run_command(["git", "branch", "-D", branch], repository_path)
    return head


def cleanup_failed_worktree(repository: dict[str, str], branch: str, worktree: Path) -> None:
    repository_path = Path(repository["path"])
    if worktree.exists():
        run_command(
            ["git", "worktree", "remove", "--force", str(worktree)],
            repository_path,
            check=False,
        )
    run_command(["git", "branch", "-D", branch], repository_path, check=False)


def execute_job(
    database: Path,
    repositories: dict[str, dict[str, str]],
    schema: Path,
    worktree_root: Path,
    job: dict[str, Any],
) -> None:
    job_id = job["id"]
    repository = repositories[job["repository"]]
    branch = ""
    worktree: Path | None = None
    try:
        existing_worktree = Path(job["worktree"]) if job.get("worktree") else None
        if existing_worktree and existing_worktree.exists() and job.get("branch"):
            branch = job["branch"]
            worktree = existing_worktree
            thread_id = job.get("thread_id")
            prompt = f"""Resume the original task using this user clarification and the
current worktree state. Continue autonomously until the task is committed and verified.

{job['prompt']}"""
            append_event(database, job_id, "resumed", "保持していた作業環境で再開しました")
        else:
            branch, worktree = prepare_worktree(repository, job_id, worktree_root)
            update_job(
                database,
                job_id,
                branch=branch,
                worktree=str(worktree),
                phase="Codex実行中",
            )
            append_event(
                database, job_id, "worktree", f"{branch} を {worktree} に作成しました"
            )
            thread_id = None
            prompt = _initial_prompt(job["prompt"])
        final_result: dict[str, Any] = {}
        for attempt in range(1, MAX_TURNS + 1):
            current = get_job(database, job_id)
            if current and current["cancel_requested"]:
                update_job(
                    database,
                    job_id,
                    status="cancelled",
                    phase="キャンセル済み",
                    finished_at=datetime.now(UTC).isoformat(),
                )
                append_event(database, job_id, "cancelled", "タスクを停止しました")
                cleanup_failed_worktree(repository, branch, worktree)
                return
            update_job(database, job_id, attempts=attempt, phase=f"Codex実行中（{attempt}回目）")
            thread_id, result, messages = run_codex_turn(worktree, schema, prompt, thread_id)
            final_result = result
            update_job(
                database,
                job_id,
                thread_id=thread_id,
                summary=result["summary"],
            )
            append_event(database, job_id, "codex", messages or result["summary"])
            if result["state"] == "done":
                break
            if result["state"] == "blocked" and result["human_input_required"]:
                update_job(
                    database,
                    job_id,
                    status="blocked",
                    phase="判断待ち",
                    summary=result["summary"],
                )
                append_event(database, job_id, "blocked", result["next_action"])
                return
            prompt = _continue_prompt(result)
        else:
            raise RuntimeError(f"Codex did not finish within {MAX_TURNS} turns")

        update_job(database, job_id, phase="mainへ統合中")
        commit = None
        for integration_attempt in range(1, 4):
            run_command(["git", "fetch", "origin"], Path(repository["path"]))
            rebase = run_command(
                ["git", "rebase", f"origin/{repository['default_branch']}"],
                worktree,
                check=False,
            )
            if rebase.returncode != 0:
                append_event(
                    database,
                    job_id,
                    "conflict",
                    "最新mainとの競合をCodexへ戻して解決しています",
                )
                conflict_prompt = """The supervisor rebased the task branch onto the latest
default branch and encountered conflicts. Resolve every conflict without discarding unrelated
upstream changes, complete the rebase, rerun the relevant verification, and keep the worktree
clean. Return done only when the rebase and verification succeed."""
                thread_id, conflict_result, messages = run_codex_turn(
                    worktree, schema, conflict_prompt, thread_id
                )
                update_job(
                    database,
                    job_id,
                    thread_id=thread_id,
                    summary=conflict_result["summary"],
                )
                append_event(
                    database,
                    job_id,
                    "codex",
                    messages or conflict_result["summary"],
                )
                if conflict_result["state"] != "done":
                    raise RuntimeError("Codex could not resolve the integration conflict")
            commit = push_and_cleanup_worktree(repository, branch, worktree)
            if commit:
                break
            append_event(
                database,
                job_id,
                "push-retry",
                f"mainが更新されたため統合を再試行します（{integration_attempt}/3）",
            )
        if not commit:
            raise RuntimeError("main changed repeatedly while the task was being integrated")
        worktree = None
        summary = final_result.get("summary", "タスクが完了しました")
        update_job(
            database,
            job_id,
            status="completed",
            phase="完了",
            summary=summary,
            worktree=None,
            finished_at=datetime.now(UTC).isoformat(),
        )
        append_event(database, job_id, "completed", f"{commit} をmainへ反映しました")
    except Exception as error:  # noqa: BLE001
        LOGGER.exception("Agent job %s failed", job_id)
        update_job(
            database,
            job_id,
            status="failed",
            phase="失敗",
            summary=str(error)[-4000:],
            finished_at=datetime.now(UTC).isoformat(),
        )
        append_event(database, job_id, "failed", str(error)[-4000:])
        if worktree is not None:
            append_event(
                database,
                job_id,
                "preserved",
                f"復旧できるよう作業環境を保持しました: {worktree}",
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run queued Daily Reader Codex tasks")
    parser.add_argument("--database", type=Path, default=Path("data/agent.sqlite3"))
    parser.add_argument(
        "--repositories", type=Path, default=Path("config/agent-repositories.toml")
    )
    parser.add_argument(
        "--schema", type=Path, default=Path("config/agent-result-schema.json")
    )
    parser.add_argument(
        "--worktree-root",
        type=Path,
        default=Path.home() / ".local/state/daily-reader/agent-worktrees",
    )
    parser.add_argument("--poll-seconds", type=int, default=5)
    parser.add_argument("--once", action="store_true")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.poll_seconds < 1:
        raise SystemExit("--poll-seconds must be at least 1")
    repositories = load_repositories(args.repositories)
    args.worktree_root.mkdir(parents=True, exist_ok=True)
    while True:
        job = claim_next_job(args.database)
        if job:
            execute_job(
                args.database,
                repositories,
                args.schema,
                args.worktree_root,
                job,
            )
        if args.once:
            return
        sleep(args.poll_seconds)


if __name__ == "__main__":
    main()
