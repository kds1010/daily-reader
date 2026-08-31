#!/usr/bin/env python3
"""Build an arm64 KeyHints macOS release artifact."""

from __future__ import annotations

import argparse
import plistlib
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROJECT = ROOT / "ios/DailyReader/DailyReader.xcodeproj"


def release_version(count: int) -> str:
    if count < 1:
        raise ValueError("commit count must be positive")
    return f"0.1.{count}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=ROOT / "data/macos")
    parser.add_argument("--derived-data", type=Path, default=Path("/tmp/keyhints-release"))
    args = parser.parse_args()
    count = int(
        subprocess.check_output(["git", "rev-list", "--count", "HEAD"], cwd=ROOT, text=True)
    )
    version = release_version(count)
    command = [
        "xcodebuild",
        "-project",
        str(PROJECT),
        "-scheme",
        "KeyHintsMac",
        "-sdk",
        "macosx",
        "-configuration",
        "Release",
        "-derivedDataPath",
        str(args.derived_data),
        "CODE_SIGNING_ALLOWED=NO",
        "ARCHS=arm64",
        f"MARKETING_VERSION={version}",
        f"CURRENT_PROJECT_VERSION={count}",
        "build",
    ]
    subprocess.run(command, cwd=ROOT, check=True)
    built = args.derived_data / "Build/Products/Release/KeyHints.app"
    args.output_dir.mkdir(parents=True, exist_ok=True)
    output = args.output_dir / "KeyHints.app"
    if output.exists():
        shutil.rmtree(output)
    shutil.copytree(built, output, symlinks=True)
    subprocess.run(
        ["/usr/bin/codesign", "--force", "--sign", "-", "--timestamp=none", str(output)], check=True
    )
    subprocess.run(["/usr/bin/codesign", "--verify", "--deep", "--strict", str(output)], check=True)
    with (output / "Contents/Info.plist").open("rb") as stream:
        info = plistlib.load(stream)
    assert info["CFBundleIdentifier"] == "net.skmin.KeyHints"
    assert info["CFBundleShortVersionString"] == version
    archive = args.output_dir / "KeyHints-macOS.zip"
    if archive.exists():
        archive.unlink()
    subprocess.run(
        ["/usr/bin/zip", "-q", "-r", "-y", "-X", str(archive), "KeyHints.app"],
        cwd=args.output_dir,
        check=True,
    )
    print(f"KeyHints release {version}: {output}")


if __name__ == "__main__":
    main()
