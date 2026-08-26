from __future__ import annotations

import argparse
import functools
import hmac
import json
import logging
import select
import subprocess
import threading
import urllib.parse
from collections import Counter
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import monotonic, sleep

from daily_reader.agent_jobs import (
    attach_to_job,
    create_job,
    get_job,
    hide_job,
    list_archived_jobs,
    list_jobs,
    load_repositories,
    request_cancel,
    resume_job,
)
from daily_reader.core import collect, load_config, load_keywords, write_output
from daily_reader.daily_planner import (
    create_task,
    delete_task,
    list_today,
    set_task_completion,
    upsert_health_checkin,
)
from daily_reader.email_assistant import (
    fetch_gmail_thread_content,
    get_gmail_sync_state,
    list_reminders,
    mark_gmail_thread_read,
    sync_gmail,
    update_status,
)
from daily_reader.highlights import generate_highlights

LOGGER = logging.getLogger(__name__)
READ_LOG_LOCK = threading.Lock()
FEEDBACK_LOG_LOCK = threading.Lock()
UPDATE_STATS_LOG_LOCK = threading.Lock()
READ_SURFACES = {
    "field_highlight",
    "official_digest",
    "gadget_digest",
    "tech_pick",
    "article_feed",
}


def _without_codex_spark_limits(result: dict[str, object]) -> dict[str, object]:
    limits = result.get("rateLimitsByLimitId")
    if not isinstance(limits, dict):
        return result
    result["rateLimitsByLimitId"] = {
        limit_id: limit
        for limit_id, limit in limits.items()
        if not (
            isinstance(limit, dict)
            and "codex-spark" in str(limit.get("limitName", "")).lower()
        )
    }
    return result


def read_codex_rate_limits(timeout: float = 10) -> dict[str, object]:
    """Read the signed-in Codex account's current limits from the app-server API."""
    process = subprocess.Popen(
        ["codex", "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    requests = (
        {
            "id": 1,
            "method": "initialize",
            "params": {
                "clientInfo": {"name": "daily-reader", "version": "0.1"},
                "capabilities": {"experimentalApi": True},
            },
        },
        {"id": 2, "method": "account/rateLimits/read", "params": None},
    )
    try:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("Codex app server pipes are unavailable")
        for request in requests:
            process.stdin.write(json.dumps(request) + "\n")
        process.stdin.flush()

        deadline = monotonic() + timeout
        while (remaining := deadline - monotonic()) > 0:
            readable, _, _ = select.select([process.stdout], [], [], remaining)
            if not readable:
                break
            line = process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError as error:
                raise RuntimeError("Codex returned invalid JSON") from error
            if message.get("id") != 2:
                continue
            if "error" in message:
                raise RuntimeError("Codex rejected the rate-limit request")
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Codex returned an invalid rate-limit response")
            return _without_codex_spark_limits(result)
        raise TimeoutError("Codex rate-limit request timed out")
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def build_deployment_info(repository: Path, deployed_at: datetime) -> dict[str, str]:
    try:
        package_version = version("daily-reader")
    except PackageNotFoundError:
        package_version = "unknown"
    try:
        revision = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "--short=12", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (FileNotFoundError, subprocess.SubprocessError):
        revision = "unknown"
    deployment_version = package_version
    if revision != "unknown":
        deployment_version = f"{package_version}+{revision}"
    return {
        "version": deployment_version,
        "deployed_at": deployed_at.isoformat(),
    }


def health_sync_authorized(token_path: Path, authorization: str) -> bool:
    try:
        expected = token_path.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return False
    return bool(expected) and hmac.compare_digest(authorization, f"Bearer {expected}")


def append_read_event(log_path: Path, article: dict[str, object], surface: str) -> None:
    event = {
        "read_at": datetime.now(UTC).isoformat(),
        "article_id": article["id"],
        "title": article["title"],
        "source": article["source"],
        "category": article["category"],
        "surface": surface,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with READ_LOG_LOCK, log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, ensure_ascii=False) + "\n")


