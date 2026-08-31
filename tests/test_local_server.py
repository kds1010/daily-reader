import json
import plistlib
import subprocess
from datetime import datetime
from io import BytesIO
from ipaddress import IPv4Network
from pathlib import Path

import pytest

from daily_reader.email_assistant import (
    GmailAuthorizationRequired,
    GmailThreadRecord,
    upsert_thread,
)
from daily_reader.local_server import (
    SideStoreLANServer,
    _normalize_codex_models,
    append_feedback_event,
    append_read_event,
    append_update_stats,
    build_deployment_info,
    build_native_release_info,
    build_parser,
    build_update_stats,
    current_sidestore_remote_ipa,
    load_feedback_events,
    load_sidestore_remote_token,
    macos_release_version,
    make_handler,
    make_sidestore_handler,
    make_sidestore_remote_handler,
    present_agent_job,
    present_agent_jobs,
    read_codex_rate_limits,
    sidestore_release_version,
    start_sidestore_remote_server,
    start_sidestore_server,
    summarize_read_events,
    update_articles,
)
from daily_reader.tanomi_client import TanomiUnavailable

TOKEN = "a" * 42 + "A"


def test_normalize_codex_models_supports_current_catalog_and_ultra() -> None:
    result = {
        "data": [
            {
                "id": "gpt-5.6-sol",
                "displayName": "GPT-5.6-Sol",
                "hidden": False,
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "ultra"},
                    {"reasoningEffort": "ultra"},
                ],
                "defaultReasoningEffort": "low",
            },
            {
                "id": "gpt-5.6-luna",
                "displayName": "GPT-5.6-Luna",
                "hidden": False,
                "supportedReasoningEfforts": [
                    {"reasoningEffort": "low"},
                    {"reasoningEffort": "medium"},
                    {"reasoningEffort": "max"},
                ],
                "defaultReasoningEffort": "medium",
            },
            {
                "id": "hidden-model",
                "hidden": True,
                "supportedReasoningEfforts": [{"reasoningEffort": "low"}],
            },
        ]
    }

    assert _normalize_codex_models(result) == [
        {
            "slug": "gpt-5.6-sol",
            "display_name": "GPT-5.6-Sol",
            "default_reasoning_effort": "low",
            "supported_reasoning_efforts": ["low", "ultra"],
        },
        {
            "slug": "gpt-5.6-luna",
            "display_name": "GPT-5.6-Luna",
            "default_reasoning_effort": "medium",
            "supported_reasoning_efforts": ["low", "medium", "max"],
        },
    ]


def test_normalize_codex_models_keeps_legacy_catalog_compatibility() -> None:
    result = {
        "models": [
            {
                "slug": "legacy-model",
                "visibility": "list",
                "display_name": "Legacy model",
                "supportedReasoningLevels": [
                    {"effort": "medium"},
                    "high",
                ],
                "default_reasoning_level": "high",
            },
            {
                "slug": "unlisted-model",
                "visibility": "hidden",
                "supportedReasoningLevels": [{"effort": "low"}],
            },
            {"slug": "malformed-model", "supportedReasoningLevels": []},
        ]
    }

    assert _normalize_codex_models(result) == [
        {
            "slug": "legacy-model",
            "display_name": "Legacy model",
            "default_reasoning_effort": "high",
            "supported_reasoning_efforts": ["medium", "high"],
        }
    ]


def test_present_agent_job_uses_display_label_without_changing_repository_key() -> None:
    job = {"id": "job-1", "repository": "daily-reader", "prompt": "Do it"}
    repositories = {"daily-reader": {"name": "daily-reader", "label": "Daymeld"}}

    presented = present_agent_job(job, repositories)

    assert presented == {**job, "repository_label": "Daymeld"}
    assert job == {"id": "job-1", "repository": "daily-reader", "prompt": "Do it"}


def test_present_agent_jobs_falls_back_for_removed_repository() -> None:
    jobs = [{"id": "job-1", "repository": "old-repository"}]

    assert present_agent_jobs(jobs, {}) == [
        {"id": "job-1", "repository": "old-repository", "repository_label": "old-repository"}
    ]


