import io
import json
import socket
import threading
import time
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from daily_reader import soundcore_cloud as cloud

URL = "https://speaker-eu.eufylife.com/knowledge/sharelink/CODE1234"
AUDIO = "https://d2htfo7ft368vg.cloudfront.net/recordings/example.ogg"


class Response(io.BytesIO):
    def __init__(self, data, headers=None, status=200):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))} if headers is None else headers
        self.status = status

    def getheader(self, key, default=None):
        return self.headers.get(key, default)


def data(**overrides):
    return {
        "title": "2026-09-01 12:00:00",
        "timestamp": int(datetime(2026, 9, 1, 3, tzinfo=UTC).timestamp()),
        "audio_url": AUDIO,
        "file_name": "recording.ogg",
        "transcript": "",
        "summary": "",
        "audio_duration": 30,
        **overrides,
    }


def responses(monkeypatch, metadata=None, audio=b"OggS" + b"\0" * 32, audio_headers=None):
    calls = []

    @contextmanager
    def open_response(url, *, body=None, deadline):
        calls.append((url, body))
        if body:
            assert json.loads(body) == {"share_code": "CODE1234"}
            yield Response(json.dumps(metadata or {"res_code": 1, "data": data()}).encode())
        else:
            yield Response(audio, audio_headers)

    monkeypatch.setattr(cloud, "_open", open_response)
    monkeypatch.setattr(cloud.shutil, "disk_usage", lambda _: SimpleNamespace(free=20 * 1024**3))
    return calls


def test_canonical_link_does_not_retain_language_or_fragment():
    assert cloud.validate_share_url(" " + URL + "?language=ja-JP#section ") == URL


@pytest.mark.parametrize(
    "url",
    [
        URL.replace("https", "http"),
        URL.replace("speaker-eu", "speaker"),
        URL.replace("speaker-eu", "evil.speaker-eu"),
        URL.replace("/sharelink/", "/sharePage/"),
        URL + "/../private",
        URL + "%2fprivate",
        URL + "?token=secret",
        URL + "?language=ja&x=y",
        URL.replace("https://", "https://user:pass@"),
        URL.replace(".com/", ".com:8080/"),
        URL.replace(".com/", ".com./"),
        URL + "\nINJECT",
        URL + "\\evil",
        None,
        1,
    ],
)
def test_rejects_other_routes_authorities_and_credentials(url):
    with pytest.raises(cloud.SoundcoreCloudError, match="共有リンク"):
        cloud.validate_share_url(url)


def test_untranscribed_audio_download_preserves_original_and_verified_time(tmp_path, monkeypatch):
    calls = responses(monkeypatch)
    result = cloud.download_share(URL, tmp_path)
    assert result.path.read_bytes() == b"OggS" + b"\0" * 32
    assert result.path.stat().st_mode & 0o777 == 0o600
    assert result.filename == "recording.ogg"
    assert result.recorded_at == "2026-09-01T03:00:00+00:00"
    assert result.metadata["transcript"] == ""
    assert result.metadata["recorded_at_confirmed"] is True
    assert len(calls) == 2
    saved = json.dumps(result.metadata)
    assert "CODE1234" not in saved and AUDIO not in saved and URL not in saved


@pytest.mark.parametrize(
    "updates",
    [
        {"timestamp": None},
        {"timestamp": float("nan")},
        {"timestamp": True},
        {"timestamp": 10**400},
        {"timestamp": -(10**400)},
        {"timestamp": float("inf")},
        {"title": "renamed recording"},
        {"title": "2026-09-01 13:00:00"},
        {"title": "2026-99-01 12:00:00"},
        {"timestamp": 0},
    ],
)
def test_ambiguous_cloud_time_never_uses_fetch_time(updates):
    assert cloud._recorded_at(data(**updates)) is None


def test_millisecond_timestamp_is_verified():
    metadata = data()
    metadata["timestamp"] *= 1000
    assert cloud._recorded_at(metadata) == "2026-09-01T03:00:00+00:00"


