import SwiftUI
import Combine
import EventKit
import UserNotifications

struct SecretaryCard: Decodable, Identifiable {
    let id: String; let source_type: String; let source_id: String
    let title: String; let reason: String; let due_at: String?; let certainty: String
    let urgent: Bool; let version: String; let status: String; let until_at: String?; let url: String
}
struct SecretarySource: Decodable, Identifiable {
    let id: String; let label: String; let state: String; let last_success_at: String?; let detail: String
    var stateLabel: String {
        ["available": "取得済み", "missing": "未取得", "stale": "古い情報", "stopped": "停止中",
         "authorization_required": "許可・認証が必要", "failed": "取得失敗"][state] ?? "未取得"
    }
}
struct SecretaryMetric: Decodable { let count: Int; let total: Int? }
struct SecretaryDay: Decodable {
    let day: String; let timezone: String; let revision: Int
    let browsing_minutes: Int?; let management_minutes: Int?; let forgotten_count: Int?
}
struct SecretaryWeekly: Decodable {
    let start_date: String; let end_date: String; let timezone: String; let recorded_days: Int
    let browsing_minutes: SecretaryMetric; let management_minutes: SecretaryMetric; let forgotten_count: SecretaryMetric
    let research_evaluations: Int; let research_useful: Int; let saved_minutes: SecretaryMetric
    let today: SecretaryDay?
}
struct SecretarySnapshot: Decodable {
    let items: [SecretaryCard]; let top_ids: [String]; let remaining_count: Int
    let urgent_count: Int; let deferred_urgent_count: Int; let sources: [SecretarySource]
    let weekly: SecretaryWeekly; let timezone: String; let generated_at: String
}
struct SecretaryCardRequest: Encodable {
    let card_id: String; let version: String; let status: String; let until_at: String?
}
struct SecretaryDayRequest: Encodable {
    let request_id: String; let day: String; let timezone: String; let revision: Int
    let browsing_minutes: Int?; let management_minutes: Int?; let forgotten_count: Int?
}