def test_agent_jobs_endpoint_presents_labels_for_active_and_archived_jobs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    repositories = {
        "daily-reader": {"name": "daily-reader", "label": "Daymeld"},
        "old-repository": {"name": "old-repository", "label": "Old"},
    }
    monkeypatch.setattr(
        "daily_reader.local_server.list_jobs",
        lambda _path: [{"id": "active", "repository": "daily-reader"}],
    )
    monkeypatch.setattr(
        "daily_reader.local_server.list_archived_jobs",
        lambda _path: [{"id": "archived", "repository": "removed"}],
    )
    model_options = [
        {
            "slug": "gpt-5.6-luna",
            "display_name": "GPT-5.6-Luna",
            "default_reasoning_effort": "low",
            "supported_reasoning_efforts": ["low", "medium"],
        }
    ]
    monkeypatch.setattr(
        "daily_reader.local_server.agent_model_options", lambda: model_options
    )
    handler_factory = make_handler(
        tmp_path / "site",
        tmp_path / "articles.json",
        tmp_path / "read.jsonl",
        tmp_path / "feedback.jsonl",
        tmp_path / "assistant.sqlite3",
        tmp_path / "gmail-client.json",
        tmp_path / "gmail-token.json",
        agent_repositories=repositories,
    )
    handler = handler_factory.func.__new__(handler_factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.path = "/api/agent-jobs"

    handler.do_GET()

    assert responses == [
        (
            200,
            {
                "repositories": [
                    {"name": "daily-reader", "label": "Daymeld"},
                    {"name": "old-repository", "label": "Old"},
                ],
                "models": model_options,
                "default_model": "gpt-5.6-luna",
                "default_reasoning_effort": "low",
                "jobs": [
                    {
                        "id": "active",
                        "repository": "daily-reader",
                        "repository_label": "Daymeld",
                    }
                ],
                "archived_jobs": [
                    {
                        "id": "archived",
                        "repository": "removed",
                        "repository_label": "removed",
                    }
                ],
            },
        )
    ]


class FakeTanomiClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str, object | None, dict[str, str] | None]] = []
        self.error = error

    def request_json(
        self,
        method: str,
        path: str,
        body: object | None = None,
        query: dict[str, str] | None = None,
    ) -> object:
        self.calls.append((method, path, body, query))
        if self.error is not None:
            raise self.error
        if path == "/api/repos":
            return {"repos": [{"path": "/tmp/example", "label": "Example"}]}
        if path == "/api/whoami":
            return {"user": "hidden", "host": "hidden", "models": ["opus"], "default_model": "opus"}
        return {"tasks": [], "archived": [], "deleted": []}

    def request_usage(self) -> object:
        self.calls.append(("GET", "/api/usage", None, None))
        return {"limits": {}, "running": 0}

    def stream(self, *_args: object, **_kwargs: object):
        return iter(())


def tanomi_handler(tmp_path: Path, client: FakeTanomiClient):
    handler_factory = make_handler(
        tmp_path / "site",
        tmp_path / "articles.json",
        tmp_path / "read.jsonl",
        tmp_path / "feedback.jsonl",
        tmp_path / "assistant.sqlite3",
        tmp_path / "gmail-client.json",
        tmp_path / "gmail-token.json",
        tanomi_client=client,
    )
    handler = handler_factory.func.__new__(handler_factory.func)
    responses: list[tuple[int, object]] = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    return handler, responses


def test_tanomi_route_forwards_query_and_rejects_encoded_query_path(tmp_path: Path) -> None:
    client = FakeTanomiClient()
    handler, responses = tanomi_handler(tmp_path, client)

    handler.path = "/api/tanomi/tasks?limit=50"
    handler.do_GET()

    assert responses == [(200, {"tasks": [], "archived": [], "deleted": []})]
    assert client.calls == [("GET", "/api/tasks", None, {"limit": "50"})]

    responses.clear()
    handler.path = "/api/tanomi/tasks%3Flimit=50"
    handler.do_GET()

    assert responses == [(400, {"error": "invalid tanomi request"})]


def test_tanomi_repositories_route_unwraps_envelope(tmp_path: Path) -> None:
    client = FakeTanomiClient()
    handler, responses = tanomi_handler(tmp_path, client)
    handler.path = "/api/tanomi/repos"

    handler.do_GET()

    assert responses == [(200, [{"path": "/tmp/example", "label": "Example"}])]
    assert client.calls == [("GET", "/api/repos", None, None)]


def test_tanomi_usage_route_uses_resilient_client_method(tmp_path: Path) -> None:
    client = FakeTanomiClient()
    handler, responses = tanomi_handler(tmp_path, client)
    handler.path = "/api/tanomi/usage"

    handler.do_GET()

    assert responses == [(200, {"limits": {}, "running": 0})]
    assert client.calls == [("GET", "/api/usage", None, None)]


def test_tanomi_config_route_filters_identity_fields(tmp_path: Path) -> None:
    client = FakeTanomiClient()
    handler, responses = tanomi_handler(tmp_path, client)
    handler.path = "/api/tanomi/config"
    handler.do_GET()
    assert responses == [
        (
            200,
            {
                "models": ["opus"],
                "default_model": "opus",
                "permission_modes": ["acceptEdits", "bypassPermissions", "manual", "plan"],
                "efforts": ["high", "low", "max", "medium", "xhigh"],
                "default_effort": None,
            },
        )
    ]
    assert client.calls == [("GET", "/api/whoami", None, None)]


def test_tanomi_post_forwards_valid_effort(tmp_path: Path) -> None:
    client = FakeTanomiClient()
    handler, responses = tanomi_handler(tmp_path, client)
    handler.path = "/api/tanomi/tasks"
    handler._read_json = lambda *_args: {
        "prompt": "確認する",
        "repo": "/tmp/example",
        "model": "opus",
        "permission_mode": "acceptEdits",
        "effort": "high",
    }
    handler.do_POST()
    assert responses == [(201, {"tasks": [], "archived": [], "deleted": []})]
    assert client.calls == [
        (
            "POST",
            "/api/tasks",
            {
                "prompt": "確認する",
                "repo": "/tmp/example",
                "model": "opus",
                "permission_mode": "acceptEdits",
                "effort": "high",
            },
            None,
        )
    ]


