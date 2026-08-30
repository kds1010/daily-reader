from __future__ import annotations

import argparse
import base64
import binascii
import functools
import hmac
import json
import logging
import plistlib
import select
import stat
import subprocess
import threading
import urllib.parse
from collections import Counter
from datetime import UTC, datetime
from datetime import date as calendar_date
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from importlib.metadata import PackageNotFoundError, version
from ipaddress import IPv4Network, ip_address, ip_network
from pathlib import Path
from time import monotonic, sleep

from daily_reader.agent_jobs import (
    DEFAULT_MODEL,
    DEFAULT_REASONING_EFFORT,
    FALLBACK_MODEL_OPTIONS,
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
    GmailAuthorizationRequired,
    fetch_gmail_thread_content,
    get_gmail_sync_state,
    get_gmail_sync_status,
    list_reminders,
    list_unread_threads,
    mark_gmail_thread_read,
    sync_gmail,
    update_status,
)
from daily_reader.highlights import generate_highlights
from daily_reader.tanomi_client import (
    DEFAULT_BASE_URL,
    MODEL,
    MODES,
    TASK_ID,
    TanomiClient,
    TanomiError,
    TanomiUnavailable,
)

LOGGER = logging.getLogger(__name__)
MODEL_CACHE_TTL = 300.0
MODEL_CACHE_LOCK = threading.Lock()
_model_cache: tuple[float, list[dict[str, object]]] | None = None
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
SIDESTORE_REQUIRED_FILES = ("source.json", "DailyReader.ipa", "icon.png")
SIDESTORE_REMOTE_REQUIRED_FILES = ("remote-source.json", "icon.png")
SIDESTORE_REMOTE_TOKEN_LENGTH = 43
SIDESTORE_REMOTE_SOURCE_SUBTITLE = "個人用の外出先更新ソース"
SIDESTORE_REMOTE_TOKEN_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
IOS_BUNDLE_IDENTIFIER = "net.skmin.DailyReader"
MACOS_BUNDLE_IDENTIFIER = "net.skmin.DailyReader.mac"


def present_agent_job(
    job: dict[str, object], repositories: dict[str, dict[str, str]]
) -> dict[str, object]:
    """Add the configured display label without changing the worker repository key."""
    presented = dict(job)
    repository = presented.get("repository")
    configured = repositories.get(repository) if isinstance(repository, str) else None
    presented["repository_label"] = (
        configured.get("label", repository) if configured is not None else repository
    )
    return presented


def present_agent_jobs(
    jobs: list[dict[str, object]], repositories: dict[str, dict[str, str]]
) -> list[dict[str, object]]:
    return [present_agent_job(job, repositories) for job in jobs]


def present_agent_notification_jobs(jobs: list[dict[str, object]]) -> list[dict[str, object]]:
    fields = ("id", "repository", "prompt", "status", "phase", "summary", "updated_at")
    return [{key: job[key] for key in fields if key in job} for job in jobs]


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


