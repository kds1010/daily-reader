import json
import subprocess
from datetime import datetime
from io import BytesIO
from ipaddress import IPv4Network
from pathlib import Path

import pytest

from daily_reader.local_server import (
    SideStoreLANServer,
    append_feedback_event,
    append_read_event,
    append_update_stats,
    build_deployment_info,
    build_parser,
    build_update_stats,
    load_feedback_events,
    make_handler,
    make_sidestore_handler,
    read_codex_rate_limits,
    start_sidestore_server,
    summarize_read_events,
    update_articles,
)


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
    assert args.update_hours == "8,10,12,17,20,22"
    assert args.read_log == Path("data/read-events.jsonl")
    assert args.feedback_log == Path("data/feedback-events.jsonl")
    assert args.selection_history == Path("data/selection-history.jsonl")
    assert args.update_stats == Path("data/update-stats.jsonl")
    assert args.gmail_client_secret == Path("secrets/gmail-client.json")
    assert args.gmail_token == Path("secrets/gmail-token.json")


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
    handler_factory = make_sidestore_handler(distribution)
    handler = handler_factory.func.__new__(handler_factory.func)
    handler.directory = str(distribution)
    handler.path = "/%2e%2e/secret.txt"
    assert Path(handler.translate_path(handler.path)).is_relative_to(distribution)

    handler.path = "/source.json"
    handler.request_version = "HTTP/1.1"
    handler._headers_buffer = []
    handler.wfile = BytesIO()
    handler.end_headers()
    assert b"Cache-Control: no-store\r\n" in handler.wfile.getvalue()


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