struct LifeAutomationSettings: Decodable { let enabled: Bool; let research_enabled: Bool; let processed: Int; let pending: Int }
struct LifeDraft: Decodable, Identifiable { let id: String; let data: LifeEntry; let evidence: LifeEvidence; let reason: String; let source_index: Int? }
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
    let subject: String?
    let type: String; let recording_id: String?; let entry_id: String?; let item_id: String?
    let quotes: [ConversationInsightEvidence]?; let sources: [LifeLink]?
}
struct LifeEntry: Decodable, Identifiable {
    let automatic: Bool?
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
    let secretary: SecretarySnapshot?
    let device_context: PhoneContextOverview?
    let automation: LifeAutomationSettings?; let drafts: [LifeDraft]?
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
func lifeDate(_ value: String?) -> Date? { value.flatMap { parseISOTimestamp($0) } }
func lifeLabel(_ value: String) -> String {
    ["task": "タスク", "research": "調査", "event": "予定", "profile": "関心・人物",
     "active": "有効", "open": "未完了", "queued": "調査待ち", "running": "調査中",
     "completed": "完了", "failed": "要確認", "cancelled": "中止", "archived": "保管",
     "planned": "予定あり", "registered": "申込済み", "interest": "関心", "goal": "目標",
     "preference": "好み", "fact": "本人が話した事実"][value] ?? value
}

@MainActor
final class LifeStore: ObservableObject {
    var isFixture = false
    @Published var snapshot: LifeSnapshot?
    @Published var error: String?
    @Published var noticeStatus = ""
    @Published var calendarStatus = ""
    private let refreshes = ResourceRefreshes()
    func refresh(afterMutation: Bool = false) async {
        guard !isFixture else { return }
        await refreshes.run("life", replacing: afterMutation) { generation in
            do {
                #if os(iOS)
                async let context: Bool = PhoneContextSync.shared.synchronize()
                #endif
                let value: LifeSnapshot = try await APIClient.shared.get("api/life")
                guard self.refreshes.isCurrent("life", generation) else { return }
                self.snapshot = value
                self.error = nil
                let notice = await LifeNotifications.shared.reconcile(value)
                guard self.refreshes.isCurrent("life", generation) else { return }
                if self.noticeStatus != notice { self.noticeStatus = notice }
                let calendar = await LifeCalendar.shared.synchronize(value.entries)
                guard self.refreshes.isCurrent("life", generation) else { return }
                if self.calendarStatus != calendar { self.calendarStatus = calendar }
                #if os(iOS)
                if await context {
                    let updated: LifeSnapshot = try await APIClient.shared.get("api/life")
                    guard self.refreshes.isCurrent("life", generation) else { return }
                    self.snapshot = updated
                }
                #endif
            } catch {
                guard self.refreshes.isCurrent("life", generation) else { return }
                self.error = error.localizedDescription
            }
        }
    }
    func setAutomation(_ key: String, _ enabled: Bool) async {
        guard !isFixture else { return }
        do {
            let _: EmptyResponse = try await APIClient.shared.post("api/life/automation", body: [key: enabled], as: EmptyResponse.self)
            await refresh(afterMutation: true)
        } catch { self.error = error.localizedDescription }
    }
    func adopt(_ draft: LifeDraft) async {
        guard !isFixture else { return }
        do {
            let _: LifeEntry = try await APIClient.shared.post("api/life/drafts/\(draft.id)/adopt", body: EmptyRequest(), as: LifeEntry.self)
            await refresh(afterMutation: true)
        } catch { self.error = error.localizedDescription }
    }
    func dismiss(_ draft: LifeDraft) async {
        guard !isFixture else { return }
        do {
            let _: EmptyResponse = try await APIClient.shared.post("api/life/drafts/\(draft.id)/dismiss", body: EmptyRequest(), as: EmptyResponse.self)
            await refresh(afterMutation: true)
        } catch { self.error = error.localizedDescription }
    }
    func save(_ request: LifeRequest, id: String? = nil, draftID: String? = nil) async throws {
        guard !isFixture else { return }
        let _: LifeEntry = try await APIClient.shared.post(draftID.map { "api/life/drafts/\($0)/adopt" } ?? id.map { "api/life/entries/\($0)" } ?? "api/life/entries", body: request, as: LifeEntry.self)
        await refresh(afterMutation: true)
    }
    func reviewCard(_ card: SecretaryCard, status: String) async {
        guard !isFixture else { return }
        do {
            let request = SecretaryCardRequest(card_id: card.id, version: card.version, status: status,
                until_at: status == "snoozed" ? Date.now.addingTimeInterval(3600).ISO8601Format() : nil)
            let _: EmptyResponse = try await APIClient.shared.post("api/life/secretary/card", body: request, as: EmptyResponse.self)
            await refresh(afterMutation: true)
        } catch { self.error = error.localizedDescription }
    }
    func saveDay(_ request: SecretaryDayRequest) async throws {
        guard !isFixture else { return }
        let _: EmptyResponse = try await APIClient.shared.post("api/life/secretary/day", body: request, as: EmptyResponse.self)
        await refresh(afterMutation: true)
    }
    func state(_ entry: LifeEntry, _ status: String) async {
        var request = LifeRequest(entry); request.status = status
        do { try await save(request, id: entry.id) } catch { let message = error.localizedDescription; await refresh(afterMutation: true); self.error = message }
    }
}

struct LifeBrief: View {
    @ObservedObject var store: LifeStore
    @EnvironmentObject private var model: AppModel
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            NavigationLink { LifeAssistantView(store: store) } label: {
                Label("暮らしのアシスタント", systemImage: "sparkles").font(.headline)
            }.disabled(model.isFixture)
            Text("調べもの・予定・タスク・関心を、会話から次の行動へ。")
                .font(.caption).foregroundStyle(.secondary)
            if let snapshot = store.snapshot {
                if let secretary = snapshot.secretary {
                    SecretaryBrief(store: store, value: secretary)
                } else {
                if let context = snapshot.device_context {
                    ForEach(context.suggestions) { slot in
                        NavigationLink { LifeEntryDetail(store: store, entryID: slot.task_id) } label: {
                            VStack(alignment: .leading) {
                                Text("空き時間の作業候補: \(slot.title)")
                                Text("\(lifeDate(slot.start_at)?.formatted(date: .omitted, time: .shortened) ?? "")から20分の着手候補。").font(.caption)
                            }
                        }
                    }
                }
                if let policy = snapshot.automation {
                    Text("自動整理 \(policy.enabled ? "オン" : "停止中") · 確認待ち \(policy.pending)件").font(.caption)
                }
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
            }
            if let error = store.error { Text(error).font(.caption).foregroundStyle(.red) }
        }.glassCard()
    }
}