def summarize_read_events(log_path: Path) -> dict[str, object]:
    events = []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {
        "total_reads": len(events),
        "unique_articles": len({event.get("article_id") for event in events}),
        "by_category": dict(Counter(event.get("category") for event in events)),
        "by_source": dict(Counter(event.get("source") for event in events).most_common(20)),
        "by_surface": dict(Counter(event.get("surface") for event in events)),
        "recent": events[-20:][::-1],
    }


def append_feedback_event(
    log_path: Path, article: dict[str, object], surface: str
) -> None:
    event = {
        "feedback_at": datetime.now(UTC).isoformat(),
        "feedback": "not_interested",
        "article_id": article["id"],
        "title": article["title"],
        "source": article["source"],
        "category": article["category"],
        "surface": surface,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_LOG_LOCK, log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_feedback_events(log_path: Path) -> list[dict[str, object]]:
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("feedback") == "not_interested" and isinstance(
            event.get("article_id"), str
        ):
            events.append(event)
    return events


def _load_article_ids(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            article["id"]
            for article in payload.get("articles", [])
            if isinstance(article.get("id"), str)
        }
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return set()


def _load_highlight_ids(path: Path) -> set[str]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return {
            item["article_id"]
            for field in payload.get("field_highlights", [])
            for item in field.get("items", [])
            if isinstance(item.get("article_id"), str)
        }
    except (FileNotFoundError, json.JSONDecodeError, TypeError):
        return set()


def build_update_stats(
    generated_at: datetime,
    current_article_ids: set[str],
    previous_article_ids: set[str],
    current_highlight_ids: set[str],
    previous_highlight_ids: set[str],
    highlights_updated: bool,
) -> dict[str, object]:
    new_article_ids = current_article_ids - previous_article_ids
    return {
        "generated_at": generated_at.isoformat(),
        "new_articles": len(new_article_ids),
        "total_articles": len(current_article_ids),
        "new_articles_highlighted": len(new_article_ids & current_highlight_ids),
        "new_highlights": len(current_highlight_ids - previous_highlight_ids),
        "kept_highlights": len(current_highlight_ids & previous_highlight_ids),
        "total_highlights": len(current_highlight_ids),
        "highlights_updated": highlights_updated,
    }


def append_update_stats(log_path: Path, stats: dict[str, object]) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with UPDATE_STATS_LOG_LOCK, log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(stats, ensure_ascii=False) + "\n")


