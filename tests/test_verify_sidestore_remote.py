import http.client
import io
import json
import os
import ssl
import subprocess
import sys
import threading
import traceback
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/verify_sidestore_remote.py"
SPEC = spec_from_file_location("verify_sidestore_remote", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = "a" * 42 + "A"


class FakeResponse:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        pass

    def read(self) -> bytes:
        return self.body


def write_release(directory: Path, token: str) -> dict[str, bytes]:
    base_url = f"https://reader.example.test:8443/{token}"
    artifacts = {
        "icon.png": b"icon",
        "DailyReader-0.1.42.ipa": b"ipa",
    }
    source = {
        "subtitle": "個人用の外出先更新ソース",
        "sourceURL": f"{base_url}/source.json",
        "apps": [
            {
                "iconURL": f"{base_url}/icon.png",
                "versions": [
                    {
                        "version": "0.1.42",
                        "date": "2026-08-28",
                        "downloadURL": f"{base_url}/DailyReader-0.1.42.ipa",
                        "size": 3,
                    }
                ],
            }
        ],
    }
    artifacts["remote-source.json"] = (
        json.dumps(source, ensure_ascii=False, indent=2) + "\n"
    ).encode()
    for name, body in artifacts.items():
        (directory / name).write_bytes(body)
    os.chmod(directory / "remote-source.json", 0o600)
    return artifacts


def test_verify_remote_release_matches_artifacts_without_returning_token(
    tmp_path: Path,
) -> None:
    token = TOKEN
    token_path = tmp_path / "token.txt"
    token_path.write_text(token + "\n", encoding="utf-8")
    os.chmod(token_path, 0o600)
    artifacts = write_release(tmp_path, token)

    def open_url(request, timeout: int):
        assert timeout == 30
        path = request.selector
        if request.method == "GET" and path == f"/{token}/source.json":
            return FakeResponse(200, artifacts["remote-source.json"])
        if request.method == "GET" and path == f"/{token}/icon.png":
            return FakeResponse(200, artifacts["icon.png"])
        if request.method == "GET" and path == f"/{token}/DailyReader-0.1.42.ipa":
            return FakeResponse(200, artifacts["DailyReader-0.1.42.ipa"])
        raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, io.BytesIO())

    results = MODULE.verify_remote_release(
        tmp_path,
        token_path,
        open_url=open_url,
    )

    assert results == [
        "source: 200 and content matched",
        "icon: 200 and content matched",
        "IPA: 200 and content matched",
        "unrelated paths: 404",
    ]
    assert token not in "\n".join(results)


def test_verify_remote_release_rejects_loose_token_permissions(tmp_path: Path) -> None:
    token_path = tmp_path / "token.txt"
    token_path.write_text(TOKEN, encoding="utf-8")
    os.chmod(token_path, 0o644)

    with pytest.raises(RuntimeError, match="permissions must be 0600"):
        MODULE.verify_remote_release(tmp_path, token_path)


def test_verify_remote_release_rejects_unsafe_source_url(tmp_path: Path) -> None:
    token = TOKEN
    token_path = tmp_path / "token.txt"
    token_path.write_text(token + "\n", encoding="utf-8")
    os.chmod(token_path, 0o600)
    write_release(tmp_path, token)
    source_path = tmp_path / "remote-source.json"
    source = json.loads(source_path.read_text(encoding="utf-8"))
    source["apps"][0]["iconURL"] = f"http://evil.example.test/{token}/icon.png"
    source_path.write_text(json.dumps(source), encoding="utf-8")
    os.chmod(source_path, 0o600)

    with pytest.raises(RuntimeError, match="unsafe artifact URLs"):
        MODULE.verify_remote_release(tmp_path, token_path)


def test_verify_remote_release_allows_only_loopback_http_override(tmp_path: Path) -> None:
    token = TOKEN
    token_path = tmp_path / "token.txt"
    token_path.write_text(token + "\n", encoding="utf-8")
    os.chmod(token_path, 0o600)
    write_release(tmp_path, token)

    with pytest.raises(ValueError, match="loopback HTTP"):
        MODULE.verify_remote_release(
            tmp_path,
            token_path,
            request_origin_override="http://192.168.10.2:8789",
        )


