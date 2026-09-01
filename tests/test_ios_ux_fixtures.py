import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_daymeld_ux_fixture_scenarios_cover_empty_failure_and_stress(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    # Swift permits top-level executable statements in `main.swift` when
    # compiling multiple source files.  Keep the contract source named that
    # way so the test exercises the real fixture files without a package.
    main = tmp_path / "main.swift"
    main.write_text(
        r'''
import Foundation

let standard = DaymeldFixture.scenario(.standard)
precondition(standard.agents.count >= 6)
precondition(standard.referenceDate == Date(timeIntervalSince1970: 1_788_220_800))
let expectedStatuses = Set(["queued", "running", "blocked", "completed", "failed", "cancelled"])
precondition(Set(standard.agents.map(\.status)) == expectedStatuses)
precondition(standard.tanomiTasks.contains(where: { $0.status == "running" }))
precondition(standard.tanomiTasks.contains(where: { $0.status == "error" }))
precondition(standard.today?.tasks.contains(where: { $0.dueDate == "2026-08-31" }) == true)
precondition(standard.today?.health?.restingHeartRate != nil)
precondition(standard.today?.health?.fatigue != nil)
precondition(standard.emails.contains(where: { $0.importance == "high" }))
if !standard.emails.contains(where: { $0.receivedAt == nil }) {
    fatalError("receivedAt values: \(standard.emails.map { String(describing: $0.receivedAt) })")
}
if !standard.articles.contains(where: { $0.imageURL != nil }) {
    fatalError("imageURL values: \(standard.articles.map { String(describing: $0.imageURL) })")
}
if !standard.articles.contains(where: { $0.imageURL == nil }) {
    fatalError("all imageURL values are present")
}

let empty = DaymeldFixture.scenario(.empty)
if !(empty.agents.isEmpty && empty.tanomiTasks.isEmpty
    && empty.emails.isEmpty && empty.articles.isEmpty) {
    let counts = "empty counts: agents=\(empty.agents.count), tanomi=\(empty.tanomiTasks.count), "
        + "emails=\(empty.emails.count), articles=\(empty.articles.count)"
    fatalError(counts)
}
if !(empty.today?.tasks.isEmpty == true && empty.today?.routines.isEmpty == true) {
    fatalError("empty today is not empty")
}

let partial = DaymeldFixture.scenario(.partialFailure)
if partial.failedResources != Set([.today, .email, .news, .tanomi]) {
    fatalError("failed resources: \(partial.failedResources)")
}
if !(partial.today != nil && !partial.articles.isEmpty) {
    fatalError("partial data was not retained")
}

let stress = DaymeldFixture.scenario(.stress)
if stress.agents.count < 40 || stress.tanomiTasks.count < 25 || stress.articles.count < 100 {
    let counts = "stress counts: agents=\(stress.agents.count), "
        + "tanomi=\(stress.tanomiTasks.count), "
        + "articles=\(stress.articles.count)"
    fatalError(counts)
}
if !stress.tanomiTasks.contains(where: { ($0.result?.count ?? 0) > 4000 }) {
    fatalError("stress tanomi result is not long enough")
}

let inFlight = DaymeldFixture.scenario(.inFlight)
precondition(inFlight.agents.allSatisfy { $0.status == "running" })
precondition(inFlight.tanomiTasks.allSatisfy { $0.status == "running" })

func jsonObject(_ value: some Encodable) -> [String: Any] {
    (try! JSONSerialization.jsonObject(with: JSONEncoder().encode(value))) as! [String: Any]
}
let taskPayload = jsonObject(NewTask(
    title: "fixture task", dueDate: "2026-09-02", priority: 1, recurrence: "daily"
))
precondition(taskPayload["title"] as? String == "fixture task")
precondition(taskPayload["due_date"] as? String == "2026-09-02")
precondition(taskPayload["priority"] as? Int == 1)
precondition(taskPayload["recurrence"] as? String == "daily")
let deletePayload = jsonObject(TaskAction(taskID: "fixture-task"))
precondition(deletePayload["task_id"] as? String == "fixture-task")
let articlePayload = jsonObject(ArticleInteraction(
    articleID: "fixture-article", surface: "article_feed"
))
precondition(articlePayload["article_id"] as? String == "fixture-article")
precondition(articlePayload["surface"] as? String == "article_feed")
let healthPayload = jsonObject(HealthSnapshot(
    date: "2026-09-01", sleepMinutes: nil, steps: nil, restingHeartRate: nil,
    hrvMS: nil, respiratoryRate: nil, fatigue: 4, mood: 2, note: "fixture"
))
precondition(healthPayload["date"] as? String == "2026-09-01")
precondition(healthPayload["fatigue"] as? Int == 4)
precondition(healthPayload["mood"] as? Int == 2)
precondition(healthPayload["note"] as? String == "fixture")
print("Daymeld UX fixture contract passed")
''',
        encoding="utf-8",
    )
    binary = tmp_path / "fixture-contract"
    module_cache = tmp_path / "module-cache"
    module_cache.mkdir()
    environment = os.environ.copy()
    environment["CLANG_MODULE_CACHE_PATH"] = str(module_cache)
    environment["SWIFT_MODULECACHE_PATH"] = str(module_cache)
    compile_result = subprocess.run(
        [
            "xcrun",
            "swiftc",
            "-D",
            "DEBUG",
            str(root / "ios/DailyReader/DailyReader/Models.swift"),
            str(root / "ios/DailyReader/DailyReader/DaymeldFixtures.swift"),
            str(root / "ios/DailyReader/DailyReader/APIClient.swift"),
            str(main),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [str(binary)], capture_output=True, text=True, check=False, env=environment
    )
    assert run_result.returncode == 0, f"{run_result.stderr}\n{run_result.stdout}"
    assert run_result.stdout.strip() == "Daymeld UX fixture contract passed"
