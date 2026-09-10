import Foundation
import SwiftUI

struct ConversationTermDraft {
    var canonical = ""
    var reading = ""
    var aliases = ""
    var enabled = true
    var revision: Int?

    init(term: ConversationVocabularyTerm? = nil) {
        if let term {
            canonical = term.canonical
            reading = term.reading
            aliases = term.aliases.joined(separator: "\n")
            enabled = term.enabled
            revision = term.revision
        }
    }
    var request: ConversationVocabularyRequest {
        ConversationVocabularyRequest(
            revision: revision, canonical: canonical.trimmingCharacters(in: .whitespacesAndNewlines),
            reading: reading.trimmingCharacters(in: .whitespacesAndNewlines),
            aliases: aliases.components(separatedBy: .newlines).map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }.filter { !$0.isEmpty },
            enabled: enabled)
    }
    var validationMessage: String? {
        let value = request
        if value.canonical.isEmpty { return "正しい表記を入力してください。" }
        if value.canonical.unicodeScalars.count > 80 || value.reading.unicodeScalars.count > 80 { return "正しい表記と読みはそれぞれ80文字以内にしてください。" }
        if value.aliases.count > 5 || value.aliases.contains(where: { $0.unicodeScalars.count > 80 }) { return "誤表記は5件まで、各80文字以内にしてください。" }
        return nil
    }
    var isValid: Bool { validationMessage == nil }
}

@MainActor
final class ConversationVocabularyStore: ObservableObject {
    @Published private(set) var terms: [ConversationVocabularyTerm] = []
    @Published private(set) var maxTerms = 100
    @Published private(set) var loaded = false
    @Published private(set) var loading = false
    @Published private(set) var busy = false
    @Published private(set) var error: String?
    @Published private(set) var conflict = false
    let isFixture: Bool
    private let api: APIClient
    private var generation = 0

    init(api: APIClient = .shared, isFixture: Bool = false) {
        self.api = api
        self.isFixture = isFixture
        if isFixture {
            terms = [ConversationVocabularyTerm(id: "example-term", revision: 1, canonical: "星見プロジェクト", reading: "ほしみぷろじぇくと", aliases: ["星身プロジェクト"], enabled: true, created_at: nil, updated_at: nil)]
            loaded = true
        }
    }
    @discardableResult
    func refresh() async -> Bool {
        if isFixture { return true }
        guard !busy else { return false }
        generation += 1
        let token = generation
        loading = true
        defer { if token == generation { loading = false } }
        do {
            let value: ConversationVocabularyEnvelope = try await api.get("api/conversation-vocabulary")
            guard token == generation, !Task.isCancelled else { return false }
            terms = value.terms
            maxTerms = value.max_terms
            loaded = true
            if !conflict { error = nil }
            return true
        } catch {
            if token == generation, !Task.isCancelled { self.error = error.localizedDescription }
            return false
        }
    }
    func save(id: String?, draft: ConversationTermDraft) async -> Bool {
        guard draft.isValid, !busy else { return false }
        busy = true
        generation += 1
        loading = false
        error = nil
        conflict = false
        defer { busy = false }
        do {
            let term: ConversationVocabularyTerm
            if isFixture {
                if let id, terms.first(where: { $0.id == id })?.revision != draft.revision { throw APIClientError.conflict }
                let value = draft.request
                term = ConversationVocabularyTerm(id: id ?? UUID().uuidString, revision: (draft.revision ?? 0) + 1, canonical: value.canonical, reading: value.reading, aliases: value.aliases, enabled: value.enabled, created_at: nil, updated_at: nil)
            } else {
                let path = "api/conversation-vocabulary" + (id.map { "/\($0)" } ?? "")
                let value = try await api.post(path, body: draft.request, as: ConversationVocabularyResponse.self, preservingConflict: true)
                term = value.term
            }
            if let index = terms.firstIndex(where: { $0.id == term.id }) { terms[index] = term }
            else { terms.append(term) }
            loaded = true
            return true
        } catch {
            conflict = (error as? APIClientError).map { if case .conflict = $0 { return true }; return false } ?? false
            self.error = error.localizedDescription
            return false
        }
    }
    func delete(_ term: ConversationVocabularyTerm, revision: Int? = nil) async -> Bool {
        guard !busy else { return false }
        busy = true
        generation += 1
        loading = false
        conflict = false
        error = nil
        defer { busy = false }
        do {
            if isFixture {
                guard terms.first(where: { $0.id == term.id })?.revision == (revision ?? term.revision) else { throw APIClientError.conflict }
            } else {
                let _: EmptyResponse = try await api.post("api/conversation-vocabulary/\(term.id)/delete", body: ConversationVocabularyDelete(revision: revision ?? term.revision), as: EmptyResponse.self, preservingConflict: true)
            }
            terms.removeAll { $0.id == term.id }
            return true
        } catch {
            conflict = (error as? APIClientError).map { if case .conflict = $0 { return true }; return false } ?? false
            self.error = error.localizedDescription
            return false
        }
    }
}

