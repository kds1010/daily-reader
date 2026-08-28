import io
import json
import os
import urllib.error
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