def make_handler(
    site: Path,
    articles_path: Path,
    read_log_path: Path,
    feedback_log_path: Path,
    assistant_db: Path,
    gmail_client_secret: Path,
    gmail_token: Path,
    planner_db: Path = Path("data/planner.sqlite3"),
    health_sync_token: Path = Path("secrets/health-sync-token.txt"),
    agent_db: Path = Path("data/agent.sqlite3"),
    agent_repositories: dict[str, dict[str, str]] | None = None,
    deployment_info: dict[str, str] | None = None,
):
    repositories = agent_repositories or {}

    class DailyReaderHandler(SimpleHTTPRequestHandler):
        def end_headers(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path in {"/", "/index.html", "/sw.js"}:
                self.send_header("Cache-Control", "no-cache")
            super().end_headers()

        def _send_json(self, status: int, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _read_json(self, max_length: int = 16_384) -> dict[str, object]:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= max_length:
                raise ValueError("invalid content length")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("invalid JSON payload")
            return payload

        def _health_sync_authorized(self) -> bool:
            return health_sync_authorized(
                health_sync_token, self.headers.get("Authorization", "")
            )

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/deployment":
                self._send_json(200, deployment_info or {})
                return
            if self.path == "/api/codex-usage":
                try:
                    self._send_json(200, read_codex_rate_limits())
                except (FileNotFoundError, OSError, RuntimeError, TimeoutError) as error:
                    LOGGER.warning("Could not read Codex usage: %s", error)
                    self._send_json(503, {"error": "Codexの使用状況を取得できませんでした"})
                return
            if self.path == "/api/agent-jobs":
                self._send_json(
                    200,
                    {
                        "repositories": [
                            {"name": item["name"], "label": item["label"]}
                            for item in repositories.values()
                        ],
                        "jobs": list_jobs(agent_db),
                        "archived_jobs": list_archived_jobs(agent_db),
                    },
                )
                return
            if self.path.startswith("/api/agent-jobs/"):
                job_id = urllib.parse.unquote(self.path.rsplit("/", 1)[-1])
                job = get_job(agent_db, job_id)
                if job is None:
                    self._send_json(404, {"error": "agent job not found"})
                else:
                    self._send_json(200, job)
                return
            if self.path == "/api/today":
                self._send_json(200, list_today(planner_db, datetime.now().astimezone().date()))
                return
            if self.path.startswith("/api/email-content/"):
                thread_id = urllib.parse.unquote(self.path.rsplit("/", 1)[-1])
                if not thread_id or not thread_id.isalnum():
                    self._send_json(400, {"error": "invalid thread id"})
                    return
                content = fetch_gmail_thread_content(
                    assistant_db, gmail_client_secret, gmail_token, thread_id
                )
                if content is None:
                    self._send_json(404, {"error": "email thread not found"})
                    return
                self._send_json(200, content)
                return
            if self.path == "/api/analytics":
                self._send_json(200, summarize_read_events(read_log_path))
                return
            if self.path == "/api/feedback":
                events = load_feedback_events(feedback_log_path)
                self._send_json(
                    200,
                    {"hidden_article_ids": list(dict.fromkeys(
                        event["article_id"] for event in events
                    ))},
                )
                return
            if self.path in {"/api/email-reminders/daily", "/api/email-reminders/weekly"}:
                period = self.path.rsplit("/", 1)[-1]
                sync_state = get_gmail_sync_state(assistant_db)
                self._send_json(
                    200,
                    {
                        "period": period,
                        "generated_at": datetime.now(UTC).isoformat(),
                        "last_sync_at": sync_state["completed_at"] if sync_state else None,
                        "synced_thread_count": sync_state["thread_count"] if sync_state else 0,
                        "items": list_reminders(assistant_db, period, datetime.now(UTC)),
                    },
                )
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            planner_paths = {
                "/api/tasks",
                "/api/task-status",
                "/api/tasks/delete",
                "/api/health/checkin",
                "/api/health/sync",
                "/api/agent-jobs",
                "/api/agent-jobs/cancel",
                "/api/agent-jobs/hide",
                "/api/agent-jobs/attach",
                "/api/agent-jobs/resume",
            }
            if self.path not in {
                "/api/read",
                "/api/feedback",
                "/api/email-status",
                *planner_paths,
            }:
                self._send_json(404, {"error": "not found"})
                return
            try:
                request = self._read_json()
                now = datetime.now(UTC)
                local_day = datetime.now().astimezone().date()
                if self.path == "/api/agent-jobs":
                    self._send_json(201, create_job(agent_db, repositories, request))
                    return
                if self.path == "/api/agent-jobs/cancel":
                    if not request_cancel(agent_db, request.get("job_id", "")):
                        raise ValueError("invalid agent job")
                    self._send_json(202, {"cancel_requested": True})
                    return
                if self.path == "/api/agent-jobs/hide":
                    if not hide_job(agent_db, request.get("job_id", "")):
                        raise ValueError("invalid agent job")
                    self._send_json(202, {"archived": True})
                    return
                if self.path == "/api/agent-jobs/resume":
                    if not resume_job(
                        agent_db,
                        request.get("job_id", ""),
                        request.get("instruction"),
                    ):
                        raise ValueError("invalid agent job response")
                    self._send_json(202, {"resumed": True})
                    return
                if self.path == "/api/agent-jobs/attach":
                    if not attach_to_job(
                        agent_db,
                        request.get("job_id", ""),
                        request.get("instruction"),
                    ):
                        raise ValueError("invalid agent job message")
                    self._send_json(202, {"attached": True})
                    return
                if self.path == "/api/tasks":
                    self._send_json(201, create_task(planner_db, request, now))
                    return
                if self.path == "/api/task-status":
                    completed = request.get("completed")
                    if not isinstance(completed, bool) or not set_task_completion(
                        planner_db, request.get("task_id", ""), completed, local_day, now
                    ):
                        raise ValueError("invalid task status")
                    self._send_json(202, {"updated": True})
                    return
                if self.path == "/api/tasks/delete":
                    if not delete_task(planner_db, request.get("task_id", "")):
                        raise ValueError("invalid task")
                    self._send_json(202, {"deleted": True})
                    return
                if self.path == "/api/health/checkin":
                    self._send_json(
                        202, upsert_health_checkin(planner_db, request, now, "manual")
                    )
                    return
                if self.path == "/api/health/sync":
                    if not self._health_sync_authorized():
                        self._send_json(401, {"error": "unauthorized"})
                        return
                    self._send_json(
                        202, upsert_health_checkin(planner_db, request, now, "shortcut")
                    )
                    return
                if self.path == "/api/email-status":
                    if request.get("action") == "read":
                        try:
                            updated = mark_gmail_thread_read(
                                assistant_db,
                                gmail_client_secret,
                                gmail_token,
                                request["thread_id"],
                                now,
                            )
                        except RuntimeError as error:
                            self._send_json(503, {"error": str(error)})
                            return
                        if not updated:
                            raise ValueError("invalid email thread")
                        self._send_json(202, {"updated": True})
                        return
                    if not update_status(
                        assistant_db,
                        request["thread_id"],
                        request["action"],
                        now,
                    ):
                        raise ValueError("invalid email action")
                    self._send_json(202, {"updated": True})
                    return
                article_id = request["article_id"]
                surface = request["surface"]
                if not isinstance(article_id, str) or surface not in READ_SURFACES:
                    raise ValueError("invalid event")
                articles = json.loads(articles_path.read_text(encoding="utf-8"))["articles"]
                article = next(item for item in articles if item["id"] == article_id)
                if self.path == "/api/read":
                    append_read_event(read_log_path, article, surface)
                else:
                    append_feedback_event(feedback_log_path, article, surface)
            except (
                FileNotFoundError,
                StopIteration,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                self._send_json(400, {"error": "invalid event"})
                return
            self._send_json(202, {"recorded": True})

    return functools.partial(DailyReaderHandler, directory=site)


def update_articles(
    feeds_path: Path,
    keywords_path: Path,
    output_path: Path,
    feedback_log_path: Path = Path("data/feedback-events.jsonl"),
    selection_history_path: Path = Path("data/selection-history.jsonl"),
    update_stats_path: Path = Path("data/update-stats.jsonl"),
) -> None:
    now = datetime.now(UTC)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    highlights_path = output_path.parent / "highlights.json"
    previous_article_ids = _load_article_ids(output_path)
    previous_highlight_ids = _load_highlight_ids(highlights_path)
    settings, feeds = load_config(feeds_path)
    keywords = load_keywords(keywords_path)
    articles, errors = collect(feeds, keywords, settings, now)
    if not articles and errors:
        LOGGER.error("All feeds failed; keeping the previous data file")
        return
    highlights_updated = generate_highlights(
        articles,
        highlights_path,
        Path("config/highlight-schema.json"),
        now,
        feedback_log_path,
        selection_history_path,
    )
    current_article_ids = {article.id for article in articles}
    stats = build_update_stats(
        now,
        current_article_ids,
        previous_article_ids,
        _load_highlight_ids(highlights_path),
        previous_highlight_ids,
        highlights_updated,
    )
    write_output(output_path, articles, errors, now, stats)
    append_update_stats(update_stats_path, stats)
    LOGGER.info(
        "Updated %s: articles=%d new=%d highlighted_new_articles=%d "
        "highlights=%d new_highlights=%d kept_highlights=%d",
        output_path,
        stats["total_articles"],
        stats["new_articles"],
        stats["new_articles_highlighted"],
        stats["total_highlights"],
        stats["new_highlights"],
        stats["kept_highlights"],
    )


def run_scheduler(
    feeds_path: Path,
    keywords_path: Path,
    output_path: Path,
    update_hours: set[int],
    feedback_log_path: Path,
    selection_history_path: Path,
    update_stats_path: Path,
) -> None:
    update_articles(
        feeds_path,
        keywords_path,
        output_path,
        feedback_log_path,
        selection_history_path,
        update_stats_path,
    )
    started_at = datetime.now().astimezone()
    last_run: tuple[str, int] | None = (
        started_at.date().isoformat(),
        started_at.hour,
    )
    while True:
        local_now = datetime.now().astimezone()
        run_key = (local_now.date().isoformat(), local_now.hour)
        if local_now.hour in update_hours and local_now.minute < 5 and run_key != last_run:
            update_articles(
                feeds_path,
                keywords_path,
                output_path,
                feedback_log_path,
                selection_history_path,
                update_stats_path,
            )
            last_run = run_key
        sleep(60)


def run_gmail_scheduler(
    database: Path,
    client_secret: Path,
    token_path: Path,
    interval_minutes: int,
) -> None:
    while True:
        try:
            count = sync_gmail(database, client_secret, token_path)
            LOGGER.info("Synchronized %d Gmail threads", count)
        except Exception:  # noqa: BLE001
            LOGGER.exception("Gmail synchronization failed")
        sleep(interval_minutes * 60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve Daily Reader on localhost")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--site", type=Path, default=Path("site"))
    parser.add_argument("--feeds", type=Path, default=Path("config/feeds.toml"))
    parser.add_argument("--keywords", type=Path, default=Path("config/keywords.toml"))
    parser.add_argument("--update-hours", default="8,10,12,17,20,22")
    parser.add_argument("--read-log", type=Path, default=Path("data/read-events.jsonl"))
    parser.add_argument(
        "--feedback-log", type=Path, default=Path("data/feedback-events.jsonl")
    )
    parser.add_argument(
        "--selection-history",
        type=Path,
        default=Path("data/selection-history.jsonl"),
    )
    parser.add_argument(
        "--update-stats", type=Path, default=Path("data/update-stats.jsonl")
    )
    parser.add_argument("--assistant-db", type=Path, default=Path("data/assistant.sqlite3"))
    parser.add_argument("--planner-db", type=Path, default=Path("data/planner.sqlite3"))
    parser.add_argument("--agent-db", type=Path, default=Path("data/agent.sqlite3"))
    parser.add_argument(
        "--agent-repositories",
        type=Path,
        default=Path("config/agent-repositories.toml"),
    )
    parser.add_argument(
        "--health-sync-token",
        type=Path,
        default=Path("secrets/health-sync-token.txt"),
    )
    parser.add_argument(
        "--gmail-client-secret", type=Path, default=Path("secrets/gmail-client.json")
    )
    parser.add_argument(
        "--gmail-token", type=Path, default=Path("secrets/gmail-token.json")
    )
    parser.add_argument("--gmail-sync-minutes", type=int, default=15)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    output_path = args.site / "data" / "articles.json"
    update_hours = {int(hour) for hour in args.update_hours.split(",")}
    if not update_hours <= set(range(24)):
        raise SystemExit("--update-hours must contain hours from 0 to 23")
    if args.gmail_sync_minutes < 1:
        raise SystemExit("--gmail-sync-minutes must be at least 1")
    agent_repositories = load_repositories(args.agent_repositories)
    deployment_info = build_deployment_info(Path.cwd(), datetime.now(UTC))

    scheduler = threading.Thread(
        target=run_scheduler,
        args=(
            args.feeds,
            args.keywords,
            output_path,
            update_hours,
            args.feedback_log,
            args.selection_history,
            args.update_stats,
        ),
        daemon=True,
    )
    scheduler.start()
    gmail_scheduler = threading.Thread(
        target=run_gmail_scheduler,
        args=(
            args.assistant_db,
            args.gmail_client_secret,
            args.gmail_token,
            args.gmail_sync_minutes,
        ),
        daemon=True,
    )
    gmail_scheduler.start()

    handler = make_handler(
        args.site,
        output_path,
        args.read_log,
        args.feedback_log,
        args.assistant_db,
        args.gmail_client_secret,
        args.gmail_token,
        args.planner_db,
        args.health_sync_token,
        args.agent_db,
        agent_repositories,
        deployment_info,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    LOGGER.info("Serving %s at http://%s:%d", args.site, args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
