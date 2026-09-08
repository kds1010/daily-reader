import Foundation

struct DiarySource: Codable, Identifiable {
    let kind: String
    let id: String
    let title: String
    let at: String?
    let detail: String
    var identity: String { "\(kind):\(id)" }
}
struct DiarySources: Codable {
    let items: [DiarySource]
    let warnings: [String]
}
struct DiaryEntry: Codable {
    let date: String
    let timezone: String
    var generated_body: String
    var edited_body: String?
    var body: String
    var source_snapshot: DiarySources
    var edited_sources: DiarySources?
    var revision: Int
    let generated_at: String
    var updated_at: String
    var deleted: Bool
}
struct DiaryEnvelope: Codable {
    let date: String
    let timezone: String
    var state: String
    var entry: DiaryEntry?
    var history: [DiaryEntry]
    var generation_error: String? = nil
}
struct DiarySettings: Codable {
    var enabled: Bool
    let since: String
    let timezone: String
}
struct DiaryRequest: Codable {
    let date: String
    let revision: Int
    var body: String? = nil
}

// MARK: - Native state
import SwiftUI
import Combine

@MainActor
final class DiaryStore: ObservableObject {
    @Published var selectedDate = Date.now
    @Published private(set) var snapshot: DiaryEnvelope?
    @Published private(set) var policy: DiarySettings?
    @Published private(set) var busy = false
    @Published private(set) var error: String?
    @Published private var edits: [String: Edit] = [:]
    private struct Edit { var text: String; let revision: Int }
    private var fixtureScenario: String?
    private var fixtureEntries: [String: DiaryEnvelope] = [:]
    private let api: APIClient
    init(api: APIClient = .shared) { self.api = api }
    private var loadID = 0
    static let zone = TimeZone(identifier: "Asia/Tokyo")!
    static func dayKey(_ date: Date) -> String {
        let format = DateFormatter()
        format.calendar = Calendar(identifier: .gregorian)
        format.locale = Locale(identifier: "en_US_POSIX")
        format.timeZone = zone
        format.dateFormat = "yyyy-MM-dd"
        return format.string(from: date)
    }
    var day: String { Self.dayKey(selectedDate) }
    var current: DiaryEnvelope? { snapshot?.date == day ? snapshot : nil }
    var hasEdits: Bool { edits[day] != nil }
    var conflict: Bool {
        guard let edit = edits[day], let current else { return false }
        return edit.revision != (current.entry?.revision ?? 0)
    }
    var text: String {
        get { edits[day]?.text ?? current?.entry?.body ?? "" }
        set { edits[day] = Edit(text: newValue, revision: edits[day]?.revision ?? current?.entry?.revision ?? 0) }
    }

    func configureFixture(_ scenario: String) {
        guard fixtureScenario == nil else { return }
        fixtureScenario = scenario
        policy = DiarySettings(enabled: true, since: "2026-09-01T00:00:00Z", timezone: "Asia/Tokyo")
    }

    func refresh() async {
        guard !busy else { return }
        loadID += 1
        let token = loadID, key = day
        do {
            let envelope: DiaryEnvelope
            let settings: DiarySettings
            if let scenario = fixtureScenario {
                if scenario == "partial-failure" { throw APIClientError.server("fixture: 日記を取得できませんでした") }
                envelope = fixtureEntries[key] ?? fixture(key, scenario: scenario)
                settings = policy ?? DiarySettings(enabled: true, since: "2026-09-01T00:00:00Z", timezone: "Asia/Tokyo")
            } else {
                envelope = try await api.get("api/diary", queryItems: [.init(name: "date", value: key)])
                settings = try await api.get("api/diary/settings")
            }
            guard token == loadID, key == day else { return }
            snapshot = envelope
            policy = settings
            error = nil
        } catch {
            if token == loadID, key == day { self.error = error.localizedDescription }
        }
    }

    func generate() async {
        await mutate("generate", request: DiaryRequest(date: day, revision: current?.entry?.revision ?? 0))
    }
    func save(confirmedRevision: Int? = nil) async {
        let revision = confirmedRevision ?? edits[day]?.revision ?? current?.entry?.revision ?? 0
        await mutate("save", request: DiaryRequest(date: day, revision: revision, body: text))
    }
    func delete(confirmedRevision: Int? = nil) async {
        await mutate("delete", request: DiaryRequest(date: day, revision: confirmedRevision ?? current?.entry?.revision ?? 0))
    }
    func discardInput() { edits[day] = nil }

