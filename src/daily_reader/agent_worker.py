from __future__ import annotations

import argparse
import functools
import json
import logging
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from time import sleep
from typing import Any

from daily_reader.agent_jobs import (
    append_event,
    claim_next_job,
    delete_expired_archived_job,
    get_job,
    list_expired_archived_jobs,
    load_repositories,
    recover_interrupted_jobs,
    take_pending_instructions,
    update_job,
)

LOGGER = logging.getLogger(__name__)
MAX_TURNS = 8
IMPLEMENTATION_MODEL = "gpt-5.6-luna"
IMPLEMENTATION_REASONING_EFFORT = "low"
DEPLOYMENT_LOCK = Lock()
ARCHIVE_CLEANUP_LOCK = Lock()


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


def _activity_from_event(event: dict[str, Any]) -> tuple[str, str] | None:
    """Convert public, non-sensitive Codex events into a compact activity entry."""
    item = event.get("item") or {}
    kind = item.get("type")
    if event.get("type") == "thread.started":
        return "activity", "Codexセッションを開始しました"
    if kind == "agent_message" and event.get("type") == "item.completed":
        return "codex", str(item.get("text", ""))[:20_000]
    if kind == "reasoning" and event.get("type") == "item.completed":
        summary = item.get("summary") or item.get("summary_text")
        if summary:
            return "activity", "推論を更新しました"
    if kind == "command_execution":
        command = item.get("command") or item.get("cmd")
        if command:
            status = "完了" if event.get("type") == "item.completed" else "開始"
            return "activity", f"コマンド実行（{status}）: {str(command)[:500]}"
    labels = {
        "file_change": "ファイル変更",
        "mcp_tool_call": "ツール呼び出し",
        "web_search": "Web検索",
        "todo_list": "作業一覧",
    }
    if kind in labels and event.get("type") in {"item.started", "item.completed"}:
        status = "完了" if event["type"] == "item.completed" else "開始"
        return "activity", f"{labels[kind]}（{status}）"
    return None


