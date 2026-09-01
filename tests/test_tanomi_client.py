import io
import json
import time
import urllib.error

import pytest

from daily_reader.tanomi_client import (
    DEFAULT_BASE_URL,
    TanomiClient,
    TanomiError,
    TanomiProtocolError,
    TanomiUnavailable,
    _NoRedirect,
)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        self.close()


def test_client_defaults_to_tailscale_tanomi_service() -> None:
    assert TanomiClient().base_url == DEFAULT_BASE_URL


def test_client_rejects_unsafe_base_urls() -> None:
    with pytest.raises(ValueError):
        TanomiClient("file:///tmp/tanomi")
    with pytest.raises(ValueError):
        TanomiClient("http://127.0.0.1:8765/?token=secret")


def test_client_reuses_one_opener_for_all_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    class FakeOpener:
        def open(self, request, timeout):
            calls.append((request, timeout))
            return Response(b"{}")

    opener = FakeOpener()
    builds = []

    def build_opener(*handlers):
        builds.append(handlers)
        return opener

    monkeypatch.setattr(urllib.request, "build_opener", build_opener)
    client = TanomiClient()

    client.request_json("GET", "/api/health")
    client.request_json("GET", "/api/repos")

    assert len(builds) == 1
    assert builds[0] == (_NoRedirect,)
    assert [request.full_url for request, _timeout in calls] == [
        f"{DEFAULT_BASE_URL}/api/health",
        f"{DEFAULT_BASE_URL}/api/repos",
    ]


def test_request_json_builds_allowlisted_style_request(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response(json.dumps({"ok": True}).encode())
    captured = {}

    def open_url(_client, request, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return response

    monkeypatch.setattr(TanomiClient, "_open", open_url)
    payload = TanomiClient().request_json(
        "POST", "/api/tasks", {"prompt": "日本語"}, {"limit": "50"}
    )

    assert payload == {"ok": True}
    assert captured["request"].full_url.endswith("/api/tasks?limit=50")
    assert captured["request"].get_method() == "POST"
    assert json.loads(captured["request"].data) == {"prompt": "日本語"}
    assert captured["timeout"] == 5.0


def test_request_json_preserves_upstream_status_and_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    error = urllib.error.HTTPError(
        "http://tanomi/api/tasks", 409, "Conflict", {}, io.BytesIO(b'{"detail":"running"}')
    )
    def raise_error(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(TanomiClient, "_open", raise_error)
    with pytest.raises(TanomiError, match="running") as raised:
        TanomiClient().request_json("POST", "/api/tasks/abc")
    assert raised.value.status == 409


def test_request_json_maps_transport_and_protocol_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "daily_reader.tanomi_client.TanomiClient._open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("offline")),
    )
    with pytest.raises(TanomiUnavailable):
        TanomiClient().request_json("GET", "/api/health")

    monkeypatch.setattr(TanomiClient, "_open", lambda *_args, **_kwargs: Response(b"not json"))
    with pytest.raises(TanomiProtocolError):
        TanomiClient().request_json("GET", "/api/health")


def test_request_usage_validates_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0

    def open_url(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        return Response(b'{"limits":{"five_hour":{"utilization":5,"resets_at":null}},"running":0}')

    monkeypatch.setattr(TanomiClient, "_open", open_url)
    client = TanomiClient()
    assert client.request_usage()["limits"]["five_hour"]["utilization"] == 5
    assert client.request_usage()["limits"]["five_hour"]["utilization"] == 5
    assert calls == 1


def test_request_usage_uses_stale_snapshot_for_upstream_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [Response(b'{"limits":{"five_hour":{"utilization":5}},"running":0}')]

    def open_url(*_args, **_kwargs):
        if responses:
            return responses.pop()
        raise urllib.error.HTTPError(
            "http://tanomi/api/usage", 429, "Too Many Requests", {}, io.BytesIO()
        )

    monkeypatch.setattr(TanomiClient, "_open", open_url)
    client = TanomiClient()
    first = client.request_usage()
    object.__setattr__(client, "_usage_cached", (time.monotonic() - 301, first))
    stale = client.request_usage()
    assert stale["stale"] is True
    assert stale["limits"] == first["limits"]


def test_request_usage_rejects_embedded_upstream_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        TanomiClient,
        "_open",
        lambda *_args, **_kwargs: Response(
            b'{"limits":{"error":"HTTP Error 429: Too Many Requests"},"running":0}'
        ),
    )
    with pytest.raises(TanomiUnavailable, match="429"):
        TanomiClient().request_usage()


def test_stream_requires_hex_id_and_forwards_lines(monkeypatch: pytest.MonkeyPatch) -> None:
    response = Response(b"event: meta\ndata: {}\n\n")
    monkeypatch.setattr(TanomiClient, "_open", lambda *_args, **_kwargs: response)
    assert list(TanomiClient().stream("0123456789ab", 3)) == [
        b"event: meta\n",
        b"data: {}\n",
        b"\n",
    ]
    with pytest.raises(ValueError):
        list(TanomiClient().stream("not-an-id"))