def read_codex_models(timeout: float = 10) -> list[dict[str, object]]:
    """Read visible model and reasoning-effort choices from the Codex app-server."""
    process = subprocess.Popen(
        ["codex", "app-server", "--stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    request = {
        "id": 2,
        "method": "model/list",
        "params": {},
    }
    try:
        if process.stdin is None or process.stdout is None:
            raise RuntimeError("Codex app server pipes are unavailable")
        process.stdin.write(
            json.dumps(
                {
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "clientInfo": {"name": "daily-reader", "version": "0.1"},
                        "capabilities": {"experimentalApi": True},
                    },
                }
            )
            + "\n"
        )
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
            if message.get("id") != request["id"]:
                continue
            if "error" in message:
                raise RuntimeError("Codex rejected the model-list request")
            result = message.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Codex returned an invalid model-list response")
            raw_models = result.get("models", result.get("data", []))
            if not isinstance(raw_models, list):
                raise RuntimeError("Codex returned an invalid model list")
            models = []
            for raw in raw_models:
                if not isinstance(raw, dict):
                    continue
                slug = raw.get("slug", raw.get("model"))
                visibility = raw.get("visibility", "list")
                if not isinstance(slug, str) or not slug or visibility != "list":
                    continue
                levels = raw.get(
                    "supportedReasoningLevels",
                    raw.get("supported_reasoning_levels", raw.get("supportedReasoningEfforts", [])),
                )
                efforts = []
                if isinstance(levels, list):
                    for level in levels:
                        effort = level.get("effort") if isinstance(level, dict) else level
                        if isinstance(effort, str) and effort and effort not in efforts:
                            efforts.append(effort)
                if not efforts:
                    continue
                display_name = raw.get("displayName", raw.get("display_name", slug))
                default_effort = raw.get(
                    "defaultReasoningLevel",
                    raw.get("default_reasoning_level", raw.get("defaultReasoningEffort")),
                )
                if not isinstance(display_name, str):
                    display_name = slug
                if not isinstance(default_effort, str) or default_effort not in efforts:
                    default_effort = efforts[0]
                models.append(
                    {
                        "slug": slug,
                        "display_name": display_name,
                        "default_reasoning_effort": default_effort,
                        "supported_reasoning_efforts": efforts,
                    }
                )
            if not models:
                raise RuntimeError("Codex returned no visible models")
            return models
        raise TimeoutError("Codex model-list request timed out")
    finally:
        process.terminate()
        try:
            process.wait(timeout=1)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=1)


def agent_model_options(*, now: float | None = None) -> list[dict[str, object]]:
    """Return a cached model catalog, retaining the last good result on failure."""
    global _model_cache
    timestamp = monotonic() if now is None else now
    with MODEL_CACHE_LOCK:
        if _model_cache is not None and timestamp - _model_cache[0] < MODEL_CACHE_TTL:
            return _model_cache[1]
        try:
            models = read_codex_models()
        except (FileNotFoundError, OSError, RuntimeError, TimeoutError) as error:
            LOGGER.warning("Could not read Codex models: %s", error)
            models = _model_cache[1] if _model_cache is not None else FALLBACK_MODEL_OPTIONS
        _model_cache = (timestamp, models)
        return models
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