struct SecretaryBrief: View {
    @ObservedObject var store: LifeStore
    let value: SecretarySnapshot
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("今日、先に確認すること").font(.headline)
            Text("期限超過・当日の項目 \(value.urgent_count)件（確認済み・保留中 \(value.deferred_urgent_count)件）")
                .font(.caption).foregroundStyle(.secondary)
            if value.top_ids.isEmpty { Text("新しく確認する項目はありません。未取得の情報は下で確認できます。").font(.caption) }
            ForEach(value.items.filter { value.top_ids.contains($0.id) }) { card in
                SecretaryCardRow(store: store, card: card)
            }
            NavigationLink { SecretaryView(store: store) } label: {
                Label("すべての提案・取得状況・振り返り（残り\(value.remaining_count)件）", systemImage: "list.bullet.clipboard")
            }
            if value.sources.contains(where: { $0.state != "available" }) {
                Text("一部の情報が未取得・停止中、または古い状態です。保存済みの情報から提案しています。")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }
}

struct SecretaryCardRow: View {
    @ObservedObject var store: LifeStore
    @EnvironmentObject private var model: AppModel
    let card: SecretaryCard
    @State private var working = false
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            NavigationLink { destination } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Text(card.title).font(.subheadline.weight(.semibold))
                    Text(card.reason).font(.caption).foregroundStyle(.secondary)
                    if let date = lifeDate(card.due_at) {
                        Text(date.formatted() + certaintyLabel).font(.caption)
                            .foregroundStyle(card.urgent ? .orange : .secondary)
                    }
                }
            }
            if let until = lifeDate(card.until_at), card.status == "snoozed" {
                Text("\(until.formatted())まで保留").font(.caption)
            }
            HStack {
                if card.status == "pending" {
                    Button("確認しました") { act("reviewed") }
                    Button("1時間保留") { act("snoozed") }
                } else {
                    Text(card.status == "reviewed" ? "確認済み" : "保留中").font(.caption)
                    Button("再表示") { act("pending") }
                }
            }.buttonStyle(.bordered).controlSize(.small).disabled(working || model.isFixture)
        }.padding(.vertical, 4)
    }
    private var certaintyLabel: String {
        ["estimated": "（メールからの推定）", "unconfirmed": "（未確認）",
         "date_only": "（日付のみ・表示時刻は目安）"][card.certainty] ?? ""
    }
    @ViewBuilder private var destination: some View {
        if model.isFixture {
            Text("\(card.title)\n\(card.reason)\n匿名の表示確認用データです。").padding()
        } else if card.source_type == "life" {
            LifeEntryDetail(store: store, entryID: card.source_id)
        } else if card.source_type == "draft", let draft = store.snapshot?.drafts?.first(where: { $0.id == card.source_id }) {
            LifeEntryEditor(store: store, request: draftRequest(draft), draftID: draft.id, reviewReason: draft.reason)
        } else if card.source_type == "email", let email = model.emails.first(where: { $0.id == card.source_id }) {
            EmailDetailView(email: email)
        } else if card.source_type == "planner", let task = ((model.today?.tasks ?? []) + (model.today?.routines ?? [])).first(where: { $0.id == card.source_id }) {
            ScrollView { TaskRow(task: task).padding() }.navigationTitle("通常タスク")
        } else {
            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    Text(card.title).font(.title3)
                    Text(card.reason)
                    if let due = lifeDate(card.due_at) { Text(due.formatted() + certaintyLabel) }
                    if let url = URL(string: card.url), ["https", "http"].contains(url.scheme ?? "") {
                        Link("元の情報を開く", destination: url)
                    }
                    if card.source_type == "calendar" {
                        Text("取得済みのカレンダー情報です。予定の変更はカレンダーアプリで行ってください。")
                    }
                    if card.source_type == "planner" || card.source_type == "email" {
                        Button("最新の項目を取得") { Task { await model.refresh() } }
                    }
                }.padding()
            }
        }
    }
    private func draftRequest(_ draft: LifeDraft) -> LifeRequest {
        var value = LifeRequest(draft.data); value.revision = nil
        value.source_type = draft.evidence.type
        value.source_id = draft.evidence.item_id ?? draft.evidence.entry_id ?? ""
        value.source_index = draft.source_index
        return value
    }
    private func act(_ status: String) {
        working = true
        Task { await store.reviewCard(card, status: status); working = false }
    }
}

