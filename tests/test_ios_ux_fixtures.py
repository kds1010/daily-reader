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
precondition(standard.conversations.count == 5)
let overview = standard.conversations[0]
precondition(overview.summaryText?.contains("説明用") == true)
precondition(overview.currentInsightItems.count == 3)
precondition(Set(overview.currentInsightItems.map(\.status))
    == ["awaiting_review", "kept", "approved"])
precondition(overview.extractionCount("task") == 1 && overview.extractionCount("decision") == 1)
precondition(overview.startLocationContext?.state == "matched_estimate")
precondition(overview.digest?.summary.points.first?.evidence.first?.utteranceID == "fixture-u1")
let originalUtterance = overview.utterances![0]
let correctedItem = overview.correctionItem(for: originalUtterance)!
precondition(correctedItem.acceptedText == "明日までに、資料を送ります。")
precondition(originalUtterance.text == "明日までに資料を送ります。")
precondition(originalUtterance.id == "fixture-u1" && originalUtterance.startSeconds == 12)
precondition(overview.correction?.contexts?.first?.sourceID == "fixture-prior-u1")
let uncertainItem = overview.correctionItem(for: overview.utterances![1])!
precondition(uncertainItem.acceptedText == nil && uncertainItem.proposedText != nil)
precondition(uncertainItem.verificationLabel.contains("原文を維持"))
let manualUtterance = overview.utterances!.first { $0.id == "fixture-u3" }!
precondition(manualUtterance.text == "星身計画の資料です。")
precondition(manualUtterance.userCorrectedText == "星見計画の資料です。")
precondition(manualUtterance.feedbackRevision == 2 && manualUtterance.startSeconds == 23)
precondition(overview.correctionItem(for: manualUtterance) == nil,
    "manual correction is independent from the accepted and uncertain AI examples")
precondition(originalUtterance.userCorrection == nil
    && overview.utterances![1].userCorrection == nil)
let inconsistentItem = ConversationCorrectionItem(utteranceID: "fixture-u1",
    originalText: "原文", proposedText: "候補", correctedText: "候補", status: "accepted",
    reason: "説明用", verification: "uncertain", contextIDs: [])
precondition(inconsistentItem.acceptedText == nil,
    "unverified proposals cannot become display text")
precondition(overview.currentInsightItems[0].evidence[0].quote == originalUtterance.text)
precondition(overview.correctedEvidence("参考補正文", revisionID: "fixture-correction-1")
    == "参考補正文")
precondition(overview.correctedEvidence("誤った版", revisionID: "other-revision") == nil)
let evidenceSnapshot = overview.currentInsightItems[0].evidence[0]
precondition(evidenceSnapshot.correctionIsSnapshot == true)
precondition(overview.correctedEvidence(evidenceSnapshot.correctedQuote,
    revisionID: evidenceSnapshot.correctionRevisionID, isSnapshot: true)
    == "明日までに、資料を送ります。")
let contextSource = overview.correction!.contexts![0]
precondition(standard.conversations.contains(where: { $0.id == contextSource.recordingID
    && $0.utterances?.contains(where: { $0.id == contextSource.sourceID }) == true }))
precondition(overview.correctedEvidence("版なし", revisionID: nil) == nil)
precondition(standard.conversations[3].correction == nil, "older servers remain decodable")
func correctionState(_ status: String) -> ConversationCorrection {
    let object: [String: Any] = ["status": status, "revision_id": "fixture-correction-1",
        "corrected_count": 1, "retained_count": 0, "flagged_count": 0, "context_count": 0,
        "error": NSNull(), "completed_at": NSNull(), "automatic_blocked": status == "failed"]
    return try! JSONDecoder().decode(ConversationCorrection.self,
        from: JSONSerialization.data(withJSONObject: object))
}
for status in ["queued", "correcting", "verifying"] {
    var pending = overview
    pending.correction = correctionState(status)
    precondition(pending.isCorrectionProcessing)
    precondition(pending.correction?.items == nil, "list projection omits full correction items")
}
for status in ["stale", "not_requested", "unknown"] {
    var unavailable = overview
    unavailable.correction = correctionState(status)
    precondition(!unavailable.isCorrectionProcessing)
    precondition(unavailable.correctedEvidence("古い補正文", revisionID: "fixture-correction-1")
        == nil)
    precondition(unavailable.correctionItem(for: originalUtterance) == nil)
    precondition(unavailable.correctedEvidence("保存時の補正文", revisionID: "older",
        isSnapshot: true) == "保存時の補正文", "historical evidence survives current revisions")
}
precondition(correctionState("failed").canDisplayCorrections,
    "a failed refresh can retain the successful revision for the same original")
var changedUtterance = ConversationUtterance(id: originalUtterance.id, speaker: nil,
    startSeconds: 12, endSeconds: 16, text: "別の原文", confidence: nil, context: "", topic: "")
changedUtterance.correction = correctedItem
precondition(overview.correctionItem(for: changedUtterance) == nil,
    "an overlay never applies to different source text")
