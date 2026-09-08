import Foundation
import AppIntents

@main struct SoundcoreImportHarness {
    @MainActor static func main() async throws {
        let server = URL(string: CommandLine.arguments[1])!
        let client = APIClient(serverURL: server)
        let imports = SoundcoreImports(api: client)
        let saved = try JSONDecoder().decode(SoundcoreImportJob.self, from: Data(#"{"id":"saved-job","status":"saved","recording_id":"recording-saved","error":null}"#.utf8))
        precondition(saved.isPending && saved.statusLabel == "音声を保存済み・解析の受付待ち")
        let canonical = "https://speaker-eu.eufylife.com/knowledge/sharelink/ABC12345"
        let decorated = "  \(canonical)?language=ja#recording  \n"
        precondition(try! normalizedSoundcoreShareURL(decorated) == canonical)
        for invalid in [
            "http://speaker-eu.eufylife.com/knowledge/sharelink/ABC12345",
            "https://speaker-us.eufylife.com/knowledge/sharelink/ABC12345",
            "https://speaker-eu.eufylife.com.evil.test/knowledge/sharelink/ABC12345",
            "https://speaker-eu.eufylife.com:8443/knowledge/sharelink/ABC12345",
            "https://speaker-eu.eufylife.com:/knowledge/sharelink/ABC12345",
            "https://speaker-eu.eufylife.com./knowledge/sharelink/ABC12345",
            "https://user:secret@speaker-eu.eufylife.com/knowledge/sharelink/ABC12345",
            "https://speaker-eu.eufylife.com/knowledge/sharelink/ABC12345?next=https://example.test",
            "https://speaker-eu.eufylife.com/knowledge/sharelink/ABC12345/audio",
            "https://speaker-eu.eufylife.com/knowledge/sharelink/%41BC12345",
            "https://speaker-eu.eufylife.com/knowledge/sharelink/short",
            "https://speaker-eu.eufylife.com/knowledge/sharelink/" + String(repeating: "a", count: 65),
            "file:///private/recording.mp3", "", "not a URL"
        ] {
            imports.draft = invalid
            let accepted = await imports.submit()
            precondition(!accepted && imports.draft == invalid && imports.jobs.isEmpty)
        }
        await imports.refresh()
        precondition(imports.loadState == .loaded && !imports.hasPendingJobs)

        // HTTP failure retains exactly the user's input, including presentation
        // query/fragment; the retry sends only the canonical share URL in JSON.
        imports.draft = decorated
        let failed = await imports.submit()
        precondition(!failed && imports.draft == decorated && imports.submissionError != nil)
        let accepted = await imports.submit()
        precondition(accepted && imports.draft.isEmpty && imports.jobs.count == 1)
        precondition(imports.jobs[0].status == "queued" && imports.hasPendingJobs)
        precondition(imports.acceptedMessage!.contains("受け付け"))
        precondition(imports.jobs[0].statusLabel != "解析済み")

        // A GET already in flight cannot remove a new job accepted by the real
        // AppIntent. It uses the same client/validator/store path as the view.
        async let staleRead: Void = imports.refresh()
        try await Task.sleep(for: .milliseconds(30))
        var intent = ImportSoundcoreLinkIntent()
        intent.url = URL(string: "https://speaker-eu.eufylife.com/knowledge/sharelink/DEF67890?language=en")!
        let intentJob = try await intent.acceptLink(into: imports)
        await staleRead
        precondition(intentJob.id == "job-2" && imports.jobs.count == 2)
        precondition(imports.jobs.contains { $0.id == "job-2" })

        // Mac persistence is authoritative: a fresh client restores jobs from
        // GET, without persisting a local queue or repeating POSTs.
        let restored = SoundcoreImports(api: client)
        await restored.refresh()
        precondition(restored.jobs.count == 2 && restored.hasPendingJobs)
        precondition(restored.jobs.first { $0.id == "job-2" }?.status == "downloading")
        await restored.refresh()
        precondition(!restored.hasPendingJobs)
        let failedJob = restored.jobs.first { $0.id == "job-1" }!
        precondition(failedJob.status == "failed" && failedJob.error != nil)
        let completed = restored.jobs.first { $0.id == "job-2" }!
        precondition(completed.status == "completed" && completed.recordingID == "recording-2")
        precondition(completed.statusLabel == "音声の取り込み済み")

        await restored.retry(failedJob)
        precondition(restored.retryErrors[failedJob.id] != nil)
        precondition(restored.jobs.first { $0.id == failedJob.id }?.status == "failed")
        // Concurrent taps are coalesced while the retry request is in flight.
        async let retryOne: Void = restored.retry(failedJob)
        async let retryTwo: Void = restored.retry(failedJob)
        _ = await (retryOne, retryTwo)
        precondition(restored.retryErrors[failedJob.id] == nil && restored.hasPendingJobs)
        await restored.refresh()
        precondition(!restored.hasPendingJobs)
        precondition(restored.jobs.allSatisfy { $0.status == "completed" })
        await restored.refresh() // Simulated status-read outage retains history.
        precondition(restored.jobs.count == 2)
        if case .failed = restored.loadState {} else { fatalError("missing read failure") }
        print("PASS: URL validation before HTTP, canonical POST, failed-input retention, real URL AppIntent, stale-read protection, persisted-job restore, pending/completed/failed states, coalesced retry, read-failure retention")
    }
}