@MainActor
final class ConversationFeedbackEditor: ObservableObject {
    @Published var correctedText: String
    @Published var registerTerm = false
    @Published var term = ConversationTermDraft()
    @Published private(set) var utterance: ConversationUtterance
    @Published private(set) var latest: ConversationUtterance?
    @Published private(set) var error: String?
    @Published private(set) var busy = false
    @Published private(set) var conflict = false
    let recordingID: String
    let isFixture: Bool
    private let api: APIClient

    init(recordingID: String, utterance: ConversationUtterance, api: APIClient = .shared, isFixture: Bool = false) {
        self.recordingID = recordingID
        self.utterance = utterance
        self.correctedText = utterance.userCorrectedText ?? utterance.text
        self.api = api
        self.isFixture = isFixture
    }
    var canSave: Bool {
        !busy && !correctedText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && correctedText.unicodeScalars.count <= 8000 && (!registerTerm || term.isValid)
    }
    func save(reset: Bool = false) async -> ConversationFeedbackResponse? {
        guard !busy, reset || canSave else { return nil }
        busy = true
        error = nil
        conflict = false
        defer { busy = false }
        let request = ConversationFeedbackRequest(expected_original_text: utterance.text,
            expected_feedback_revision: utterance.feedbackRevision,
            corrected_text: reset ? nil : correctedText,
            term: !reset && registerTerm ? term.request : nil, reset: reset ? true : nil)
        do {
            if isFixture {
                let feedback = reset ? nil : ConversationUserCorrection(id: utterance.userCorrection?.id ?? "example-feedback", revision: utterance.feedbackRevision + 1, originalText: utterance.text, correctedText: correctedText)
                return ConversationFeedbackResponse(feedback: feedback, vocabulary_term: nil, feedback_revision: utterance.feedbackRevision + 1)
            }
            return try await api.post("api/conversations/\(recordingID)/utterances/\(utterance.id)/feedback", body: request, as: ConversationFeedbackResponse.self, preservingConflict: true)
        } catch {
            conflict = (error as? APIClientError).map { if case .conflict = $0 { return true }; return false } ?? false
            self.error = error.localizedDescription
            return nil
        }
    }
    func loadLatest() async {
        guard !isFixture, !busy else { return }
        latest = nil
        busy = true
        defer { busy = false }
        do {
            let recording: ConversationRecording = try await api.get("api/conversations/\(recordingID)")
            latest = recording.utterances?.first { $0.id == utterance.id }
            if latest == nil { error = "再解析などにより元の発言が見つかりません。会話へ戻って現在の文字起こしを確認してください。" }
        } catch { self.error = error.localizedDescription }
    }
    func editLatest() {
        guard let latest, !busy else { return }
        utterance = latest
        correctedText = latest.userCorrectedText ?? latest.text
        self.latest = nil
        conflict = false
        error = nil
    }
}

// MARK: - Views
private struct ConversationVocabularyExplanation: View {
    var body: some View {
        Text("有効な用語は次回以降の文字起こしと関連する文脈補正の参考に使います。音声認識はMac内で処理します。文脈補正では関連する登録語もCodexへ渡します。登録操作だけではCodexへ送りません。登録だけで既存の録音を再解析したり、発言を一括置換したりはしません。認識処理の長さ制限により、すべての登録語が毎回使われるとは限りません。")
            .appFont(.caption).foregroundStyle(.secondary)
    }
}

