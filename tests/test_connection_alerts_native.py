"""Run production Swift reconciliation with an anonymous notification transport."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios/DailyReader/DailyReader"


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_connection_alert_delivery_and_snapshot_races(tmp_path):
    env = {
        **os.environ,
        "CLANG_MODULE_CACHE_PATH": str(tmp_path / "cache"),
        "SWIFT_MODULECACHE_PATH": str(tmp_path / "cache"),
    }
    executable = tmp_path / "connection-alerts"
    compiled = subprocess.run(
        [
            "xcrun", "swiftc", "-whole-module-optimization",
            str(IOS / "Models.swift"), str(IOS / "APIClient.swift"),
            str(IOS / "ConnectionAlerts.swift"),
            str(ROOT / "tests/swift/ConnectionAlertsHarness.swift"),
            "-o", str(executable),
        ],
        capture_output=True, text=True, env=env, timeout=120,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run(
        [str(executable)], capture_output=True, text=True, env=env, timeout=30,
    )
    assert result.returncode == 0, f"{result.stderr}\n{result.stdout}"
    assert "Connection alert delivery and snapshot races passed" in result.stdout