def test_tanomi_post_forwards_follow_up_parent_only(tmp_path: Path) -> None:
    client = FakeTanomiClient()
    handler, responses = tanomi_handler(tmp_path, client)
    handler.path = "/api/tanomi/tasks"
    handler._read_json = lambda *_args: {"prompt": "追加確認", "parent_id": "0123456789ab"}
    handler.do_POST()
    assert responses == [(201, {"tasks": [], "archived": [], "deleted": []})]
    assert client.calls == [
        ("POST", "/api/tasks", {"prompt": "追加確認", "parent_id": "0123456789ab"}, None)
    ]


def test_tanomi_post_rejects_invalid_follow_up_parent(tmp_path: Path) -> None:
    client = FakeTanomiClient()
    handler, responses = tanomi_handler(tmp_path, client)
    handler.path = "/api/tanomi/tasks"
    handler._read_json = lambda *_args: {"prompt": "追加確認", "parent_id": "not-a-task"}
    handler.do_POST()
    assert responses == [(400, {"error": "invalid tanomi request"})]
    assert client.calls == []


def test_tanomi_route_maps_unavailable_upstream_to_503(tmp_path: Path) -> None:
    client = FakeTanomiClient(TanomiUnavailable("tanomi に接続できません"))
    handler, responses = tanomi_handler(tmp_path, client)
    handler.path = "/api/tanomi/health"

    handler.do_GET()

    assert responses == [(503, {"error": "tanomi に接続できません"})]


def test_tanomi_delete_rejects_non_tanomi_path(tmp_path: Path) -> None:
    client = FakeTanomiClient()
    handler, responses = tanomi_handler(tmp_path, client)
    handler.path = "/api/other/tasks/0123456789ab"

    handler.do_DELETE()

    assert responses == [(404, {"error": "not found"})]
    assert client.calls == []


class FakeCodexProcess:
    def __init__(self) -> None:
        import io

        self.stdin = io.StringIO()
        self.stdout = io.StringIO(
            '{"id":1,"result":{}}\n'
            '{"id":2,"result":{"rateLimits":{"planType":"pro"},'
            '"rateLimitsByLimitId":{"codex":{"primary":{"usedPercent":18}},'
            '"codex_bengalfox":{"limitName":"GPT-5.3-Codex-Spark",'
            '"primary":{"usedPercent":0}}}}}\n'
        )

    def terminate(self) -> None:
        pass

    def wait(self, timeout: float) -> int:
        return 0

    def kill(self) -> None:
        pass