struct ConversationVocabularyView: View {
    @StateObject private var store: ConversationVocabularyStore
    @State private var editing: ConversationVocabularyTerm?
    @State private var adding = false
    init(api: APIClient = .shared, isFixture: Bool = false) {
        _store = StateObject(wrappedValue: ConversationVocabularyStore(api: api, isFixture: isFixture))
    }
    var body: some View {
        List {
            Section {
                ConversationVocabularyExplanation()
                if store.isFixture { Text("説明用データです。操作はこの画面の中だけで反映します。").foregroundStyle(.secondary) }
            }
            if let error = store.error { Section { Text(error).foregroundStyle(.orange); Button("再読み込み") { Task { await store.refresh() } } } }
            Section("登録語（\(store.terms.count)/\(store.maxTerms)）") {
                if store.loading { ProgressView() }
                if store.loaded && store.terms.isEmpty { Text("登録された用語はありません。人名や製品名など、誤認識しやすい表記を追加できます。").foregroundStyle(.secondary) }
                ForEach(store.terms) { term in
                    Button { editing = term } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            HStack { Text(verbatim: term.canonical).foregroundStyle(.primary); if !term.enabled { Text("無効").appFont(.caption).foregroundStyle(.secondary) } }
                            if !term.reading.isEmpty { Text(verbatim: term.reading).appFont(.caption).foregroundStyle(.secondary) }
                            if !term.aliases.isEmpty { Text("確認済みの誤表記: \(term.aliases.joined(separator: "、"))").appFont(.caption).foregroundStyle(.secondary) }
                        }
                    }.buttonStyle(.plain)
                }
            }
        }
        .navigationTitle("文字起こしの用語辞書")
        .toolbar { Button("追加", systemImage: "plus") { adding = true }.disabled(store.busy || !store.loaded || store.terms.count >= store.maxTerms) }
        .task { await store.refresh() }
        .refreshable { await store.refresh() }
        .sheet(isPresented: $adding) { ConversationTermEditor(store: store, term: nil) }
        .sheet(item: $editing) { term in ConversationTermEditor(store: store, term: term) }
    }
}

private struct ConversationTermFields: View {
    @Binding var draft: ConversationTermDraft
    var body: some View {
        TextField("正しい表記（必須）", text: $draft.canonical)
            .autocorrectionDisabled()
        TextField("読み（任意）", text: $draft.reading)
            .autocorrectionDisabled()
        Text("確認済みの誤表記（任意・1行に1つ）").appFont(.caption).foregroundStyle(.secondary)
        TextEditor(text: $draft.aliases).frame(minHeight: 70)
            .autocorrectionDisabled()
        Text("実際に誤認識された表記だけを入力してください。例: 正しい表記「星見プロジェクト」、誤表記「星身プロジェクト」。")
            .appFont(.caption).foregroundStyle(.secondary)
        Toggle("今後の認識・補正で使う", isOn: $draft.enabled)
        Text("正しい表記・読みは各80文字、誤表記は5件まで・各80文字です。")
            .appFont(.caption).foregroundStyle(.secondary)
        if let message = draft.validationMessage { Text(message).appFont(.caption).foregroundStyle(.orange) }
    }
}

private struct ConversationTermEditor: View {
    @ObservedObject var store: ConversationVocabularyStore
    let term: ConversationVocabularyTerm?
    @State private var draft: ConversationTermDraft
    @State private var confirmsDelete = false
    @State private var showsLatest = false
    @Environment(\.dismiss) private var dismiss
    init(store: ConversationVocabularyStore, term: ConversationVocabularyTerm?) {
        self.store = store
        self.term = term
        _draft = State(initialValue: ConversationTermDraft(term: term))
    }
    var body: some View {
        NavigationStack {
            Form {
                Section { ConversationTermFields(draft: $draft) }.disabled(store.busy)
                Section { ConversationVocabularyExplanation() }
                if let error = store.error {
                    Section {
                        Text(error).foregroundStyle(.orange)
                        if store.conflict {
                            Button("最新の保存内容を確認") { Task { showsLatest = false; showsLatest = await store.refresh() } }
                            if showsLatest, let id = term?.id {
                                if let latest = store.terms.first(where: { $0.id == id }) {
                                    Text("現在の表記: \(latest.canonical)")
                                    Button("最新の内容から編集し直す") { draft = ConversationTermDraft(term: latest); showsLatest = false }
                                } else { Text("この用語は削除されています。一覧から追加し直してください。") }
                            }
                        }
                    }
                }
                Section {
                    Button(store.busy ? "保存中…" : "保存") { Task { if await store.save(id: term?.id, draft: draft) { dismiss() } } }
                        .disabled(store.busy || !draft.isValid)
                    if term != nil { Button("用語を削除", role: .destructive) { confirmsDelete = true }.disabled(store.busy) }
                }
            }
            .formStyle(.grouped)
            .navigationTitle(term == nil ? "用語を追加" : "用語を編集")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("閉じる") { dismiss() }.disabled(store.busy) } }
            .confirmationDialog("この用語を削除しますか？", isPresented: $confirmsDelete, titleVisibility: .visible) {
                Button("削除", role: .destructive) { Task { if let term, await store.delete(term, revision: draft.revision) { dismiss() } } }
            } message: { Text("保存済みの発言訂正は残ります。") }
        }
        .interactiveDismissDisabled(store.busy)
        #if os(macOS)
        .frame(minWidth: 480, minHeight: 580)
        #endif
    }
}