def _codex_command(
    worktree: Path,
    schema: Path,
    prompt: str,
    thread_id: str | None,
    output_path: Path,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> list[str]:
    model_options = []
    if model:
        model_options.extend(["--model", model])
    if reasoning_effort:
        model_options.extend(
            ["--config", f'model_reasoning_effort="{reasoning_effort}"']
        )
    if thread_id:
        return [
            "codex",
            "exec",
            "resume",
            "--json",
            "--output-schema",
            str(schema),
            "-o",
            str(output_path),
            *model_options,
            thread_id,
            prompt,
        ]
    return [
        "codex",
        "exec",
        "--approve-for-me",
        "--json",
        "--output-schema",
        str(schema),
        "-o",
        str(output_path),
        "-C",
        str(worktree),
        *model_options,
        prompt,
    ]


def run_codex_turn(
    worktree: Path,
    schema: Path,
    prompt: str,
    thread_id: str | None,
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
    on_event: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[str | None, dict[str, Any], str]:
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as output_file:
        output_path = Path(output_file.name)
    try:
        command = _codex_command(
            worktree,
            schema,
            prompt,
            thread_id,
            output_path,
            model,
            reasoning_effort,
        )
        with tempfile.TemporaryFile(mode="w+", encoding="utf-8") as stderr_file:
            process = subprocess.Popen(command, cwd=worktree, stdout=subprocess.PIPE,
                                       stderr=stderr_file, text=True)
            lines: list[str] = []
            assert process.stdout is not None
            for line in process.stdout:
                lines.append(line)
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if on_event is not None:
                    on_event(event)
            process.wait()
            stderr_file.seek(0)
            stderr = stderr_file.read()
            returncode = process.returncode
        parsed_thread_id, messages = _parse_codex_events("".join(lines))
        if returncode != 0:
            error = stderr.strip() or "Codex exited without an error message"
            raise RuntimeError(error[-4000:])
        payload = json.loads(output_path.read_text(encoding="utf-8"))
        return parsed_thread_id or thread_id, payload, "\n".join(messages)
    finally:
        output_path.unlink(missing_ok=True)


def run_deployment_turn(
    worktree: Path, schema: Path, prompt: str
) -> tuple[str | None, dict[str, Any], str]:
    # `codex exec resume` cannot accept `--approve-for-me`. Start deployment in a
    # fresh session so launchctl and live checks can use automatic approval review.
    return run_codex_turn(worktree, schema, prompt, None)


def _initial_prompt(task: str, mode: str = "execute") -> str:
    if mode == "requirements":
        return f"""Deepen the requirements for the following proposed coding task before
implementing it.

Proposed task:
{task}

You are already running in a dedicated task worktree and branch. Inspect all applicable
AGENTS.md files and the relevant existing implementation, but do not change files or commit
anything in this first turn. Identify material ambiguities, hidden constraints, expected user
experience, acceptance criteria, and verification needs. Then ask a concise, prioritized set
of questions through next_action and return state=blocked with human_input_required=true.

After the user answers, continue requirement discovery in the same thread if material
ambiguities remain. Once the requirements are sufficiently concrete, summarize the agreed
requirements in an event message, implement them autonomously, run the relevant verification,
and commit only task-scoped changes. Do not merge, push, remove the worktree, or delete the
branch; the external supervisor performs those steps. Return state=done only after the
implementation is committed and verification succeeds.
"""
    return f"""Plan the following coding task before implementation.

Task:
{task}

You are already running in a dedicated task worktree and branch. Inspect all applicable
AGENTS.md files and the relevant implementation. Do not change files or commit in this turn.
Produce a concrete implementation plan, including affected files, risks, and verification, in
summary and next_action. Return state=continue and human_input_required=false unless progress is
impossible without credentials, an irreversible high-risk decision, or a materially ambiguous
product decision that cannot be resolved from the repository. A lower-cost implementation model
will continue in this same thread after this planning turn.
"""


def _implementation_prompt(result: dict[str, Any]) -> str:
    return f"""Implement the original task autonomously, following the plan from the previous
turn.

Plan summary: {result.get('summary', '')}
Planned next action: {result.get('next_action', '')}

You are already running in a dedicated task worktree and branch. Do not create another
worktree. Make the task-scoped changes, run the relevant verification, and commit them. Do not
merge, push, remove the worktree, or delete the branch; the external supervisor performs those
steps. Continue without asking the user unless the configured high-risk blocker criteria are
met. Return state=done only after the implementation is committed and verification succeeds.

{_sandbox_retry_guidance()}
"""


def _continue_prompt(result: dict[str, Any]) -> str:
    return f"""Continue the task autonomously. The previous turn reported:

Summary: {result.get('summary', '')}
Next action: {result.get('next_action', '')}

Inspect the current repository state and keep working until the original task is committed and
verified. Do not ask the user unless the configured high-risk blocker criteria are met.

{_sandbox_retry_guidance()}
"""


def _attached_prompt(instructions: list[str]) -> str:
    messages = "\n\n".join(
        f"User message {index}:\n{instruction}"
        for index, instruction in enumerate(instructions, start=1)
    )
    return f"""The user attached to this task with additional instructions:

{messages}

Apply these instructions to the original task and current worktree state. Continue
autonomously until the task is committed and verified.
"""


def _follow_up_prompt(task: str, summary: str, instructions: list[str]) -> str:
    messages = "\n\n".join(
        f"User message {index}:\n{instruction}"
        for index, instruction in enumerate(instructions, start=1)
    )
    return f"""Answer the user's follow-up questions about a completed coding task.

Original task:
{task}

Completion summary:
{summary}

{messages}

Inspect the current repository when useful so the answer is grounded in the implemented code.
This is a read-only confirmation conversation: do not edit files, create commits, push, deploy,
or start new implementation work. Answer clearly in the summary field. Return state=done after
answering. If the user requests a new code change, explain that it should be submitted as a new
task instead of implementing it here.
"""


def _deployment_prompt(commit: str) -> str:
    return f"""The supervisor has integrated and pushed commit {commit} to the default branch.
The coding and verification phase is complete. Now deploy this completed task before reporting
success.

Read all applicable AGENTS.md deployment instructions again and deploy the pushed default
branch to the real runtime environment. Perform every required restart and live-environment
check, including a representative check of the changed behavior. Do not edit files, create
commits, or push anything during this phase. If the change only affects documentation, tests,
or comments and the repository instructions explicitly allow deployment to be skipped, verify
that classification and state it in the summary.

Respect the repository's delivery boundary. If its instructions define publishing and verifying
an installable artifact as deployment completion while device installation is a separate user
operation, return done after the distribution checks pass. Do not wait for, require, or treat the
absence of a connected physical device as a deployment failure. Report the published version and
the pending device installation separately in the summary.

The deployment may restart the Agent worker that launched this session. Before restarting that
worker, inspect its current PID, start time, deployed commit, and recent service logs. If those
show that the worker has already restarted onto this pushed commit, treat the required restart
as complete and do not restart it again. This check is mandatory because restarting the parent
worker interrupts this session; repeating the restart after recovery creates an endless restart
loop and launches duplicate Codex sessions against the same worktree. Restart the worker only
when it has not yet loaded this commit, and perform all checks that can precede that restart
first.

Return state=done only after deployment and live verification succeed, or after confirming that
deployment is not required under the repository instructions. If deployment fails, investigate
and retry safe fixes. Return blocked only when human input is genuinely required. Never report
the task as complete while runtime changes remain undeployed or unverified.

{_sandbox_retry_guidance()}
"""


def _sandbox_retry_guidance() -> str:
    return """A failed command can reflect the Codex sandbox rather than the host environment.
If a required macOS or Xcode command reports Operation not permitted, CoreSimulatorService
connection invalid/refused, or no available simulator runtimes, retry that same command through
the approval escalation mechanism before diagnosing missing software or asking for environment
changes. Do not repeat an unchanged sandboxed command across turns. If escalation is denied,
report the denied approval and the exact remaining verification instead."""


def _default_branch_conflict_prompt(default_branch: str) -> str:
    return f"""The supervisor could not fast-forward the local {default_branch} branch to its
remote because the local branch also contains commits. A rebase onto origin/{default_branch}
was started and encountered conflicts. Resolve every conflict without discarding either the
local commits or unrelated upstream changes, complete the rebase, and rerun the relevant
verification. Leave the checkout clean. Do not push. Return done only when the rebase and
verification succeed."""


def push_worktree(
    repository: dict[str, str],
    worktree: Path,
    *,
    worktree_label: str = "task worktree",
) -> str | None:
    status = run_command(["git", "status", "--porcelain"], worktree).stdout.strip()
    if status:
        raise RuntimeError(f"uncommitted changes remain in the {worktree_label}")
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
    return head


def sync_default_worktree(repository: dict[str, str], commit: str) -> str:
    repository_path = Path(repository["path"])
    default_branch = repository["default_branch"]
    current_branch = run_command(
        ["git", "branch", "--show-current"], repository_path
    ).stdout.strip()
    if current_branch != default_branch:
        raise RuntimeError(
            f"repository checkout is on {current_branch or 'detached HEAD'}, "
            f"not {default_branch}"
        )
    run_command(["git", "fetch", "origin", default_branch], repository_path)
    remote_head = run_command(
        ["git", "rev-parse", f"origin/{default_branch}"], repository_path
    ).stdout.strip()
    contains_commit = run_command(
        ["git", "merge-base", "--is-ancestor", commit, remote_head],
        repository_path,
        check=False,
    )
    if contains_commit.returncode != 0:
        raise RuntimeError("pushed commit is no longer on the remote default branch")
    status = run_command(["git", "status", "--porcelain"], repository_path)
    if status.stdout.strip():
        return "dirty"
    merged = run_command(
        ["git", "merge", "--ff-only", remote_head], repository_path, check=False
    )
    if merged.returncode == 0:
        return "synced"
    rebased = run_command(
        ["git", "rebase", f"origin/{default_branch}"],
        repository_path,
        check=False,
    )
    return "rebased" if rebased.returncode == 0 else "conflict"


def cleanup_worktree(repository: dict[str, str], branch: str, worktree: Path) -> None:
    repository_path = Path(repository["path"])
    run_command(["git", "worktree", "remove", str(worktree)], repository_path)
    run_command(["git", "branch", "-D", branch], repository_path)


def cleanup_failed_worktree(repository: dict[str, str], branch: str, worktree: Path) -> None:
    repository_path = Path(repository["path"])
    if worktree.exists():
        run_command(
            ["git", "worktree", "remove", "--force", str(worktree)],
            repository_path,
            check=False,
        )
    run_command(["git", "branch", "-D", branch], repository_path, check=False)


def cleanup_archived_worktree(
    repository: dict[str, str], branch: str, worktree: Path
) -> None:
    """Remove an expired archive's worktree and branch without masking failures."""
    repository_path = Path(repository["path"])
    if worktree.exists():
        run_command(
            ["git", "worktree", "remove", "--force", str(worktree)],
            repository_path,
        )
    else:
        run_command(["git", "worktree", "prune"], repository_path)
    branch_exists = run_command(
        ["git", "show-ref", "--verify", f"refs/heads/{branch}"],
        repository_path,
        check=False,
    )
    if branch_exists.returncode == 0:
        run_command(["git", "branch", "-D", branch], repository_path)


def cleanup_expired_archives(
    database: Path,
    repositories: dict[str, dict[str, str]],
    now: datetime | None = None,
) -> int:
    """Remove expired archive resources before permanently deleting their records."""
    deleted = 0
    with ARCHIVE_CLEANUP_LOCK:
        for job in list_expired_archived_jobs(database, now=now):
            repository = repositories.get(job["repository"])
            if repository is None:
                LOGGER.error(
                    "Cannot clean archived job %s: repository %s is not configured",
                    job["id"],
                    job["repository"],
                )
                continue
            cleanup_resources = None
            if job.get("worktree") and job.get("branch"):
                cleanup_resources = functools.partial(
                    cleanup_archived_worktree,
                    repository,
                    job["branch"],
                    Path(job["worktree"]),
                )

            try:
                removed = delete_expired_archived_job(
                    database,
                    job["id"],
                    job["hidden_at"],
                    before_delete=cleanup_resources,
                )
            except (OSError, subprocess.CalledProcessError):
                LOGGER.exception("Could not clean resources for archived job %s", job["id"])
                continue
            if removed:
                deleted += 1
    return deleted


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
    deployment_lock_acquired = False
    try:
        follow_up = bool(job.get("follow_up"))
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
            prompt = (
                ""
                if follow_up
                else _initial_prompt(job["prompt"], job.get("mode", "execute"))
            )
        attached = take_pending_instructions(database, job_id)
        if follow_up:
            prompt = _follow_up_prompt(job["prompt"], job.get("summary", ""), attached)
            append_event(database, job_id, "follow-up", "完了内容を確認しています")
        elif attached:
            prompt = f"{prompt}\n\n{_attached_prompt(attached)}"
        final_result: dict[str, Any] = {}
        def record_activity(event: dict[str, Any]) -> None:
            activity = _activity_from_event(event)
            if activity is not None:
                append_event(database, job_id, *activity)
        planning_turn = (
            not follow_up
            and not existing_worktree
            and job.get("mode", "execute") == "execute"
        )
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
            use_implementation_model = not follow_up and not (
                planning_turn and attempt == 1
            )
            thread_id, result, messages = run_codex_turn(
                worktree,
                schema,
                prompt,
                thread_id,
                model=IMPLEMENTATION_MODEL if use_implementation_model else None,
                reasoning_effort=(
                    IMPLEMENTATION_REASONING_EFFORT
                    if use_implementation_model
                    else None
                ),
                on_event=record_activity,
            )
            final_result = result
            update_fields = {"thread_id": thread_id}
            if not follow_up:
                update_fields["summary"] = result["summary"]
            update_job(database, job_id, **update_fields)
            append_event(database, job_id, "codex", messages or result["summary"])
            if planning_turn and attempt == 1 and result["state"] != "blocked":
                prompt = _implementation_prompt(result)
                append_event(
                    database,
                    job_id,
                    "model-routing",
                    f"実装を低コストモデル（{IMPLEMENTATION_MODEL}）へ切り替えました",
                )
                continue
            attached = take_pending_instructions(database, job_id)
            if attached:
                prompt = (
                    _follow_up_prompt(job["prompt"], job.get("summary", ""), attached)
                    if follow_up
                    else _attached_prompt(attached)
                )
                continue
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

        if follow_up:
            status = run_command(["git", "status", "--porcelain"], worktree).stdout.strip()
            if status:
                raise RuntimeError("Codex changed files during a read-only follow-up")
            cleanup_failed_worktree(repository, branch, worktree)
            worktree = None
            update_job(
                database,
                job_id,
                status="completed",
                phase="完了内容を確認済み",
                follow_up=0,
                worktree=None,
                finished_at=datetime.now(UTC).isoformat(),
            )
            return

        DEPLOYMENT_LOCK.acquire()
        deployment_lock_acquired = True
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
            commit = push_worktree(repository, worktree)
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
        default_worktree = Path(repository["path"])
        for sync_attempt in range(1, 4):
            sync_result = sync_default_worktree(repository, commit)
            if sync_result == "dirty":
                append_event(
                    database,
                    job_id,
                    "sync-skipped",
                    "ローカルmainに未コミット変更があるため同期をスキップしました",
                )
                break
            if sync_result == "synced":
                break
            if sync_result == "conflict":
                append_event(
                    database,
                    job_id,
                    "conflict",
                    "ローカルmainの競合をCodexへ戻して解決しています",
                )
                conflict_prompt = _default_branch_conflict_prompt(
                    repository["default_branch"]
                )
                _, conflict_result, messages = run_codex_turn(
                    default_worktree, schema, conflict_prompt, None
                )
                append_event(
                    database,
                    job_id,
                    "codex",
                    messages or conflict_result["summary"],
                )
                if conflict_result["state"] != "done":
                    raise RuntimeError("Codex could not resolve the local main conflict")
            synced_commit = push_worktree(
                repository,
                default_worktree,
                worktree_label="local default worktree",
            )
            if synced_commit:
                commit = synced_commit
                break
            append_event(
                database,
                job_id,
                "push-retry",
                f"mainが更新されたためローカル同期を再試行します（{sync_attempt}/3）",
            )
        else:
            raise RuntimeError("main changed repeatedly while the local checkout was synced")
        if repository.get("deploy", True):
            update_job(database, job_id, phase="デプロイ・実環境確認中")
            append_event(
                database,
                job_id,
                "deploying",
                f"{commit} のデプロイと実環境確認を開始しました",
            )
            thread_id, deployment_result, messages = run_deployment_turn(
                worktree, schema, _deployment_prompt(commit)
            )
            update_job(
                database,
                job_id,
                thread_id=thread_id,
                summary=deployment_result["summary"],
            )
            append_event(
                database,
                job_id,
                "codex",
                messages or deployment_result["summary"],
            )
            if deployment_result["state"] != "done":
                detail = deployment_result.get("next_action") or deployment_result["summary"]
                raise RuntimeError(f"deployment did not complete: {detail}")
            append_event(database, job_id, "deployed", deployment_result["summary"])
            implementation_summary = final_result.get("summary", "タスクが完了しました")
            deployment_summary = deployment_result.get("summary", "")
            summary = implementation_summary
            if deployment_summary and deployment_summary != implementation_summary:
                summary = (
                    f"実装・検証: {implementation_summary}\n\n"
                    f"デプロイ確認: {deployment_summary}"
                )
        else:
            summary = final_result.get("summary", "タスクが完了しました")
            append_event(
                database,
                job_id,
                "deployment-skipped",
                f"{commit} をpushしました（このリポジトリはデプロイ対象外です）",
            )
        cleanup_worktree(repository, branch, worktree)
        worktree = None
        DEPLOYMENT_LOCK.release()
        deployment_lock_acquired = False
        update_job(
            database,
            job_id,
            status="completed",
            phase="完了",
            summary=summary,
            worktree=None,
            finished_at=datetime.now(UTC).isoformat(),
        )
        append_event(
            database,
            job_id,
            "completed",
            f"{commit} をmainへ反映し、デプロイを確認しました",
        )
    except Exception as error:  # noqa: BLE001
        if deployment_lock_acquired:
            DEPLOYMENT_LOCK.release()
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
    parser = argparse.ArgumentParser(description="Run queued Daymeld Codex tasks")
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
    parser.add_argument(
        "--max-workers",
        type=int,
        default=10,
        help="Maximum number of Agent tasks to run concurrently (default: 10)",
    )
    parser.add_argument("--once", action="store_true")
    return parser


def run_worker(
    args: argparse.Namespace, repositories: dict[str, dict[str, str]]
) -> None:
    while True:
        cleanup_expired_archives(args.database, repositories)
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


def resolve_schema_path(schema: Path) -> Path:
    return schema.expanduser().resolve()


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.poll_seconds < 1:
        raise SystemExit("--poll-seconds must be at least 1")
    if args.max_workers < 1:
        raise SystemExit("--max-workers must be at least 1")
    args.database = args.database.expanduser().resolve()
    args.repositories = args.repositories.expanduser().resolve()
    args.schema = resolve_schema_path(args.schema)
    recovered = recover_interrupted_jobs(args.database)
    if recovered:
        LOGGER.warning("Recovered %s jobs interrupted by a worker restart", recovered)
    repositories = load_repositories(args.repositories)
    args.worktree_root.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(
        max_workers=args.max_workers, thread_name_prefix="agent-worker"
    ) as executor:
        futures = [
            executor.submit(run_worker, args, repositories)
            for _ in range(args.max_workers)
        ]
        for future in futures:
            future.result()


if __name__ == "__main__":
    main()
