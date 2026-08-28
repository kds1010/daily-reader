#!/usr/bin/env python3
"""Verify the token-protected SideStore endpoint without printing its URL."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import stat
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import date
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data/sidestore"
DEFAULT_TOKEN_FILE = REPOSITORY_ROOT / "secrets/sidestore-remote-token.txt"
DEFAULT_TAILSCALE = Path("/usr/local/bin/tailscale")
TOKEN_LENGTH = 43
SOURCE_SUBTITLE = "個人用の外出先更新ソース"
TOKEN_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)

OpenURL = Callable[..., object]


def fetch(open_url: OpenURL, url: str, method: str = "GET") -> tuple[int, bytes]:
    request = urllib.request.Request(url, method=method)
    try:
        with open_url(request, timeout=30) as response:
            return response.status, response.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()
    except urllib.error.URLError as error:
        raise RuntimeError("Could not reach the remote SideStore endpoint") from error


def request_url(origin: str, path: str) -> str:
    parsed = urllib.parse.urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("Request origin has an invalid port") from error
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
        or any(character.isspace() for character in origin)
        or urllib.parse.urlunsplit(parsed) != origin
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("Request origin must be an HTTP(S) origin without a path")
    return f"{origin.rstrip('/')}{path}"


def validated_artifact_paths(
    source: dict[str, object], token: str
) -> tuple[dict[str, str], dict[str, int]]:
    if not isinstance(source, dict) or source.get("subtitle") != SOURCE_SUBTITLE:
        raise RuntimeError("Remote SideStore source would reveal its credential URL")
    try:
        app = source["apps"][0]
        versions = app["versions"]
        urls = {
            "source": source["sourceURL"],
            "icon": app["iconURL"],
        }
    except (IndexError, KeyError, TypeError) as error:
        raise RuntimeError("Remote SideStore source has invalid metadata") from error
    if not isinstance(versions, list) or not versions:
        raise RuntimeError("Remote SideStore source has no versions")
    ipa_names = []
    declared_sizes = {}
    for index, version_item in enumerate(versions):
        try:
            version_value = version_item["version"]
            date_value = version_item["date"]
            size_value = version_item["size"]
            download_url = version_item["downloadURL"]
        except (KeyError, TypeError) as error:
            raise RuntimeError("Remote SideStore source has invalid metadata") from error
        if (
            not isinstance(version_value, str)
            or not isinstance(date_value, str)
            or not isinstance(size_value, int)
            or isinstance(size_value, bool)
            or size_value < 0
            or not isinstance(download_url, str)
        ):
            raise RuntimeError("Remote SideStore source has invalid version metadata")
        try:
            parsed_date = date.fromisoformat(date_value)
        except ValueError as error:
            raise RuntimeError("Remote SideStore source has invalid version metadata") from error
        if parsed_date.isoformat() != date_value:
            raise RuntimeError("Remote SideStore source has invalid version metadata")
        label = f"IPA-{index}"
        urls[label] = download_url
        ipa_name = urllib.parse.urlsplit(download_url).path.rsplit("/", 1)[-1]
        if ipa_name != f"DailyReader-{version_value}.ipa":
            raise RuntimeError("Remote SideStore source contains unsafe artifact URLs")
        ipa_names.append(ipa_name)
        declared_sizes[label] = size_value
    if not all(isinstance(url, str) for url in urls.values()):
        raise RuntimeError("Remote SideStore source contains non-string artifact URLs")

    parsed_urls = {label: urllib.parse.urlsplit(url) for label, url in urls.items()}
    try:
        for parsed in parsed_urls.values():
            _ = parsed.port
    except ValueError as error:
        raise RuntimeError("Remote SideStore source contains an invalid URL port") from error
    origin = parsed_urls["source"].netloc
    expected_paths = {
        "source": f"/{token}/source.json",
        "icon": f"/{token}/icon.png",
        **{
            f"IPA-{index}": f"/{token}/{ipa_name}"
            for index, ipa_name in enumerate(ipa_names)
        },
    }
    urls_are_safe = all(
        parsed.scheme == "https"
        and parsed.hostname is not None
        and parsed.netloc == origin
        and parsed.username is None
        and parsed.password is None
        and parsed.query == ""
        and parsed.fragment == ""
        and parsed.path == expected_paths[label]
        for label, parsed in parsed_urls.items()
    )
    versions_are_safe = len(set(ipa_names)) == len(ipa_names) and all(
        ipa_name.startswith("DailyReader-")
        and ipa_name.endswith(".ipa")
        and len(
            ipa_name.removeprefix("DailyReader-").removesuffix(".ipa").split(".")
        )
        == 3
        and all(
            part.isdigit()
            for part in ipa_name.removeprefix("DailyReader-")
            .removesuffix(".ipa")
            .split(".")
        )
        for ipa_name in ipa_names
    )
    if not versions_are_safe or not urls_are_safe:
        raise RuntimeError("Remote SideStore source contains unsafe artifact URLs")
    return expected_paths, declared_sizes


def load_tailscale_config(tailscale: Path, command: str) -> dict[str, object]:
    try:
        result = subprocess.run(
            [str(tailscale), command, "status", "--json"],
            check=True,
            capture_output=True,
            text=True,
        )
        config = json.loads(result.stdout)
    except (FileNotFoundError, subprocess.CalledProcessError, json.JSONDecodeError):
        raise RuntimeError("Could not read the Tailscale distribution configuration") from None
    if not isinstance(config, dict):
        raise RuntimeError("Tailscale returned an invalid distribution configuration")
    return config


def verify_tailscale_config(
    serve_config: dict[str, object],
    funnel_config: dict[str, object],
    hostname: str,
) -> str:
    expected_tcp = {
        "443": {"HTTPS": True},
        "8443": {"HTTPS": True},
    }
    expected_web = {
        f"{hostname}:443": {
            "Handlers": {"/": {"Proxy": "http://127.0.0.1:8787"}}
        },
        f"{hostname}:8443": {
            "Handlers": {"/": {"Proxy": "http://127.0.0.1:8789"}}
        },
    }
    expected_funnel = {f"{hostname}:8443": True}
    if (
        serve_config != funnel_config
        or serve_config.get("TCP") != expected_tcp
        or serve_config.get("Web") != expected_web
        or serve_config.get("AllowFunnel") != expected_funnel
        or serve_config.get("Services")
        or serve_config.get("Foreground")
    ):
        raise RuntimeError("Tailscale Serve/Funnel boundary does not match the approved layout")
    return "Tailscale: 443 private and 8443 distribution-only"


def verify_remote_release(
    directory: Path,
    token_path: Path,
    request_origin_override: str | None = None,
    open_url: OpenURL = urllib.request.urlopen,
) -> list[str]:
    if token_path.is_symlink():
        raise RuntimeError("Remote SideStore token file must not be a symlink")
    token = token_path.read_text(encoding="utf-8").strip()
    if stat.S_IMODE(token_path.stat().st_mode) != 0o600:
        raise RuntimeError("Remote SideStore token file permissions must be 0600")
    try:
        decoded = base64.b64decode(token + "=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    is_canonical = (
        len(decoded) == 32
        and base64.urlsafe_b64encode(decoded).decode().rstrip("=") == token
    )
    if (
        len(token) != TOKEN_LENGTH
        or not set(token) <= TOKEN_CHARACTERS
        or not is_canonical
    ):
        raise RuntimeError("Remote SideStore token is not a canonical URL-safe token")

    source_path = directory / "remote-source.json"
    icon_path = directory / "icon.png"
    if source_path.is_symlink() or icon_path.is_symlink() or not icon_path.is_file():
        raise RuntimeError("Remote SideStore source references an unsafe local artifact")
    if stat.S_IMODE(source_path.stat().st_mode) != 0o600:
        raise RuntimeError("Remote SideStore source file permissions must be 0600")
    source_bytes = source_path.read_bytes()
    source = json.loads(source_bytes)
    artifact_paths, declared_sizes = validated_artifact_paths(source, token)
    source_url = urllib.parse.urlsplit(source["sourceURL"])

    if request_origin_override:
        override = urllib.parse.urlsplit(request_origin_override)
        if override.scheme != "http" or override.hostname != "127.0.0.1":
            raise ValueError("Request origin override must use loopback HTTP")
        origin = request_origin_override
    else:
        origin = f"{source_url.scheme}://{source_url.netloc}"
    request_url(origin, "/")

    ipa_labels = [label for label in artifact_paths if label.startswith("IPA-")]
    ipa_names = [artifact_paths[label].rsplit("/", 1)[-1] for label in ipa_labels]
    if not all(
        (directory / name).is_file() and not (directory / name).is_symlink()
        and (directory / name).stat().st_size == declared_sizes[label]
        for label, name in zip(ipa_labels, ipa_names, strict=True)
    ):
        raise RuntimeError("Remote SideStore source references a missing local IPA")
    ipa_name = ipa_names[0]
    artifact_urls = {
        "source": (artifact_paths["source"], source_bytes),
        "icon": (artifact_paths["icon"], icon_path.read_bytes()),
        "IPA": (artifact_paths["IPA-0"], (directory / ipa_name).read_bytes()),
    }

    results = []
    for label, (path, expected_body) in artifact_urls.items():
        status, body = fetch(open_url, request_url(origin, path))
        if status != 200 or body != expected_body:
            raise RuntimeError(f"Remote SideStore {label} did not match the local artifact")
        results.append(f"{label}: 200 and content matched")

    wrong_token = "A" * TOKEN_LENGTH
    if wrong_token == token:
        wrong_token = "B" * len(wrong_token)
    rejected_requests = (
        (f"/{wrong_token}/source.json", "GET"),
        ("/source.json", "GET"),
        (f"/{token}/api/deployment", "GET"),
        (f"/{token}/../source.json", "GET"),
        (f"/{token}/remote-source.json", "GET"),
        (f"/{token}/source.json", "POST"),
        ("/", "GET"),
    )
    for path, method in rejected_requests:
        status, _ = fetch(open_url, request_url(origin, path), method)
        if status != 404:
            raise RuntimeError("Remote SideStore endpoint exposed an unexpected path")
    results.append("unrelated paths: 404")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    parser.add_argument("--tailscale", type=Path, default=DEFAULT_TAILSCALE)
    parser.add_argument("--skip-tailscale-config", action="store_true")
    parser.add_argument(
        "--request-origin",
        help="Override only the endpoint used for checks, for example http://127.0.0.1:8789",
    )
    args = parser.parse_args()

    results = verify_remote_release(
        args.output_dir,
        args.token_file,
        args.request_origin,
    )
    if not args.skip_tailscale_config:
        source = json.loads(
            (args.output_dir / "remote-source.json").read_text(encoding="utf-8")
        )
        hostname = urllib.parse.urlsplit(source["sourceURL"]).hostname
        if hostname is None:
            raise RuntimeError("Remote SideStore source has no hostname")
        serve_config = load_tailscale_config(args.tailscale, "serve")
        funnel_config = load_tailscale_config(args.tailscale, "funnel")
        results.append(verify_tailscale_config(serve_config, funnel_config, hostname))
    for result in results:
        print(result)


if __name__ == "__main__":
    main()
