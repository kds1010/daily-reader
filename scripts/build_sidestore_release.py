#!/usr/bin/env python3
"""Build a SideStore seed IPA and publish its private AltSources."""

from __future__ import annotations

import argparse
import base64
import binascii
import json
import os
import plistlib
import secrets
import shutil
import stat
import subprocess
import tempfile
import urllib.parse
from datetime import date
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROJECT = REPOSITORY_ROOT / "ios/DailyReader/DailyReader.xcodeproj"
ICON = (
    REPOSITORY_ROOT
    / "ios/DailyReader/DailyReader/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
)
ENTITLEMENTS = REPOSITORY_ROOT / "ios/DailyReader/DailyReader/DailyReader.entitlements"
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data/sidestore"
DEFAULT_BASE_URL = "http://sk-mins-Mac-mini.local:8788"
DEFAULT_REMOTE_ORIGIN = "https://sk-mins-mac-mini.tailc193b2.ts.net:8443"
DEFAULT_REMOTE_TOKEN_FILE = REPOSITORY_ROOT / "secrets/sidestore-remote-token.txt"
BUNDLE_IDENTIFIER = "net.skmin.DailyReader"
REMOTE_TOKEN_LENGTH = 43
REMOTE_VERSION_RETENTION = 10
REMOTE_SOURCE_SUBTITLE = "個人用の外出先更新ソース"
REQUIRED_SIDESTORE_ENTITLEMENTS = frozenset(
    {
        "com.apple.developer.healthkit",
        "com.apple.developer.healthkit.background-delivery",
    }
)
REMOTE_TOKEN_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
)
BACKGROUND_REFRESH_IDENTIFIER = "net.skmin.DailyReader.agent-refresh"


def validate_background_refresh_metadata(app: Path) -> None:
    info = plistlib.loads((app / "Info.plist").read_bytes())
    if "fetch" not in info.get("UIBackgroundModes", []):
        raise RuntimeError("DailyReader.app is missing Background Fetch mode")
    if BACKGROUND_REFRESH_IDENTIFIER not in info.get("BGTaskSchedulerPermittedIdentifiers", []):
        raise RuntimeError("DailyReader.app is missing the agent refresh identifier")


def run(*command: str, cwd: Path = REPOSITORY_ROOT) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def build_source(version: str, ipa_size: int, base_url: str) -> dict[str, object]:
    base_url = base_url.rstrip("/")
    return {
        "$schema": "https://github.com/SideStore/sidestore-source-types/raw/main/schema.json",
        "name": "Daymeld",
        "identifier": "net.skmin.DailyReader.source",
        "sourceURL": f"{base_url}/source.json",
        "apps": [
            {
                "name": "Daymeld",
                "bundleIdentifier": BUNDLE_IDENTIFIER,
                "developerName": "sk-min",
                "subtitle": "生活・情報・Codex Agentをまとめる個人ダッシュボード",
                "localizedDescription": (
                    "Tailscale内のMac miniへ接続し、Agent操作、今日の予定、HealthKit、"
                    "未読メール、ニュースをネイティブ表示します。"
                ),
                "iconURL": f"{base_url}/icon.png",
                "tintColor": "#34D399",
                "versions": [
                    {
                        "version": version,
                        "date": date.today().isoformat(),
                        "localizedDescription": "Daymeldの最新ネイティブ版です。",
                        "downloadURL": f"{base_url}/DailyReader.ipa",
                        "size": ipa_size,
                        "minOSVersion": "17.0",
                    }
                ],
            }
        ],
    }


def validate_remote_token(token: str) -> str:
    try:
        decoded = base64.b64decode(token + "=", altchars=b"-_", validate=True)
    except (binascii.Error, ValueError):
        decoded = b""
    is_canonical = (
        len(decoded) == 32
        and base64.urlsafe_b64encode(decoded).decode().rstrip("=") == token
    )
    if (
        len(token) != REMOTE_TOKEN_LENGTH
        or not set(token) <= REMOTE_TOKEN_CHARACTERS
        or not is_canonical
    ):
        raise ValueError("SideStore remote token must be a canonical 32-byte URL-safe token")
    return token


def load_or_create_remote_token(path: Path) -> str:
    try:
        if path.is_symlink():
            raise ValueError("SideStore remote token file must not be a symlink")
        token = validate_remote_token(path.read_text(encoding="utf-8").strip())
    except FileNotFoundError:
        pass
    else:
        if stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise ValueError("SideStore remote token file permissions must be 0600")
        return token

    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(32)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return load_or_create_remote_token(path)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(token + "\n")
    return validate_remote_token(token)