    private func mutate(_ action: String, request: DiaryRequest) async {
        guard !busy else { return }
        busy = true
        loadID += 1 // Ignore any reads started before this mutation.
        do {
            let result: DiaryEnvelope
            if fixtureScenario != nil {
                var envelope = fixtureEntries[request.date] ?? fixture(request.date, scenario: fixtureScenario!)
                if action == "generate" && envelope.state != "ready" {
                    envelope = fixture(request.date, scenario: "standard")
                } else if action == "delete" {
                    envelope.state = "deleted"
                    envelope.entry?.deleted = true
                    envelope.entry?.body = ""
                    envelope.entry?.generated_body = ""
                    envelope.entry?.edited_body = nil
                    envelope.entry?.source_snapshot = DiarySources(items: [], warnings: [])
                    envelope.entry?.edited_sources = nil
                    envelope.history = []
                } else if action == "save" {
                    if fixtureScenario == "in-flight", fixtureEntries[request.date] == nil {
                        envelope.entry?.revision += 1
                        fixtureEntries[request.date] = envelope
                    }
                    guard request.revision == envelope.entry?.revision else {
                        throw APIClientError.server("日記が更新されています。最新内容を確認してください。")
                    }
                    envelope.entry?.body = request.body ?? ""
                    envelope.entry?.edited_body = request.body
                    let sources = envelope.entry?.source_snapshot
                    envelope.entry?.edited_sources = sources
                }
                envelope.entry?.revision += 1
                fixtureEntries[request.date] = envelope
                result = envelope
            } else {
                result = try await api.post("api/diary/\(action)", body: request, as: DiaryEnvelope.self)
            }
            if action == "save" || action == "delete" { edits[request.date] = nil }
            if day == request.date { snapshot = result }
            error = nil
            busy = false
        } catch {
            busy = false
            let message = error.localizedDescription
            let recoveryID = loadID + 1
            await refresh() // Keep the edit buffer and load the revision needed to resolve a conflict.
            if loadID == recoveryID { self.error = message }
        }
    }

    func setAutomatic(_ enabled: Bool) async {
        guard !busy else { return }
        busy = true
        loadID += 1
        defer { busy = false }
        do {
            if fixtureScenario != nil { policy?.enabled = enabled }
            else { policy = try await api.post("api/diary/settings", body: ["enabled": enabled], as: DiarySettings.self) }
            error = nil
        } catch { self.error = error.localizedDescription }
    }

    private func fixture(_ day: String, scenario: String) -> DiaryEnvelope {
        if scenario == "empty" { return DiaryEnvelope(date: day, timezone: "Asia/Tokyo", state: "missing", entry: nil, history: []) }
        let sources = DiarySources(items: [DiarySource(kind: "task_completed", id: "fixture-task", title: "本を返す", at: "2026-09-01T09:00:00+09:00", detail: "完了と記録されています。"), DiarySource(kind: "planned", id: "fixture-event", title: "読書会", at: nil, detail: "予定の記録です。参加実績は未確認です。")], warnings: ["カレンダーの取得範囲がこの日の全体をカバーしていません。"])
        let body = scenario == "stress" ? String(repeating: "会話の記録を振り返り、本人の気持ちを追記できます。\n", count: 160) : "完了と記録したタスク：\n・本を返す\n\n登録されていた予定（参加実績は未確認）：\n・読書会"
        let entry = DiaryEntry(date: day, timezone: "Asia/Tokyo", generated_body: body, edited_body: nil, body: body, source_snapshot: sources, edited_sources: nil, revision: 1, generated_at: "2026-09-01T09:00:00+09:00", updated_at: "2026-09-01T09:00:00+09:00", deleted: false)
        return DiaryEnvelope(date: day, timezone: "Asia/Tokyo", state: "ready", entry: entry, history: [])
    }
}

struct DiaryView: View {
    @ObservedObject var store: DiaryStore
    @State private var confirmDelete = false
    @State private var deleteRevision: Int?
    @State private var overwriteRevision: Int?
    @State private var confirmOverwrite = false
    @State private var confirmDiscard = false
    var body: some View {
        List {
            Section {
                DatePicker("日付", selection: $store.selectedDate, in: ...Date.now, displayedComponents: .date)
                    .environment(\.timeZone, DiaryStore.zone)
                    .disabled(store.busy)
                Text("日本時間（Asia/Tokyo）の日記です。記録からMac内で下書きを作成します。")
                    .font(.caption).foregroundStyle(.secondary)
                if let policy = store.policy {
                    Toggle("日記を自動生成", isOn: Binding(get: { policy.enabled }, set: { value in Task { await store.setAutomatic(value) } }))
                        .disabled(store.busy)
                }
                Text("当日と過去7日分を5分ごとに更新します。本人の編集は保持します。健康や会話を含む日記の材料を外部AIへ送りません。")
                    .font(.caption).foregroundStyle(.secondary)
                if store.busy { ProgressView("処理中…") }
                if let error = store.error { Text(error).foregroundStyle(.red) }
                Button("最新内容を読み込む") { Task { await store.refresh() } }.disabled(store.busy)
            }
            if let current = store.current {
                if let message = current.generation_error {
                    Text(message).foregroundStyle(.orange)
                }
                if current.state == "ready", let entry = current.entry {
                    Section(store.hasEdits ? "日記（未保存の入力あり）" : entry.edited_body == nil ? "自動下書き" : "保存した日記") {
                        TextEditor(text: Binding(get: { store.text }, set: { store.text = $0 }))
                            .frame(minHeight: 280).disabled(store.busy)
                            .accessibilityLabel("日記の本文")
                        Text("\(store.text.count) / 20000文字").font(.caption).foregroundStyle(.secondary)
                        Button("日記を保存") { Task { await store.save() } }
                            .disabled(store.busy || store.text.count > 20_000 || store.conflict)
                        if store.hasEdits {
                            Button("未保存の入力を破棄") { confirmDiscard = true }.disabled(store.busy)
                        }
                    }
                    if store.conflict {
                        Section("最新の日記と入力が異なります") {
                            Text("入力は保持されています。最新内容を確認し、必要な部分を入力欄へ反映してから保存してください。")
                            Text(entry.body).textSelection(.enabled)
                            Button("この入力で最新の日記を置き換える") { overwriteRevision = entry.revision; confirmOverwrite = true }
                                .disabled(store.busy || store.text.count > 20_000)
                        }
                    }
                    Section {
                        Button("記録から下書きを更新") { Task { await store.generate() } }.disabled(store.busy)
                        Text("下書き生成: \(entry.generated_at)\n改訂: \(entry.revision)").font(.caption)
                        if entry.edited_body != nil {
                            DisclosureGroup("現在の自動下書き") { Text(entry.generated_body).textSelection(.enabled) }
                        }
                        DisclosureGroup("現在の下書きの根拠") { DiaryEvidence(sources: entry.source_snapshot) }
                        if let sources = entry.edited_sources {
                            DisclosureGroup("本人が保存した時点の根拠") { DiaryEvidence(sources: sources) }
                        }
                        if !current.history.isEmpty {
                            DisclosureGroup("以前の日記と根拠（最大20版）") {
                                ForEach(current.history, id: \.revision) { version in
                                    DisclosureGroup("改訂 \(version.revision) · \(version.updated_at)") {
                                        Text(version.body).textSelection(.enabled)
                                        DiaryEvidence(sources: version.edited_sources ?? version.source_snapshot)
                                    }
                                }
                            }
                        }
                        Button("この日の日記を削除", role: .destructive) { deleteRevision = entry.revision; confirmDelete = true }.disabled(store.busy)
                    }
                } else {
                    Section {
                        Text(current.state == "deleted" ? "この日の日記は削除済みです。自動では再作成しません。" : "この日の日記はまだ作成されていません。")
                        if store.hasEdits {
                            Text("未保存の入力は保持しています。下書きを作成すると入力欄へ戻ります。")
                        }
                        Button(current.state == "deleted" ? "記録から日記を再作成" : "日記の下書きを作成") { Task { await store.generate() } }
                            .disabled(store.busy)
                    }
                }
            } else if store.error == nil { ProgressView("日記を読み込み中…") }
        }
        .navigationTitle("日記")
        .task(id: store.day) { await store.refresh() }
        .refreshable { await store.refresh() }
        .alert("この日の日記を削除しますか？", isPresented: $confirmDelete) {
            Button("削除", role: .destructive) { Task { await store.delete(confirmedRevision: deleteRevision) } }
            Button("キャンセル", role: .cancel) {}
        } message: { Text("日記の本文・根拠のコピー・改訂履歴を削除します。元の生活記録は保持します。") }
        .alert("最新の日記をこの入力で置き換えますか？", isPresented: $confirmOverwrite) {
            Button("保存") { Task { await store.save(confirmedRevision: overwriteRevision) } }
            Button("キャンセル", role: .cancel) {}
        }
        .alert("未保存の入力を破棄しますか？", isPresented: $confirmDiscard) {
            Button("破棄", role: .destructive) { store.discardInput() }
            Button("キャンセル", role: .cancel) {}
        }
    }
}

private struct DiaryEvidence: View {
    let sources: DiarySources
    var body: some View {
        ForEach(sources.warnings, id: \.self) { Text($0).font(.caption).foregroundStyle(.orange) }
        if sources.items.isEmpty { Text("材料となる記録はまだありません。活動がなかったことを示すものではありません。") }
        ForEach(sources.items, id: \.identity) { item in
            VStack(alignment: .leading, spacing: 4) {
                Text(item.title)
                if !item.detail.isEmpty { Text(item.detail).font(.caption).textSelection(.enabled) }
                if let at = item.at { Text(at).font(.caption).foregroundStyle(.secondary) }
            }
        }
    }
}
