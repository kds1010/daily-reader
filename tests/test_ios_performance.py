"""Exercise the real AppModel and URLSession path with delayed anonymous responses."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

IOS = Path(__file__).resolve().parents[1] / "ios/DailyReader/DailyReader"


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_refresh_latency_coalescing_and_stale_responses(tmp_path):
    # Only OS side effects are replaced. Scheduling, decoding, cancellation and
    # Combine publication use production code. Animation/rendering belongs to
    # the native build and Simulator checks, not this headless contract.
    stubs = tmp_path / "SideEffects.swift"
    stubs.write_text("""
import Foundation
enum Animation {
    case test
    static func easeInOut(duration: Double) -> Self { .test }
    static func spring(response: Double, dampingFraction: Double) -> Self { .test }
}
func withAnimation<T>(_ animation: Animation, _ body: () -> T) -> T { body() }
struct NotificationOptions: OptionSet {
    let rawValue: Int
    static let alert = Self(rawValue: 1)
    static let badge = Self(rawValue: 2)
    static let sound = Self(rawValue: 4)
}
@MainActor final class UNUserNotificationCenter {
    static func current() -> UNUserNotificationCenter { UNUserNotificationCenter() }
    func requestAuthorization(options: NotificationOptions) async throws -> Bool { false }
}
@MainActor final class LifeStore { func refresh() async {} }
@MainActor final class AgentNotificationCoordinator {
    static let shared = AgentNotificationCoordinator()
    func changedJobs(active: [AgentJob], archived: [AgentJob]) -> [AgentJob] { [] }
    func schedule(for job: AgentJob) async {}
}
""")
    diary = tmp_path / "Diary.swift"
    diary.write_text(
        (IOS / "Diary.swift").read_text().split("struct DiaryView: View")[0]
        .replace("import SwiftUI\n", "")
    )
    runner = tmp_path / "Runner.swift"
    runner.write_text((Path(__file__).parent / "swift/AppRefreshHarness.swift").read_text())
    model = tmp_path / "AppModel.swift"
    baseline_path = os.environ.get("DAYMELD_BASELINE_MODEL")
    source_path = Path(baseline_path) if baseline_path else IOS / "AppModel.swift"
    source = source_path.read_text()
    if baseline_path:
        # Optional before/after experiment: add dependency injection only to a
        # read-only copy of the pre-change model, leaving its control flow intact.
        source = source.replace("private let api = APIClient.shared", "private let api: APIClient")
        source = source.replace(
            "init(fixture: DaymeldFixture? = nil) {",
            "init(fixture: DaymeldFixture? = nil, api: APIClient = .shared) { self.api = api",
        )
    # Avoid importing the UI SDK for a test with no views. This changes no
    # AppModel control flow; the animation closure still executes synchronously.
    source = source.replace("import SwiftUI\n", "").replace("import UserNotifications\n", "")
    model.write_text(source)
    env = os.environ.copy()
    module_cache = os.environ.get("DAYMELD_SWIFT_TEST_CACHE", str(tmp_path / "module-cache"))
    env["CLANG_MODULE_CACHE_PATH"] = module_cache
    env["SWIFT_MODULECACHE_PATH"] = module_cache
    binary = tmp_path / "refresh-contract"
    result = subprocess.run(
        ["xcrun", "swiftc", "-whole-module-optimization",
         str(IOS / "Models.swift"), str(IOS / "APIClient.swift"),
         str(IOS / "DaymeldFixtures.swift"), str(model), str(diary), str(stubs), str(runner),
         *(["-D", "BASELINE"] if baseline_path else []), "-o", str(binary)],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run(
        [str(binary), *(["--baseline"] if baseline_path else [])],
        capture_output=True, text=True, env=env, timeout=90,
    )
    assert result.returncode == 0, f"{result.stderr}\n{result.stdout}"
    print(result.stdout)
    assert "first_agent_seconds=" in result.stdout