def test_codex_rate_limits_use_app_server_protocol(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeCodexProcess()
    monkeypatch.setattr(
        "daily_reader.local_server.subprocess.Popen",
        lambda *args, **kwargs: process,
    )
    monkeypatch.setattr(
        "daily_reader.local_server.select.select", lambda streams, *_: (streams, [], [])
    )

    result = read_codex_rate_limits()

    requests = [json.loads(line) for line in process.stdin.getvalue().splitlines()]
    assert requests[0]["method"] == "initialize"
    assert requests[1] == {"id": 2, "method": "account/rateLimits/read", "params": None}
    assert result["rateLimitsByLimitId"]["codex"]["primary"]["usedPercent"] == 18
    assert "codex_bengalfox" not in result["rateLimitsByLimitId"]


def test_deployment_info_includes_package_version_and_revision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deployed_at = datetime.fromisoformat("2026-08-25T01:23:45+00:00")
    monkeypatch.setattr("daily_reader.local_server.version", lambda _: "0.1.0")
    monkeypatch.setattr(
        "daily_reader.local_server.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args[0], 0, "abc123def456\n", ""),
    )

    info = build_deployment_info(Path("/repo"), deployed_at)

    assert info == {
        "version": "0.1.0+abc123def456",
        "deployed_at": "2026-08-25T01:23:45+00:00",
    }


def write_native_release_artifacts(
    directory: Path,
    *,
    ios_version: str = "0.1.41",
    macos_version: str = "0.1.42",
) -> tuple[Path, Path]:
    sidestore = directory / "sidestore"
    sidestore.mkdir()
    ipa = sidestore / "DailyReader.ipa"
    ipa.write_bytes(b"ipa")
    (sidestore / "icon.png").write_bytes(b"png")
    (sidestore / "source.json").write_text(
        json.dumps(
            {
                "apps": [
                    {
                        "bundleIdentifier": "net.skmin.DailyReader",
                        "versions": [
                            {
                                "version": ios_version,
                                "downloadURL": "http://reader.test/DailyReader.ipa",
                                "size": ipa.stat().st_size,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    macos_app = directory / "Daymeld.app"
    contents = macos_app / "Contents"
    executable_directory = contents / "MacOS"
    executable_directory.mkdir(parents=True)
    (executable_directory / "Daymeld").write_bytes(b"app")
    (contents / "Info.plist").write_bytes(
        plistlib.dumps(
            {
                "CFBundleIdentifier": "net.skmin.DailyReader.mac",
                "CFBundleShortVersionString": macos_version,
            }
        )
    )
    return sidestore, macos_app


def test_native_release_info_reads_published_ios_and_macos_versions(
    tmp_path: Path,
) -> None:
    sidestore, macos_app = write_native_release_artifacts(tmp_path)

    assert sidestore_release_version(sidestore) == "0.1.41"
    assert macos_release_version(macos_app) == "0.1.42"
    assert build_native_release_info(sidestore, macos_app) == {
        "ios_release_version": "0.1.41",
        "macos_release_version": "0.1.42",
    }


def test_native_release_info_omits_incomplete_or_unsafe_artifacts(
    tmp_path: Path,
) -> None:
    sidestore, macos_app = write_native_release_artifacts(tmp_path)
    (sidestore / "DailyReader.ipa").write_bytes(b"different size")
    executable = macos_app / "Contents/MacOS/Daymeld"
    executable.unlink()
    executable.symlink_to(tmp_path / "outside")

    assert build_native_release_info(sidestore, macos_app) == {}


def test_deployment_endpoint_reads_current_native_release_versions(
    tmp_path: Path,
) -> None:
    sidestore, macos_app = write_native_release_artifacts(tmp_path)
    handler_factory = make_handler(
        tmp_path / "site",
        tmp_path / "articles.json",
        tmp_path / "read.jsonl",
        tmp_path / "feedback.jsonl",
        tmp_path / "assistant.sqlite3",
        tmp_path / "gmail-client.json",
        tmp_path / "gmail-token.json",
        deployment_info={
            "version": "0.1.0+abc123def456",
            "deployed_at": "2026-08-25T01:23:45+00:00",
        },
        sidestore_directory=sidestore,
        macos_release_app=macos_app,
    )
    handler = handler_factory.func.__new__(handler_factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.path = "/api/deployment"

    handler.do_GET()

    assert responses == [
        (
            200,
            {
                "version": "0.1.0+abc123def456",
                "deployed_at": "2026-08-25T01:23:45+00:00",
                "ios_release_version": "0.1.41",
                "macos_release_version": "0.1.42",
            },
        )
    ]


def test_local_server_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["daily-reader-local"])

    args = build_parser().parse_args()

    assert args.host == "127.0.0.1"
    assert args.port == 8787
    assert args.site == Path("site")
    assert args.sidestore_lan_host == "0.0.0.0"
    assert args.sidestore_lan_port == 8788
    assert args.sidestore_lan_networks == "192.168.10.0/24,169.254.0.0/16"
    assert args.sidestore_dir == Path("data/sidestore")
    assert args.macos_release_app == Path("data/macos/Daymeld.app")
    assert args.sidestore_remote_port == 8789
    assert args.sidestore_remote_token == Path("secrets/sidestore-remote-token.txt")
    assert args.update_hours == "8,10,12,17,20,22"
    assert args.read_log == Path("data/read-events.jsonl")
    assert args.feedback_log == Path("data/feedback-events.jsonl")
    assert args.selection_history == Path("data/selection-history.jsonl")
    assert args.update_stats == Path("data/update-stats.jsonl")
    assert args.gmail_client_secret == Path("secrets/gmail-client.json")
    assert args.gmail_token == Path("secrets/gmail-token.json")
    assert args.tanomi_base_url == "https://xh23040023-l.tailc193b2.ts.net"


def test_local_server_accepts_legacy_sidestore_network_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["daily-reader-local", "--sidestore-lan-network", "192.168.1.0/24"],
    )

    args = build_parser().parse_args()

    assert args.sidestore_lan_networks == "192.168.1.0/24"


def test_local_server_accepts_plural_sidestore_network_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "sys.argv",
        [
            "daily-reader-local",
            "--sidestore-lan-networks",
            "10.0.0.0/8,169.254.0.0/16",
        ],
    )

    args = build_parser().parse_args()

    assert args.sidestore_lan_networks == "10.0.0.0/8,169.254.0.0/16"


def test_sidestore_handler_serves_only_distribution_directory(tmp_path: Path) -> None:
    distribution = tmp_path / "sidestore"
    distribution.mkdir()
    (distribution / "source.json").write_text("{}", encoding="utf-8")
    (distribution / "remote-source.json").write_text("secret", encoding="utf-8")
    handler_factory = make_sidestore_handler(distribution)
    handler = handler_factory.func.__new__(handler_factory.func)
    handler.directory = str(distribution)
    handler.path = "/source.json"
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /source.json HTTP/1.1"
    handler.client_address = ("127.0.0.1", 1234)
    handler.headers = {}
    handler._headers_buffer = []
    handler.wfile = BytesIO()
    handler.do_GET()
    response = handler.wfile.getvalue()
    assert b"200 OK" in response
    assert response.endswith(b"{}")
    assert b"Cache-Control: no-store\r\n" in response

    for path in ("/remote-source.json", "/DailyReader-0.1.42.ipa", "/../secret.txt"):
        handler.path = path
        handler.requestline = f"GET {path} HTTP/1.1"
        handler._headers_buffer = []
        handler.wfile = BytesIO()
        handler.do_GET()
        assert b"404 Not Found" in handler.wfile.getvalue()


def test_sidestore_handler_rejects_allowed_name_symlink(tmp_path: Path) -> None:
    distribution = tmp_path / "sidestore"
    distribution.mkdir()
    secret = distribution / "remote-source.json"
    secret.write_text("credential", encoding="utf-8")
    (distribution / "source.json").symlink_to(secret)
    handler_factory = make_sidestore_handler(distribution)
    handler = handler_factory.func.__new__(handler_factory.func)
    handler.directory = str(distribution)
    handler.path = "/source.json"
    handler.command = "GET"
    handler.request_version = "HTTP/1.1"
    handler.requestline = "GET /source.json HTTP/1.1"
    handler.client_address = ("127.0.0.1", 1234)
    handler.headers = {}
    handler._headers_buffer = []
    handler.wfile = BytesIO()

    handler.do_GET()

    response = handler.wfile.getvalue()
    assert b"404 Not Found" in response
    assert b"credential" not in response


def test_main_handler_never_serves_sidestore_release(tmp_path: Path) -> None:
    handler_factory = make_handler(
        tmp_path / "site",
        tmp_path / "articles.json",
        tmp_path / "read.jsonl",
        tmp_path / "feedback.jsonl",
        tmp_path / "assistant.sqlite3",
        tmp_path / "gmail-client.json",
        tmp_path / "gmail-token.json",
    )
    handler = handler_factory.func.__new__(handler_factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.path = "/sidestore/DailyReader.ipa?download=1"

    handler.do_GET()

    assert responses == [(404, {"error": "not found"})]


def test_main_handler_exposes_all_unread_emails(tmp_path: Path) -> None:
    database = tmp_path / "assistant.sqlite3"
    upsert_thread(
        database,
        GmailThreadRecord(
            "thread-1", "message-1", "me@example.com", "お知らせ", "a@example.com",
            "2020-01-01T00:00:00+00:00", "本文", "https://example.com", "low", 0,
            "明確な期限・依頼・警告を検出していません", "対応不要の可能性", None,
            "open", "classified",
        ),
        datetime.fromisoformat("2026-08-12T03:00:00+00:00"),
    )
    handler_factory = make_handler(
        tmp_path / "site", tmp_path / "articles.json", tmp_path / "read.jsonl",
        tmp_path / "feedback.jsonl", database, tmp_path / "gmail-client.json",
        tmp_path / "gmail-token.json",
    )
    handler = handler_factory.func.__new__(handler_factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.path = "/api/emails/unread"

    handler.do_GET()

    assert responses[0][0] == 200
    assert [item["thread_id"] for item in responses[0][1]["items"]] == ["thread-1"]
    assert responses[0][1]["items"][0]["received_at"] == "2020-01-01T00:00:00+00:00"
    assert responses[0][1]["sync_error"] is None


def test_main_handler_completion_marks_gmail_thread_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "daily_reader.local_server.mark_gmail_thread_read",
        lambda *_args: calls.append(("read", "thread-1")) or True,
    )
    monkeypatch.setattr(
        "daily_reader.local_server.update_status",
        lambda _db, thread_id, action, _now: calls.append((action, thread_id)) or True,
    )
    handler_factory = make_handler(
        tmp_path / "site", tmp_path / "articles.json", tmp_path / "read.jsonl",
        tmp_path / "feedback.jsonl", tmp_path / "assistant.sqlite3",
        tmp_path / "gmail-client.json", tmp_path / "gmail-token.json",
    )
    handler = handler_factory.func.__new__(handler_factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler._read_json = lambda: {"thread_id": "thread-1", "action": "done"}
    handler.path = "/api/email-status"

    handler.do_POST()

    assert responses == [(202, {"updated": True})]
    assert calls == [("read", "thread-1"), ("done", "thread-1")]


def test_main_handler_does_not_complete_when_gmail_read_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        "daily_reader.local_server.mark_gmail_thread_read",
        lambda *_args: (_ for _ in ()).throw(
            GmailAuthorizationRequired("Gmail authorization required")
        ),
    )
    monkeypatch.setattr(
        "daily_reader.local_server.update_status",
        lambda _db, thread_id, action, _now: status_calls.append((action, thread_id)) or True,
    )
    handler_factory = make_handler(
        tmp_path / "site", tmp_path / "articles.json", tmp_path / "read.jsonl",
        tmp_path / "feedback.jsonl", tmp_path / "assistant.sqlite3",
        tmp_path / "gmail-client.json", tmp_path / "gmail-token.json",
    )
    handler = handler_factory.func.__new__(handler_factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler._read_json = lambda: {"thread_id": "thread-1", "action": "done"}
    handler.path = "/api/email-status"

    handler.do_POST()

    assert responses == [(503, {"error": "Gmail authorization required"})]
    assert status_calls == []


def test_main_handler_maps_gmail_content_authorization_error_to_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "daily_reader.local_server.fetch_gmail_thread_content",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            GmailAuthorizationRequired("Gmail authorization required")
        ),
    )
    handler_factory = make_handler(
        tmp_path / "site", tmp_path / "articles.json", tmp_path / "read.jsonl",
        tmp_path / "feedback.jsonl", tmp_path / "assistant.sqlite3",
        tmp_path / "gmail-client.json", tmp_path / "gmail-token.json",
    )
    handler = handler_factory.func.__new__(handler_factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.path = "/api/email-content/thread1"

    handler.do_GET()

    assert responses == [(503, {"error": "Gmail authorization required"})]


def test_sidestore_lan_server_parses_and_applies_trusted_networks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "daily_reader.local_server.ThreadingHTTPServer.__init__",
        lambda *_args, **_kwargs: None,
    )
    server = SideStoreLANServer(
        ("0.0.0.0", 8788),
        object,
        " 192.168.10.0/24, 169.254.0.0/16 ",
    )

    assert server.allowed_networks == (
        IPv4Network("192.168.10.0/24"),
        IPv4Network("169.254.0.0/16"),
    )

    assert server.verify_request(None, ("127.0.0.1", 1234))
    assert server.verify_request(None, ("192.168.10.42", 1234))
    assert server.verify_request(None, ("169.254.172.225", 1234))
    assert not server.verify_request(None, ("169.253.255.255", 1234))
    assert not server.verify_request(None, ("169.255.0.1", 1234))
    assert not server.verify_request(None, ("100.90.223.13", 1234))
    assert not server.verify_request(None, ("8.8.8.8", 1234))


@pytest.mark.parametrize(
    "network",
    [
        "",
        "::1/128",
        "192.168.10.0/24,::1/128",
        "192.168.10.1/24",
        "0.0.0.0/0",
        "8.8.8.0/24",
    ],
)
def test_sidestore_lan_server_rejects_invalid_allowed_network(network: str) -> None:
    with pytest.raises(ValueError):
        SideStoreLANServer(("127.0.0.1", 0), object, network)


def test_sidestore_lan_server_stays_disabled_without_release(tmp_path: Path) -> None:
    assert start_sidestore_server(tmp_path, "0.0.0.0", 8788, "192.168.10.0/24") is None


def test_sidestore_lan_server_stays_disabled_for_symlinked_release(tmp_path: Path) -> None:
    secret = tmp_path / "remote-source.json"
    secret.write_text("credential", encoding="utf-8")
    (tmp_path / "source.json").symlink_to(secret)
    (tmp_path / "DailyReader.ipa").write_bytes(b"ipa")
    (tmp_path / "icon.png").write_bytes(b"icon")

    assert start_sidestore_server(
        tmp_path,
        "0.0.0.0",
        8788,
        "192.168.10.0/24",
    ) is None


def test_sidestore_lan_server_failure_does_not_stop_main_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("source.json", "DailyReader.ipa", "icon.png"):
        (tmp_path / name).touch()

    class FailingServer:
        def __init__(self, *args, **kwargs) -> None:
            raise OSError("port unavailable")

    monkeypatch.setattr("daily_reader.local_server.SideStoreLANServer", FailingServer)

    assert start_sidestore_server(tmp_path, "0.0.0.0", 8788, "192.168.10.0/24") is None


@pytest.mark.parametrize("port", [-1, 0, 65536])
def test_sidestore_lan_server_invalid_port_does_not_stop_main_server(
    tmp_path: Path,
    port: int,
) -> None:
    for name in ("source.json", "DailyReader.ipa", "icon.png"):
        (tmp_path / name).touch()

    assert start_sidestore_server(
        tmp_path,
        "0.0.0.0",
        port,
        "192.168.10.0/24",
    ) is None


def make_remote_request(handler_factory, path: str, method: str = "GET") -> bytes:
    handler = handler_factory.__new__(handler_factory)
    handler.path = path
    handler.command = method
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"{method} {path} HTTP/1.1"
    handler.client_address = ("127.0.0.1", 1234)
    handler._headers_buffer = []
    handler.wfile = BytesIO()
    getattr(handler, f"do_{method}")()
    return handler.wfile.getvalue()


def write_remote_release(directory: Path, token: str) -> None:
    ipa_name = "DailyReader-0.1.42.ipa"
    base_url = f"https://reader.example.test:8443/{token}"
    (directory / ipa_name).write_bytes(b"test ipa")
    (directory / "icon.png").write_bytes(b"test icon")
    (directory / "remote-source.json").write_text(
        json.dumps(
            {
                "subtitle": "個人用の外出先更新ソース",
                "sourceURL": f"{base_url}/source.json",
                "apps": [
                    {
                        "iconURL": f"{base_url}/icon.png",
                        "versions": [
                            {
                                "version": "0.1.42",
                                "date": "2026-08-28",
                                "downloadURL": f"{base_url}/{ipa_name}",
                                "size": 8,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_sidestore_remote_handler_serves_only_token_protected_artifacts(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    token = TOKEN
    write_remote_release(tmp_path, token)
    handler = make_sidestore_remote_handler(tmp_path, token)

    source_response = make_remote_request(handler, f"/{token}/source.json")
    ipa_response = make_remote_request(handler, f"/{token}/DailyReader-0.1.42.ipa")
    head_response = make_remote_request(handler, f"/{token}/icon.png", "HEAD")

    assert b"200 OK" in source_response
    assert b'"sourceURL"' in source_response
    assert b"200 OK" in ipa_response
    assert ipa_response.endswith(b"test ipa")
    assert b"200 OK" in head_response
    assert not head_response.endswith(b"test icon")
    assert b"Cache-Control: private, no-store" in source_response
    assert token not in caplog.text


def test_sidestore_remote_handler_keeps_manifested_previous_version_available(
    tmp_path: Path,
) -> None:
    token = TOKEN
    write_remote_release(tmp_path, token)
    previous_name = "DailyReader-0.1.41.ipa"
    (tmp_path / previous_name).write_bytes(b"previous ipa")
    source_path = tmp_path / "remote-source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["apps"][0]["versions"].append(
        {
            "version": "0.1.41",
            "date": "2026-08-27",
            "downloadURL": (
                f"https://reader.example.test:8443/{token}/{previous_name}"
            ),
            "size": 12,
        }
    )
    source_path.write_text(json.dumps(source), encoding="utf-8")
    handler = make_sidestore_remote_handler(tmp_path, token)

    response = make_remote_request(handler, f"/{token}/{previous_name}")

    assert b"200 OK" in response
    assert response.endswith(b"previous ipa")


@pytest.mark.parametrize(
    ("path", "method"),
    [
        ("/source.json", "GET"),
        ("/wrong-token/source.json", "GET"),
        ("/token/api/deployment", "GET"),
        ("/token/../source.json", "GET"),
        ("/token/source.json?download=1", "GET"),
        ("/token/source.json#fragment", "GET"),
        ("https://example.test/token/source.json", "GET"),
        ("/token/source.json", "POST"),
        ("/token/source.json", "OPTIONS"),
    ],
)
def test_sidestore_remote_handler_returns_404_for_every_other_path(
    tmp_path: Path,
    path: str,
    method: str,
) -> None:
    token = TOKEN
    write_remote_release(tmp_path, token)
    handler = make_sidestore_remote_handler(tmp_path, token)

    response = make_remote_request(handler, path.replace("token", token), method)

    assert b"404 Not Found" in response


def test_sidestore_remote_handler_maps_unknown_methods_to_404(tmp_path: Path) -> None:
    token = TOKEN
    write_remote_release(tmp_path, token)
    handler_factory = make_sidestore_remote_handler(tmp_path, token)
    handler = handler_factory.__new__(handler_factory)
    handler.path = f"/{token}/source.json"
    handler.command = "PROPFIND"
    handler.request_version = "HTTP/1.1"
    handler.requestline = f"PROPFIND /{token}/source.json HTTP/1.1"
    handler.client_address = ("127.0.0.1", 1234)
    handler._headers_buffer = []
    handler.wfile = BytesIO()

    handler.send_error(501)

    assert b"404 Not Found" in handler.wfile.getvalue()


def test_sidestore_remote_token_validation(tmp_path: Path) -> None:
    token_path = tmp_path / "token.txt"
    token_path.write_text(TOKEN + "\n", encoding="utf-8")
    token_path.chmod(0o600)

    assert load_sidestore_remote_token(token_path) == TOKEN

    token_path.write_text("weak\n", encoding="utf-8")
    with pytest.raises(ValueError, match="32-byte URL-safe"):
        load_sidestore_remote_token(token_path)

    token_path.write_text(TOKEN + "\n", encoding="utf-8")
    token_path.chmod(0o644)
    with pytest.raises(ValueError, match="permissions must be 0600"):
        load_sidestore_remote_token(token_path)

    real_token = tmp_path / "real-token.txt"
    real_token.write_text(TOKEN + "\n", encoding="utf-8")
    real_token.chmod(0o600)
    token_path.unlink()
    token_path.symlink_to(real_token)
    with pytest.raises(ValueError, match="must not be a symlink"):
        load_sidestore_remote_token(token_path)


@pytest.mark.parametrize(
    "source",
    [
        {"apps": []},
        {"apps": [{"versions": []}]},
        {
            "sourceURL": 1,
            "apps": [{"iconURL": "value", "versions": [{"downloadURL": "value"}]}],
        },
    ],
)
def test_sidestore_remote_server_stays_disabled_for_empty_source_arrays(
    tmp_path: Path,
    source: dict[str, object],
) -> None:
    token_path = tmp_path / "token.txt"
    token_path.write_text(TOKEN + "\n", encoding="utf-8")
    token_path.chmod(0o600)
    (tmp_path / "icon.png").write_bytes(b"icon")
    (tmp_path / "remote-source.json").write_text(json.dumps(source), encoding="utf-8")

    assert start_sidestore_remote_server(tmp_path, 8789, token_path) is None


@pytest.mark.parametrize(
    ("field", "url"),
    [
        ("sourceURL", "http://reader.example.test:8443/token/source.json"),
        ("iconURL", "https://evil.example.test:8443/token/icon.png"),
        (
            "downloadURL",
            "https://reader.example.test:8443/token/DailyReader-0.1.42.ipa?download=1",
        ),
        (
            "downloadURL",
            "https://reader.example.test:99999/token/DailyReader-0.1.42.ipa",
        ),
    ],
)
def test_sidestore_remote_source_rejects_unsafe_artifact_urls(
    tmp_path: Path,
    field: str,
    url: str,
) -> None:
    token = TOKEN
    write_remote_release(tmp_path, token)
    source_path = tmp_path / "remote-source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    url = url.replace("token", token)
    if field == "sourceURL":
        source[field] = url
    elif field == "iconURL":
        source["apps"][0][field] = url
    else:
        source["apps"][0]["versions"][0][field] = url
    source_path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="unsafe artifact URLs|invalid URL port"):
        current_sidestore_remote_ipa(tmp_path, token)


def test_sidestore_remote_source_rejects_symlinked_artifact(tmp_path: Path) -> None:
    token = TOKEN
    write_remote_release(tmp_path, token)
    ipa_path = tmp_path / "DailyReader-0.1.42.ipa"
    outside_path = tmp_path / "outside.txt"
    outside_path.write_text("secret", encoding="utf-8")
    ipa_path.unlink()
    ipa_path.symlink_to(outside_path)

    with pytest.raises(ValueError, match="unsafe artifact URLs"):
        current_sidestore_remote_ipa(tmp_path, token)


def test_sidestore_remote_source_requires_noncredential_subtitle(tmp_path: Path) -> None:
    token = TOKEN
    write_remote_release(tmp_path, token)
    source_path = tmp_path / "remote-source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source.pop("subtitle")
    source_path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="hide its credential URL"):
        current_sidestore_remote_ipa(tmp_path, token)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("date", None),
        ("date", "not-a-date"),
        ("date", "20260828"),
        ("date", "2026-W35-5"),
        ("size", True),
        ("size", -1),
    ],
)
def test_sidestore_remote_source_rejects_invalid_required_version_metadata(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    token = TOKEN
    write_remote_release(tmp_path, token)
    source_path = tmp_path / "remote-source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["apps"][0]["versions"][0][field] = value
    source_path.write_text(json.dumps(source), encoding="utf-8")

    with pytest.raises(ValueError, match="invalid version metadata"):
        current_sidestore_remote_ipa(tmp_path, token)


def test_sidestore_remote_server_stays_disabled_for_invalid_configuration(
    tmp_path: Path,
) -> None:
    token_path = tmp_path / "token.txt"
    token_path.write_text(TOKEN + "\n", encoding="utf-8")
    token_path.chmod(0o600)

    assert start_sidestore_remote_server(tmp_path, 8789, token_path) is None

    write_remote_release(tmp_path, TOKEN)
    assert start_sidestore_remote_server(tmp_path, 0, token_path) is None


def test_read_events_are_appended_and_summarized(tmp_path: Path) -> None:
    log_path = tmp_path / "read-events.jsonl"
    article = {
        "id": "article-1",
        "title": "Data Governance",
        "source": "Example",
        "category": "データマネジメント",
    }

    append_read_event(log_path, article, "field_highlight")
    append_read_event(log_path, article, "article_feed")

    summary = summarize_read_events(log_path)
    assert summary["total_reads"] == 2
    assert summary["unique_articles"] == 1
    assert summary["by_category"] == {"データマネジメント": 2}
    assert summary["by_surface"] == {"field_highlight": 1, "article_feed": 1}


def test_feedback_events_are_appended_and_loaded(tmp_path: Path) -> None:
    log_path = tmp_path / "feedback-events.jsonl"
    article = {
        "id": "article-1",
        "title": "Generic AI News",
        "source": "Example",
        "category": "テクノロジー",
    }

    append_feedback_event(log_path, article, "field_highlight")
    log_path.write_text(
        log_path.read_text(encoding="utf-8") + "invalid json\n",
        encoding="utf-8",
    )

    events = load_feedback_events(log_path)
    assert len(events) == 1
    assert events[0]["article_id"] == "article-1"
    assert events[0]["feedback"] == "not_interested"


def test_update_stats_compare_articles_and_highlights(tmp_path: Path) -> None:
    generated_at = datetime.fromisoformat("2026-08-13T12:00:00+00:00")
    stats = build_update_stats(
        generated_at,
        {"article-1", "article-2", "article-3"},
        {"article-1", "article-2"},
        {"article-2", "article-3"},
        {"article-1", "article-2"},
        True,
    )

    assert stats == {
        "generated_at": "2026-08-13T12:00:00+00:00",
        "new_articles": 1,
        "total_articles": 3,
        "new_articles_highlighted": 1,
        "new_highlights": 1,
        "kept_highlights": 1,
        "total_highlights": 2,
        "highlights_updated": True,
    }

    log_path = tmp_path / "update-stats.jsonl"
    append_update_stats(log_path, stats)
    assert json.loads(log_path.read_text(encoding="utf-8")) == stats


def test_update_articles_creates_untracked_snapshot_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = tmp_path / "site" / "data" / "articles.json"
    monkeypatch.setattr("daily_reader.local_server.load_config", lambda _path: ({}, []))
    monkeypatch.setattr("daily_reader.local_server.load_keywords", lambda _path: {})
    monkeypatch.setattr(
        "daily_reader.local_server.collect",
        lambda *_args: ([], [{"source": "test", "error": "offline"}]),
    )

    update_articles(Path("feeds.toml"), Path("keywords.toml"), output)

    assert output.parent.is_dir()
