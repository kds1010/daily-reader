import Foundation

private struct Stub {
    var status = 200
    var body: String
    var delay: Double = 0
}
private final class MockProtocol: URLProtocol {
    static let lock = NSLock()
    static var replies: [Stub] = []
    static var requests: [(String, [String: Any])] = []
    static func enqueue(_ body: String, status: Int = 200, delay: Double = 0) {
        lock.lock(); defer { lock.unlock() }
        replies.append(Stub(status: status, body: body, delay: delay))
    }
    static var count: Int { lock.lock(); defer { lock.unlock() }; return requests.count }
    static var last: (String, [String: Any]) { lock.lock(); defer { lock.unlock() }; return requests.last! }
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    private let stateLock = NSLock()
    private var stopped = false
    override func startLoading() {
        var body = request.httpBody ?? Data()
        if let stream = request.httpBodyStream {
            stream.open(); defer { stream.close() }
            var buffer = [UInt8](repeating: 0, count: 4096)
            while stream.hasBytesAvailable {
                let count = stream.read(&buffer, maxLength: buffer.count)
                if count <= 0 { break }
                body.append(buffer, count: count)
            }
        }
        let json = (try? JSONSerialization.jsonObject(with: body)) as? [String: Any] ?? [:]
        Self.lock.lock()
        Self.requests.append((request.url!.path, json))
        precondition(!Self.replies.isEmpty, "unexpected HTTP, including fixture side effect")
        let stub = Self.replies.removeFirst()
        Self.lock.unlock()
        DispatchQueue.global().asyncAfter(deadline: .now() + stub.delay) {
            self.stateLock.lock(); defer { self.stateLock.unlock() }
            guard !self.stopped else { return }
            self.client?.urlProtocol(self, didReceive: HTTPURLResponse(url: self.request.url!, statusCode: stub.status, httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
            self.client?.urlProtocol(self, didLoad: Data(stub.body.utf8))
            self.client?.urlProtocolDidFinishLoading(self)
        }
    }
    override func stopLoading() { stateLock.lock(); stopped = true; stateLock.unlock() }
}
@main struct ConversationVocabularyHarness {
    static let term = #"{"id":"t1","revision":1,"canonical":"星見計画","reading":"ほしみけいかく","aliases":["星身計画"],"enabled":true}"#
    static let original = #"{"id":"u1","speaker":"話者1","start_seconds":12.5,"end_seconds":15.2,"text":"星身計画を確認します。","context":"説明用","topic":"計画"}"#
    static let corrected = #"{"id":"f1","revision":3,"original_text":"星身計画を確認します。","corrected_text":"星見計画を確認します。"}"#
    @MainActor static func main() async throws {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [MockProtocol.self]
        let api = APIClient(session: URLSession(configuration: configuration), serverURL: URL(string: "https://example.invalid/")!)
        let decoder = JSONDecoder()
        let oldMetadata = try decoder.decode(ConversationTranscriptionMetadata.self, from: Data(#"{"model":"example"}"#.utf8))
        precondition(oldMetadata.vocabularyRevision == nil && oldMetadata.vocabularyUsageLabel.contains("未記録"))
        let hintMetadata = try decoder.decode(ConversationTranscriptionMetadata.self, from: Data(#"{"vocabulary_revision":2,"vocabulary_term_ids":["t1"],"vocabulary_omitted_count":3,"hotword_tokens":100,"hotword_token_limit":160}"#.utf8))
        precondition(hintMetadata.vocabularyUsageLabel == "用語ヒント 1件" && hintMetadata.vocabularyOmittedCount == 3 && hintMetadata.hotwordTokenLimit == 160)
        let zeroMetadata = try decoder.decode(ConversationTranscriptionMetadata.self, from: Data(#"{"vocabulary_revision":0,"vocabulary_term_ids":[],"vocabulary_omitted_count":0}"#.utf8))
        precondition(zeroMetadata.vocabularyUsageLabel == "用語ヒント 0件")
        let old = try decoder.decode(ConversationUtterance.self, from: Data(original.utf8))
        precondition(old.userCorrection == nil && old.feedbackRevision == 0)
        let tombstone = try decoder.decode(ConversationUtterance.self, from: Data(original.dropLast().appending(",\"user_correction_revision\":2,\"user_correction\":null}").utf8))
        precondition(tombstone.feedbackRevision == 2 && tombstone.userCorrectedText == nil)
        var row = tombstone
        row.userCorrection = try decoder.decode(ConversationUserCorrection.self, from: Data(corrected.utf8))
        row.userCorrectionRevision = 3
        precondition(row.userCorrectedText == "星見計画を確認します。")
        row.userCorrection = ConversationUserCorrection(id: "wrong", revision: 9, originalText: "別の発言", correctedText: "適用禁止")
        precondition(row.userCorrectedText == nil)
        precondition(row.text == old.text && row.startSeconds == 12.5 && row.endSeconds == 15.2 && row.id == old.id)

        var draft = ConversationTermDraft()
        precondition(!draft.isValid)
        draft.canonical = String(repeating: "語", count: 81)
        precondition(!draft.isValid)
        draft.canonical = "星見計画"
        draft.reading = "ほしみけいかく"
        draft.aliases = "星身計画\n\n 星み計画 "
        precondition(draft.request.aliases == ["星身計画", "星み計画"])
        draft.aliases = Array(repeating: "誤表記", count: 6).joined(separator: "\n")
        precondition(!draft.isValid)
        draft.aliases = "星身計画"
        let store = ConversationVocabularyStore(api: api)
        MockProtocol.enqueue("{\"revision\":1,\"terms\":[\(term)],\"max_terms\":100}")
        await store.refresh()
        precondition(store.loaded && store.terms.count == 1)
        MockProtocol.enqueue(#"{"error":"匿名の通信失敗"}"#, status: 503)
        await store.refresh()
        precondition(store.terms.count == 1 && store.error != nil)
        MockProtocol.enqueue(#"{"error":"匿名の保存失敗"}"#, status: 503)
        let failed = await store.save(id: nil, draft: draft)
        precondition(!failed && draft.canonical == "星見計画" && draft.aliases == "星身計画")
        precondition(MockProtocol.last.0 == "/api/conversation-vocabulary")
        precondition(MockProtocol.last.1["revision"] == nil)
        var edit = ConversationTermDraft(term: store.terms[0])
        edit.canonical = "星見計画・新版"
        MockProtocol.enqueue(#"{"error":"更新済み"}"#, status: 409)
        let conflicted = await store.save(id: "t1", draft: edit)
        precondition(!conflicted && store.conflict && edit.canonical == "星見計画・新版")
        MockProtocol.enqueue(#"{"error":"匿名の再取得失敗"}"#, status: 503)
        let failedLatest = await store.refresh()
        precondition(!failedLatest && edit.canonical == "星見計画・新版" && store.terms[0].revision == 1)
        MockProtocol.enqueue("{\"revision\":1,\"terms\":[\(term)],\"max_terms\":100}")
        await store.refresh()
        precondition(store.conflict && store.error != nil && edit.canonical == "星見計画・新版")
        // A delayed pre-mutation snapshot must not undo the successful save.
        MockProtocol.enqueue("{\"revision\":1,\"terms\":[\(term)],\"max_terms\":100}", delay: 0.15)
        async let stale: Bool = store.refresh()
        try await Task.sleep(for: .milliseconds(25))
        let term2 = term.replacingOccurrences(of: "\"revision\":1", with: "\"revision\":2").replacingOccurrences(of: "星見計画", with: "星見計画・新版")
        MockProtocol.enqueue("{\"term\":\(term2)}")
        let saved = await store.save(id: "t1", draft: edit)
        _ = await stale
        precondition(saved && store.terms[0].revision == 2 && store.terms[0].canonical == edit.canonical)
        MockProtocol.enqueue(#"{"error":"更新済み"}"#, status: 409)
        let notDeleted = await store.delete(store.terms[0])
        precondition(!notDeleted && store.terms.count == 1 && store.conflict)
        MockProtocol.enqueue(#"{"deleted":true}"#)
        let deleted = await store.delete(store.terms[0])
        precondition(deleted && store.terms.isEmpty)
        precondition(MockProtocol.last.0 == "/api/conversation-vocabulary/t1/delete")
        precondition(MockProtocol.last.1["revision"] as? Int == 2)

        let editor = ConversationFeedbackEditor(recordingID: "r1", utterance: tombstone, api: api)
        precondition(editor.correctedText == old.text && editor.term.canonical.isEmpty)
        editor.correctedText = "星見計画を確認します。"
        editor.registerTerm = true
        precondition(!editor.canSave)
        editor.term = draft
        MockProtocol.enqueue(#"{"error":"更新済み"}"#, status: 409)
        let feedbackConflict = await editor.save()
        precondition(feedbackConflict == nil && editor.conflict && editor.correctedText == "星見計画を確認します。")
        let feedbackRequest = MockProtocol.last.1
        precondition(feedbackRequest["expected_feedback_revision"] as? Int == 2)
        precondition(feedbackRequest["expected_original_text"] as? String == old.text)
        precondition((feedbackRequest["term"] as? [String: Any])?["canonical"] as? String == "星見計画")
        precondition((feedbackRequest["term"] as? [String: Any])?["revision"] == nil)
        MockProtocol.enqueue(#"{"error":"登録済み","code":"duplicate_term"}"#, status: 409)
        let duplicate = await editor.save()
        precondition(duplicate == nil && !editor.conflict && editor.error!.contains("チェックを外して") && editor.correctedText == "星見計画を確認します。")
        MockProtocol.enqueue(#"{"error":"処理中","code":"recording_busy"}"#, status: 409)
        let processing = await editor.save()
        precondition(processing == nil && !editor.conflict && editor.error!.contains("処理中") && editor.correctedText == "星見計画を確認します。")
        MockProtocol.enqueue("{\"feedback\":\(corrected),\"feedback_revision\":3,\"vocabulary_term\":\(term)}")
        let feedback = await editor.save()
        precondition(feedback?.feedback?.correctedText == editor.correctedText && feedback?.feedback_revision == 3)
        let current = ConversationFeedbackEditor(recordingID: "r1", utterance: row, api: api)
        MockProtocol.enqueue(#"{"feedback":null,"feedback_revision":4,"vocabulary_term":null}"#)
        let reset = await current.save(reset: true)
        precondition(reset?.feedback == nil && reset?.feedback_revision == 4)
        precondition(MockProtocol.last.1["reset"] as? Bool == true)
        precondition(MockProtocol.last.1["corrected_text"] == nil && MockProtocol.last.1["term"] == nil)
        current.correctedText = String(repeating: "字", count: 8001)
        precondition(!current.canSave)

        // Cancelling an in-flight request retains the draft and releases busy state.
        let cancelledEditor = ConversationFeedbackEditor(recordingID: "r1", utterance: old, api: api)
        cancelledEditor.correctedText = "本人の入力を保持"
        MockProtocol.enqueue("{\"feedback\":null,\"feedback_revision\":1,\"vocabulary_term\":null}", delay: 0.2)
        let cancelled = Task { await cancelledEditor.save() }
        try await Task.sleep(for: .milliseconds(25))
        cancelled.cancel()
        let cancelledResult = await cancelled.value
        precondition(cancelledResult == nil && !cancelledEditor.busy && cancelledEditor.correctedText == "本人の入力を保持")
        let reloadEditor = ConversationFeedbackEditor(recordingID: "r1", utterance: old, api: api)
        reloadEditor.correctedText = "まだ保存していない入力"
        MockProtocol.enqueue(#"{"error":"更新済み"}"#, status: 409)
        let reloadConflict = await reloadEditor.save()
        precondition(reloadConflict == nil && reloadEditor.conflict)
        let latestRow = original.dropLast().appending(",\"user_correction_revision\":3,\"user_correction\":\(corrected)}")
        MockProtocol.enqueue("{\"id\":\"r1\",\"filename\":\"example.txt\",\"byte_size\":100,\"status\":\"completed\",\"created_at\":\"2026-09-01T00:00:00Z\",\"utterances\":[\(latestRow)]}")
        await reloadEditor.loadLatest()
        precondition(reloadEditor.latest?.feedbackRevision == 3 && reloadEditor.correctedText == "まだ保存していない入力")
        MockProtocol.enqueue(#"{"error":"匿名の再取得失敗"}"#, status: 503)
        await reloadEditor.loadLatest()
        precondition(reloadEditor.latest == nil && reloadEditor.correctedText == "まだ保存していない入力")
        MockProtocol.enqueue("{\"id\":\"r1\",\"filename\":\"example.txt\",\"byte_size\":100,\"status\":\"completed\",\"created_at\":\"2026-09-01T00:00:00Z\",\"utterances\":[\(latestRow)]}")
        await reloadEditor.loadLatest()
        reloadEditor.editLatest()
        precondition(reloadEditor.utterance.feedbackRevision == 3 && reloadEditor.correctedText == "星見計画を確認します。" && !reloadEditor.conflict)

        let beforeFixture = MockProtocol.count
        let fixture = ConversationVocabularyStore(api: api, isFixture: true)
        await fixture.refresh()
        let fixtureSaved = await fixture.save(id: nil, draft: draft)
        precondition(fixtureSaved && fixture.terms.count == 2)
        let fixtureDeleted = await fixture.delete(fixture.terms[0])
        precondition(fixtureDeleted && fixture.terms.count == 1)
        let fixtureEditor = ConversationFeedbackEditor(recordingID: "r1", utterance: old, api: api, isFixture: true)
        fixtureEditor.correctedText = "説明用の訂正"
        let fixtureFeedback = await fixtureEditor.save()
        precondition(fixtureFeedback != nil && MockProtocol.count == beforeFixture)
        print("PASS vocabulary and feedback: compatibility, original/time preservation, tombstone concurrency, bounded input, POST shapes, failure/cancellation retention, explicit conflict recovery, stale GET, delete, isolated fixtures")
    }
}