@pytest.mark.parametrize(
    "value,code",
    [
        ("", "no_audio"),
        ("https://evil.test/example.ogg", "audio_host"),
        ("https://d2htfo7ft368vg.cloudfront.net.evil.test/example.ogg", "audio_host"),
        ("https://user:pass@d2htfo7ft368vg.cloudfront.net/example.ogg", "url"),
        ("https://d2htfo7ft368vg.cloudfront.net/example.m3u", "audio"),
    ],
)
def test_audio_url_validation_precedes_download(tmp_path, monkeypatch, value, code):
    calls = responses(monkeypatch, {"res_code": 1, "data": data(audio_url=value)})
    with pytest.raises(cloud.SoundcoreCloudError) as caught:
        cloud.download_share(URL, tmp_path)
    assert caught.value.code == code
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())


def test_expired_link_uses_safe_error(tmp_path, monkeypatch):
    responses(monkeypatch, {"res_code": 5016, "message": "private token message"})
    with pytest.raises(cloud.SoundcoreCloudError) as caught:
        cloud.download_share(URL, tmp_path)
    assert caught.value.code == "expired"
    assert "private" not in str(caught.value)
    assert caught.value.retryable is False


@pytest.mark.parametrize(
    "audio,headers,code",
    [
        (b"<html>not audio</html>", None, "audio"),
        (b"OggS1234", {"Content-Length": "20"}, "network"),
        (b"OggS1234", {"Content-Length": str(cloud.MAX_AUDIO_BYTES + 1)}, "size"),
        (b"OggS1234", {"Content-Length": "broken"}, "response"),
    ],
)
def test_failed_download_removes_only_new_partial(tmp_path, monkeypatch, audio, headers, code):
    responses(monkeypatch, audio=audio, audio_headers=headers)
    with pytest.raises(cloud.SoundcoreCloudError) as caught:
        cloud.download_share(URL, tmp_path)
    assert caught.value.code == code
    assert not list(tmp_path.iterdir())


def test_chunked_stream_enforces_limit(tmp_path, monkeypatch):
    responses(monkeypatch, audio=b"OggS12345678", audio_headers={})
    monkeypatch.setattr(cloud, "MAX_AUDIO_BYTES", 8)
    with pytest.raises(cloud.SoundcoreCloudError) as caught:
        cloud.download_share(URL, tmp_path)
    assert caught.value.code == "size"
    assert not list(tmp_path.iterdir())


def test_storage_reserves_both_download_and_permanent_copy(tmp_path, monkeypatch):
    responses(monkeypatch)
    monkeypatch.setattr(
        cloud.shutil, "disk_usage", lambda _: SimpleNamespace(free=cloud.MINIMUM_FREE_BYTES + 40)
    )
    with pytest.raises(cloud.SoundcoreCloudError) as caught:
        cloud.download_share(URL, tmp_path)
    assert caught.value.code == "storage"
    assert not list(tmp_path.iterdir())


def test_existing_destination_is_not_deleted(tmp_path, monkeypatch):
    responses(monkeypatch)
    existing = tmp_path / "soundcore-original.ogg"
    existing.write_bytes(b"user original")
    with pytest.raises(cloud.SoundcoreCloudError):
        cloud.download_share(URL, tmp_path)
    assert existing.read_bytes() == b"user original"


def test_path_filename_and_cloud_summary_do_not_become_recording_path_or_speech(
    tmp_path, monkeypatch
):
    responses(
        monkeypatch,
        {
            "res_code": 1,
            "data": data(
                file_name="../../private.ogg", summary="a cloud summary", transcript="cloud text"
            ),
        },
    )
    result = cloud.download_share(URL, tmp_path)
    assert result.filename == "Soundcore.ogg"
    assert result.metadata["transcript"] == "cloud text"
    assert result.metadata["summary"] == "a cloud summary"


def test_mp3_original_is_not_transcoded(tmp_path, monkeypatch):
    audio = b"ID3" + b"\0" * 32
    responses(
        monkeypatch,
        {
            "res_code": 1,
            "data": data(audio_url=AUDIO.replace(".ogg", ".mp3"), file_name="recording.mp3"),
        },
        audio=audio,
    )
    result = cloud.download_share(URL, tmp_path)
    assert result.path.read_bytes() == audio
    assert result.filename == "recording.mp3"


def test_dns_mixed_private_answers_are_rejected(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *a, **k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))
            for ip in ("18.193.246.36", "127.0.0.1")
        ],
    )
    with pytest.raises(cloud.SoundcoreCloudError):
        cloud._public_addresses(cloud.SHARE_HOST, time.monotonic() + 2)


