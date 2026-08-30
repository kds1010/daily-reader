#!/usr/bin/env python3
"""Build and publish an ad-hoc signed Daymeld app for this Mac."""

from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROJECT = REPOSITORY_ROOT / "ios/DailyReader/DailyReader.xcodeproj"
ENTITLEMENTS = (
    REPOSITORY_ROOT
    / "ios/DailyReader/DailyReaderMac/DailyReaderMac.entitlements"
)
DEFAULT_OUTPUT = REPOSITORY_ROOT / "data/macos"
DEFAULT_DERIVED_DATA = Path("/tmp/daily-reader-macos-release")
APP_NAME = "Daymeld.app"
BUNDLE_IDENTIFIER = "net.skmin.DailyReader.mac"
REQUIRED_ENTITLEMENTS = frozenset(
    {
        "com.apple.security.app-sandbox",
        "com.apple.security.network.client",
    }
)
FORBIDDEN_ENTITLEMENTS = frozenset(
    {
        "com.apple.developer.healthkit",
        "com.apple.developer.healthkit.background-delivery",
    }
)


def run(*command: str, cwd: Path = REPOSITORY_ROOT) -> str:
    result = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def release_version(commit_count: int) -> str:
    if commit_count < 1:
        raise ValueError("commit count must be positive")
    return f"0.1.{commit_count}"


def validate_entitlements(entitlements: dict[str, object]) -> None:
    missing = sorted(
        key for key in REQUIRED_ENTITLEMENTS if entitlements.get(key) is not True
    )
    forbidden = sorted(key for key in FORBIDDEN_ENTITLEMENTS if key in entitlements)
    unexpected = sorted(set(entitlements) - REQUIRED_ENTITLEMENTS)
    if missing:
        raise RuntimeError("macOS app is missing entitlements: " + ", ".join(missing))
    if forbidden:
        raise RuntimeError(
            "macOS app unexpectedly contains iPhone entitlements: "
            + ", ".join(forbidden)
        )
    if unexpected:
        raise RuntimeError(
            "macOS app contains unexpected entitlements: " + ", ".join(unexpected)
        )


def read_signed_entitlements(app: Path) -> dict[str, object]:
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
        return plistlib.loads(displayed.stdout)
    except plistlib.InvalidFileException as error:
        raise RuntimeError("macOS app entitlements could not be decoded") from error


def sign_and_validate_app(app: Path, version: str, build_number: str) -> None:
    if not app.is_dir() or app.is_symlink():
        raise FileNotFoundError(f"built application not found or unsafe: {app}")
    if not ENTITLEMENTS.is_file() or ENTITLEMENTS.is_symlink():
        raise FileNotFoundError("macOS entitlements file is missing or unsafe")

    subprocess.run(
        [
            "/usr/bin/codesign",
            "--force",
            "--sign",
            "-",
            "--entitlements",
            str(ENTITLEMENTS),
            "--options",
            "runtime",
            "--timestamp=none",
            str(app),
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(app)],
        cwd=REPOSITORY_ROOT,
        check=True,
    )

    info_path = app / "Contents/Info.plist"
    with info_path.open("rb") as stream:
        info = plistlib.load(stream)
    expected = {
        "CFBundleIdentifier": BUNDLE_IDENTIFIER,
        "CFBundleShortVersionString": version,
        "CFBundleVersion": build_number,
    }
    mismatches = [
        f"{key}={info.get(key)!r} (expected {value!r})"
        for key, value in expected.items()
        if info.get(key) != value
    ]
    if mismatches:
        raise RuntimeError("macOS app metadata mismatch: " + "; ".join(mismatches))

    architectures = set(
        run("/usr/bin/lipo", "-archs", str(app / "Contents/MacOS/Daymeld")).split()
    )
    if "arm64" not in architectures:
        raise RuntimeError("macOS app does not contain the required arm64 architecture")
    validate_entitlements(read_signed_entitlements(app))


def remove_artifact(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def publish_app(app: Path, output: Path) -> tuple[Path, Path]:
    if output.is_symlink():
        raise ValueError("macOS output directory must not be a symlink")
    output.mkdir(parents=True, exist_ok=True)

    published_app = output / APP_NAME
    temporary_app = output / f".{APP_NAME}.tmp"
    remove_artifact(temporary_app)
    shutil.copytree(app, temporary_app, symlinks=True)
    remove_artifact(published_app)
    temporary_app.replace(published_app)

    archive = output / "Daymeld-macOS.zip"
    temporary_archive = output / ".Daymeld-macOS.zip.tmp"
    remove_artifact(temporary_archive)
    subprocess.run(
        [
            "/usr/bin/zip",
            "-q",
            "-r",
            "-y",
            "-X",
            str(temporary_archive),
            APP_NAME,
        ],
        cwd=output,
        check=True,
    )
    temporary_archive.replace(archive)
    return published_app, archive


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--derived-data", type=Path, default=DEFAULT_DERIVED_DATA)
    args = parser.parse_args()

    commit_count = int(run("git", "rev-list", "--count", "HEAD"))
    version = release_version(commit_count)
    build_number = str(commit_count)
    subprocess.run(
        [
            "xcodebuild",
            "-project",
            str(PROJECT),
            "-scheme",
            "DaymeldMac",
            "-sdk",
            "macosx",
            "-configuration",
            "Release",
            "-derivedDataPath",
            str(args.derived_data),
            "CODE_SIGNING_ALLOWED=NO",
            "ARCHS=arm64",
            f"MARKETING_VERSION={version}",
            f"CURRENT_PROJECT_VERSION={build_number}",
            "build",
        ],
        cwd=REPOSITORY_ROOT,
        check=True,
    )
    app = args.derived_data / "Build/Products/Release" / APP_NAME
    sign_and_validate_app(app, version, build_number)
    published_app, archive = publish_app(app, args.output_dir)
    sign_and_validate_app(published_app, version, build_number)
    print(f"macOS release {version}: {published_app}")
    print(f"Archive: {archive}")


if __name__ == "__main__":
    main()
