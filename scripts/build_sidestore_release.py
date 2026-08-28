#!/usr/bin/env python3
"""Build an unsigned Daily Reader IPA and publish a SideStore AltSource."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from datetime import date
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROJECT = REPOSITORY_ROOT / "ios/DailyReader/DailyReader.xcodeproj"
ICON = (
    REPOSITORY_ROOT
    / "ios/DailyReader/DailyReader/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
)
DEFAULT_OUTPUT = REPOSITORY_ROOT / "site/sidestore"
DEFAULT_BASE_URL = "https://sk-mins-mac-mini.tailc193b2.ts.net/sidestore"
BUNDLE_IDENTIFIER = "net.skmin.DailyReader"


def run(*command: str, cwd: Path = REPOSITORY_ROOT) -> str:
    result = subprocess.run(command, cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def build_source(version: str, ipa_size: int, base_url: str) -> dict[str, object]:
    base_url = base_url.rstrip("/")
    return {
        "$schema": "https://github.com/SideStore/sidestore-source-types/raw/main/schema.json",
        "name": "Daily Reader",
        "identifier": "net.skmin.DailyReader.source",
        "sourceURL": f"{base_url}/source.json",
        "apps": [
            {
                "name": "Daily Reader",
                "bundleIdentifier": BUNDLE_IDENTIFIER,
                "developerName": "sk-min",
                "subtitle": "生活・情報・Codex Agentをまとめる個人ダッシュボード",
                "localizedDescription": (
                    "Tailscale内のMac miniへ接続し、Agent操作、今日の予定、HealthKit、"
                    "重要メール、ニュースをネイティブ表示します。"
                ),
                "iconURL": f"{base_url}/icon.png",
                "tintColor": "#34D399",
                "versions": [
                    {
                        "version": version,
                        "date": date.today().isoformat(),
                        "localizedDescription": "Daily Readerの最新ネイティブ版です。",
                        "downloadURL": f"{base_url}/DailyReader.ipa",
                        "size": ipa_size,
                        "minOSVersion": "17.0",
                    }
                ],
            }
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
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

    args.output_dir.mkdir(parents=True, exist_ok=True)
    ipa = args.output_dir / "DailyReader.ipa"
    with tempfile.TemporaryDirectory(prefix="daily-reader-ipa-") as temporary:
        payload = Path(temporary) / "Payload"
        payload.mkdir()
        shutil.copytree(app, payload / app.name)
        subprocess.run(
            ["/usr/bin/ditto", "-c", "-k", "--keepParent", "Payload", ipa],
            cwd=temporary,
            check=True,
        )

    shutil.copy2(ICON, args.output_dir / "icon.png")
    source = build_source(version, ipa.stat().st_size, args.base_url)
    (args.output_dir / "source.json").write_text(
        json.dumps(source, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"SideStore release {version}: {ipa}")
    print(f"Source URL: {args.base_url.rstrip('/')}/source.json")


if __name__ == "__main__":
    main()