def _validated_release_version(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    parts = value.split(".")
    if len(parts) != 3 or not all(part.isdigit() for part in parts):
        return None
    return value


def sidestore_release_version(directory: Path) -> str | None:
    source_path = directory / "source.json"
    ipa_path = directory / "DailyReader.ipa"
    icon_path = directory / "icon.png"
    if not all(
        path.is_file() and not path.is_symlink()
        for path in (source_path, ipa_path, icon_path)
    ):
        return None
    try:
        source = json.loads(source_path.read_text(encoding="utf-8"))
        app = source["apps"][0]
        release = app["versions"][0]
    except (IndexError, KeyError, OSError, TypeError, UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(app, dict) or not isinstance(release, dict):
        return None
    version_value = _validated_release_version(release.get("version"))
    size_value = release.get("size")
    download_url = release.get("downloadURL")
    try:
        ipa_size = ipa_path.stat().st_size
    except OSError:
        return None
    if (
        app.get("bundleIdentifier") != IOS_BUNDLE_IDENTIFIER
        or version_value is None
        or not isinstance(size_value, int)
        or isinstance(size_value, bool)
        or size_value != ipa_size
        or not isinstance(download_url, str)
        or urllib.parse.urlsplit(download_url).path.rsplit("/", 1)[-1]
        != "DailyReader.ipa"
    ):
        return None
    return version_value


def macos_release_version(app: Path) -> str | None:
    contents = app / "Contents"
    macos_directory = contents / "MacOS"
    info_path = app / "Contents/Info.plist"
    executable = app / "Contents/MacOS/Daymeld"
    if (
        not app.is_dir()
        or app.is_symlink()
        or contents.is_symlink()
        or macos_directory.is_symlink()
        or not info_path.is_file()
        or info_path.is_symlink()
        or not executable.is_file()
        or executable.is_symlink()
    ):
        return None
    try:
        info = plistlib.loads(info_path.read_bytes())
    except (OSError, plistlib.InvalidFileException):
        return None
    if info.get("CFBundleIdentifier") != MACOS_BUNDLE_IDENTIFIER:
        return None
    return _validated_release_version(info.get("CFBundleShortVersionString"))


def build_native_release_info(
    sidestore_directory: Path, macos_app: Path
) -> dict[str, str]:
    releases = {}
    if ios_version := sidestore_release_version(sidestore_directory):
        releases["ios_release_version"] = ios_version
    if mac_version := macos_release_version(macos_app):
        releases["macos_release_version"] = mac_version
    return releases


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
    tanomi_client: TanomiClient | None = None,
    sidestore_directory: Path = Path("data/sidestore"),
    macos_release_app: Path = Path("data/macos/Daymeld.app"),
):
    repositories = agent_repositories or {}
    tanomi = tanomi_client

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

        def _tanomi_error(self, error: Exception) -> None:
            if isinstance(error, TanomiError) and error.status is not None:
                self._send_json(error.status, {"error": str(error)})
            elif isinstance(error, TanomiUnavailable):
                self._send_json(503, {"error": str(error)})
            else:
                LOGGER.warning("tanomi request failed: %s", error)
                self._send_json(502, {"error": "tanomi の応答を処理できませんでした"})

        def _tanomi_parts(self) -> tuple[list[str], dict[str, str]]:
            parsed = urllib.parse.urlsplit(self.path)
            parts = [urllib.parse.unquote(part) for part in parsed.path.split("/")]
            query = dict(urllib.parse.parse_qsl(parsed.query, keep_blank_values=True))
            return parts, query

        def do_GET(self) -> None:  # noqa: N802
            path = urllib.parse.urlsplit(self.path).path
            if path == "/sidestore" or path.startswith("/sidestore/"):
                self._send_json(404, {"error": "not found"})
                return
            if path.startswith("/api/tanomi/"):
                if tanomi is None:
                    self._send_json(503, {"error": "tanomi は設定されていません"})
                    return
                parts, query = self._tanomi_parts()
                try:
                    if parts == ["", "api", "tanomi", "repos"]:
                        payload = tanomi.request_json("GET", "/api/repos")
                        if isinstance(payload, dict) and isinstance(payload.get("repos"), list):
                            payload = payload["repos"]
                    elif parts == ["", "api", "tanomi", "tasks"]:
                        limit = int(query.get("limit", "50"))
                        if not 1 <= limit <= 200:
                            raise ValueError("invalid limit")
                        payload = tanomi.request_json(
                            "GET", "/api/tasks", query={"limit": str(limit)}
                        )
                    elif parts == ["", "api", "tanomi", "usage"]:
                        payload = tanomi.request_json("GET", "/api/usage")
                    elif parts == ["", "api", "tanomi", "health"]:
                        payload = tanomi.request_json("GET", "/api/health")
                    elif len(parts) == 5 and parts[4] == "tasks":
                        raise ValueError("invalid tanomi path")
                    elif len(parts) == 5 and parts[3] == "tasks" and TASK_ID.fullmatch(parts[4]):
                        payload = tanomi.request_json("GET", f"/api/tasks/{parts[4]}")
                    elif (
                        len(parts) == 6
                        and parts[3] == "tasks"
                        and TASK_ID.fullmatch(parts[4])
                        and parts[5] == "stream"
                    ):
                        offset = int(query.get("offset", "0"))
                        if offset < 0:
                            raise ValueError("invalid offset")
                        self.send_response(200)
                        self.send_header("Content-Type", "text/event-stream")
                        self.send_header("Cache-Control", "no-cache")
                        self.send_header("Connection", "keep-alive")
                        self.end_headers()
                        for line in tanomi.stream(parts[4], offset):
                            self.wfile.write(line)
                            self.wfile.flush()
                        return
                    else:
                        raise ValueError("invalid tanomi path")
                    self._send_json(200, payload)
                except ValueError:
                    self._send_json(400, {"error": "invalid tanomi request"})
                except Exception as error:  # noqa: BLE001
                    self._tanomi_error(error)
                return
            if self.path == "/api/deployment":
                payload = dict(deployment_info or {})
                payload.update(
                    build_native_release_info(
                        sidestore_directory,
                        macos_release_app,
                    )
                )
                self._send_json(200, payload)
                return
            if self.path == "/api/codex-usage":
                try:
                    self._send_json(200, read_codex_rate_limits())
                except (FileNotFoundError, OSError, RuntimeError, TimeoutError) as error:
                    LOGGER.warning("Could not read Codex usage: %s", error)
                    self._send_json(503, {"error": "Codexの使用状況を取得できませんでした"})
                return
            if self.path == "/api/agent-jobs":
                model_options = agent_model_options()
                self._send_json(
                    200,
                    {
                        "repositories": [
                            {"name": item["name"], "label": item["label"]}
                            for item in repositories.values()
                        ],
                        "models": model_options,
                        "default_model": DEFAULT_MODEL,
                        "default_reasoning_effort": DEFAULT_REASONING_EFFORT,
                        "jobs": present_agent_jobs(list_jobs(agent_db), repositories),
                        "archived_jobs": present_agent_jobs(
                            list_archived_jobs(agent_db), repositories
                        ),
                    },
                )
                return
            if self.path == "/api/agent-notifications":
                self._send_json(200, {"jobs": present_agent_notification_jobs(list_jobs(agent_db))})
                return
            if self.path.startswith("/api/agent-jobs/"):
                job_id = urllib.parse.unquote(self.path.rsplit("/", 1)[-1])
                job = get_job(agent_db, job_id)
                if job is None:
                    self._send_json(404, {"error": "agent job not found"})
                else:
                    self._send_json(200, present_agent_job(job, repositories))
                return
            if self.path == "/api/today":
                self._send_json(200, list_today(planner_db, datetime.now().astimezone().date()))
                return
            if self.path.startswith("/api/email-content/"):
                thread_id = urllib.parse.unquote(self.path.rsplit("/", 1)[-1])
                if not thread_id or not thread_id.isalnum():
                    self._send_json(400, {"error": "invalid thread id"})
                    return
                try:
                    content = fetch_gmail_thread_content(
                        assistant_db, gmail_client_secret, gmail_token, thread_id
                    )
                except GmailAuthorizationRequired as error:
                    self._send_json(503, {"error": str(error)})
                    return
                except Exception:  # noqa: BLE001
                    LOGGER.exception("Could not fetch Gmail thread content")
                    self._send_json(502, {"error": "Gmail本文を取得できませんでした"})
                    return
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
            if self.path == "/api/emails/unread":
                sync_state = get_gmail_sync_state(assistant_db)
                sync_status = get_gmail_sync_status(assistant_db) or {}
                self._send_json(
                    200,
                    {
                        "generated_at": datetime.now(UTC).isoformat(),
                        "last_sync_at": sync_state["completed_at"] if sync_state else None,
                        "synced_thread_count": sync_state["thread_count"] if sync_state else 0,
                        "last_attempt_at": sync_status.get("last_attempt_at"),
                        "sync_error": sync_status.get("last_error"),
                        "authorization_required": bool(sync_status.get("authorization_required")),
                        "can_mark_read": bool(sync_status.get("can_mark_read")),
                        "items": list_unread_threads(assistant_db, datetime.now(UTC)),
                    },
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
            path = urllib.parse.urlsplit(self.path).path
            if path.startswith("/api/tanomi/"):
                if tanomi is None:
                    self._send_json(503, {"error": "tanomi は設定されていません"})
                    return
                parts, _ = self._tanomi_parts()
                try:
                    request = self._read_json(1_000_000)
                    if parts == ["", "api", "tanomi", "tasks"]:
                        prompt = request.get("prompt")
                        if (
                            not isinstance(prompt, str)
                            or not prompt.strip()
                            or len(prompt) > 100_000
                        ):
                            raise ValueError("invalid prompt")
                        payload: dict[str, object] = {"prompt": prompt}
                        parent_id = request.get("parent_id")
                        if parent_id is not None:
                            if not isinstance(parent_id, str) or not TASK_ID.fullmatch(parent_id):
                                raise ValueError("invalid parent_id")
                            payload["parent_id"] = parent_id
                        else:
                            for key in ("repo", "model", "permission_mode"):
                                if key in request:
                                    payload[key] = request[key]
                            if "model" in payload and (
                                not isinstance(payload["model"], str)
                                or not MODEL.fullmatch(payload["model"])
                            ):
                                raise ValueError("invalid model")
                            if (
                                "permission_mode" in payload
                                and payload["permission_mode"] not in MODES
                            ):
                                raise ValueError("invalid permission_mode")
                        self._send_json(201, tanomi.request_json("POST", "/api/tasks", payload))
                        return
                    if (
                        len(parts) == 6
                        and parts[3] == "tasks"
                        and TASK_ID.fullmatch(parts[4])
                        and parts[5]
                        in {"stop", "retry", "deploy", "archive", "unarchive", "restore"}
                    ):
                        self._send_json(
                            200,
                            tanomi.request_json("POST", f"/api/tasks/{parts[4]}/{parts[5]}"),
                        )
                        return
                    raise ValueError("invalid tanomi path")
                except ValueError:
                    self._send_json(400, {"error": "invalid tanomi request"})
                except Exception as error:  # noqa: BLE001
                    self._tanomi_error(error)
                return
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
                    job = create_job(
                        agent_db,
                        repositories,
                        request,
                        agent_model_options(),
                    )
                    self._send_json(201, present_agent_job(job, repositories))
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

        def do_DELETE(self) -> None:  # noqa: N802
            if not urllib.parse.urlsplit(self.path).path.startswith("/api/tanomi/"):
                self._send_json(404, {"error": "not found"})
                return
            if tanomi is None:
                self._send_json(503, {"error": "tanomi は設定されていません"})
                return
            parts, query = self._tanomi_parts()
            if len(parts) != 5 or parts[3] != "tasks" or not TASK_ID.fullmatch(parts[4]):
                self._send_json(400, {"error": "invalid tanomi path"})
                return
            purge = query.get("purge", "false")
            if purge not in {"true", "false"}:
                self._send_json(400, {"error": "invalid purge"})
                return
            try:
                payload = tanomi.request_json(
                    "DELETE", f"/api/tasks/{parts[4]}", query={"purge": purge}
                )
                self._send_json(200, payload)
            except Exception as error:  # noqa: BLE001
                self._tanomi_error(error)

    return functools.partial(DailyReaderHandler, directory=site)


def make_sidestore_handler(directory: Path):
    """Serve only generated SideStore distribution files on the local network."""

    class SideStoreHandler(SimpleHTTPRequestHandler):
        allowed_paths = frozenset({"/source.json", "/DailyReader.ipa", "/icon.png"})

        def send_head(self):
            parsed = urllib.parse.urlsplit(self.path)
            if (
                parsed.scheme
                or parsed.netloc
                or parsed.query
                or parsed.fragment
                or parsed.path not in self.allowed_paths
            ):
                self.send_error(404)
                return None
            requested_file = Path(self.translate_path(self.path))
            if requested_file.is_symlink() or not requested_file.is_file():
                self.send_error(404)
                return None
            return super().send_head()

        def end_headers(self) -> None:
            path = urllib.parse.urlsplit(self.path).path
            if path == "/source.json":
                self.send_header("Cache-Control", "no-store")
            super().end_headers()

        def log_message(self, format: str, *args: object) -> None:
            LOGGER.info("SideStore LAN: " + format, *args)

    return functools.partial(SideStoreHandler, directory=directory)


def load_sidestore_remote_token(path: Path) -> str:
    if path.is_symlink():
        raise ValueError("SideStore remote token file must not be a symlink")
    token = path.read_text(encoding="utf-8").strip()
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise ValueError("SideStore remote token file permissions must be 0600")
    try:
        decoded = base64.b64decode(token + "=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    is_canonical = (
        len(decoded) == 32
        and base64.urlsafe_b64encode(decoded).decode().rstrip("=") == token
    )
    if (
        len(token) != SIDESTORE_REMOTE_TOKEN_LENGTH
        or not set(token) <= SIDESTORE_REMOTE_TOKEN_CHARACTERS
        or not is_canonical
    ):
        raise ValueError("SideStore remote token must be a canonical 32-byte URL-safe token")
    return token


def sidestore_remote_ipas(directory: Path, token: str) -> tuple[str, ...]:
    source = json.loads((directory / "remote-source.json").read_text(encoding="utf-8"))
    if (
        not isinstance(source, dict)
        or source.get("subtitle") != SIDESTORE_REMOTE_SOURCE_SUBTITLE
    ):
        raise ValueError("SideStore remote source must hide its credential URL")
    app = source["apps"][0]
    versions = app["versions"]
    if not isinstance(versions, list) or not versions:
        raise ValueError("SideStore remote source has no versions")
    urls: dict[str, str] = {
        "source.json": source["sourceURL"],
        "icon.png": app["iconURL"],
    }
    ipa_names = []
    declared_sizes = {}
    for version_item in versions:
        if not isinstance(version_item, dict):
            raise ValueError("SideStore remote source contains invalid version metadata")
        version_value = version_item.get("version")
        date_value = version_item.get("date")
        size_value = version_item.get("size")
        download_url = version_item["downloadURL"]
        if (
            not isinstance(version_value, str)
            or not isinstance(date_value, str)
            or not isinstance(size_value, int)
            or isinstance(size_value, bool)
            or size_value < 0
            or not isinstance(download_url, str)
        ):
            raise ValueError("SideStore remote source contains invalid version metadata")
        try:
            parsed_date = calendar_date.fromisoformat(date_value)
        except ValueError as error:
            raise ValueError(
                "SideStore remote source contains invalid version metadata"
            ) from error
        if parsed_date.isoformat() != date_value:
            raise ValueError("SideStore remote source contains invalid version metadata")
        download_path = urllib.parse.urlsplit(download_url).path
        ipa_name = download_path.rsplit("/", 1)[-1]
        version_parts = version_value.split(".")
        if (
            ipa_name in urls
            or ipa_name != f"DailyReader-{version_value}.ipa"
            or len(version_parts) != 3
            or not all(part.isdigit() for part in version_parts)
        ):
            raise ValueError("SideStore remote source contains duplicate artifact URLs")
        urls[ipa_name] = download_url
        ipa_names.append(ipa_name)
        declared_sizes[ipa_name] = size_value
    if not all(isinstance(url, str) for url in urls.values()):
        raise ValueError("SideStore remote source contains non-string artifact URLs")
    expected_prefix = f"/{token}/"
    parsed_urls = {
        name: urllib.parse.urlsplit(url)
        for name, url in urls.items()
    }
    try:
        for parsed in parsed_urls.values():
            _ = parsed.port
    except ValueError as error:
        raise ValueError("SideStore remote source contains an invalid URL port") from error
    source_origin = parsed_urls["source.json"].netloc
    urls_are_safe = all(
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.netloc == source_origin
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.path == f"{expected_prefix}{name}"
        for name, parsed in parsed_urls.items()
    )
    artifact_paths = (
        directory / "remote-source.json",
        directory / "icon.png",
        *(directory / ipa_name for ipa_name in ipa_names),
    )
    if (
        not urls_are_safe
        or not all(path.is_file() and not path.is_symlink() for path in artifact_paths)
        or not all(
            (directory / ipa_name).stat().st_size == declared_sizes[ipa_name]
            for ipa_name in ipa_names
        )
    ):
        raise ValueError("SideStore remote source contains unsafe artifact URLs")
    return tuple(ipa_names)


def current_sidestore_remote_ipa(directory: Path, token: str) -> str:
    return sidestore_remote_ipas(directory, token)[0]


def make_sidestore_remote_handler(directory: Path, token: str):
    """Serve three token-protected SideStore artifacts without exposing a directory."""

    class SideStoreRemoteHandler(BaseHTTPRequestHandler):
        server_version = "DailyReaderSideStore/1.0"
        sys_version = ""

        def _requested_path(self) -> Path | None:
            parsed = urllib.parse.urlsplit(self.path)
            parts = parsed.path.split("/")
            if (
                parsed.scheme
                or parsed.netloc
                or parsed.query
                or parsed.fragment
                or len(parts) != 3
                or parts[0]
            ):
                return None
            if not hmac.compare_digest(parts[1], token):
                return None
            try:
                ipa_names = sidestore_remote_ipas(directory, token)
            except (IndexError, KeyError, OSError, TypeError, UnicodeError, ValueError):
                LOGGER.exception("SideStore remote source is invalid")
                return None
            files = {
                "source.json": directory / "remote-source.json",
                "icon.png": directory / "icon.png",
                **{ipa_name: directory / ipa_name for ipa_name in ipa_names},
            }
            return files.get(parts[2])

        def _serve(self, *, include_body: bool) -> None:
            path = self._requested_path()
            if path is None or not path.is_file() or path.is_symlink():
                self.send_error(404)
                return
            content_types = {
                ".json": "application/json; charset=utf-8",
                ".ipa": "application/octet-stream",
                ".png": "image/png",
            }
            self.send_response(200)
            self.send_header("Content-Type", content_types[path.suffix])
            self.send_header("Content-Length", str(path.stat().st_size))
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            if include_body:
                with path.open("rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        self.wfile.write(chunk)

        def do_GET(self) -> None:
            self._serve(include_body=True)

        def do_HEAD(self) -> None:
            self._serve(include_body=False)

        def _reject_method(self) -> None:
            self.send_error(404)

        def send_error(
            self,
            code: int,
            message: str | None = None,
            explain: str | None = None,
        ) -> None:
            if code == HTTPStatus.NOT_IMPLEMENTED:
                code = HTTPStatus.NOT_FOUND
                message = None
                explain = None
            super().send_error(code, message, explain)

        do_CONNECT = _reject_method
        do_DELETE = _reject_method
        do_OPTIONS = _reject_method
        do_PATCH = _reject_method
        do_POST = _reject_method
        do_PUT = _reject_method
        do_TRACE = _reject_method

        def log_message(self, format: str, *args: object) -> None:
            message = (format % args).replace(token, "<redacted>")
            LOGGER.info("SideStore remote: %s", message)

    return SideStoreRemoteHandler


class SideStoreLANServer(ThreadingHTTPServer):
    """Restrict distribution requests to this Mac and trusted local networks."""

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler,
        allowed_networks: str,
    ) -> None:
        networks = tuple(
            ip_network(value.strip(), strict=True)
            for value in allowed_networks.split(",")
            if value.strip()
        )
        if not networks or not all(isinstance(network, IPv4Network) for network in networks):
            raise ValueError("SideStore LAN networks must contain IPv4 networks")
        trusted_ranges = tuple(
            IPv4Network(value)
            for value in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16")
        )
        if not all(
            any(network.subnet_of(trusted_range) for trusted_range in trusted_ranges)
            for network in networks
        ):
            raise ValueError("SideStore LAN networks must be private or link-local")
        self.allowed_networks = networks
        super().__init__(server_address, request_handler)

    def verify_request(self, request, client_address: tuple[str, int]) -> bool:
        client_ip = ip_address(client_address[0])
        allowed = client_ip.is_loopback or any(
            client_ip in network for network in self.allowed_networks
        )
        if not allowed:
            LOGGER.warning("Rejected SideStore request from %s", client_ip)
        return allowed


def start_sidestore_server(
    directory: Path,
    host: str,
    port: int,
    allowed_networks: str,
) -> SideStoreLANServer | None:
    unsafe = [
        name
        for name in SIDESTORE_REQUIRED_FILES
        if not (directory / name).is_file() or (directory / name).is_symlink()
    ]
    if unsafe:
        LOGGER.info(
            "SideStore LAN server disabled; missing or unsafe files: %s",
            ", ".join(unsafe),
        )
        return None
    try:
        if not 1 <= port <= 65535:
            raise ValueError("SideStore LAN port must be between 1 and 65535")
        server = SideStoreLANServer(
            (host, port),
            make_sidestore_handler(directory),
            allowed_networks,
        )
    except (OSError, ValueError):
        LOGGER.exception("Could not start SideStore LAN server")
        return None
    threading.Thread(
        target=server.serve_forever,
        name="sidestore-lan-server",
        daemon=True,
    ).start()
    LOGGER.info("Serving SideStore files from %s at http://%s:%d", directory, host, port)
    return server


def start_sidestore_remote_server(
    directory: Path,
    port: int,
    token_path: Path,
) -> ThreadingHTTPServer | None:
    missing = [
        name for name in SIDESTORE_REMOTE_REQUIRED_FILES if not (directory / name).is_file()
    ]
    if missing:
        LOGGER.info("SideStore remote server disabled; missing files: %s", ", ".join(missing))
        return None
    try:
        if not 1 <= port <= 65535:
            raise ValueError("SideStore remote port must be between 1 and 65535")
        token = load_sidestore_remote_token(token_path)
        sidestore_remote_ipas(directory, token)
        server = ThreadingHTTPServer(
            ("127.0.0.1", port),
            make_sidestore_remote_handler(directory, token),
        )
    except (IndexError, KeyError, OSError, TypeError, UnicodeError, ValueError):
        LOGGER.exception("Could not start SideStore remote server")
        return None
    threading.Thread(
        target=server.serve_forever,
        name="sidestore-remote-server",
        daemon=True,
    ).start()
    LOGGER.info(
        "Serving token-protected SideStore files from %s at http://127.0.0.1:%d",
        directory,
        port,
    )
    return server


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
    parser = argparse.ArgumentParser(description="Serve Daymeld on localhost")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--tanomi-base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--site", type=Path, default=Path("site"))
    parser.add_argument("--sidestore-lan-host", default="0.0.0.0")
    parser.add_argument("--sidestore-lan-port", type=int, default=8788)
    parser.add_argument(
        "--sidestore-lan-network",
        "--sidestore-lan-networks",
        dest="sidestore_lan_networks",
        default="192.168.10.0/24,169.254.0.0/16",
    )
    parser.add_argument("--sidestore-dir", type=Path, default=Path("data/sidestore"))
    parser.add_argument(
        "--macos-release-app",
        type=Path,
        default=Path("data/macos/Daymeld.app"),
    )
    parser.add_argument("--sidestore-remote-port", type=int, default=8789)
    parser.add_argument(
        "--sidestore-remote-token",
        type=Path,
        default=Path("secrets/sidestore-remote-token.txt"),
    )
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
        TanomiClient(args.tanomi_base_url),
        sidestore_directory=args.sidestore_dir,
        macos_release_app=args.macos_release_app,
    )
    server = ThreadingHTTPServer((args.host, args.port), handler)
    sidestore_server = start_sidestore_server(
        args.sidestore_dir,
        args.sidestore_lan_host,
        args.sidestore_lan_port,
        args.sidestore_lan_networks,
    )
    sidestore_remote_server = start_sidestore_remote_server(
        args.sidestore_dir,
        args.sidestore_remote_port,
        args.sidestore_remote_token,
    )
    LOGGER.info("Serving %s at http://%s:%d", args.site, args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping")
    finally:
        if sidestore_server is not None:
            sidestore_server.shutdown()
            sidestore_server.server_close()
        if sidestore_remote_server is not None:
            sidestore_remote_server.shutdown()
            sidestore_remote_server.server_close()
        server.server_close()


if __name__ == "__main__":
    main()