def test_verify_tailscale_config_requires_exact_private_and_public_ports() -> None:
    hostname = "reader.example.test"
    config = {
        "TCP": {"443": {"HTTPS": True}, "8443": {"HTTPS": True}},
        "Web": {
            f"{hostname}:443": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:8787"}}
            },
            f"{hostname}:8443": {
                "Handlers": {"/": {"Proxy": "http://127.0.0.1:8789"}}
            },
        },
        "AllowFunnel": {f"{hostname}:8443": True},
    }

    assert MODULE.verify_tailscale_config(config, config, hostname) == (
        "Tailscale: 443 private and 8443 distribution-only"
    )

    unsafe_config = json.loads(json.dumps(config))
    unsafe_config["AllowFunnel"][f"{hostname}:443"] = True
    with pytest.raises(RuntimeError, match="approved layout"):
        MODULE.verify_tailscale_config(unsafe_config, unsafe_config, hostname)


@pytest.mark.parametrize("count", [0, 2])
def test_verify_rejects_a_source_without_one_current_target(tmp_path: Path, count: int) -> None:
    artifacts = write_release(tmp_path, TOKEN)
    source = json.loads(artifacts["remote-source.json"])
    version = source["apps"][0]["versions"][0]
    source["apps"][0]["versions"] = [version] * count
    with pytest.raises(RuntimeError, match="exactly one current version"):
        MODULE.validated_artifact_paths(source, TOKEN)


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("during_read", [False, True])
@pytest.mark.parametrize(
    ("error_type", "diagnostic"),
    [
        (ssl.SSLCertVerificationError, "TLS certificate verification failed"),
        (ssl.SSLError, "TLS connection failed"),
        (TimeoutError, "timed out"),
        (ConnectionRefusedError, "connection failed"),
        (http.client.BadStatusLine, "invalid or interrupted HTTP response"),
    ],
)
def test_fetch_classifies_failures_without_exposing_exception_chain(
    wrapped, during_read, error_type, diagnostic,
) -> None:
    url = f"https://reader.example.test:8443/{TOKEN}/source.json"
    original = error_type(url)
    failure = urllib.error.URLError(original) if wrapped else original

    class BrokenResponse(FakeResponse):
        def read(self):
            raise failure

    def open_url(_request, timeout):
        if during_read:
            return BrokenResponse(200, b"")
        raise failure

    with pytest.raises(MODULE.VerificationError, match=diagnostic) as captured:
        MODULE.fetch(open_url, url)
    formatted = "".join(traceback.format_exception(captured.value))
    assert TOKEN not in formatted
    assert url not in formatted


def test_fetch_handles_string_urlerror_and_incomplete_response() -> None:
    for failure, diagnostic in [
        (urllib.error.URLError(TOKEN), "connection failed"),
        (http.client.IncompleteRead(TOKEN.encode(), 999), "incomplete HTTP response"),
    ]:
        def open_url(_request, timeout, failure=failure):
            raise failure

        with pytest.raises(MODULE.VerificationError, match=diagnostic) as captured:
            MODULE.fetch(open_url, "https://reader.example.test/")
        assert TOKEN not in "".join(traceback.format_exception(captured.value))


def test_fetch_does_not_read_http_error_body_and_closes_it() -> None:
    class BrokenBody(io.BytesIO):
        def read(self, *_args):
            raise TimeoutError(TOKEN)

    body = BrokenBody()

    def open_url(request, timeout):
        raise urllib.error.HTTPError(request.full_url, 502, TOKEN, {}, body)

    assert MODULE.fetch(open_url, "https://reader.example.test/") == (502, b"")
    assert body.closed