struct SecretaryView: View {
    @ObservedObject var store: LifeStore
    var body: some View {
        List {
            if let value = store.snapshot?.secretary {
                Section {
                    Text("確認済みは用事の完了やメールの既読とは別です。内容の変更や期限当日には再提示します。")
                    Text("期限超過・当日 \(value.urgent_count)件。確認済み・保留中にも\(value.deferred_urgent_count)件あります。")
                }.font(.caption)
                Section("用事・判断待ち") {
                    ForEach(value.items.filter { $0.source_type != "news" }) { card in
                        SecretaryCardRow(store: store, card: card)
                    }
                }
                Section("関心のある更新") {
                    ForEach(value.items.filter { $0.source_type == "news" }) { card in
                        SecretaryCardRow(store: store, card: card)
                    }
                    Text("今回のおすすめはここまでです。必要なときにニュース一覧をご利用ください。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Section("取得できている情報") {
                    ForEach(value.sources) { source in
                        VStack(alignment: .leading, spacing: 4) {
                            Text("\(source.label) · \(source.stateLabel)")
                            if let date = lifeDate(source.last_success_at) { Text("最終成功 \(date.formatted())").font(.caption) }
                            Text(source.detail).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    Text("全アプリの閲覧時間や、録音・入力されていない約束は取得していません。GPS・健康から無駄な時間や忘れを判定しません。")
                        .font(.caption)
                }
                if let context = store.snapshot?.device_context {
                    Section("カレンダー上の着手候補") {
                        ForEach(context.suggestions) { slot in
                            NavigationLink { LifeEntryDetail(store: store, entryID: slot.task_id) } label: {
                                Text("\(slot.title) · \(lifeDate(slot.start_at)?.formatted() ?? "")から20分")
                            }
                        }
                        Text("予定のない時間の候補です。20分での完了や、実際に空いていることは保証しません。")
                            .font(.caption)
                    }
                    Section("予定の重なり") {
                        ForEach(context.conflicts) { item in
                            Text("\(item.title) / \(item.calendar_title)")
                        }
                    }
                }
                SecretaryWeeklySection(store: store, weekly: value.weekly)
            }
            if let error = store.error { Text(error).foregroundStyle(.red) }
        }.navigationTitle("暮らしの秘書").refreshable { await store.refresh() }
    }
}

struct SecretaryWeeklySection: View {
    @ObservedObject var store: LifeStore
    let weekly: SecretaryWeekly
    var body: some View {
        Section("今週の振り返り") {
            Text("\(weekly.start_date)〜\(weekly.end_date) · \(weekly.timezone)").font(.caption)
            Text("自己記録 \(weekly.recorded_days)日")
            metric("巡回・比較", weekly.browsing_minutes, unit: "分")
            metric("用事の管理", weekly.management_minutes, unit: "分")
            metric("忘れた約束", weekly.forgotten_count, unit: "件")
            Text("調査の評価 \(weekly.research_evaluations)件 · 役立った \(weekly.research_useful)件")
            if let minutes = weekly.saved_minutes.total {
                Text("調査で省けた時間（本人の見積もり）\(minutes)分 · 入力\(weekly.saved_minutes.count)件")
            } else { Text("調査で省けた時間：未入力") }
            NavigationLink { SecretaryDayEditor(store: store, weekly: weekly) } label: {
                Label("今日の短い記録（任意）", systemImage: "square.and.pencil")
            }
            Text("空欄は0とみなしません。自己記録と見積もりは別に表示し、改善率は推定しません。楽しみの閲覧は巡回時間から除いてください。")
                .font(.caption).foregroundStyle(.secondary)
        }
    }
    private func metric(_ label: String, _ value: SecretaryMetric, unit: String) -> some View {
        Text(value.total.map { "\(label) \($0)\(unit) · 入力\(value.count)日" } ?? "\(label)：未入力")
    }
}

struct SecretaryDayEditor: View {
    @ObservedObject var store: LifeStore
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    let weekly: SecretaryWeekly
    @State private var browsing = ""
    @State private var management = ""
    @State private var forgotten = ""
    @State private var requestID = UUID().uuidString
    @State private var saving = false
    @State private var message: String?
    var body: some View {
        Form {
            Text("\(weekly.end_date) · \(weekly.timezone)").font(.caption)
            Text("減らしたい巡回・比較、用事の管理に使った時間だけを記録します。すべて任意です。")
            TextField("巡回・比較（分）", text: $browsing)
            TextField("用事の管理（分）", text: $management)
            TextField("忘れた約束（件）", text: $forgotten)
            Text("空欄は未入力、0は実際に0だった場合です。期限超過だけでは忘れた件数に含めません。")
                .font(.caption)
            if let message { Text(message).foregroundStyle(.red) }
            Button(saving ? "保存中…" : "保存") { save() }.disabled(saving || model.isFixture)
        }.navigationTitle("今日の記録")
            .onAppear {
                browsing = weekly.today?.browsing_minutes.map(String.init) ?? ""
                management = weekly.today?.management_minutes.map(String.init) ?? ""
                forgotten = weekly.today?.forgotten_count.map(String.init) ?? ""
            }
            .onChange(of: browsing) { _, _ in requestID = UUID().uuidString }
            .onChange(of: management) { _, _ in requestID = UUID().uuidString }
            .onChange(of: forgotten) { _, _ in requestID = UUID().uuidString }
    }
    private func save() {
        let fields = [browsing, management, forgotten].map { $0.trimmingCharacters(in: .whitespaces) }
        for (index, text) in fields.enumerated() where !text.isEmpty {
            guard let number = Int(text), (0...(index == 2 ? 100 : 1440)).contains(number) else {
                message = "分数は0〜1440、忘れ件数は0〜100で入力してください。"; return
            }
        }
        let request = SecretaryDayRequest(request_id: requestID, day: weekly.end_date, timezone: weekly.timezone,
            revision: weekly.today?.revision ?? 0, browsing_minutes: Int(fields[0]),
            management_minutes: Int(fields[1]), forgotten_count: Int(fields[2]))
        saving = true
        Task {
            do { try await store.saveDay(request); dismiss() }
            catch { message = error.localizedDescription }
            saving = false
        }
    }
}

struct LifeAssistantView: View {
    @ObservedObject var store: LifeStore
    @EnvironmentObject private var model: AppModel
    @AppStorage("life.calendar.automatic") private var calendarAutomatic = true
    @State private var captureText = ""
    @State private var captureID = UUID().uuidString
    @State private var capturing = false
    @State private var captureMessage = ""
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
                Text("録音・メモから用事を自動整理します。確認が必要な項目だけここに残ります。")
                if let policy = store.snapshot?.automation {
                    Toggle("新しい録音・メモを自動整理", isOn: Binding(get: { policy.enabled }, set: { value in Task { await store.setAutomation("enabled", value) } }))
                    Toggle("明示した調べものを自動実行", isOn: Binding(get: { policy.research_enabled }, set: { value in Task { await store.setAutomation("research_enabled", value) } }))
                    Text("新規録音の文字起こし・日時・話者をCodexへ送り、明確な用事は自動追加します。自動Web調査は公開用の質問だけを1日3件まで。停止中の取り込みは再開後に処理します。開始済みの調査は個別に停止できます。原音・GPSは送りません。").font(.caption)
                }
                if let error = store.error { Text(error).foregroundStyle(.red) }
                Text(store.noticeStatus).font(.caption).foregroundStyle(.secondary)
                Button("通知を有効にする") { Task {
                    do { _ = try await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .sound, .badge]); await store.refresh() }
                    catch { store.error = error.localizedDescription }
                } }
            }
            Section("iPhoneから取得した情報") { PhoneContextPanel(overview: store.snapshot?.device_context, onSync: { await store.refresh(afterMutation: true) }) }
            Section("一文で追加") {
                TextField("例：明日の午前中に資料を確認する。週末の近所の催しを調べて", text: $captureText, axis: .vertical).lineLimit(2...6)
                    .disabled(capturing)
                    .onChange(of: captureText) { _, _ in if !capturing { captureID = UUID().uuidString } }
                Button(capturing ? "送信中…" : "メモを自動整理") {
                    capturing = true
                    Task {
                        do {
                            let _: EmptyResponse = try await APIClient.shared.post("api/life/capture", body: ["text": captureText, "request_id": captureID], as: EmptyResponse.self)
                            captureText = ""; captureID = UUID().uuidString
                            captureMessage = "保存しました。Mac側で整理を進めています。"
                            await store.refresh(afterMutation: true)
                        } catch { captureMessage = error.localizedDescription }
                        capturing = false
                    }
                }.disabled(capturing || captureText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || store.snapshot?.automation?.enabled != true)
                Text(captureMessage).font(.caption)
            }
            if let drafts = store.snapshot?.drafts, !drafts.isEmpty {
                Section("ここだけ確認（\(drafts.count)件）") {
                    ForEach(drafts) { draft in
                        VStack(alignment: .leading, spacing: 8) {
                            Text("\(lifeLabel(draft.data.kind)): \(draft.data.title)").font(.headline)
                            Text(draft.reason).font(.caption).foregroundStyle(.secondary)
                            if let quote = draft.evidence.quotes?.first {
                                Text("「\(quote.quote)」").font(.caption).lineLimit(3)
                            }
                            if let subject = draft.evidence.subject, !subject.isEmpty { Text("対象者の候補: \(subject)").font(.caption) }
                            NavigationLink("入力済みの内容を確認・補足") {
                                LifeEntryEditor(store: store, request: draftRequest(draft), draftID: draft.id, reviewReason: draft.reason)
                            }
                            HStack {
                                Button("この内容で追加") { Task { await store.adopt(draft) } }
                                    .disabled(!canAdopt(draft))
                                Spacer()
                                Button("不要", role: .destructive) { Task { await store.dismiss(draft) } }
                            }
                        }
                    }
                }
            }
            Section("カレンダー連携") {
                Toggle("予定の追加・変更・中止を自動反映", isOn: $calendarAutomatic)
                    .onChange(of: calendarAutomatic) { _, _ in Task { await store.refresh() } }
                Text(store.calendarStatus).font(.caption)
                Button("カレンダーへのアクセスを許可") { Task {
                    do { try await LifeCalendar.shared.requestAccess(); await store.refresh() }
                    catch { store.error = error.localizedDescription }
                } }
                Text("初回の許可後は、この端末の更新時に反映します。カレンダー側で変更した予定は上書きせず確認を求めます。").font(.caption)
            }
            Section("項目を指定して追加") {
                Text("時刻のないタスクは期限日の23:59、通知は9:00に設定します。時刻指定のタスクは期限時刻に通知します。予定の通知は30分前、準備は前日が初期値です。詳細で変更できます。").font(.caption).foregroundStyle(.secondary)
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
                            Text("\(lifeLabel(entry.kind)) · \(lifeLabel(entry.status))\(entry.automatic == true ? " · 自動作成" : "")\(entry.expired ? " · 期限切れ" : "")").font(.caption).foregroundStyle(.secondary)
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
                        personName = ""; await store.refresh(afterMutation: true)
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
                await store.refresh()
            }
    }
    private func canAdopt(_ draft: LifeDraft) -> Bool {
        if draft.data.kind == "profile" { return !(draft.data.person_id ?? "").isEmpty }
        if draft.data.kind == "event" { return draft.data.start_at != nil && draft.data.end_at != nil }
        return true
    }
    private func draftRequest(_ draft: LifeDraft) -> LifeRequest {
        var value = LifeRequest(draft.data); value.revision = nil
        value.source_type = draft.evidence.type
        value.source_id = draft.evidence.item_id ?? draft.evidence.entry_id ?? ""
        value.source_index = draft.source_index
        return value
    }

}

