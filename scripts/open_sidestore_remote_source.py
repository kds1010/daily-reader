#!/usr/bin/env python3
"""Open the private remote source in SideStore without revealing its URL."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import stat
import subprocess
import urllib.parse
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data/sidestore"
DEFAULT_TOKEN_FILE = REPOSITORY_ROOT / "secrets/sidestore-remote-token.txt"
SOURCE_SUBTITLE = "個人用の外出先更新ソース"
TOKEN_LENGTH = 43
TOKEN_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)


def load_source_url(directory: Path, token_path: Path) -> str:
    if token_path.is_symlink():
        raise RuntimeError("Remote source token file must not be a symlink")
    token = token_path.read_text(encoding="utf-8").strip()
    if stat.S_IMODE(token_path.stat().st_mode) != 0o600:
        raise RuntimeError("Remote source token file permissions must be 0600")
    try:
        decoded = base64.b64decode(token + "=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    if (
        len(token) != TOKEN_LENGTH
        or not set(token) <= TOKEN_CHARACTERS
        or len(decoded) != 32
        or base64.urlsafe_b64encode(decoded).decode().rstrip("=") != token
    ):
        raise RuntimeError("Remote source token is not canonical")
    source = json.loads((directory / "remote-source.json").read_text(encoding="utf-8"))
    if not isinstance(source, dict) or source.get("subtitle") != SOURCE_SUBTITLE:
        raise RuntimeError("Remote source would reveal its credential URL")
    source_url = source["sourceURL"]
    parsed = urllib.parse.urlsplit(source_url)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path != f"/{token}/source.json"
        or parsed.query
        or parsed.fragment
    ):
        raise RuntimeError("Remote source URL does not match the local credential")
    return source_url


def sidestore_deep_link(source_url: str) -> str:
    return "sidestore://source?" + urllib.parse.urlencode({"url": source_url})


def open_source(device: str, bundle_id: str, source_url: str) -> None:
    command = [
        "xcrun",
        "devicectl",
        "device",
        "process",
        "launch",
        "--quiet",
        "--device",
        device,
        "--payload-url",
        sidestore_deep_link(source_url),
        bundle_id,
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        raise RuntimeError("Could not open the remote source on the selected iPhone") from None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--device", required=True)
    parser.add_argument("--bundle-id", required=True, help="Installed SideStore bundle identifier")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--token-file", type=Path, default=DEFAULT_TOKEN_FILE)
    args = parser.parse_args()

    source_url = load_source_url(args.output_dir, args.token_file)
    open_source(args.device, args.bundle_id, source_url)
    print("Opened the credential-protected remote source in SideStore")


if __name__ == "__main__":
    main()