def validate_remote_origin(origin: str) -> str:
    parsed = urllib.parse.urlsplit(origin)
    try:
        port = parsed.port
    except ValueError as error:
        raise ValueError("SideStore remote origin has an invalid port") from error
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
        or any(character.isspace() for character in origin)
        or urllib.parse.urlunsplit(parsed) != origin
    ):
        raise ValueError("SideStore remote origin must be an HTTPS origin without a path")
    return origin.rstrip("/")


def build_remote_source(
    version: str,
    ipa_size: int,
    origin: str,
    token: str,
    previous_versions: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    base_url = f"{validate_remote_origin(origin)}/{validate_remote_token(token)}"
    source = build_source(version, ipa_size, base_url)
    source["name"] = "Daymeld Remote"
    source["subtitle"] = REMOTE_SOURCE_SUBTITLE
    source["identifier"] = "net.skmin.DailyReader.remote-source"
    app = source["apps"][0]
    app["versions"][0]["downloadURL"] = f"{base_url}/DailyReader-{version}.ipa"
    retained_versions = [
        previous
        for previous in (previous_versions or [])
        if isinstance(previous, dict) and previous.get("version") != version
    ][: REMOTE_VERSION_RETENTION - 1]
    app["versions"] = [app["versions"][0], *retained_versions]
    return source


def load_previous_remote_versions(
    directory: Path,
    origin: str,
    token: str,
) -> list[dict[str, object]]:
    base_url = f"{validate_remote_origin(origin)}/{validate_remote_token(token)}"
    try:
        source = json.loads(
            (directory / "remote-source.json").read_text(encoding="utf-8")
        )
        app = source["apps"][0]
        versions = app["versions"]
    except (IndexError, KeyError, OSError, TypeError, UnicodeError, json.JSONDecodeError):
        return []
    if (
        source.get("sourceURL") != f"{base_url}/source.json"
        or app.get("iconURL") != f"{base_url}/icon.png"
        or not isinstance(versions, list)
    ):
        return []

    retained = []
    seen_versions = set()
    for previous in versions:
        if not isinstance(previous, dict):
            continue
        previous_version = previous.get("version")
        previous_date = previous.get("date")
        previous_size = previous.get("size")
        if (
            not isinstance(previous_version, str)
            or previous_version in seen_versions
            or not isinstance(previous_date, str)
            or not isinstance(previous_size, int)
            or isinstance(previous_size, bool)
            or previous_size < 0
        ):
            continue
        try:
            parsed_date = date.fromisoformat(previous_date)
        except ValueError:
            continue
        if parsed_date.isoformat() != previous_date:
            continue
        parts = previous_version.split(".")
        ipa_name = f"DailyReader-{previous_version}.ipa"
        ipa_path = directory / ipa_name
        if (
            len(parts) != 3
            or not all(part.isdigit() for part in parts)
            or previous.get("downloadURL") != f"{base_url}/{ipa_name}"
            or not ipa_path.is_file()
            or ipa_path.is_symlink()
            or ipa_path.stat().st_size != previous_size
        ):
            continue
        canonical = {
            "version": previous_version,
            "date": previous_date,
            "downloadURL": previous["downloadURL"],
            "size": previous_size,
        }
        for optional_key in ("localizedDescription", "minOSVersion", "maxOSVersion"):
            optional_value = previous.get(optional_key)
            if isinstance(optional_value, str):
                canonical[optional_key] = optional_value
        retained.append(canonical)
        seen_versions.add(previous_version)
        if len(retained) == REMOTE_VERSION_RETENTION - 1:
            break
    return retained


def write_source(path: Path, source: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    os.fchmod(descriptor, 0o644)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(source, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def write_private_source(path: Path, source: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.unlink(missing_ok=True)
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.fchmod(descriptor, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
        stream.write(json.dumps(source, ensure_ascii=False, indent=2) + "\n")
    temporary.replace(path)


def atomic_copy(source: Path, destination: Path) -> None:
    temporary = destination.with_name(f".{destination.name}.tmp")
    temporary.unlink(missing_ok=True)
    shutil.copy2(source, temporary)
    temporary.replace(destination)


def sign_app_for_sidestore(app: Path) -> None:
    """Add seed entitlements that SideStore must preserve while re-signing."""
    if not ENTITLEMENTS.is_file() or ENTITLEMENTS.is_symlink():
        raise FileNotFoundError("Daily Reader entitlements file is missing or unsafe")
    subprocess.run(
        [
            "/usr/bin/codesign",
            "--force",
            "--sign",
            "-",
            "--entitlements",
            str(ENTITLEMENTS),
            "--timestamp=none",
            str(app),
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    displayed = subprocess.run(
        [
            "/usr/bin/codesign",
            "--display",
            "--entitlements",
            ":-",
            str(app),
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
        capture_output=True,
    )
    try:
        signed_entitlements = plistlib.loads(displayed.stdout)
    except plistlib.InvalidFileException as error:
        raise RuntimeError("SideStore seed entitlements could not be decoded") from error
    missing = sorted(
        entitlement
        for entitlement in REQUIRED_SIDESTORE_ENTITLEMENTS
        if signed_entitlements.get(entitlement) is not True
    )
    if missing:
        raise RuntimeError(
            "SideStore seed IPA is missing required entitlements: " + ", ".join(missing)
        )
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--strict", str(app)],
        cwd=REPOSITORY_ROOT,
        check=True,
    )


def prune_unlisted_versioned_ipas(
    directory: Path,
    versions: list[dict[str, object]],
) -> None:
    retained_names = {
        f"DailyReader-{version_item['version']}.ipa"
        for version_item in versions
    }
    for candidate in directory.glob("DailyReader-*.ipa"):
        if (
            candidate.name not in retained_names
            and candidate.is_file()
            and not candidate.is_symlink()
        ):
            candidate.unlink()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--remote-origin", default=DEFAULT_REMOTE_ORIGIN)
    parser.add_argument("--remote-token-file", type=Path, default=DEFAULT_REMOTE_TOKEN_FILE)
    parser.add_argument("--disable-remote-source", action="store_true")
    parser.add_argument("--derived-data", type=Path, default=Path("/tmp/daily-reader-sidestore"))
    args = parser.parse_args()

    commit_count = int(run("git", "rev-list", "--count", "HEAD"))
    version = f"0.1.{commit_count}"
    build_number = str(commit_count)
    subprocess.run(
        [
            "xcodebuild",
            "-project",
            str(PROJECT),
            "-scheme",
            "DailyReader",
            "-sdk",
            "iphoneos",
            "-configuration",
            "Release",
            "-derivedDataPath",
            str(args.derived_data),
            "CODE_SIGNING_ALLOWED=NO",
            f"MARKETING_VERSION={version}",
            f"CURRENT_PROJECT_VERSION={build_number}",
            "build",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    app = args.derived_data / "Build/Products/Release-iphoneos/DailyReader.app"
    if not app.is_dir():
        raise FileNotFoundError(f"built application not found: {app}")
    validate_background_refresh_metadata(app)
    sign_app_for_sidestore(app)

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ipa = args.output_dir / "DailyReader.ipa"
    temporary_ipa = args.output_dir / ".DailyReader.ipa.tmp"
    temporary_ipa.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="daily-reader-ipa-") as temporary:
        payload = Path(temporary) / "Payload"
        payload.mkdir()
        shutil.copytree(app, payload / app.name)
        subprocess.run(
            ["/usr/bin/zip", "-q", "-r", "-y", "-X", str(temporary_ipa), "Payload"],
            cwd=temporary,
            check=True,
            env={**os.environ, "COPYFILE_DISABLE": "1"},
        )
    temporary_ipa.replace(ipa)

    atomic_copy(ICON, args.output_dir / "icon.png")
    source = build_source(version, ipa.stat().st_size, args.base_url)
    write_source(args.output_dir / "source.json", source)
    if args.disable_remote_source:
        (args.output_dir / "remote-source.json").unlink(missing_ok=True)
        prune_unlisted_versioned_ipas(args.output_dir, [])
    else:
        token = load_or_create_remote_token(args.remote_token_file)
        previous_versions = load_previous_remote_versions(
            args.output_dir,
            args.remote_origin,
            token,
        )
        versioned_ipa = args.output_dir / f"DailyReader-{version}.ipa"
        atomic_copy(ipa, versioned_ipa)
        remote_source = build_remote_source(
            version,
            versioned_ipa.stat().st_size,
            args.remote_origin,
            token,
            previous_versions,
        )
        write_private_source(args.output_dir / "remote-source.json", remote_source)
        prune_unlisted_versioned_ipas(
            args.output_dir,
            remote_source["apps"][0]["versions"],
        )
    print(f"SideStore release {version}: {ipa}")
    print(f"Source URL: {args.base_url.rstrip('/')}/source.json")
    if not args.disable_remote_source:
        print("Remote SideStore source generated; credential URL omitted")


if __name__ == "__main__":
    main()