let sortedConversations = standard.conversations.sorted(by: ConversationRecording.newestFirst)
precondition(sortedConversations.first?.id == "fixture-conversation-3")
precondition(sortedConversations.last?.id == "fixture-conversation-4")
precondition(standard.conversations[1].summaryStateLabel.contains("文字起こし中"))
precondition(standard.conversations[2].summaryStateLabel.contains("失敗"))
precondition(standard.conversations[3].summaryText == nil,
    "legacy topic excerpts must never be presented as summaries")
precondition(ConversationExtractionKind.allCases.count == 9)
precondition(ConversationExtractionKind.allCases.allSatisfy { !$0.explanation.isEmpty })
let summaryWire = #"""
{"status":"ready","source":"codex","text":"前回の要約","points":[],"chunk_count":1,
 "scope":"full_recording","quality_warnings":[],"generated_at":null,
 "generation_status":"analyzing"}
"""#
let generatingSummary = try! JSONDecoder().decode(ConversationSummary.self,
    from: Data(summaryWire.utf8))
precondition(generatingSummary.isGenerating)
func recordingWithSummary(_ status: String, generation: String) -> ConversationRecording {
    let summary: [String: Any] = ["status": status, "source": "codex", "text": "前回の要約",
        "points": [], "chunk_count": 1, "scope": "full_recording", "quality_warnings": [],
        "generated_at": NSNull(), "generation_status": generation]
    let row: [String: Any] = ["id": "test", "filename": "anonymous.ogg", "byte_size": 10,
        "status": "completed", "created_at": "2026-09-01T00:00:00Z",
        "recorded_at": "2026-09-01T00:00:00Z", "recorded_at_verified": 1,
        "digest": ["summary": summary, "counts": [], "preview_items": [],
                   "location_context": NSNull()]]
    return try! JSONDecoder().decode(ConversationRecording.self,
        from: JSONSerialization.data(withJSONObject: row))
}
let updating = recordingWithSummary("ready", generation: "queued")
precondition(updating.isSummaryProcessing && updating.summaryText == "前回の要約")
precondition(updating.previousSummaryLabel?.contains("更新中") == true)
precondition(updating.verifiedDate != nil)
let failedUpdate = recordingWithSummary("ready", generation: "failed")
precondition(failedUpdate.summaryText == "前回の要約"
    && failedUpdate.previousSummaryLabel?.contains("失敗") == true)
precondition(!failedUpdate.isSummaryProcessing)
let staleSummary = recordingWithSummary("stale", generation: "not_requested")
precondition(staleSummary.summaryText == nil,
    "superseded transcript summary is never a current summary")
precondition(standard.agents.count >= 6)
precondition(standard.lifeSnapshot?.secretary?.top_ids.count == 3)
precondition(standard.lifeSnapshot?.secretary?.weekly.browsing_minutes.total == nil)
precondition(standard.referenceDate == Date(timeIntervalSince1970: 1_788_220_800))
let expectedStatuses = Set(["queued", "running", "blocked", "completed", "failed", "cancelled"])
precondition(Set(standard.agents.map(\.status)) == expectedStatuses)
precondition(standard.agents.contains(where: {
    $0.model == "gpt-5.6-sol" && $0.reasoningEffort == "xhigh"
}))
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
precondition(empty.conversations.isEmpty)
if !(empty.agents.isEmpty && empty.tanomiTasks.isEmpty
    && empty.emails.isEmpty && empty.articles.isEmpty) {
    let counts = "empty counts: agents=\(empty.agents.count), tanomi=\(empty.tanomiTasks.count), "
        + "emails=\(empty.emails.count), articles=\(empty.articles.count)"
    fatalError(counts)
}
if !(empty.today?.tasks.isEmpty == true && empty.today?.routines.isEmpty == true) {
    fatalError("empty today is not empty")
}

precondition(empty.lifeSnapshot?.secretary?.items.isEmpty == true)
let partial = DaymeldFixture.scenario(.partialFailure)
precondition(partial.lifeSnapshot?.secretary?.sources.first?.state == "failed")
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

precondition((stress.lifeSnapshot?.secretary?.remaining_count ?? 0) > 3)
let inFlight = DaymeldFixture.scenario(.inFlight)
precondition(inFlight.agents.allSatisfy { $0.status == "running" })
precondition(inFlight.agents.allSatisfy { $0.model != nil && $0.reasoningEffort != nil })
precondition(inFlight.tanomiTasks.allSatisfy { $0.status == "running" })

let notificationJSON = """
{"id":"notification","repository":"daily-reader",
"prompt":"通知","status":"completed","phase":"完了",
"updated_at":"2026-09-01T09:00:00+09:00"}
"""
let notificationData = Data(notificationJSON.utf8)
let notificationJob = try! JSONDecoder().decode(AgentJob.self, from: notificationData)
precondition(notificationJob.model == nil && notificationJob.reasoningEffort == nil)

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
    life_types = tmp_path / "LifeModels.swift"
    source = (root / "ios/DailyReader/DailyReader/LifeAssistant.swift").read_text()
    source = source.split("@MainActor", 1)[0]
    for module in ("SwiftUI", "Combine", "EventKit", "UserNotifications"):
        source = source.replace(f"import {module}\n", "")
    life_types.write_text("import Foundation\n" + source)
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
            str(life_types),
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