struct LifeEntryEditor: View {
    @ObservedObject var store: LifeStore
    @Environment(\.dismiss) private var dismiss
    @State var request: LifeRequest
    var entryID: String? = nil
    var draftID: String? = nil
    var reviewReason = ""
    @State private var saving = false
    @State private var error: String?
    var body: some View {
        Form {
            if !reviewReason.isEmpty { Text(reviewReason).font(.caption).foregroundStyle(.orange) }
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
                    if request.start_at == nil {
                        LifeOptionalDate(label: "開始日時を指定", value: $request.start_at)
                    } else { DatePicker("開始", selection: dateBinding($request.start_at)) }
                    if request.end_at == nil {
                        LifeOptionalDate(label: "終了日時を指定", value: $request.end_at, defaultDate: (lifeDate(request.start_at) ?? .now).addingTimeInterval(3600))
                    } else { DatePicker("終了", selection: dateBinding($request.end_at, offset: 3600)) }
                    TextField("タイムゾーン", text: $request.timezone)
                    TextField("場所", text: $request.location)
                    TextField("準備すること", text: $request.preparation, axis: .vertical)
                    LifeOptionalDate(label: "申し込み期限", value: $request.deadline_at)
                    LifeOptionalDate(label: "準備", value: $request.prepare_at)
                    LifeOptionalDate(label: "出発", value: $request.depart_at)
                    LifeOptionalDate(label: "予定の通知", value: $request.remind_at)
                    Text("関連する未完了の準備・申込タスクの期限も更新します。タスク側で個別に変更した期限は保ちます。移動時間からの自動計算は行いません。出発時刻を確認して指定してください。自動連携が有効なら、保存後にカレンダーへ反映します。")
                        .font(.caption)
                }
            }
            if let error { Text(error).foregroundStyle(.red) }
            Button(request.kind == "research" ? "この内容で調査開始" : "確認して保存") {
                saving = true
                Task {
                    do {
                        try await store.save(request, id: entryID, draftID: draftID); dismiss()
                    } catch { self.error = error.localizedDescription }
                    saving = false
                }
            }.disabled(saving || request.title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || (request.kind == "profile" && request.person_id.isEmpty) || (request.kind == "event" && (request.start_at == nil || request.end_at == nil)))
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
    var defaultDate: Date = .now.addingTimeInterval(3600)
    var body: some View {
        Toggle(label, isOn: Binding(get: { value != nil }, set: { value = $0 ? defaultDate.ISO8601Format() : nil }))
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
                        Text(calendarMessage.isEmpty ? "自動連携が有効なら更新時に反映します。必要なときはこのボタンで再反映できます。申し込み自体は出典サイトで行ってください。" : calendarMessage).font(.caption)
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
                        await store.refresh(afterMutation: true); dismiss()
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

actor LifeCalendar {
    static let shared = LifeCalendar()
    private lazy var store = EKEventStore()
    private var synchronizing = false
    private var candidateOffset = 0
    private struct Synced: Codable { let revision: Int; let signature: String }
    private func state(_ id: String) -> Synced? {
        UserDefaults.standard.data(forKey: "life.calendar.state.\(id)").flatMap { try? JSONDecoder().decode(Synced.self, from: $0) }
    }
    private func remember(_ entry: LifeEntry, event: EKEvent?) throws {
        let value = Synced(revision: entry.revision, signature: event.map(signature) ?? "cancelled")
        UserDefaults.standard.set(try JSONEncoder().encode(value), forKey: "life.calendar.state.\(entry.id)")
    }
    private func signature(_ event: EKEvent) -> String {
        [event.title ?? "", String(event.startDate.timeIntervalSince1970), String(event.endDate.timeIntervalSince1970),
         event.location ?? "", event.notes ?? "", event.url?.absoluteString ?? "",
         event.timeZone?.identifier ?? "", (event.alarms ?? []).map { String($0.absoluteDate?.timeIntervalSince1970 ?? $0.relativeOffset) }.joined(separator: ",")].joined(separator: "\u{001f}")
    }
    func requestAccess() async throws {
        guard try await store.requestFullAccessToEvents() else { throw APIClientError.server("カレンダーへのアクセスを許可してください") }
    }
    func synchronize(_ entries: [LifeEntry]) async -> String {
        guard UserDefaults.standard.object(forKey: "life.calendar.automatic") as? Bool != false else { return "自動反映は停止しています。必要な予定だけ手動で反映できます。" }
        guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else { return "初回のカレンダー許可後に、自動反映を始めます。" }
        guard !synchronizing else { return "カレンダーへ反映中…" }
        synchronizing = true; defer { synchronizing = false }
        var updated = 0; var problems: [String] = []
        let candidates = entries.filter { entry in
            guard entry.kind == "event", state(entry.id)?.revision != entry.revision else { return false }
            return ["planned", "registered"].contains(entry.status) || UserDefaults.standard.string(forKey: "life.calendar.\(entry.id)") != nil
        }
        let offset = candidates.isEmpty ? 0 : candidateOffset % candidates.count
        let batch = (Array(candidates.dropFirst(offset)) + Array(candidates.prefix(offset))).prefix(10)
        candidateOffset = offset + batch.count
        for entry in batch {
            if Task.isCancelled { break }
            do { _ = try await apply(entry, automatic: true); updated += 1 }
            catch { problems.append("\(entry.title): \(error.localizedDescription)") }
        }
        if !problems.isEmpty { return problems.joined(separator: "\n") }
        return "カレンダー自動反映: 最新です" + (updated > 0 ? "（\(updated)件更新）" : "") + (candidates.count > 10 ? "。残りは次の更新で反映します。" : "")
    }
    func apply(_ entry: LifeEntry, automatic: Bool = false) async throws -> String {
        if !automatic { try await requestAccess() }
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
        if automatic {
            let collisions = store.events(matching: store.predicateForEvents(withStart: start, end: end, calendars: nil)).filter {
                $0.url != marker && $0.status != .canceled && $0.startDate < end && $0.endDate > start
            }
            if collisions.contains(where: { ($0.title ?? "").trimmingCharacters(in: .whitespacesAndNewlines) == entry.title && $0.startDate == start && $0.endDate == end }) {
                throw APIClientError.server("同じ予定がカレンダーに存在します。重複して追加せず、元の予定を確認してください")
            }
            if collisions.contains(where: { $0.availability != .free && (!$0.isAllDay || [.busy, .tentative, .unavailable].contains($0.availability)) }) {
                throw APIClientError.server("カレンダーの別の予定と重なります。予定詳細から確認して反映してください")
            }
        }
        let saved = state(entry.id)
        let owned = existing ?? matches.first
        if automatic {
            if let owned, let saved, signature(owned) != saved.signature {
                throw APIClientError.server("カレンダー側で変更されています。予定詳細から確認して再反映してください")
            }
            if owned != nil && saved == nil {
                throw APIClientError.server("以前に登録した予定です。予定詳細から一度反映して自動連携を開始してください")
            }
            if identifier != nil && owned == nil && entry.status != "cancelled" {
                throw APIClientError.server("カレンダー側で削除されています。再作成する場合は予定詳細から反映してください")
            }
        }
        try Task.checkCancellation()
        if automatic {
            guard UserDefaults.standard.object(forKey: "life.calendar.automatic") as? Bool != false,
                  EKEventStore.authorizationStatus(for: .event) == .fullAccess else {
                throw APIClientError.server("カレンダーの自動反映は停止しています")
            }
        }
        if entry.status == "cancelled" {
            if let owned { try store.remove(owned, span: .thisEvent, commit: true) }
            UserDefaults.standard.removeObject(forKey: key)
            try remember(entry, event: nil)
            return "関連する予定をカレンダーから削除しました"
        }
        let event = owned ?? EKEvent(eventStore: store)
        if event.calendar == nil { event.calendar = store.defaultCalendarForNewEvents }
        guard event.calendar?.allowsContentModifications == true else { throw APIClientError.server("書き込みできる標準カレンダーを選択してください") }
        event.title = entry.title; event.startDate = start; event.endDate = end
        event.timeZone = TimeZone(identifier: entry.timezone ?? "Asia/Tokyo")
        event.location = entry.location; event.notes = entry.detail + "\n" + (entry.preparation ?? "") + "\n" + (entry.source_url ?? "")
        event.url = marker
        event.alarms = lifeDate(entry.remind_at).map { [EKAlarm(absoluteDate: $0)] } ?? []
        try store.save(event, span: .thisEvent, commit: true)
        UserDefaults.standard.set(event.eventIdentifier, forKey: key)
        try remember(entry, event: event)
        return "カレンダーに反映しました"
    }
}

@MainActor
final class LifeNotifications {
    static let shared = LifeNotifications()
    private let center = UNUserNotificationCenter.current()
    private var reconciliation: Task<String, Never>?
    private var reconciliationID = 0

    func reconcile(_ snapshot: LifeSnapshot) async -> String {
        // A replaced read may still be finishing an OS notification request.
        // Serialize reconciliation so an older add cannot land after a newer
        // snapshot has removed it. The UI remains free while the OS responds.
        let previous = reconciliation
        reconciliationID += 1
        let id = reconciliationID
        let task = Task {
            _ = await previous?.value
            guard !Task.isCancelled else { return "通知の更新を中断しました" }
            return await reconcileNow(snapshot)
        }
        reconciliation = task
        let result = await withTaskCancellationHandler { await task.value } onCancel: { task.cancel() }
        if id == reconciliationID { reconciliation = nil }
        return result
    }

    private func reconcileNow(_ snapshot: LifeSnapshot) async -> String {
        let settings = await center.notificationSettings()
        guard [.authorized, .provisional].contains(settings.authorizationStatus) else { return "通知は未許可です。予定を保存しても通知されません。" }
        let future = snapshot.notifications.filter { (lifeDate($0.at) ?? .distantPast) > .now }
        let selected = Array(future.prefix(40))
        let identifiers = Set(selected.map(\.id))
        let pending = await center.pendingNotificationRequests()
        center.removePendingNotificationRequests(withIdentifiers: pending.filter { $0.identifier.hasPrefix("life.") && !identifiers.contains($0.identifier) }.map(\.identifier))
        do {
            for notice in selected {
                try Task.checkCancellation()
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