@pytest.mark.parametrize(
    ("stage", "status"),
    [(stage, status) for stage in ["source", "IPA", "rejection"]
     for status in [200, 404, 502, 503] if (stage, status) != ("rejection", 404)],
)
def test_verification_distinguishes_http_status_from_content_and_rejection(
    tmp_path: Path, stage: str, status: int,
) -> None:
    token_path = tmp_path / "token.txt"
    token_path.write_text(TOKEN)
    token_path.chmod(0o600)
    artifacts = write_release(tmp_path, TOKEN)

    def open_url(request, timeout):
        name = request.selector.rsplit("/", 1)[-1]
        label = "source" if name == "source.json" else "IPA"
        if not request.selector.startswith(f"/{TOKEN}/"):
            label = "rejection"
        if label == stage and name != "icon.png":
            # Include the credential in the response body/reason to test output safety.
            if status == 200:
                return FakeResponse(status, TOKEN.encode())
            raise urllib.error.HTTPError(request.full_url, status, TOKEN, {}, io.BytesIO())
        if label == "rejection":
            raise urllib.error.HTTPError(request.full_url, 404, "not found", {}, io.BytesIO())
        return FakeResponse(200, artifacts.get(name, artifacts["remote-source.json"]))

    with pytest.raises(MODULE.VerificationError) as captured:
        MODULE.verify_remote_release(tmp_path, token_path, open_url=open_url)
    message = str(captured.value)
    assert f"HTTP {status}" in message
    if stage == "rejection":
        assert "expected HTTP 404" in message
        assert "exposed" not in message
    elif status == 200:
        assert "content did not match" in message
    elif status == 502:
        assert "gateway failure" in message and "8789" in message
        assert "content did not match" not in message
    else:
        assert "content did not match" not in message
    assert TOKEN not in "".join(traceback.format_exception(captured.value))


@pytest.mark.parametrize("value", [f"https://host:{TOKEN}/", f"https://[{TOKEN}]/"])
def test_invalid_url_does_not_expose_input_in_traceback(tmp_path: Path, value: str) -> None:
    artifacts = write_release(tmp_path, TOKEN)
    source = json.loads(artifacts["remote-source.json"])
    source["apps"][0]["iconURL"] = value
    with pytest.raises(RuntimeError) as captured:
        MODULE.validated_artifact_paths(source, TOKEN)
    assert TOKEN not in "".join(traceback.format_exception(captured.value))


def test_cli_reports_invalid_metadata_without_traceback_or_secret(tmp_path: Path) -> None:
    token_path = tmp_path / "token.txt"
    token_path.write_text(TOKEN)
    token_path.chmod(0o600)
    artifacts = write_release(tmp_path, TOKEN)
    source = json.loads(artifacts["remote-source.json"])
    source["apps"][0]["iconURL"] = f"https://host:{TOKEN}/"
    (tmp_path / "remote-source.json").write_text(json.dumps(source))
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path),
         "--token-file", str(token_path), "--skip-tailscale-config"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 1
    assert "invalid URL port" in result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize("status", [200, 404, 502])
def test_cli_with_real_loopback_http(tmp_path: Path, status: int) -> None:
    """Exercise urllib's real HTTPError path using synthetic credentials only."""
    token_path = tmp_path / "token.txt"
    token_path.write_text(TOKEN)
    token_path.chmod(0o600)
    artifacts = write_release(tmp_path, TOKEN)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            name = self.path.removeprefix(f"/{TOKEN}/")
            allowed = name in {"source.json", "icon.png", "DailyReader-0.1.42.ipa"}
            if name == "source.json":
                name = "remote-source.json"
            if allowed and self.path.startswith(f"/{TOKEN}/"):
                self.send_response(status)
                self.end_headers()
                self.wfile.write(artifacts[name] if status == 200 else TOKEN.encode())
            else:
                self.send_error(404)

        def do_POST(self):
            self.send_error(404)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--output-dir", str(tmp_path),
             "--token-file", str(token_path), "--skip-tailscale-config",
             "--request-origin", f"http://127.0.0.1:{server.server_port}"],
            capture_output=True, text=True, check=False, timeout=15,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    if status == 200:
        assert result.returncode == 0
        assert "IPA: 200 and content matched" in result.stdout
        assert "unrelated paths: 404" in result.stdout
    else:
        assert result.returncode == 1
        assert f"HTTP {status}" in result.stderr
        assert "content did not match" not in result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr
