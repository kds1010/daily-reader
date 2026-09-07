import SwiftUI
import Combine
import EventKit
import UserNotifications

struct LifeFeedback: Codable { let useful: Bool; let saved_minutes: Int? }
struct LifeRevision: Decodable { let revision: Int; let data: String; let status: String; let changed_at: String }
struct LifePerson: Decodable, Identifiable { let id: String; let name: String }
struct LifeLink: Decodable { let title: String; let url: String }
struct LifeAction: Decodable { let title: String; let detail: String; let kind: String }
struct LifeOption: Decodable { let title: String; let detail: String }
struct LifeResult: Decodable {
    let outcome: String; let conclusion: String; let unresolved: String
    let sources: [LifeLink]; let actions: [LifeAction]; let options: [LifeOption]
}
struct LifeEvidence: Decodable {
    let type: String; let recording_id: String?; let entry_id: String?; let item_id: String?
    let quotes: [ConversationInsightEvidence]?; let sources: [LifeLink]?
}
struct LifeEntry: Decodable, Identifiable {
    let feedback: LifeFeedback?; let history: [LifeRevision]?
    let id: String; let kind: String; let status: String; let revision: Int
    let title: String; let detail: String; let source_url: String?
    let person_id: String?; let category: String?; let expires_at: String?
    let constraints: String?; let assignee: String?; let due_at: String?; let remind_at: String?
    let start_at: String?; let end_at: String?; let deadline_at: String?; let prepare_at: String?; let depart_at: String?
    let timezone: String?; let location: String?; let preparation: String?
    let result: LifeResult?; let error: String?; let checked_at: String?; let evidence: LifeEvidence
    var active: Bool { !["completed", "cancelled", "archived"].contains(status) }
    var expired: Bool { lifeDate(expires_at).map { $0 < .now } ?? false }
}
struct LifeNews: Decodable, Identifiable { let id: String; let title: String; let url: String; let reason: String }
struct LifeNotice: Decodable { let id: String; let entry_id: String; let title: String; let at: String }
struct LifeSnapshot: Decodable {
    let entries: [LifeEntry]; let people: [LifePerson]; let news: [LifeNews]
    let news_remaining: Int; let notifications: [LifeNotice]
}
struct LifeSearchItem: Decodable, Identifiable {
    let id: String; let recording_id: String; let text: String; let filename: String; let speaker: String?
    let latitude: Double?; let longitude: Double?
}
struct LifeSearchResponse: Decodable { let items: [LifeSearchItem] }
struct LifeRequest: Encodable {
    var feedback: LifeFeedback?
    var kind = "task"; var title = ""; var detail = ""; var source_url = ""
    var person_id = ""; var category = "interest"; var expires_at: String?
    var constraints = ""; var assignee = ""; var due_at: String?; var remind_at: String?
    var start_at: String?; var end_at: String?; var deadline_at: String?; var prepare_at: String?; var depart_at: String?
    var timezone = TimeZone.current.identifier; var location = ""; var preparation = ""
    var source_type = "manual"; var source_id = ""; var source_index: Int?; var source_role: String?
    var request_id = UUID().uuidString; var revision: Int?; var status: String?
    init(kind: String = "task", title: String = "", detail: String = "") {
        self.kind = kind; self.title = title; self.detail = detail
        if kind == "event" {
            let start = Date.now.addingTimeInterval(86400)
            start_at = start.ISO8601Format(); end_at = start.addingTimeInterval(3600).ISO8601Format()
        }
    }
    init(_ e: LifeEntry) {
        kind = e.kind; title = e.title; detail = e.detail; source_url = e.source_url ?? ""
        person_id = e.person_id ?? ""; category = e.category ?? "interest"; expires_at = e.expires_at
        constraints = e.constraints ?? ""; assignee = e.assignee ?? ""; due_at = e.due_at; remind_at = e.remind_at
        start_at = e.start_at; end_at = e.end_at; deadline_at = e.deadline_at; prepare_at = e.prepare_at; depart_at = e.depart_at
        timezone = e.timezone ?? TimeZone.current.identifier; location = e.location ?? ""; preparation = e.preparation ?? ""
        revision = e.revision
    }
    enum CodingKeys: String, CodingKey {
        case kind, title, detail, source_url, person_id, category, constraints, assignee, timezone, location, preparation, source_type, source_id, request_id, expires_at, due_at, remind_at, start_at, end_at, deadline_at, prepare_at, depart_at, revision, status, source_index, source_role, feedback
    }
    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(kind, forKey: .kind)
        try values.encode(title, forKey: .title)
        try values.encode(detail, forKey: .detail)
        try values.encode(source_url, forKey: .source_url)
        try values.encode(person_id, forKey: .person_id)
        try values.encode(category, forKey: .category)
        try values.encode(constraints, forKey: .constraints)
        try values.encode(assignee, forKey: .assignee)
        try values.encode(timezone, forKey: .timezone)
        try values.encode(location, forKey: .location)
        try values.encode(preparation, forKey: .preparation)
        try values.encode(source_type, forKey: .source_type)
        try values.encode(source_id, forKey: .source_id)
        try values.encode(request_id, forKey: .request_id)
        try values.encode(expires_at, forKey: .expires_at)
        try values.encode(due_at, forKey: .due_at)
        try values.encode(remind_at, forKey: .remind_at)
        try values.encode(start_at, forKey: .start_at)
        try values.encode(end_at, forKey: .end_at)
        try values.encode(deadline_at, forKey: .deadline_at)
        try values.encode(prepare_at, forKey: .prepare_at)
        try values.encode(depart_at, forKey: .depart_at)
        try values.encodeIfPresent(revision, forKey: .revision)
        try values.encodeIfPresent(status, forKey: .status)
        try values.encodeIfPresent(source_index, forKey: .source_index)
        try values.encodeIfPresent(source_role, forKey: .source_role)
        try values.encodeIfPresent(feedback, forKey: .feedback)
    }
}
func lifeDate(_ value: String?) -> Date? { value.flatMap { ISO8601DateFormatter().date(from: $0) } }
func lifeLabel(_ value: String) -> String {
    ["task": "タスク", "research": "調査", "event": "予定", "profile": "関心・人物",
     "active": "有効", "open": "未完了", "queued": "調査待ち", "running": "調査中",
     "completed": "完了", "failed": "要確認", "cancelled": "中止", "archived": "保管",
     "planned": "予定あり", "registered": "申込済み", "interest": "関心", "goal": "目標",
     "preference": "好み", "fact": "本人が話した事実"][value] ?? value
}

@MainActor
final class LifeStore: ObservableObject {
    @Published var snapshot: LifeSnapshot?
    @Published var error: String?
    @Published var noticeStatus = ""
    private var refreshing = false
    func refresh() async {
        guard !refreshing else { return }; refreshing = true; defer { refreshing = false }
        do {
            let value: LifeSnapshot = try await APIClient.shared.get("api/life")
            snapshot = value; error = nil
            noticeStatus = await LifeNotifications.shared.reconcile(value)
        } catch { self.error = error.localizedDescription }
    }
    func save(_ request: LifeRequest, id: String? = nil) async throws {
        let _: LifeEntry = try await APIClient.shared.post(id.map { "api/life/entries/\($0)" } ?? "api/life/entries", body: request, as: LifeEntry.self)
        await refresh()
    }
    func state(_ entry: LifeEntry, _ status: String) async {
        var request = LifeRequest(entry); request.status = status
        do { try await save(request, id: entry.id) } catch { let message = error.localizedDescription; await refresh(); self.error = message }
    }
}

struct LifeBrief: View {
    @ObservedObject var store: LifeStore
    @EnvironmentObject private var model: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            NavigationLink { LifeAssistantView(store: store) } label: {
                Label("暮らしのアシスタント", systemImage: "sparkles").font(.headline)
            }
            Text("調べもの・予定・タスク・関心を、会話から次の行動へ。")
                .font(.caption).foregroundStyle(.secondary)
            if let snapshot = store.snapshot {
                let pending = snapshot.entries.filter { $0.active && $0.kind != "profile" }
                Text("対応中 \(pending.count)件 · 調査完了 \(snapshot.entries.filter { $0.kind == "research" && $0.status == "completed" }.count)件")
                    .font(.caption)
                ForEach(Array(pending.filter { $0.kind != "research" }.sorted {
                    (lifeDate($0.start_at ?? $0.due_at) ?? .distantFuture) < (lifeDate($1.start_at ?? $1.due_at) ?? .distantFuture)
                }.prefix(3))) { entry in
                    NavigationLink { LifeEntryDetail(store: store, entryID: entry.id) } label: {
                        VStack(alignment: .leading) {
                            Text(entry.title)
                            if let date = lifeDate(entry.start_at ?? entry.due_at) { Text(date.formatted()).font(.caption).foregroundStyle(date < .now ? .orange : .secondary) }
                        }
                    }
                }
                ForEach(snapshot.news.filter { !model.readArticleIDs.contains($0.id) && !model.hiddenArticleIDs.contains($0.id) }) { news in
                    if let url = URL(string: news.url) {
                        Link(destination: url) { VStack(alignment: .leading) { Text(news.title); Text(news.reason).font(.caption).foregroundStyle(.secondary) } }
                    }
                }
                Text("今日のおすすめは最大3件。続きは「ニュース」で必要なときに確認できます。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if let error = store.error { Text(error).font(.caption).foregroundStyle(.red) }
        }.glassCard()
    }
}

struct LifeAssistantView: View {
    @ObservedObject var store: LifeStore
    @EnvironmentObject private var model: AppModel
    @State private var filter = "all"
    @State private var history = false
    @State private var person = "all"
    @State private var personName = ""
    @State private var search = ""
    @State private var results: [LifeSearchItem] = []
    @State private var searchMessage = ""
    var body: some View {
        List {
            Section {
                Text("必要なことを確認し、決めたことだけ実行に移します。調査はMac側で続きます。")
                if let error = store.error { Text(error).foregroundStyle(.red) }
                Text(store.noticeStatus).font(.caption).foregroundStyle(.secondary)
                Button("通知を有効にする") { Task {
                    do { _ = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]); await store.refresh() }
                    catch { store.error = error.localizedDescription }
                } }
            }
            Section("新しく追加") {
                ForEach(["research", "event", "task", "profile"], id: \.self) { kind in
                    NavigationLink(lifeLabel(kind)) { LifeEntryEditor(store: store, request: LifeRequest(kind: kind)) }
                }
            }
            Section("保存した情報") {
                Picker("種類", selection: $filter) {
                    Text("すべて").tag("all")
                    ForEach(["research", "event", "task", "profile"], id: \.self) { Text(lifeLabel($0)).tag($0) }
                }
                if filter == "profile" {
                    Picker("人物", selection: $person) {
                        Text("全員").tag("all")
                        ForEach(store.snapshot?.people ?? []) { Text($0.name).tag($0.id) }
                    }
                }
                Toggle("完了・保管・期限切れも表示", isOn: $history)
                ForEach((store.snapshot?.entries ?? []).filter { (filter == "all" || $0.kind == filter) && (history || ($0.active && !$0.expired) || $0.kind == "research" && $0.status == "completed") && (filter != "profile" || person == "all" || $0.person_id == person) }) { entry in
                    NavigationLink { LifeEntryDetail(store: store, entryID: entry.id) } label: {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(entry.title).font(.headline)
                            Text("\(lifeLabel(entry.kind)) · \(lifeLabel(entry.status))\(entry.expired ? " · 期限切れ" : "")").font(.caption).foregroundStyle(.secondary)
                            if let personID = entry.person_id { Text(store.snapshot?.people.first { $0.id == personID }?.name ?? "人物未確認").font(.caption) }
                            if let date = lifeDate(entry.start_at ?? entry.due_at) { Text(date.formatted()).font(.caption) }
                        }
                    }
                }
            }
            Section("人物を追加") {
                TextField("名前（同名の人物は自動統合しません）", text: $personName)
                Button("追加") { Task {
                    do {
                        let _: LifePerson = try await APIClient.shared.post("api/life/people", body: ["name": personName], as: LifePerson.self)
                        personName = ""; await store.refresh()
                    } catch { store.error = error.localizedDescription }
                } }.disabled(personName.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            }
            Section("会話を振り返る") {
                TextField("発言・話者名・録音日・ファイル名", text: $search)
                Button("検索") { Task {
                    do {
                        let response: LifeSearchResponse = try await APIClient.shared.get("api/life/search", queryItems: [URLQueryItem(name: "q", value: search)])
                        results = response.items; searchMessage = results.isEmpty ? "見つかりませんでした" : "\(results.count)件（最大50件）"
                    } catch { searchMessage = error.localizedDescription }
                } }.disabled(search.isEmpty)
                Text(searchMessage).font(.caption)
                ForEach(results) { item in
                    NavigationLink { ConversationDetailView(recordingID: item.recording_id) } label: {
                        VStack(alignment: .leading) {
                            Text(item.text).lineLimit(3)
                            Text("\(item.filename) · \(item.speaker ?? "話者未確認")\(item.latitude != nil ? " · GPSあり" : "")").font(.caption)
                        }
                    }
                }
            }
        }.navigationTitle("暮らしのアシスタント")
            .refreshable { await store.refresh() }
            .task {
                guard !model.isFixture else { return }
                while !Task.isCancelled {
                    await store.refresh()
                    do { try await Task.sleep(for: .seconds(15)) } catch { return }
                }
            }
    }
}

struct LifeEntryEditor: View {
    @ObservedObject var store: LifeStore
    @Environment(\.dismiss) private var dismiss
    @State var request: LifeRequest
    var entryID: String? = nil
    @State private var saving = false
    @State private var error: String?
    var body: some View {
        Form {
            Section("内容を確認") {
                TextField("タイトル", text: $request.title)
                TextField("詳細・根拠となる情報", text: $request.detail, axis: .vertical).lineLimit(3...10)
                TextField("出典URL（任意）", text: $request.source_url)
            }
            if request.kind == "profile" {
                Section("誰の、どのような情報ですか") {
                    Text("発言した人と情報の対象者を区別し、本人が話した内容を確認して保存してください。健康・性格などを推測して確定しません。")
                        .font(.caption)
                    Picker("対象者", selection: $request.person_id) {
                        Text("選択してください").tag("")
                        ForEach(store.snapshot?.people ?? []) { Text($0.name).tag($0.id) }
                    }
                    Picker("種類", selection: $request.category) {
                        ForEach(["interest", "goal", "preference", "fact"], id: \.self) { Text(lifeLabel($0)).tag($0) }
                    }
                    LifeOptionalDate(label: "見直す期限", value: $request.expires_at)
                    Text("自分の関心・目標だけをおすすめに使います。タイトルは「写真」「英語」のような短い語にすると記事と照合できます。期限切れの情報はおすすめから外れます。")
                        .font(.caption)
                }
            }
            if request.kind == "research" {
                Section("調査の範囲") {
                    TextField("条件・予算・地域・比較したいこと", text: $request.constraints, axis: .vertical).lineLimit(3...8)
                    LifeOptionalDate(label: "必要な期限", value: $request.due_at)
                    Text("開始すると、このタイトル・詳細・条件・期限・URLをCodexに送って公開Webを調べます。会話全体や人物一覧は送りません。最大10分で、出典と次の行動をまとめます。")
                        .font(.caption)
                }
            }
            if request.kind == "task" {
                Section("期限・通知") {
                    TextField("担当者", text: $request.assignee)
                    LifeOptionalDate(label: "期限", value: $request.due_at)
                    LifeOptionalDate(label: "通知", value: $request.remind_at)
                }
            }
            if request.kind == "event" {
                Section("日時・準備") {
                    DatePicker("開始", selection: dateBinding($request.start_at))
                    DatePicker("終了", selection: dateBinding($request.end_at, offset: 3600))
                    TextField("タイムゾーン", text: $request.timezone)
                    TextField("場所", text: $request.location)
                    TextField("準備すること", text: $request.preparation, axis: .vertical)
                    LifeOptionalDate(label: "申し込み期限", value: $request.deadline_at)
                    LifeOptionalDate(label: "準備", value: $request.prepare_at)
                    LifeOptionalDate(label: "出発", value: $request.depart_at)
                    LifeOptionalDate(label: "予定の通知", value: $request.remind_at)
                    Text("関連する未完了の準備・申込タスクの期限も更新します。タスク側で個別に変更した期限は保ちます。移動時間からの自動計算は行いません。出発時刻を確認して指定してください。保存後にカレンダーへ反映できます。")
                        .font(.caption)
                }
            }
            if let error { Text(error).foregroundStyle(.red) }
            Button(request.kind == "research" ? "この内容で調査開始" : "確認して保存") {
                saving = true
                Task {
                    do {
                        if request.kind == "event" {
                            if request.start_at == nil { request.start_at = Date.now.ISO8601Format() }
                            if request.end_at == nil { request.end_at = Date.now.addingTimeInterval(3600).ISO8601Format() }
                        }
                        try await store.save(request, id: entryID); dismiss()
                    } catch { self.error = error.localizedDescription }
                    saving = false
                }
            }.disabled(saving || request.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || (request.kind == "profile" && request.person_id.isEmpty))
        }.environment(\.timeZone, request.kind == "event" ? (TimeZone(identifier: request.timezone) ?? .current) : .current)
            .formStyle(.grouped).navigationTitle(lifeLabel(request.kind))
    }
    private func dateBinding(_ binding: Binding<String?>, offset: TimeInterval = 0) -> Binding<Date> {
        Binding(get: { lifeDate(binding.wrappedValue) ?? .now.addingTimeInterval(offset) }, set: { binding.wrappedValue = $0.ISO8601Format() })
    }
}
struct LifeOptionalDate: View {
    let label: String
    @Binding var value: String?
    var body: some View {
        Toggle(label, isOn: Binding(get: { value != nil }, set: { value = $0 ? Date.now.addingTimeInterval(3600).ISO8601Format() : nil }))
        if value != nil { DatePicker(label, selection: Binding(get: { lifeDate(value) ?? .now }, set: { value = $0.ISO8601Format() })) }
    }
}

struct LifeEntryDetail: View {
    @ObservedObject var store: LifeStore
    let entryID: String
    @State private var calendarMessage = ""
    @State private var busy = false
    @State private var confirmDelete = false
    @State private var savedMinutes = ""
    @State private var history: [LifeRevision] = []
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        List {
            if let e = store.snapshot?.entries.first(where: { $0.id == entryID }) {
                Section {
                    Text(e.title).font(.title2)
                    Text("\(lifeLabel(e.kind)) · \(lifeLabel(e.status))")
                    Text(e.detail).textSelection(.enabled)
                    if let source = e.source_url, let url = URL(string: source), !source.isEmpty { Link("元の情報", destination: url) }
                    if let error = e.error { Text(error).foregroundStyle(.red) }
                    if e.kind != "research" { NavigationLink("内容・日時を修正") { LifeEntryEditor(store: store, request: LifeRequest(e), entryID: e.id) } }
                }
                if let result = e.result {
                    Section("調査結果") {
                        if let date = lifeDate(e.checked_at) { Text("確認: \(date.formatted())").font(.caption) }
                        Text(result.conclusion).textSelection(.enabled)
                        ForEach(Array(result.options.enumerated()), id: \.offset) { _, option in
                            VStack(alignment: .leading) { Text(option.title).font(.headline); Text(option.detail) }
                        }
                        if !result.unresolved.isEmpty { Text("未確認: \(result.unresolved)").foregroundStyle(.secondary) }
                        ForEach(Array(result.sources.enumerated()), id: \.offset) { _, source in
                            if let url = URL(string: source.url) { Link(source.title, destination: url) }
                        }
                    }
                    if e.status == "completed" {
                        Section("役立ちましたか") {
                            TextField("節約できた時間（分・任意）", text: $savedMinutes)
                            HStack {
                                Button("役立った") { report(e, useful: true) }
                                Button("使わなかった") { report(e, useful: false) }
                            }
                            if let feedback = e.feedback {
                                Text(feedback.useful ? "役立ったと記録済み" : "使わなかったと記録済み")
                                if let minutes = feedback.saved_minutes { Text("本人の見積もり: \(minutes)分").font(.caption) }
                            }
                            Text("ジョブの完了を、生活改善の実績として自動で数えません。").font(.caption)
                        }
                    }
                    Section("次の行動を選ぶ") {
                        ForEach(Array(result.actions.enumerated()), id: \.offset) { index, action in
                            NavigationLink("\(lifeLabel(action.kind)): \(action.title)") {
                                LifeEntryEditor(store: store, request: actionRequest(action, source: e.id, index: index))
                            }
                        }
                    }
                }
                if e.kind == "event" {
                    Section("予定と準備") {
                        ForEach([("開始", e.start_at), ("終了", e.end_at), ("申込期限", e.deadline_at), ("準備", e.prepare_at), ("出発", e.depart_at), ("通知", e.remind_at)], id: \.0) { label, value in
                            if let date = lifeDate(value) { LabeledContent(label, value: date.formatted()) }
                        }
                        Text(e.location ?? ""); Text(e.preparation ?? "")
                        Button(e.status == "cancelled" ? "カレンダーからこの予定を削除" : "カレンダーへ追加・変更を反映") {
                            busy = true
                            Task {
                                do { calendarMessage = try await LifeCalendar.shared.apply(e) }
                                catch { calendarMessage = error.localizedDescription }
                                busy = false
                            }
                        }.disabled(busy)
                        Text(calendarMessage.isEmpty ? "カレンダーの変更・中止は、このボタンで反映してください。申し込み自体は出典サイトで行ってください。" : calendarMessage).font(.caption)
                        NavigationLink("準備をタスクにする") { LifeEntryEditor(store: store, request: eventRequest(e, role: "prepare")) }
                        NavigationLink("申し込みをタスクにする") { LifeEntryEditor(store: store, request: eventRequest(e, role: "deadline")) }
                        NavigationLink("最新情報を調べる") { LifeEntryEditor(store: store, request: eventRequest(e, role: "research")) }
                    }
                }
                if e.kind == "profile" {
                    Section("プロフィール") {
                        Text("対象: \(store.snapshot?.people.first { $0.id == e.person_id }?.name ?? "未確認")")
                        Text(lifeLabel(e.category ?? "interest"))
                        if let date = lifeDate(e.expires_at) { Text("見直す期限: \(date.formatted())") }
                        Text("誤りや関心の変化は修正できます。削除すると、このプロフィールと保存した根拠を消します。元の録音は別に管理します。")
                            .font(.caption)
                        Button("プロフィールを削除", role: .destructive) { confirmDelete = true }
                    }
                }
                Section("根拠とつながり") {
                    ForEach(Array((e.evidence.quotes ?? []).enumerated()), id: \.offset) { _, quote in Text(quote.quote).textSelection(.enabled) }
                    if let recording = e.evidence.recording_id { NavigationLink("元の会話・時刻・GPSを確認") { ConversationDetailView(recordingID: recording) } }
                    if let parent = e.evidence.entry_id { NavigationLink("元の調査・予定を確認") { LifeEntryDetail(store: store, entryID: parent) } }
                    ForEach((store.snapshot?.entries ?? []).filter { $0.evidence.entry_id == e.id }) { child in
                        NavigationLink("\(lifeLabel(child.kind)): \(child.title)") { LifeEntryDetail(store: store, entryID: child.id) }
                    }
                    if e.evidence.type == "manual" { Text("手動で確認・保存した情報です").font(.caption) }
                }
                if !history.isEmpty {
                    Section("変更前の記録（直近20件）") {
                        ForEach(history, id: \.revision) { revision in
                            DisclosureGroup("版 \(revision.revision) · \(lifeLabel(revision.status)) · \(lifeDate(revision.changed_at)?.formatted() ?? revision.changed_at)") {
                                Text(historyDescription(revision)).font(.caption).textSelection(.enabled)
                            }
                        }
                    }
                }
                Section("状態を変更") {
                    ForEach(states(e), id: \.self) { status in
                        Button(lifeLabel(status)) { Task { await store.state(e, status) } }
                    }
                    if let error = store.error { Text(error).foregroundStyle(.red) }
                }
            } else { Text("この情報は削除されたか、まだ読み込まれていません。") }
        }.navigationTitle("内容と次の行動")
            .task(id: store.snapshot?.entries.first(where: { $0.id == entryID })?.revision) {
                do {
                    let detail: LifeEntry = try await APIClient.shared.get("api/life/entries/\(entryID)")
                    history = detail.history ?? []
                } catch { store.error = error.localizedDescription }
            }
            .confirmationDialog("このプロフィールを削除しますか", isPresented: $confirmDelete) {
                Button("削除", role: .destructive) { Task {
                    guard let e = store.snapshot?.entries.first(where: { $0.id == entryID }) else { return }
                    do {
                        let _: EmptyResponse = try await APIClient.shared.post("api/life/entries/\(e.id)/delete", body: ["revision": e.revision], as: EmptyResponse.self)
                        await store.refresh(); dismiss()
                    } catch { store.error = error.localizedDescription }
                } }
            }
    }
    private func historyDescription(_ revision: LifeRevision) -> String {
        guard let data = revision.data.data(using: .utf8), let values = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return "履歴を読み込めません" }
        let fields = [("title", "タイトル"), ("due_at", "期限"), ("start_at", "開始"), ("end_at", "終了"), ("deadline_at", "申込期限"), ("prepare_at", "準備"), ("depart_at", "出発"), ("remind_at", "通知")]
        return fields.compactMap { key, label in
            guard let value = values[key] as? String else { return nil }
            return "\(label): \(lifeDate(value)?.formatted() ?? value)"
        }.joined(separator: "\n")
    }
    private func report(_ entry: LifeEntry, useful: Bool) {
        let raw = savedMinutes.trimmingCharacters(in: .whitespacesAndNewlines)
        if !raw.isEmpty && (Int(raw) == nil || !(0...600).contains(Int(raw)!)) { store.error = "時間は0〜600分で入力してください"; return }
        var request = LifeRequest(entry); request.feedback = LifeFeedback(useful: useful, saved_minutes: raw.isEmpty ? nil : Int(raw))
        Task { do { try await store.save(request, id: entry.id) } catch { store.error = error.localizedDescription } }
    }
    private func states(_ e: LifeEntry) -> [String] {
        if e.kind == "research" {
            switch e.status { case "queued", "running": return ["cancelled"]; case "failed", "cancelled": return ["queued", "archived"]; case "completed": return ["archived"]; default: return [] }
        }
        let values = e.kind == "profile" ? ["active", "archived"] : e.kind == "event" ? ["planned", "registered", "completed", "cancelled"] : ["open", "completed", "cancelled"]
        return values.filter { $0 != e.status }
    }
    private func actionRequest(_ action: LifeAction, source: String, index: Int) -> LifeRequest {
        var request = LifeRequest(kind: action.kind, title: action.title, detail: action.detail)
        request.source_type = "research"; request.source_id = source; request.source_index = index
        return request
    }
    private func eventRequest(_ event: LifeEntry, role: String) -> LifeRequest {
        var request = LifeRequest(kind: role == "research" ? "research" : "task", title: (role == "prepare" ? "準備: " : role == "deadline" ? "申し込み: " : "最新情報: ") + event.title, detail: event.preparation ?? event.detail)
        request.source_type = "event"; request.source_id = event.id; request.source_role = role
        request.source_url = event.source_url ?? ""
        request.due_at = role == "deadline" ? event.deadline_at : event.prepare_at
        request.remind_at = request.due_at
        return request
    }
}

@MainActor
final class LifeCalendar {
    static let shared = LifeCalendar()
    private let store = EKEventStore()
    func apply(_ entry: LifeEntry) async throws -> String {
        guard try await store.requestFullAccessToEvents() else { throw APIClientError.server("カレンダーへのアクセスを許可してください") }
        let key = "life.calendar.\(entry.id)"
        let marker = URL(string: "daymeld://event/\(entry.id)")!
        let identifier = UserDefaults.standard.string(forKey: key)
        let existing = identifier.flatMap { store.event(withIdentifier: $0) }
        // Only events carrying this entry's ownership marker may be updated or deleted.
        if let existing, existing.url != marker { throw APIClientError.server("カレンダーの関連付けが変わっています。元の予定を確認してください") }
        guard let start = lifeDate(entry.start_at), let end = lifeDate(entry.end_at) else { throw APIClientError.server("予定の日時を確認してください") }
        // Find the same marker after reinstall or iCloud sync to avoid duplicate exports.
        let matches = store.events(matching: store.predicateForEvents(withStart: start.addingTimeInterval(-86400 * 30), end: end.addingTimeInterval(86400 * 30), calendars: nil)).filter { $0.url == marker }
        guard matches.count <= 1 else { throw APIClientError.server("同じ予定が複数あります。カレンダーで重複を確認してください") }
        if entry.status == "cancelled" {
            if let owned = existing ?? matches.first { try store.remove(owned, span: .thisEvent, commit: true) }
            UserDefaults.standard.removeObject(forKey: key)
            return "関連する予定をカレンダーから削除しました（見つからない場合はカレンダーを確認してください）"
        }
        let event = existing ?? matches.first ?? EKEvent(eventStore: store)
        if event.calendar == nil { event.calendar = store.defaultCalendarForNewEvents }
        guard event.calendar?.allowsContentModifications == true else { throw APIClientError.server("書き込みできる標準カレンダーを選択してください") }
        event.title = entry.title; event.startDate = start; event.endDate = end
        event.timeZone = TimeZone(identifier: entry.timezone ?? "Asia/Tokyo")
        event.location = entry.location; event.notes = entry.detail + "\n" + (entry.preparation ?? "") + "\n" + (entry.source_url ?? "")
        event.url = marker
        event.alarms = lifeDate(entry.remind_at).map { [EKAlarm(absoluteDate: $0)] } ?? []
        try store.save(event, span: .thisEvent, commit: true)
        UserDefaults.standard.set(event.eventIdentifier, forKey: key)
        return "カレンダーに反映しました"
    }
}

@MainActor
final class LifeNotifications {
    static let shared = LifeNotifications()
    private let center = UNUserNotificationCenter.current()
    func reconcile(_ snapshot: LifeSnapshot) async -> String {
        let settings = await center.notificationSettings()
        guard [.authorized, .provisional].contains(settings.authorizationStatus) else { return "通知は未許可です。予定を保存しても通知されません。" }
        let future = snapshot.notifications.filter { (lifeDate($0.at) ?? .distantPast) > .now }
        let selected = Array(future.prefix(40))
        let identifiers = Set(selected.map(\.id))
        let pending = await center.pendingNotificationRequests()
        center.removePendingNotificationRequests(withIdentifiers: pending.filter { $0.identifier.hasPrefix("life.") && !identifiers.contains($0.identifier) }.map(\.identifier))
        do {
            for notice in selected {
                guard let date = lifeDate(notice.at) else { continue }
                if pending.contains(where: { $0.identifier == notice.id && $0.content.title == notice.title && ($0.trigger as? UNCalendarNotificationTrigger)?.nextTriggerDate() == date }) { continue }
                let content = UNMutableNotificationContent(); content.title = notice.title; content.sound = .default
                content.userInfo = ["life_entry_id": notice.entry_id]
                var calendar = Calendar(identifier: .gregorian); calendar.timeZone = TimeZone(secondsFromGMT: 0)!
                var components = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date); components.timeZone = calendar.timeZone
                try await center.add(UNNotificationRequest(identifier: notice.id, content: content, trigger: UNCalendarNotificationTrigger(dateMatching: components, repeats: false)))
            }
            let previous = UserDefaults.standard.dictionary(forKey: "life.research.states") as? [String: String]
            let jobs = snapshot.entries.filter { $0.kind == "research" }
            for job in jobs where previous != nil && previous?[job.id] != job.status && ["completed", "failed"].contains(job.status) {
                let content = UNMutableNotificationContent(); content.title = "調査\(lifeLabel(job.status)): \(job.title)"; content.sound = .default
                content.userInfo = ["life_entry_id": job.id]
                try await center.add(UNNotificationRequest(identifier: "life-result.\(job.id).\(job.revision)", content: content, trigger: nil))
            }
            UserDefaults.standard.set(Dictionary(uniqueKeysWithValues: jobs.map { ($0.id, $0.status) }), forKey: "life.research.states")
            return "この端末の通知: \(selected.count)件予約済み" + (future.count > 40 ? "。残り\(future.count - 40)件は次回の更新時に順次予約します。" : "")
        } catch { return "通知の予約に失敗しました: \(error.localizedDescription)" }
    }
}

struct ConversationLifeChoices: View {
    @ObservedObject var store: LifeStore
    let item: ConversationInsightItem
    let title: String; let detail: String; let assignee: String
    var body: some View {
        List {
            Section("元の発言を確認") {
                Text(title).font(.headline); Text(detail)
                ForEach(Array(item.evidence.enumerated()), id: \.offset) { _, quote in Text(quote.quote).font(.caption) }
                if !assignee.isEmpty { Text("発言にある人物: \(assignee)（対象者は次の画面で選択）").font(.caption) }
            }
            Section("何につなげますか") {
                ForEach(["task", "research", "event", "profile"], id: \.self) { kind in
                    if let existing = store.snapshot?.entries.first(where: { $0.kind == kind && $0.evidence.item_id == item.id }) {
                        NavigationLink("\(lifeLabel(kind))に保存済み") { LifeEntryDetail(store: store, entryID: existing.id) }
                    } else {
                        NavigationLink(lifeLabel(kind)) { LifeEntryEditor(store: store, request: request(kind)) }
                    }
                }
            }
        }.navigationTitle("会話から次の行動へ").task { await store.refresh() }
    }
    private func request(_ kind: String) -> LifeRequest {
        var value = LifeRequest(kind: kind, title: title, detail: detail)
        value.source_type = "conversation"; value.source_id = item.id; value.assignee = assignee
        value.category = item.kind == "preference" ? "preference" : "interest"
        // A date-only extraction is not an exact timestamp. Preserve it for explicit review.
        if let due = item.dueDate, !due.isEmpty { value.detail += "\n会話の期限候補: \(due)（日時を確認してください）" }
        return value
    }
}