def test_tls_connects_to_validated_address_with_hostname_verification(monkeypatch):
    raw = SimpleNamespace(close=lambda: None)
    connects = []
    handshakes = []
    wrapped = SimpleNamespace(
        settimeout=lambda _: None, do_handshake=lambda: handshakes.append(True)
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda address, timeout: connects.append((address, timeout)) or raw,
    )
    client = cloud._PinnedHTTPSConnection(cloud.SHARE_HOST, "18.193.246.36")
    names = []
    client._context = SimpleNamespace(
        wrap_socket=lambda sock, server_hostname, do_handshake_on_connect: (
            names.append((sock, server_hostname, do_handshake_on_connect)) or wrapped
        )
    )
    client.connect()
    assert connects == [(("18.193.246.36", 443), cloud.SOCKET_SECONDS)]
    assert names == [(raw, cloud.SHARE_HOST, False)]
    assert handshakes == [True]
    assert client.sock is wrapped


@pytest.mark.parametrize(
    "status,expected",
    [(302, "response"), (403, "response"), (404, "expired"), (429, "network"), (502, "network")],
)
def test_http_errors_and_redirects_never_follow_or_expose_urls(monkeypatch, status, expected):
    requests = []
    client = SimpleNamespace(
        transport=None,
        request=lambda *args: requests.append(args),
        close=lambda: None,
        getresponse=lambda: Response(b"private error", {"Location": "http://127.0.0.1"}, status),
    )
    monkeypatch.setattr(cloud, "_public_addresses", lambda *a: ["18.193.246.36"])
    monkeypatch.setattr(cloud, "_PinnedHTTPSConnection", lambda *a: client)
    with (
        pytest.raises(cloud.SoundcoreCloudError) as caught,
        cloud._open(URL, deadline=time.monotonic() + 2),
    ):
        pass
    assert caught.value.code == expected
    assert len(requests) == 1
    assert "CODE1234" not in str(caught.value)


@pytest.mark.parametrize("slow_headers", [False, True])
def test_absolute_deadline_interrupts_trickling_headers_and_close_body(monkeypatch, slow_headers):
    receiver, sender = socket.socketpair()
    receiver.settimeout(0.3)
    stopped = threading.Event()

    def trickle():
        try:
            header = b"HTTP/1.1 200 OK\r\nConnection: close\r\nContent-Length: 100\r\n\r\n"
            if not slow_headers:
                sender.sendall(header)
            for byte in header if slow_headers else b"x" * 100:
                if stopped.wait(0.03):
                    break
                sender.sendall(bytes([byte]))
        except OSError:
            pass

    def get_response():
        response = cloud.http.client.HTTPResponse(receiver)
        response.begin()
        client.sock = None  # Connection: close detaches it from HTTPConnection.
        return response

    client = SimpleNamespace(
        transport=receiver,
        sock=receiver,
        request=lambda *a: None,
        getresponse=get_response,
        close=receiver.close,
    )
    monkeypatch.setattr(cloud, "_public_addresses", lambda *a: ["18.193.246.36"])
    monkeypatch.setattr(cloud, "_PinnedHTTPSConnection", lambda *a: client)
    worker = threading.Thread(target=trickle, daemon=True)
    worker.start()
    started = time.monotonic()
    deadline = started + 0.2
    try:
        with (
            pytest.raises(cloud.SoundcoreCloudError) as caught,
            cloud._open(URL, deadline=deadline) as response,
        ):
            cloud._read(response, 100)
            cloud._remaining(deadline)
        assert caught.value.code == "network"
        assert time.monotonic() - started < 1.0
    finally:
        stopped.set()
        sender.close()
        receiver.close()
        worker.join(timeout=1)


def test_blocked_dns_resolver_does_not_block_worker(monkeypatch):
    release = threading.Event()

    def resolve(*args, **kwargs):
        release.wait(2)
        return []

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    started = time.monotonic()
    try:
        with pytest.raises(cloud.SoundcoreCloudError) as caught:
            cloud._public_addresses(cloud.SHARE_HOST, started + 0.05)
        assert caught.value.code == "network"
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