struct ConversationFeedbackView: View {
    @StateObject var editor: ConversationFeedbackEditor
    let onSaved: (ConversationFeedbackResponse) async -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var confirmsReset = false
    @State private var completing = false
    var body: some View {
        NavigationStack {
            Form {
                Section("元の文字起こし（保持します）") { Text(verbatim: editor.utterance.text).textSelection(.enabled) }
                Section("訂正後の発言") {
                    TextEditor(text: $editor.correctedText).frame(minHeight: 120).disabled(editor.busy)
                    Text("訂正文は8000文字以内です。現在 \(editor.correctedText.unicodeScalars.count)文字")
                        .appFont(.caption).foregroundStyle(editor.correctedText.unicodeScalars.count > 8000 ? .orange : .secondary)
                    Text("この発言に対する訂正として保存します。発言の時刻・話者・GPSと元の文字起こしは変わりません。保存済みの要約や抽出候補も自動では書き換えません。")
                        .appFont(.caption).foregroundStyle(.secondary)
                }
                Section {
                    Toggle("今後も使う用語として登録", isOn: $editor.registerTerm).disabled(editor.busy)
                    if editor.registerTerm {
                        ConversationTermFields(draft: $editor.term).disabled(editor.busy)
                        Text("発言全文ではなく、覚えておきたい用語だけを別に入力してください。")
                            .appFont(.caption).foregroundStyle(.secondary)
                        ConversationVocabularyExplanation()
                    }
                }
                if editor.isFixture { Text("説明用です。Mac miniへは送信しません。").foregroundStyle(.secondary) }
                if let error = editor.error {
                    Section {
                        Text(error).foregroundStyle(.orange)
                        if editor.conflict {
                            Button("最新の保存内容を確認") { Task { await editor.loadLatest() } }.disabled(editor.busy)
                            if let latest = editor.latest {
                                Text("現在の原文: \(latest.text)")
                                Text("現在の訂正: \(latest.userCorrectedText ?? "訂正なし")")
                                Button("最新の内容から編集し直す") { editor.editLatest() }
                            }
                        }
                    }
                }
                Section {
                    Button(editor.busy ? "保存中…" : "訂正を保存") { Task { await save() } }.disabled(!editor.canSave)
                    if editor.utterance.userCorrection != nil {
                        Button("本人の訂正を取り消す", role: .destructive) { confirmsReset = true }.disabled(editor.busy)
                    }
                }
            }
            .formStyle(.grouped)
            .disabled(completing)
            .navigationTitle("発言を訂正")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("閉じる") { dismiss() }.disabled(editor.busy || completing) } }
            .confirmationDialog("本人の訂正を取り消しますか？", isPresented: $confirmsReset, titleVisibility: .visible) {
                Button("訂正を取り消す", role: .destructive) { Task { await save(reset: true) } }
            } message: { Text("元の文字起こしと登録した用語は残ります。") }
        }
        .interactiveDismissDisabled(editor.busy || completing)
        #if os(macOS)
        .frame(minWidth: 500, minHeight: 620)
        #endif
    }
    private func save(reset: Bool = false) async {
        guard !completing else { return }
        completing = true
        defer { completing = false }
        if let response = await editor.save(reset: reset) {
            await onSaved(response)
            dismiss()
        }
    }
}
