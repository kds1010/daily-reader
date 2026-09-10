import SwiftUI
import MapKit
import UniformTypeIdentifiers
#if os(macOS)
import AppKit
#endif

private struct AppContentScaleKey: EnvironmentKey {
    static let defaultValue: CGFloat = 1
}

extension EnvironmentValues {
    var appContentScale: CGFloat {
        get { self[AppContentScaleKey.self] }
        set { self[AppContentScaleKey.self] = newValue }
    }
}

enum AppTypography {
    static func font(
        for style: Font.TextStyle,
        scale: CGFloat,
        weight: Font.Weight? = nil,
        monospacedDigit: Bool = false
    ) -> Font {
        #if os(macOS)
        let preferred = NSFont.preferredFont(forTextStyle: nsTextStyle(for: style), options: [:])
        let scaled = NSFont(
            descriptor: preferred.fontDescriptor,
            size: preferred.pointSize * scale
        ) ?? preferred
        var result = Font(scaled)
        if let weight { result = result.weight(weight) }
        #else
        var result = Font.system(style, weight: weight)
        #endif
        if monospacedDigit { result = result.monospacedDigit() }
        return result
    }

    #if os(macOS)
    private static func nsTextStyle(for style: Font.TextStyle) -> NSFont.TextStyle {
        switch style {
        case .largeTitle: .largeTitle
        case .title: .title1
        case .title2: .title2
        case .title3: .title3
        case .headline: .headline
        case .subheadline: .subheadline
        case .callout: .callout
        case .footnote: .footnote
        case .caption: .caption1
        case .caption2: .caption2
        default: .body
        }
    }
    #endif
}

private struct AppFontModifier: ViewModifier {
    @Environment(\.appContentScale) private var scale

    let style: Font.TextStyle
    let weight: Font.Weight?
    let monospacedDigit: Bool

    func body(content: Content) -> some View {
        content.font(
            AppTypography.font(
                for: style,
                scale: scale,
                weight: weight,
                monospacedDigit: monospacedDigit
            )
        )
    }
}

extension View {
    func appFont(
        _ style: Font.TextStyle,
        weight: Font.Weight? = nil,
        monospacedDigit: Bool = false
    ) -> some View {
        modifier(AppFontModifier(style: style, weight: weight, monospacedDigit: monospacedDigit))
    }
}

#if DEBUG
#if os(iOS)
struct DaymeldRootPreview: PreviewProvider {
    static var previews: some View {
        RootView()
            .environmentObject(AppModel(fixture: .scenario(.standard)))
    }
}
#else
struct DaymeldRootPreview: PreviewProvider {
    static var previews: some View {
        RootView()
            .environmentObject(AppModel(fixture: .scenario(.standard)))
            .environmentObject(MacAgentKeyboardController())
            .frame(width: 1120, height: 760)
    }
}
#endif
#endif

struct RootView: View {
    @ObservedObject private var connections = ConnectionAlerts.shared
    @ObservedObject private var conversationImports = ConversationImports.shared
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    #if os(macOS)
    @EnvironmentObject private var macAgentKeyboard: MacAgentKeyboardController
    #endif

    var body: some View {
        TabView(selection: $model.selectedTab) {
            NavigationStack { AgentView() }.tabItem { Label("Agent", systemImage: "sparkles") }.tag(0)
            NavigationStack { TodayView() }.tabItem { Label("今日", systemImage: "checkmark.circle") }.tag(1)
            NavigationStack { ConversationsView() }.tabItem { Label("会話", systemImage: "waveform") }.tag(4)
            NavigationStack { SoanView() }.tabItem { Label("資料", systemImage: "doc.text") }.tag(5)
            NavigationStack { EmailView() }.tabItem { Label("メール", systemImage: "envelope") }.badge(model.emails.count).tag(2)
            NavigationStack { NewsView() }.tabItem { Label("ニュース", systemImage: "newspaper") }.tag(3)
            NavigationStack { SettingsView() }.tabItem { Label("設定", systemImage: "gearshape") }.tag(6)
        }
        .tint(.mint)
        .sheet(item: $connections.destination) { destination in
            ConnectionAlertsView(connections: connections, selectedAlertID: destination.alertID, isFixture: model.isFixture)
        }
        .onReceive(connections.$destination) { destination in
            if destination != nil { model.selectedTab = 4 }
        }
        .task(id: scenePhase) {
            guard scenePhase == .active, !model.isFixture else { return }
            // Separate from Agent polling: a slow connection check never delays
            // task display, and resumes immediately when the app becomes active.
            while !Task.isCancelled {
                await connections.refresh()
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
            }
        }
        .alert("接続できませんでした", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("閉じる", role: .cancel) {}
        } message: { Text(model.errorMessage ?? "") }
        .task(id: scenePhase) {
            guard scenePhase == .active, !model.isFixture else { return }
            while !Task.isCancelled {
                await model.pollDaymeldAgents()
                do { try await Task.sleep(for: .seconds(5)) } catch { return }
            }
        }
        .task(id: scenePhase) {
            guard scenePhase == .active, !model.isFixture else { return }
            while !Task.isCancelled {
                await model.pollTanomiTasks()
                do { try await Task.sleep(for: .seconds(5)) } catch { return }
            }
        }
        .task(id: scenePhase) {
            guard scenePhase == .active, !model.isFixture else { return }
            while !Task.isCancelled {
                // Initial full refresh already loads these. Keep slow work out
                // of the five-second task/notification loop.
                do { try await Task.sleep(for: .seconds(30)) } catch { return }
                async let life: Void = model.life.refresh()
                async let metadata: Void = model.refreshTanomiMetadata()
                _ = await (life, metadata)
            }
        }
        #if os(iOS)
        .task(id: scenePhase) {
            guard scenePhase == .active, !model.isFixture else { return }
            while !Task.isCancelled {
                await model.deviceLocation.syncPending()
                do { try await Task.sleep(for: .seconds(5)) } catch { return }
            }
        }
        #endif
        .onReceive(NotificationCenter.default.publisher(for: .openLifeFromNotification)) { _ in
            model.selectedTab = 1
            Task { await model.life.refresh() }
        }
        .onReceive(NotificationCenter.default.publisher(for: .openAgentFromNotification)) { _ in
            model.selectedTab = 0
        }
        .task(id: scenePhase) {
            guard scenePhase == .active, !model.isFixture else { return }
            await conversationImports.resume()
        }
        .onReceive(conversationImports.$openRequest) { request in
            if request > 0, !model.isFixture { model.selectedTab = 4 }
        }
        .onChange(of: conversationImports.completedCount) { _, _ in
            guard !model.isFixture else { return }
            Task { await model.refreshConversations(afterMutation: true) }
        }
        #if os(macOS)
        .onAppear { macAgentKeyboard.isEnabled = model.selectedTab == 0 }
        .onChange(of: model.selectedTab) { _, tab in
            macAgentKeyboard.isEnabled = tab == 0
        }
        #endif
    }
}

private struct ConnectionAlertsView: View {
    @ObservedObject var connections: ConnectionAlerts
    let selectedAlertID: String?
    let isFixture: Bool
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            List {
                Section("認証・接続の確認") {
                    if let error = connections.loadError {
                        Text(error).foregroundStyle(.orange)
                    }
                    if connections.alerts.isEmpty {
                        if connections.hasLoaded {
                            Text(connections.loadError == nil ? "現在、対応が必要な接続アラートはありません。" : "前回の確認時には、対応が必要な接続アラートはありませんでした。")
                                .foregroundStyle(.secondary)
                        } else if connections.loadError == nil {
                            ProgressView("接続状況を確認しています。")
                        }
                    }
                    ForEach(connections.alerts.sorted { ($0.id == selectedAlertID ? 0 : 1) < ($1.id == selectedAlertID ? 0 : 1) }) { alert in
                        VStack(alignment: .leading, spacing: 8) {
                            Label(alert.providerName, systemImage: "exclamationmark.triangle.fill")
                                .foregroundStyle(.orange).appFont(.caption)
                            Text(verbatim: alert.title).appFont(.headline)
                            Text(verbatim: alert.message)
                            Text(alert.guidance).appFont(.subheadline).foregroundStyle(.secondary)
                        }.padding(.vertical, 4)
                    }
                    Button("接続状況を再確認") { Task { await connections.refresh() } }
                        .disabled(isFixture)
                }
                Section("この端末への通知") {
                    Text(permissionDescription)
                    if let error = connections.deliveryError { Text(error).foregroundStyle(.orange) }
                    if connections.notificationPermission == .notDetermined {
                        Button("通知を許可する") { Task { await connections.requestPermission() } }
                            .disabled(isFixture)
                    }
                    #if os(iOS)
                    Button("iPhoneの通知設定を開く") {
                        guard let url = URL(string: UIApplication.openNotificationSettingsURLString) else { return }
                        UIApplication.shared.open(url)
                    }.disabled(isFixture)
                    Text("アプリを開いた時と、iOSが許可するバックグラウンド更新時に確認します。閉じている間や強制終了中に、すぐ届くことは保証されません。Mac miniへの接続も必要です。")
                        .appFont(.caption).foregroundStyle(.secondary)
                    #else
                    Text("表示方法はシステム設定 → 通知 → Daymeldで変更できます。アプリを開いて更新した時に確認するため、終了中の即時通知はありません。Mac miniへの接続も必要です。")
                        .appFont(.caption).foregroundStyle(.secondary)
                    #endif
                    Text("同じ障害は一度だけ通知し、復旧後に再発した場合は再び通知します。通知が許可されていなくても、この一覧で確認できます。")
                        .appFont(.caption).foregroundStyle(.secondary)
                }
            }
            .navigationTitle("接続と通知")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("閉じる") { dismiss() } } }
            .task {
                guard !isFixture else { return }
                await connections.refreshPermission()
                await connections.refresh()
            }
        }
        #if os(macOS)
        .frame(minWidth: 520, minHeight: 480)
        #endif
    }

    private var permissionDescription: String {
        switch connections.notificationPermission {
        case .allowed: return "通知はOSで許可されています。表示方法は端末の設定に従います。"
        case .denied: return "通知は許可されていません。端末の設定から変更できます。"
        case .notDetermined: return "通知の許可がまだ設定されていません。"
        case .unknown: return "通知の許可状態を確認しています。"
        }
    }
}

struct ConversationsView: View {
    @ObservedObject private var connections = ConnectionAlerts.shared
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @ObservedObject private var imports = ConversationImports.shared
    @ObservedObject private var soundcore = SoundcoreImports.shared
    @State private var showsImport = false

    private var recordings: [ConversationRecording] { model.conversations.sorted(by: ConversationRecording.newestFirst) }
    private var isActive: Bool { scenePhase == .active && model.selectedTab == 4 && !model.isFixture }
    private var hasPendingProcessing: Bool {
        soundcore.hasPendingJobs || recordings.contains {
            ["pending", "queued", "analyzing"].contains($0.status)
                || ["queued", "extracting"].contains($0.insightStatus ?? "")
                || $0.isSummaryProcessing || $0.isCorrectionProcessing
        }
    }
    private func refreshConversationData() async {
        async let cloud: Void = soundcore.refresh()
        async let recordings: Void = model.refreshConversations()
        _ = await (cloud, recordings)
    }

    var body: some View {
        List {
            if !connections.alerts.isEmpty {
                Section {
                    Button { connections.open() } label: {
                        Label("取り込みの接続確認が必要（\(connections.alerts.count)件）", systemImage: "exclamationmark.triangle")
                            .foregroundStyle(.orange)
                    }
                }
            }
            if !imports.pending.isEmpty || !imports.failures.isEmpty || imports.storageError != nil || imports.unreadableCount > 0 || soundcore.jobs.contains(where: { $0.isPending || $0.status == "failed" }) {
                Section {
                    Button { showsImport = true } label: {
                        Label("取り込み中・要確認のデータがあります", systemImage: "arrow.down.circle")
                    }
                }
            }
            Section {
                NavigationLink { ConversationExtractionGuide() } label: {
                    Label("会話から何が見つかる？", systemImage: "sparkles")
                }
                NavigationLink {
                    ConversationVocabularyView(api: model.conversationAPI, isFixture: model.isFixture)
                } label: {
                    Label("文字起こしの用語辞書", systemImage: "character.book.closed")
                }
                NavigationLink { ConversationRecordingsMap(recordings: recordings) } label: {
                    Label("場所から会話を探す", systemImage: "map")
                }
            }
            Section("録音ごとの会話") {
                ResourceStatusView(state: model.conversationLoadState, label: "会話一覧") {
                    Task { await model.refreshConversations() }
                }
                if recordings.isEmpty && model.conversationLoadState == .loaded {
                    Text("会話はまだありません。取り込んだ録音を、日時・要点・抽出内容ごとに確認できます。")
                        .foregroundStyle(.secondary)
                    Button("録音を取り込む") { showsImport = true }
                }
                ForEach(recordings) { recording in
                    NavigationLink { ConversationDetailView(recordingID: recording.id) } label: {
                        ConversationOverviewCard(recording: recording)
                    }.padding(.vertical, 5)
                }
            }
        }
        .navigationTitle("会話")
        .toolbar {
            ToolbarItemGroup(placement: .primaryAction) {
                Button { connections.open() } label: { Image(systemName: "bell.badge") }
                    .accessibilityLabel("接続と通知を確認")
                Button { showsImport = true } label: { Label("取り込む", systemImage: "plus") }
            }
        }
        .sheet(isPresented: $showsImport) { ConversationImportView() }
        .refreshable { if !model.isFixture { await refreshConversationData() } }
        .task(id: "\(isActive)-\(soundcore.pollRevision)-\(imports.completedCount)") {
            guard isActive else { return }
            await refreshConversationData()
            while !Task.isCancelled, hasPendingProcessing {
                do { try await Task.sleep(for: .seconds(3)) } catch { return }
                await refreshConversationData()
            }
        }
    }
}

private struct ConversationImportView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @ObservedObject private var imports = ConversationImports.shared
    @ObservedObject private var soundcore = SoundcoreImports.shared
    @State private var importing = false
    var body: some View {
        NavigationStack {
            List {
            SoundcoreImportSection(imports: soundcore, isFixture: model.isFixture)
            Section {
                Button { importing = true } label: {
                    Label("MP3または文字起こしTXTを取り込む", systemImage: "square.and.arrow.down")
                }
                Text("音声の原本とTXTの原文はMac miniに保存され、自動削除されません。")
                    .appFont(.caption).foregroundStyle(.secondary)
                DisclosureGroup("MP3・TXTをファイルから取り込む") {
                    Text("共有リンクを使わない場合は、Soundcoreで録音をMP3として書き出し、共有先にDaymeldを選ぶと送信が始まります。")
                    Text("ショートカットに「MP3をDaymeldに取り込む」を追加し、入力を「ショートカットの入力」または書き出し済みMP3に設定できます。固定ファイルを指定してホーム画面に追加すれば、次回はそのボタンから取り込めます。")
                    Text("送信が終わるまでDaymeldを開いておいてください。失敗時は端末に保持し、再送できます。")
                }.appFont(.caption)
            }
            if !model.isFixture {
                if imports.unreadableCount > 0 {
                    Section {
                        Text("端末コピー\(imports.unreadableCount)件を読み込めませんでした。コピーは保持しています。元のMP3・TXTから再度取り込んでください。")
                            .appFont(.caption).foregroundStyle(.orange)
                    }
                }
                if let error = imports.storageError {
                    Section {
                        Text(error).foregroundStyle(.orange)
                        Button("送信待ちを再読み込み") { Task { await imports.resume() } }
                    }
                }
                if !imports.pending.isEmpty {
                    Section("Mac miniへ送信待ち（\(imports.pending.count)件）") {
                        ForEach(imports.pending) { entry in
                            VStack(alignment: .leading, spacing: 5) {
                                Text(entry.filename)
                                if imports.activeID == entry.id {
                                    ProgressView("送信中")
                                } else if let error = imports.failures[entry.id] {
                                    Text(error).appFont(.caption).foregroundStyle(.orange)
                                } else {
                                    Text("端末に保存済み・送信待ち").appFont(.caption).foregroundStyle(.secondary)
                                }
                            }
                        }
                        if !imports.failures.isEmpty {
                            Button("失敗したファイルを再送") { imports.retry() }
                                .disabled(imports.activeID != nil)
                        }
                    }
                }
            }
            }
            .navigationTitle("録音を取り込む")
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("閉じる") { dismiss() } } }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.mp3, .plainText], allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                Task { await model.importConversationFile(url) }
            } else if case .failure(let error) = result {
                model.errorMessage = error.localizedDescription
            }
        }
        }
        #if os(macOS)
        .frame(minWidth: 560, minHeight: 520)
        #endif
    }
}

private struct ConversationOverviewCard: View {
    let recording: ConversationRecording
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .firstTextBaseline) {
                Text(recording.displayDate).appFont(.headline)
                Spacer()
                if let duration = recording.durationSeconds ?? recording.transcriptionMetadata?.durationSeconds, duration.isFinite, duration > 0 {
                    Text("\(Int(duration) / 60)分\(Int(duration) % 60)秒").appFont(.caption)
                }
            }
            if recording.verifiedDate == nil, let imported = conversationDate(recording.createdAt) {
                Text("取り込み: \(imported.formatted(date: .abbreviated, time: .shortened))")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            if let summary = recording.summaryText {
                if let label = recording.previousSummaryLabel {
                    Text(label).appFont(.caption).foregroundStyle(.orange)
                }
                Text(verbatim: summary).appFont(.subheadline).lineLimit(4)
            } else {
                Text(recording.summaryStateLabel).appFont(.subheadline).foregroundStyle(.secondary)
            }
            if recording.summary?.scope == "chunked" {
                Text("区間ごとの要点").appFont(.caption2).foregroundStyle(.secondary)
            }
            let kinds = ConversationExtractionKind.allCases.filter { recording.extractionCount($0.rawValue) > 0 }
            if !kinds.isEmpty {
                Text(kinds.map { "\($0.label) \(recording.extractionCount($0.rawValue))" }.joined(separator: " · "))
                    .appFont(.caption, weight: .semibold).foregroundStyle(.mint)
            } else if recording.digest != nil && recording.insightStatus == "completed" {
                Text("抽出された項目はありません").appFont(.caption).foregroundStyle(.secondary)
            }
            ForEach(Array((recording.digest?.previewItems ?? []).prefix(3))) { item in
                Text("\(ConversationExtractionKind(rawValue: item.kind)?.label ?? item.kind): \(item.title)")
                    .appFont(.caption).lineLimit(1)
            }
            Label(recording.locationStateLabel, systemImage: recording.startLocationContext?.location == nil ? "location.slash" : "mappin.and.ellipse")
                .appFont(.caption).foregroundStyle(.secondary)
            if let warning = recording.summary?.qualityWarnings.first {
                Text(warning).appFont(.caption).foregroundStyle(.orange).lineLimit(2)
            }
            if recording.insightStatus == "failed" {
                Text("抽出処理に失敗しています。詳細で再試行できます。")
                    .appFont(.caption).foregroundStyle(.orange)
            }
            if recording.status == "failed" {
                Text("文字起こしに失敗しています。詳細で確認できます。")
                    .appFont(.caption).foregroundStyle(.orange)
            }
            if let correction = recording.correction,
               correction.isProcessing || ["failed", "stale"].contains(correction.status) {
                Text(correction.statusLabel).appFont(.caption).foregroundStyle(.secondary)
            }
            Text(recording.filename).appFont(.caption2).foregroundStyle(.tertiary).lineLimit(1)
        }
    }
}

private struct ConversationExtractionGuide: View {
    private let examples = ["task": "『明日、資料を送ります』→ 用事の候補", "follow_up": "『返事がなければ確認します』→ 連絡・確認の候補", "decision": "『今回はA案にしましょう』→ 決定事項", "idea": "『入力を自動化できそう』→ 改善案", "friction": "『毎回ログインするのが面倒』→ 困りごと", "research": "『この2製品の違いを調べたい』→ 調べもの", "event": "『金曜の15時に打ち合わせ』→ 予定の候補", "interest": "『最近、写真に興味がある』→ 関心の候補", "preference": "『静かな店が好き』→ 好みの候補"]
    var body: some View {
        List {
            Section {
                Text("以下は説明用の例です。実際の録音から抽出した内容ではありません。")
                Text("発言の根拠を残し、明言・推定・曖昧を区別します。日時や相手が不明な約束は、確認が必要な候補として扱います。")
                    .foregroundStyle(.secondary)
            }
            ForEach(ConversationExtractionKind.allCases) { kind in
                Section {
                    Label(kind.label, systemImage: kind.icon).appFont(.headline)
                    Text(kind.explanation)
                    Text(examples[kind.rawValue] ?? "").appFont(.subheadline).foregroundStyle(.secondary)
                }
            }
            Section {
                Text("関心・好みは発言に基づく候補です。声や場所だけで人物を同定せず、同じ話者名を別の録音へ自動で結び付けません。")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
        }.navigationTitle("会話から見つかること")
    }
}

private struct ConversationRecordingsMap: View {
    let recordings: [ConversationRecording]
    @State private var selectedID: String?
    private var located: [ConversationRecording] { recordings.filter { $0.startLocationContext?.state == "matched_estimate" && $0.startLocationContext?.location != nil } }
    var body: some View {
        List {
            if located.isEmpty {
                Text("GPSと照合できた会話はまだありません。日時不明や近いGPSがない会話も、会話一覧には表示されます。")
            } else {
                Map(selection: $selectedID) {
                    ForEach(located) { recording in
                        if let location = recording.startLocationContext?.location {
                            Marker(recording.displayDate, coordinate: CLLocationCoordinate2D(latitude: location.latitude, longitude: location.longitude))
                                .tag(recording.id)
                        }
                    }
                }.frame(height: 300)
                Text("録音開始付近の端末の位置です。会話全体の場所や人物を示すものではありません。")
                    .appFont(.caption).foregroundStyle(.secondary)
                if let selected = located.first(where: { $0.id == selectedID }) {
                    Section("選択した会話") {
                        NavigationLink { ConversationDetailView(recordingID: selected.id) } label: { ConversationOverviewCard(recording: selected) }
                    }
                }
                Section("GPSと照合した会話（\(located.count)件）") {
                    ForEach(located) { recording in
                        NavigationLink { ConversationDetailView(recordingID: recording.id) } label: { ConversationOverviewCard(recording: recording) }
                    }
                }
            }
            if recordings.count > located.count {
                Text("位置未照合の会話: \(recordings.count - located.count)件。会話一覧から内容を確認できます。")
                    .foregroundStyle(.secondary)
            }
        }.navigationTitle("場所と会話")
    }
}

private struct SoundcoreImportSection: View {
    @ObservedObject var imports: SoundcoreImports
    let isFixture: Bool

    var body: some View {
        Section("Soundcoreの共有リンクを取り込む") {
            TextField("Soundcoreの共有URLを貼り付け", text: $imports.draft)
                .autocorrectionDisabled()
                #if os(iOS)
                .textInputAutocapitalization(.never)
                .keyboardType(.URL)
                #endif
                .disabled(isFixture || imports.isSubmitting)
                .accessibilityLabel("Soundcore共有リンク")
            Button {
                Task { await imports.submit() }
            } label: {
                Label(imports.isSubmitting ? "Mac miniへ送信中…" : "共有リンクから取り込む", systemImage: "icloud.and.arrow.down")
            }
            .disabled(isFixture || imports.isSubmitting || imports.draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
            if !isFixture {
                if let error = imports.submissionError {
                    Text(error).appFont(.caption).foregroundStyle(.orange)
                }
                if let message = imports.acceptedMessage {
                    Text(message).appFont(.caption).foregroundStyle(.secondary)
                }
            }
            Text("Soundcoreで録音の共有リンクを作り、ここへ貼り付けてください。Mac miniがクラウドから音声原本を取得するため、MP3の書き出しやSoundcore側の文字起こしは不要です。")
                .appFont(.caption).foregroundStyle(.secondary)
            DisclosureGroup("取り込み後の流れ・ショートカット") {
                Text("受付後はアプリを閉じても、Mac miniで取得と文字起こしが続きます。録音日時とGPSを照合し、自動整理が有効ならタスク・予定・調べものなどを整理します。")
                Text("ショートカットの「SoundcoreリンクをDaymeldに取り込む」に共有URLを渡すこともできます。Mac miniでの受付が済むまでは接続が必要です。")
                Text("指定した共有リンクの録音だけを取得します。Soundcoreアカウント全体の自動同期ではありません。対応するのはspeaker-eu.eufylife.comの共有リンクです。")
            }.appFont(.caption)
        }
        if !isFixture {
            Section("クラウドからの取得状況") {
                ResourceStatusView(state: imports.loadState, label: "クラウド取り込み") {
                    Task { await imports.refresh() }
                }
                if imports.jobs.isEmpty && imports.loadState == .loaded {
                    Text("共有リンクの取り込みはまだありません。")
                        .appFont(.caption).foregroundStyle(.secondary)
                }
                ForEach(imports.jobs) { job in
                    VStack(alignment: .leading, spacing: 6) {
                        HStack {
                            if job.isPending { ProgressView().controlSize(.small) }
                            Text(job.statusLabel).appFont(.subheadline)
                        }
                        if let created = job.createdAt, let date = parseISOTimestamp(created) {
                            Text("受付: \(date.formatted(date: .abbreviated, time: .shortened))")
                                .appFont(.caption).foregroundStyle(.secondary)
                        }
                        if job.status == "completed" {
                            Text("音声の保存が完了しました。文字起こし・整理の状況は録音の詳細で確認してください。")
                                .appFont(.caption).foregroundStyle(.secondary)
                        }
                        if let error = job.error { Text(error).appFont(.caption).foregroundStyle(.orange) }
                        if let error = imports.retryErrors[job.id] { Text(error).appFont(.caption).foregroundStyle(.orange) }
                        if job.status == "failed" {
                            Button(imports.retryingIDs.contains(job.id) ? "再試行を送信中…" : "取得を再試行") {
                                Task { await imports.retry(job) }
                            }.disabled(imports.retryingIDs.contains(job.id))
                        }
                        if let recordingID = job.recordingID {
                            NavigationLink("録音と解析状況を確認") { ConversationDetailView(recordingID: recordingID) }
                        }
                    }
                }
            }
        }
    }
}

private func insightKindIcon(_ kind: String) -> String {
    switch kind {
    case "task": "checkmark.circle"
    case "follow_up": "arrow.turn.up.right"
    case "decision": "checkmark.seal"
    case "idea": "lightbulb"
    default: "exclamationmark.bubble"
    }
}

struct ConversationDetailView: View {
    @EnvironmentObject private var model: AppModel
    let recordingID: String
    @State private var recording: ConversationRecording?
    @State private var showExtractionConfirmation = false
    @State private var showTranscriptionConfirmation = false
    @State private var transcriptionInFlight = false
    @State private var extractionInFlight = false
    @State private var overviewInFlight = false
    @State private var selectedKind = ""

    var body: some View {
        List {
            if let error = model.conversationDetailErrors[recordingID] {
                Section {
                    Text(error).foregroundStyle(.orange)
                    Button("再読み込み") { Task { await reload() } }
                }
            }
            if let recording {
                let contexts = recording.locationContexts ?? recording.startLocationContext.map { [$0] } ?? []
                Section("話したこと") {
                    Text(recording.displayDate).appFont(.headline)
                    ConversationSummaryContent(recording: recording)
                    Button(recording.summaryText != nil ? "最新の要約を確認" : "要約を作成") {
                        Task {
                            guard !overviewInFlight else { return }
                            overviewInFlight = true
                            defer { overviewInFlight = false }
                            if await model.summarizeConversation(recordingID) { await reload(afterMutation: true) }
                        }
                    }
                    .disabled(model.isFixture || overviewInFlight || recording.status != "completed" || recording.isSummaryProcessing || recording.isCorrectionProcessing || ["queued", "extracting"].contains(recording.insightStatus ?? ""))
                    Text("要約だけを作成・更新します。本文が変わっていなければ保存済み要約を表示します。抽出済みの候補や追加済みの用事は変更しません。この録音の文字起こしをCodexへ渡し、原音・GPSは送りません。")
                        .appFont(.caption).foregroundStyle(.secondary)
                }
                Section("抽出された内容") {
                    Picker("種類", selection: $selectedKind) {
                        Text("すべて").tag("")
                        ForEach(ConversationExtractionKind.allCases) { kind in
                            Text("\(kind.label)（\(recording.currentInsightItems.filter { $0.kind == kind.rawValue }.count)）").tag(kind.rawValue)
                        }
                    }
                    if recording.currentInsightItems.isEmpty {
                        Text(recording.insightStatus == "completed" ? "抽出された項目はありません。要約と原文から会話の内容を確認できます。" : recording.insightStatus == "failed" ? "抽出処理に失敗しています。下の処理情報から再試行できます。" : "抽出結果はまだありません。処理状況は下で確認できます。")
                            .foregroundStyle(.secondary)
                    }
                    NavigationLink("抽出される種類と説明用の例") { ConversationExtractionGuide() }
                }
                ForEach(ConversationExtractionKind.allCases.filter { selectedKind.isEmpty || selectedKind == $0.rawValue }) { kind in
                    let items = recording.currentInsightItems.filter { $0.kind == kind.rawValue }
                    if !items.isEmpty {
                        Section("\(kind.label)（\(items.count)件）") {
                            ForEach(items) { item in
                                ConversationExtractedItemView(item: item, recording: recording) { await reload(afterMutation: true) }
                            }
                        }
                    }
                }
                Section("文字起こしの補正") {
                    NavigationLink {
                        ConversationCorrectionView(recording: recording)
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            Text("原文・補正内容・検証を確認")
                            Text(recording.correction?.statusLabel ?? "補正状態は未取得")
                                .appFont(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                Section("原文と根拠") {
                    NavigationLink("文字起こしの全文を読む（\(recording.utterances?.count ?? 0)発話）") {
                        ConversationTranscriptView(recording: recording)
                    }
                    Text("話者ラベルはこの録音内の区別です。声や場所だけで人物を同定しません。")
                        .appFont(.caption).foregroundStyle(.secondary)
                }
                Section("録音日時と場所") {
                    if let start = contexts.first(where: { $0.utteranceID == nil }) {
                        ConversationLocationSummary(link: start)
                        if let context = start.deviceContext { PhoneEvidenceView(evidence: context) }
                    } else {
                        Text("位置情報の紐付けをまだ確認できません。")
                    }
                    if contexts.contains(where: { $0.location != nil }) {
                        NavigationLink("録音に紐付いたGPSを地図で見る") {
                            ConversationLocationMap(links: contexts)
                        }
                    }
                    Text("前後5分以内・精度200 m以内のGPSを自動照合します。場所は端末の位置からの推定です。")
                        .appFont(.caption).foregroundStyle(.secondary)
                    Button("位置情報を再照合") {
                        Task {
                            do {
                                let _: EmptyResponse = try await APIClient.shared.post(
                                    "api/conversations/\(recordingID)/match-location",
                                    body: EmptyRequest(), as: EmptyResponse.self)
                                await reload(afterMutation: true)
                            } catch { model.errorMessage = error.localizedDescription }
                        }
                    }.disabled(model.isFixture)
                }
                Section("処理と取り込み情報") {
                    Text(recording.filename)
                    if recording.status == "failed" {
                        Text(recording.error ?? "解析に失敗しました").foregroundStyle(.orange)
                    }
                    if let name = recording.transcriptionMetadata?.model {
                        Text("文字起こしモデル: \(name)").appFont(.caption).foregroundStyle(.secondary)
                    }
                    if !recording.isTranscript {
                        Text(recording.transcriptionMetadata?.vocabularyUsageLabel ?? "用語ヒントの使用状況は未記録です")
                            .appFont(.caption).foregroundStyle(.secondary)
                        if let omitted = recording.transcriptionMetadata?.vocabularyOmittedCount, omitted > 0 {
                            Text("上限により \(omitted)件は今回未使用です。")
                                .appFont(.caption).foregroundStyle(.secondary)
                        }
                    }
                    ForEach(recording.transcriptionMetadata?.warnings ?? [], id: \.self) { warning in
                        Text(warning).appFont(.caption).foregroundStyle(.orange)
                    }
                    if recording.transcriptionNeedsReview == 1 {
                        Text("再解析後の自動整理は停止しています。Codexで整理した候補も、追加済みの用事との重複を確認してから保存してください。")
                            .appFont(.caption).foregroundStyle(.secondary)
                    }
                    if !recording.isTranscript {
                        if recording.status == "queued" || recording.status == "analyzing" {
                            HStack { ProgressView(); Text("Macで文字起こし・話者分離を処理しています…") }
                        } else {
                            Button("文字起こしを再実行") { showTranscriptionConfirmation = true }
                                .disabled(transcriptionInFlight || extractionInFlight || recording.isSummaryProcessing || recording.isCorrectionProcessing || recording.insightStatus == "queued" || recording.insightStatus == "extracting")
                        }
                    }
                    insightExtractionControls(recording)
                }.disabled(model.isFixture)
            } else if model.conversationDetailErrors[recordingID] == nil {
                ProgressView("会話を読み込んでいます…")
            }
        }
        .navigationTitle("会話の内容")
        .task(id: "\(recording?.status ?? ""):\(recording?.insightStatus ?? ""):\(recording?.summary?.status ?? ""):\(recording?.summary?.generationStatus ?? ""):\(recording?.correction?.status ?? "")") {
            while !Task.isCancelled {
                await reload()
                guard !model.isFixture, let recording,
                      recording.status == "queued" || recording.status == "analyzing"
                        || recording.insightStatus == "queued" || recording.insightStatus == "extracting"
                        || recording.isSummaryProcessing || recording.isCorrectionProcessing
                else { return }
                do { try await Task.sleep(for: .seconds(2)) }
                catch { return }
            }
        }
        .refreshable { await reload() }
        .confirmationDialog("文字起こしを再実行しますか？", isPresented: $showTranscriptionConfirmation, titleVisibility: .visible) {
            Button("Macで再実行") {
                Task {
                    guard !transcriptionInFlight else { return }
                    transcriptionInFlight = true
                    defer { transcriptionInFlight = false }
                    await model.analyzeConversation(recordingID)
                    await reload(afterMutation: true)
                }
            }
            Button("キャンセル", role: .cancel) {}
        } message: {
            Text("Macの現在の認識モデルで原音を解析します。長い録音は時間がかかります。成功時に本文・話者を更新し、旧本文はMac内の履歴に保持します。確認待ち候補は更新対象になります。保存済みの用事は保持し、再解析後の候補は重複を確認してから追加します。")
        }
        .confirmationDialog(
            "文字起こしをCodexで整理しますか？",
            isPresented: $showExtractionConfirmation,
            titleVisibility: .visible
        ) {
            Button("Codexで整理") { Task { await extractInsights() } }
            Button("キャンセル", role: .cancel) {}
        } message: {
            if recording?.transcriptionNeedsReview == 1 {
                Text("この録音の日時、話者、発話時刻、文字起こしだけをCodexへ渡し、確認候補として保存します。原音、GPS、ファイル名、ほかの録音は渡しません。追加済みの用事・調査との重複を確認してから保存してください。")
            } else {
                Text("この録音の日時、話者、発話時刻、文字起こしだけをCodexへ渡します。原音、GPS、ファイル名、ほかの録音は渡しません。自動整理が有効な場合、明確な用事はタスク化し、公開用に整理した明示的な調べものを自動実行します。曖昧な日時・人物は確認待ちになります。")
            }
        }
    }

    private func reload(afterMutation: Bool = false) async {
        if let value = await model.loadConversation(recordingID, afterMutation: afterMutation), !Task.isCancelled { recording = value }
    }

    @ViewBuilder
    private func insightExtractionControls(_ recording: ConversationRecording) -> some View {
        switch recording.insightStatus {
        case "queued", "extracting":
            HStack {
                ProgressView()
                Text("Codexが候補を整理しています…")
            }
        case "completed":
            Label("Codexによる整理が完了しました", systemImage: "checkmark.circle.fill")
                .foregroundStyle(.green)
        case "failed":
            Text(recording.insightError ?? "Codexによる整理に失敗しました。")
                .foregroundStyle(.orange)
            Button("Codex整理を再試行") { showExtractionConfirmation = true }
                .disabled(recording.status != "completed" || !model.conversationLLMAvailable || extractionInFlight || recording.isSummaryProcessing || recording.isCorrectionProcessing)
        default:
            Button("Codexでタスク・予定・関心などを整理") { showExtractionConfirmation = true }
                .disabled(recording.status != "completed" || !model.conversationLLMAvailable || extractionInFlight || recording.isSummaryProcessing || recording.isCorrectionProcessing)
            if !model.conversationLLMAvailable {
                Text("Mac miniでCodexへChatGPTログインすると利用できます。")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private func extractInsights() async {
        guard !extractionInFlight else { return }
        extractionInFlight = true
        defer { extractionInFlight = false }
        guard await model.extractConversationInsights(recordingID) else { return }
        await reload(afterMutation: true)
    }
}

private struct ConversationCorrectionView: View {
    @EnvironmentObject private var model: AppModel
    @State var recording: ConversationRecording
    @State private var starting = false
    private var canStart: Bool {
        !model.isFixture && recording.correction != nil && recording.status == "completed"
            && !starting && !recording.isCorrectionProcessing && !recording.isSummaryProcessing
            && !["queued", "extracting"].contains(recording.insightStatus ?? "")
    }
    private var actionLabel: String {
        switch recording.correction?.status {
        case "failed": return "補正を再試行"
        case "completed": return "最新の補正を確認"
        default: return "文脈による補正・検証を開始"
        }
    }
    var body: some View {
        List {
            if let error = model.conversationDetailErrors[recording.id] {
                Section {
                    Text(error).foregroundStyle(.orange)
                    Button("再読み込み") { Task { await reload() } }
                }
            }
            Section("補正の状態") {
                Text(recording.correction?.statusLabel ?? "補正状態を取得できません。サーバーの更新と接続を確認してください。")
                if let correction = recording.correction {
                    if correction.isProcessing { ProgressView() }
                    Text("補正 \(correction.correctedCount)件 · 原文維持 \(correction.retainedCount)件 · 要確認 \(correction.flaggedCount)件")
                        .appFont(.caption).foregroundStyle(.secondary)
                    if let completed = conversationDate(correction.completedAt) {
                        Text("前回完了: \(completed.formatted(date: .abbreviated, time: .shortened))")
                            .appFont(.caption).foregroundStyle(.secondary)
                    }
                    if correction.status == "failed", correction.canDisplayCorrections {
                        Text("今回の補正に失敗したため、同じ原文に対する前回の補正を表示しています。")
                            .appFont(.caption).foregroundStyle(.orange)
                    }
                    if let error = correction.error { Text(verbatim: error).foregroundStyle(.orange) }
                    if correction.automaticBlocked {
                        Text("補正後の自動処理は保留されています。原文と検証結果を確認してください。")
                            .appFont(.caption).foregroundStyle(.orange)
                    }
                }
                Button(actionLabel) {
                    Task {
                        guard canStart else { return }
                        starting = true
                        defer { starting = false }
                        if await model.correctConversation(recording.id) { await reload(afterMutation: true) }
                    }
                }.disabled(!canStart)
                Text("この録音の文字起こしと、Mac内の本人確認済みの関連する過去の文脈をCodexで確認します。原音・GPSは送りません。入力が同じなら保存済み補正を表示します。")
                    .appFont(.caption).foregroundStyle(.secondary)
                Text("第2段階で補正を提案し、第3段階でテキストと文脈の整合性を確認します。音声との照合や正しさの保証ではありません。確証がない箇所は原文を維持します。")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            if let correction = recording.correction {
                Section("補正の比較") {
                    Text("元の発話ID・時刻・GPSの対応を保持します。補正によって、話していない内容を新しい発言として追加しません。")
                        .appFont(.caption).foregroundStyle(.secondary)
                    if correction.canDisplayCorrections, let items = correction.items, !items.isEmpty {
                        ForEach(items) { item in
                            NavigationLink {
                                ConversationCorrectionItemView(recording: recording, item: item)
                            } label: {
                                VStack(alignment: .leading, spacing: 6) {
                                    Text(item.verificationLabel).appFont(.caption).foregroundStyle(.secondary)
                                    Text(verbatim: item.acceptedText ?? item.originalText).lineLimit(3)
                                }
                            }
                        }
                    } else {
                        Text(correction.status == "stale" ? "原文・本人訂正・参考情報が更新されたため、以前の補正文は表示していません。" : correction.status == "completed" ? "表示する変更はありません。原文を維持しています。" : "比較できる補正結果はまだありません。")
                            .foregroundStyle(.secondary)
                    }
                }
                Section("参照した過去の文脈") {
                    Text("過去の情報は表記を判断する参考です。この録音で発言された事実や人物同定の根拠にはしません。")
                        .appFont(.caption).foregroundStyle(.secondary)
                    if let contexts = correction.contexts, !contexts.isEmpty {
                        ForEach(contexts) { context in ConversationCorrectionContextView(context: context) }
                    } else {
                        Text(correction.contextMessage ?? "参照した過去の文脈はありません。")
                            .foregroundStyle(.secondary)
                    }
                }
            }
        }
        .navigationTitle("文字起こしの補正")
        .task(id: recording.correction?.status) {
            while !Task.isCancelled {
                await reload()
                guard !model.isFixture, recording.isCorrectionProcessing else { return }
                do { try await Task.sleep(for: .seconds(2)) } catch { return }
            }
        }
        .refreshable { await reload() }
    }
    private func reload(afterMutation: Bool = false) async {
        if let value = await model.loadConversation(recording.id, afterMutation: afterMutation), !Task.isCancelled { recording = value }
    }
}

private struct ConversationCorrectionItemView: View {
    let recording: ConversationRecording
    let item: ConversationCorrectionItem
    var body: some View {
        List {
            Section("元の文字起こし") { Text(verbatim: item.originalText).textSelection(.enabled) }
            if let corrected = item.acceptedText {
                Section("補正後") { Text(verbatim: corrected).textSelection(.enabled) }
            } else {
                Section("原文を維持") {
                    Text(item.verificationLabel)
                    if let proposed = item.proposedText, proposed != item.originalText {
                        Text("採用していない補正案").appFont(.caption).foregroundStyle(.secondary)
                        Text(verbatim: proposed).textSelection(.enabled)
                    }
                }
            }
            Section("変更理由と検証") {
                Text(item.verificationLabel)
                Text(verbatim: item.reason)
                Text("テキストと文脈の整合性確認であり、原音との照合ではありません。")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            Section("発言の根拠") {
                if let utterance = recording.utterances?.first(where: { $0.id == item.utteranceID }) {
                    ConversationEvidenceQuote(recording: recording, utteranceID: utterance.id, quote: item.originalText,
                                              speaker: utterance.speaker, startSeconds: utterance.startSeconds, position: 0)
                } else {
                    Text("現在の原文との対応を確認できません。保持された補正記録を表示しています。")
                }
            }
            Section("参考にした過去の文脈") {
                if item.contextIDs.isEmpty {
                    Text("過去の情報を根拠とする補正ではありません。")
                } else {
                    ForEach(item.contextIDs, id: \.self) { id in
                        if let context = recording.correction?.contexts?.first(where: { $0.id == id }) {
                            ConversationCorrectionContextView(context: context)
                        } else { Text("参照元の詳細を取得できませんでした。") }
                    }
                }
                Text("この参照元の情報を、この録音で話された内容として補いません。")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
        }.navigationTitle("原文と補正の比較")
    }
}

private struct ConversationCorrectionContextView: View {
    let context: ConversationCorrectionContext
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(context.sourceType == "confirmed_self_utterance" ? "本人として確認された過去の発言" : "補正の参考情報")
                .appFont(.caption).foregroundStyle(.secondary)
            Text(verbatim: context.title)
            if let date = conversationDate(context.recordedAt) {
                Text("参照元の録音: \(date.formatted(date: .abbreviated, time: .shortened))")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            if context.sourceType == "confirmed_self_utterance", context.recordingID != nil {
                NavigationLink("参照した発言を確認") { ConversationCorrectionSourceView(context: context) }
            }
        }
    }
}

private struct ConversationCorrectionSourceView: View {
    @EnvironmentObject private var model: AppModel
    let context: ConversationCorrectionContext
    @State private var recording: ConversationRecording?
    @State private var loaded = false
    var body: some View {
        List {
            Text(verbatim: context.title)
            Text("過去の参照元です。補正対象の録音で話された内容とは区別します。")
                .appFont(.caption).foregroundStyle(.secondary)
            if let recording,
               let utterance = recording.utterances?.first(where: { $0.id == context.sourceID }) {
                ConversationEvidenceQuote(recording: recording, utteranceID: utterance.id,
                                          quote: utterance.text, speaker: utterance.speaker,
                                          startSeconds: utterance.startSeconds, position: 0)
            } else if loaded {
                Text("参照した発言を取得できませんでした。録音の再解析や接続状態により、現在の原文と対応しない場合があります。")
                Button("再読み込み") { Task { await reload() } }
            } else { ProgressView("参照元を読み込んでいます…") }
        }
        .navigationTitle("補正の参照元")
        .task { await reload() }
    }
    private func reload() async {
        guard let id = context.recordingID else { loaded = true; return }
        recording = await model.loadConversation(id)
        loaded = true
    }
}

private struct ConversationSummaryContent: View {
    let recording: ConversationRecording
    var body: some View {
        if let summary = recording.summary, recording.summaryText != nil || (!summary.points.isEmpty && summary.status != "stale") {
            if let label = recording.previousSummaryLabel {
                Text(label).appFont(.caption).foregroundStyle(.orange)
            }
            if summary.scope == "chunked" {
                Text("長い録音を区間ごとに整理した要点です。")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            if summary.points.isEmpty, let text = recording.summaryText { Text(verbatim: text) }
            ForEach(Array(summary.points.enumerated()), id: \.offset) { _, point in
                NavigationLink {
                    ConversationSummaryEvidenceView(recording: recording, point: point)
                } label: {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(verbatim: point.text)
                        Label("この要点の根拠（\(point.evidence.count)件）", systemImage: "text.quote")
                            .appFont(.caption)
                    }
                }.padding(.vertical, 4)
            }
        } else {
            Text(recording.summaryStateLabel).foregroundStyle(.secondary)
        }
        ForEach(recording.summary?.qualityWarnings ?? [], id: \.self) { warning in
            Text(warning).appFont(.caption).foregroundStyle(.orange)
        }
    }
}

private struct ConversationSummaryEvidenceView: View {
    let recording: ConversationRecording
    let point: ConversationSummaryPoint
    var body: some View {
        List {
            Section("要点") {
                Text(verbatim: point.text)
            }
            Section("この要点の根拠（\(point.evidence.count)件）") {
                ForEach(Array(point.evidence.enumerated()), id: \.offset) { index, evidence in
                    ConversationEvidenceQuote(recording: recording, utteranceID: evidence.utteranceID,
                                              quote: evidence.quote, speaker: evidence.speaker,
                                              startSeconds: evidence.startSeconds, position: index,
                                              correctedQuote: evidence.correctedQuote, correctionRevisionID: evidence.correctionRevisionID,
                                              correctionIsSnapshot: evidence.correctionIsSnapshot == true)
                }
            }
        }.navigationTitle("要点の根拠")
    }
}

private struct ConversationExtractedItemView: View {
    @EnvironmentObject private var model: AppModel
    let item: ConversationInsightItem
    let recording: ConversationRecording
    let onChanged: () async -> Void
    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text(item.status == "awaiting_review" ? "確認待ち" : item.status == "kept" ? "保存済み" : "追加済み")
                    .appFont(.caption, weight: .bold).foregroundStyle(item.status == "awaiting_review" ? .orange : .mint)
                Text(item.certainty == "explicit" ? "明言" : item.certainty == "inferred" ? "推定" : "曖昧")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            Text(verbatim: item.title).appFont(.headline)
            if !item.detail.isEmpty { Text(verbatim: item.detail).appFont(.subheadline) }
            if let assignee = item.assignee { Text("担当候補: \(assignee)").appFont(.caption) }
            if let due = item.dueDate { Text("期限候補: \(due)").appFont(.caption) }
            DisclosureGroup("根拠（\(item.evidence.count)件）") {
                ForEach(Array(item.evidence.enumerated()), id: \.offset) { _, evidence in
                    VStack(alignment: .leading, spacing: 6) {
                        ConversationEvidenceQuote(recording: recording, utteranceID: evidence.utteranceID,
                                                  quote: evidence.quote, speaker: evidence.speaker,
                                                  startSeconds: evidence.startSeconds, position: evidence.position,
                                                  correctedQuote: evidence.correctedQuote, correctionRevisionID: evidence.correctionRevisionID,
                                              correctionIsSnapshot: evidence.correctionIsSnapshot == true)
                        if let context = evidence.locationContext {
                            if evidence.locationContextIsSnapshot == true {
                                Text("保存・追加時点の位置照合です。").appFont(.caption2).foregroundStyle(.secondary)
                            }
                            ConversationLocationSummary(link: context)
                            if context.location != nil {
                                NavigationLink("根拠の推定場所") { ConversationLocationMap(links: [context]) }
                            }
                        }
                    }
                }
            }
            if item.status == "awaiting_review" {
                DisclosureGroup("内容を確認・修正する") {
                    ConversationInsightCard(item: item, onChanged: onChanged)
                        .disabled(model.isFixture)
                }
            } else if item.status == "approved" {
                if item.approvedTarget == "life" {
                    NavigationLink("暮らしに追加した内容を確認") { LifeAssistantView(store: model.life) }
                } else if item.approvedTarget == "agent" {
                    Button("Agentの一覧へ") { model.selectedTab = 0 }
                } else if item.approvedTarget == "planner" {
                    Button("今日のタスクへ") { model.selectedTab = 1 }
                }
            }
        }.padding(.vertical, 4)
    }
}

private struct ConversationEvidenceQuote: View {
    let recording: ConversationRecording
    let utteranceID: String?
    let quote: String
    let speaker: String?
    let startSeconds: Double?
    let position: Int
    var correctedQuote: String? = nil
    var correctionRevisionID: String? = nil
    var correctionIsSnapshot = false
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(speaker ?? "話者未判定")
                if recording.isTranscript {
                    Text("文字起こしの根拠")
                } else if let seconds = startSeconds, seconds.isFinite, seconds >= 0 {
                    Text(String(format: "%d:%02d", Int(seconds) / 60, Int(seconds) % 60))
                }
            }.appFont(.caption2).foregroundStyle(.secondary)
            Text("根拠として保存した原文").appFont(.caption2).foregroundStyle(.secondary)
            Text(verbatim: quote).appFont(.caption).textSelection(.enabled)
            if let corrected = recording.correctedEvidence(correctedQuote, revisionID: correctionRevisionID, isSnapshot: correctionIsSnapshot), corrected != quote {
                Text(correctionIsSnapshot ? "この根拠の生成時の補正文" : "補正後の参考表示（元の根拠は保持）").appFont(.caption2).foregroundStyle(.secondary)
                Text(verbatim: corrected).appFont(.caption).textSelection(.enabled)
                if correctionIsSnapshot && (recording.correction?.status == "stale" || correctionRevisionID != recording.correction?.revisionID) {
                    Text("現在の補正版とは別に、生成時の根拠を保持しています。")
                        .appFont(.caption2).foregroundStyle(.secondary)
                }
            }
            if let utteranceID, recording.utterances?.contains(where: { $0.id == utteranceID }) == true {
                NavigationLink("前後の発言を確認") {
                    ConversationTranscriptView(recording: recording, selectedUtteranceID: utteranceID)
                }.appFont(.caption)
            } else {
                Text("この根拠の引用を保持しています。現在の原文との対応は確認できません。")
                    .appFont(.caption2).foregroundStyle(.secondary)
            }
        }.padding(.vertical, 4)
    }
}

private struct ConversationTranscriptView: View {
    @EnvironmentObject private var model: AppModel
    let initialRecording: ConversationRecording
    @State private var updatedRecording: ConversationRecording?
    @State private var editing: ConversationUtterance?
    private var recording: ConversationRecording { updatedRecording ?? initialRecording }
    init(recording: ConversationRecording, selectedUtteranceID: String? = nil) {
        initialRecording = recording
        self.selectedUtteranceID = selectedUtteranceID
    }
    var selectedUtteranceID: String? = nil
    private var shown: [ConversationUtterance] {
        let utterances = recording.utterances ?? []
        guard let selectedUtteranceID, let index = utterances.firstIndex(where: { $0.id == selectedUtteranceID }) else { return utterances }
        return Array(utterances[max(0, index - 1)...min(utterances.count - 1, index + 1)])
    }
    var body: some View {
        List {
            if selectedUtteranceID != nil {
                Text("根拠の発言と、その前後を表示しています。")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            if let error = model.conversationDetailErrors[recording.id] {
                Text(error).foregroundStyle(.orange)
                Button("最新の文字起こしを確認") { Task { await reload() } }
            }
            ForEach(shown) { utterance in
                VStack(alignment: .leading, spacing: 8) {
                    HStack {
                        Text(utterance.speaker ?? "話者未判定").bold()
                        if !recording.isTranscript, utterance.startSeconds.isFinite, utterance.startSeconds >= 0 {
                            Text(String(format: "%d:%02d", Int(utterance.startSeconds) / 60, Int(utterance.startSeconds) % 60))
                        }
                        if utterance.id == selectedUtteranceID { Text("根拠").foregroundStyle(.mint) }
                    }.appFont(.caption)
                    Text("元の文字起こし").appFont(.caption2).foregroundStyle(.secondary)
                    Text(verbatim: utterance.text).textSelection(.enabled)
                    if let corrected = utterance.userCorrectedText {
                        Text("本人が訂正した発言").appFont(.caption2).foregroundStyle(.secondary)
                        Text(verbatim: corrected).textSelection(.enabled)
                    } else if let correction = recording.correctionItem(for: utterance) {
                        if let corrected = correction.acceptedText {
                            Text("補正後（文脈による確認）").appFont(.caption2).foregroundStyle(.secondary)
                            Text(verbatim: corrected).textSelection(.enabled)
                        } else {
                            Text(correction.verificationLabel).appFont(.caption2).foregroundStyle(.secondary)
                        }
                    }
                    Button("訂正", systemImage: "pencil") { editing = utterance }
                        .buttonStyle(.borderless)
                        .disabled(recording.status != "completed")
                    if let link = recording.locationContexts?.first(where: { $0.utteranceID == utterance.id }) {
                        ConversationLocationSummary(link: link)
                        if link.location != nil {
                            NavigationLink("この発言の推定場所") { ConversationLocationMap(links: [link]) }
                        }
                    }
                }.padding(.vertical, 4)
            }
        }
        .navigationTitle(selectedUtteranceID == nil ? "文字起こし" : "根拠の前後")
        .sheet(item: $editing) { utterance in
            ConversationFeedbackView(editor: ConversationFeedbackEditor(recordingID: recording.id, utterance: utterance, api: model.conversationAPI, isFixture: model.isFixture)) { response in
                if model.isFixture {
                    var copy = recording
                    if let index = copy.utterances?.firstIndex(where: { $0.id == utterance.id }) {
                        copy.utterances?[index].userCorrection = response.feedback
                        copy.utterances?[index].userCorrectionRevision = response.feedback_revision
                    }
                    updatedRecording = copy
                } else {
                    await reload(afterMutation: true)
                    await model.refreshConversations(afterMutation: true)
                }
            }
        }
        .refreshable { await reload() }
    }
    private func reload(afterMutation: Bool = false) async {
        if let latest = await model.loadConversation(recording.id, afterMutation: afterMutation) { updatedRecording = latest }
    }
}

private extension ConversationContextLocation {
    var event: LocationEvent {
        LocationEvent(timestamp: timestamp, latitude: latitude, longitude: longitude,
                      horizontal_accuracy: horizontalAccuracy, is_approximate: isApproximate)
    }
}

private struct ConversationLocationSummary: View {
    let link: ConversationLocationContext
    private func displayTime(_ value: String) -> String {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let fractionalDate = formatter.date(from: value)
        formatter.formatOptions = [.withInternetDateTime]
        return (fractionalDate ?? formatter.date(from: value))?
            .formatted(date: .abbreviated, time: .standard) ?? value
    }
    private var dateLabel: String {
        switch link.dateSource {
        case "soundcore_filename_jst": "ファイル名・日本時間"
        case "soundcore_cloud_timestamp": "Soundcoreクラウドの日時"
        case "soundcore_drive_folder_name": "Driveフォルダー名・日本時間"
        case "explicit": "明示指定の日時"
        case "legacy_verified": "確認済みの既存日時"
        default: "日時不明"
        }
    }
    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if let timestamp = link.targetTimestamp {
                Text("\(link.utteranceID == nil ? "録音開始" : "推定発言時刻"): \(displayTime(timestamp))")
            }
            if link.utteranceID == nil { Text("日時の出典: \(dateLabel)") }
            switch link.state {
            case "matched_estimate":
                Label("GPSと紐付け済み（場所は推定）", systemImage: "mappin.and.ellipse")
                    .foregroundStyle(.cyan)
                if let location = link.location {
                    Text("GPSとの時刻差 \(Int(link.timeDeltaSeconds ?? 0))秒・水平精度 約\(Int(location.horizontalAccuracy)) m")
                    if let speed = location.speedMPS, let accuracy = location.speedAccuracyMPS {
                        Text("取得時の速度 約\(Int(speed * 3.6)) km/h（精度 ±\(Int(accuracy * 3.6)) km/h）").font(.caption)
                    }
                }
            case "unknown_time": Text("録音日時が不明なため、場所は紐付けていません。")
            case "low_accuracy": Text("近い時刻のGPSはありますが、位置精度・移動による時刻差・取得元の条件を満たしません。")
            default: Text("近い時刻のGPSがありません。履歴が同期されると再照合します。")
            }
        }
        .appFont(.caption)
        .foregroundStyle(.secondary)
    }
}

private struct ConversationLocationMap: View {
    let links: [ConversationLocationContext]
    private var events: [LocationEvent] {
        var ids = Set<String>()
        return links.compactMap { link in
            guard let id = link.locationEventID, ids.insert(id).inserted else { return nil }
            return link.location?.event
        }
    }
    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                HistoryNativeMap(events: events).frame(maxWidth: .infinity).frame(height: 320)
                Text("録音・発言に紐付いたGPS \(events.count)地点")
                    .appFont(.headline)
                Text("発言時刻は録音開始＋音声内の経過時間からの推定です。録音の一時停止、音声変換、機器の時計ずれにより、実際の発言場所と異なる場合があります。")
                    .appFont(.caption).foregroundStyle(.secondary)
                ForEach(links.filter { $0.location != nil }) { link in
                    ConversationLocationSummary(link: link).glassCard()
                }
            }.padding()
        }
        .navigationTitle("会話の推定場所")
    }
}

struct ConversationInsightCard: View {
    @EnvironmentObject private var model: AppModel
    let item: ConversationInsightItem
    let showsRecordingLink: Bool
    let onChanged: () async -> Void
    @State private var title: String
    @State private var detail: String
    @State private var assignee: String
    @State private var dueDate: String
    @State private var repository = ""
    @State private var actionInFlight = false

    init(
        item: ConversationInsightItem,
        showsRecordingLink: Bool = false,
        onChanged: @escaping () async -> Void
    ) {
        self.item = item
        self.showsRecordingLink = showsRecordingLink
        self.onChanged = onChanged
        _title = State(initialValue: item.title)
        _detail = State(initialValue: item.detail)
        _assignee = State(initialValue: item.assignee ?? "")
        _dueDate = State(initialValue: item.dueDate ?? "")
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Label(kindLabel, systemImage: kindIcon)
                    .appFont(.caption, weight: .bold)
                    .foregroundStyle(kindColor)
                Text(certaintyLabel)
                    .appFont(.caption2, weight: .semibold)
                    .foregroundStyle(.secondary)
                Spacer()
                Text(item.source == "codex" ? "Codex" : item.source == "openai" ? "LLM" : "ルール")
                    .appFont(.caption2).foregroundStyle(.tertiary)
            }
            TextField("タイトル", text: $title, axis: .vertical)
                .appFont(.headline)
            TextField("詳細", text: $detail, axis: .vertical)
                .lineLimit(2...6)
            HStack {
                TextField("担当者（任意）", text: $assignee)
                TextField("期限 YYYY-MM-DD", text: $dueDate)
            }
            .textFieldStyle(.roundedBorder)

            if !item.evidence.isEmpty {
                DisclosureGroup("根拠 \(item.evidence.count)件") {
                    VStack(alignment: .leading, spacing: 8) {
                        ForEach(Array(item.evidence.enumerated()), id: \.offset) { _, evidence in
                            VStack(alignment: .leading, spacing: 3) {
                                Text(evidenceLabel(evidence))
                                    .appFont(.caption2, weight: .bold).foregroundStyle(.secondary)
                                Text(evidence.quote)
                                    .appFont(.caption).textSelection(.enabled)
                            }
                        }
                    }
                }
            }

            NavigationLink("調査・予定・関心・タスクにつなげる") {
                ConversationLifeChoices(store: model.life, item: item, title: title, detail: detail, assignee: assignee)
            }
            if item.isActionable {
                HStack {
                    Button("通常タスクに追加") { performDispatch(target: "planner") }
                        .buttonStyle(.bordered)
                    Picker("Agentのリポジトリ", selection: $repository) {
                        ForEach(model.repositories) { Text($0.label).tag($0.name) }
                    }
                    .labelsHidden().pickerStyle(.menu)
                    Button("Agentへ依頼") { performDispatch(target: "agent") }
                        .buttonStyle(.borderedProminent).tint(.mint)
                        .disabled(repository.isEmpty)
                }
            }
            HStack {
                Button("気づきとして保存") { performReview(action: "keep") }
                Spacer()
                Button("破棄", role: .destructive) { performReview(action: "dismiss") }
            }
            .buttonStyle(.borderless)

            if showsRecordingLink {
                NavigationLink {
                    ConversationDetailView(recordingID: item.recordingID)
                } label: {
                    Label(item.recordingFilename ?? "録音を表示", systemImage: "waveform")
                        .appFont(.caption)
                }
            }
        }
        .disabled(actionInFlight)
        .onAppear { synchronizeRepositorySelection() }
        .onChange(of: model.repositories, initial: true) { _, _ in synchronizeRepositorySelection() }
    }

    private var kindLabel: String {
        switch item.kind {
        case "task": "タスク"
        case "follow_up": "フォローアップ"
        case "decision": "決定事項"
        case "idea": "アイデア"
        case "friction": "困りごと"
        case "research": "調べもの"
        case "event": "予定候補"
        case "interest": "関心候補"
        case "preference": "好み候補"
        default: item.kind
        }
    }

    private var kindIcon: String {
        insightKindIcon(item.kind)
    }

    private var kindColor: Color {
        switch item.kind {
        case "task", "follow_up": .mint
        case "decision": .blue
        case "idea": .yellow
        default: .orange
        }
    }

    private var certaintyLabel: String {
        switch item.certainty {
        case "explicit": "明言"
        case "inferred": "推定"
        default: "曖昧"
        }
    }

    private func evidenceLabel(_ evidence: ConversationInsightEvidence) -> String {
        let speaker = evidence.speaker ?? "話者"
        guard let seconds = evidence.startSeconds else { return speaker }
        let minute = Int(seconds) / 60
        let second = Int(seconds) % 60
        return String(format: "%@ %d:%02d", speaker, minute, second)
    }

    private func synchronizeRepositorySelection() {
        if !model.repositories.contains(where: { $0.name == repository }) {
            repository = model.repositories.first?.name ?? ""
        }
    }

    private func performReview(action: String) {
        guard !actionInFlight else { return }
        actionInFlight = true
        Task {
            _ = await model.reviewConversationItem(
                item, action: action, title: title, detail: detail,
                assignee: assignee, dueDate: dueDate
            )
            await onChanged()
            actionInFlight = false
        }
    }

    private func performDispatch(target: String) {
        guard !actionInFlight else { return }
        actionInFlight = true
        Task {
            _ = await model.dispatchConversationItem(
                item, target: target, title: title, detail: detail,
                assignee: assignee, dueDate: dueDate,
                repository: target == "agent" ? repository : nil
            )
            await onChanged()
            actionInFlight = false
        }
    }
}

struct ResourceStatusView: View {
    let state: ResourceLoadState
    let label: String
    let retry: (() -> Void)?

    var body: some View {
        switch state {
        case .idle, .loading:
            ProgressView("\(label)を読み込んでいます…")
                .frame(maxWidth: .infinity, alignment: .leading)
                .glassCard()
        case .failed(let message):
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: "wifi.exclamationmark")
                    .foregroundStyle(.orange)
                VStack(alignment: .leading, spacing: 6) {
                    Text("\(label)を更新できませんでした")
                        .appFont(.subheadline, weight: .semibold)
                    Text(message)
                        .appFont(.caption)
                        .foregroundStyle(.secondary)
                    if let retry {
                        Button("再試行", action: retry)
                            .buttonStyle(.bordered)
                            .appFont(.caption, weight: .semibold)
                    }
                }
                Spacer(minLength: 0)
            }
            .glassCard()
        case .loaded:
            EmptyView()
        }
    }
}

struct AgentView: View {
    @EnvironmentObject private var model: AppModel
    #if os(macOS)
    @EnvironmentObject private var macAgentKeyboard: MacAgentKeyboardController
    #endif
    @State private var expandedTaskIDs = Set<String>()
    @State private var expandedTaskOrder: [String] = []
    @State private var selectedTaskID: String?
    @State private var archiveExpanded = false

    var body: some View {
        let activeSnapshot = activeTasks
        let archivedSnapshot = archivedTasks
        let displayedSnapshot = displayedTasks(from: activeSnapshot)
        ScrollViewReader { proxy in
            List {
                AgentComposer()
                    .agentListRow()
                RuntimeInfo(info: model.deploymentInfo, refreshedAt: model.lastUpdated)
                    .agentListRow()
                if model.agentLoadState != .loaded {
                    ResourceStatusView(state: model.agentLoadState, label: "Agent") {
                        Task { await model.refresh() }
                    }
                    .agentListRow()
                }
                AgentUsageCard()
                    .agentListRow()
                TanomiComposer()
                    .agentListRow()
                if model.tanomiLoadState != .loaded {
                    ResourceStatusView(state: model.tanomiLoadState, label: "tanomi") {
                        Task { await model.refresh() }
                    }
                    .agentListRow()
                }
                #if os(macOS)
                Text("j/k 選択 · Enter/l 開く · Esc/h 閉じる · Ctrl+u/d · gg/G · zt/zz/zb · dd/dj/dk 非表示")
                    .appFont(.caption2)
                    .foregroundStyle(.secondary)
                    .agentListRow()
                #endif
                ForEach(displayedSnapshot) { item in
                    switch item {
                    case .daymeld(let job):
                        AgentCard(
                            job: job,
                            requestedExpanded: expandedTaskIDs.contains(item.id),
                            keyboardSelected: isKeyboardSelected(item.id)
                        ) { isExpanded in
                            setExpanded(item.id, isExpanded: isExpanded)
                        }
                            .id(item.id)
                            .agentListRow()
                            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                archiveButton(for: job)
                            }
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                archiveButton(for: job)
                            }
                    case .tanomi(let task):
                        TanomiTaskCard(
                            task: task,
                            requestedExpanded: expandedTaskIDs.contains(item.id),
                            keyboardSelected: isKeyboardSelected(item.id)
                        ) { isExpanded in
                            setExpanded(item.id, isExpanded: isExpanded)
                        }
                            .id(item.id)
                            .agentListRow()
                            .swipeActions(edge: .leading, allowsFullSwipe: true) {
                                archiveButton(for: task)
                            }
                            .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                                archiveButton(for: task)
                            }
                    }
                }
                if activeSnapshot.isEmpty && model.agentLoadState == .loaded {
                    EmptyState(icon: "sparkles", title: "Agentは待機中です", detail: "新しい依頼を送ると、ここに進捗が表示されます。")
                        .agentListRow()
                }
                if !archivedSnapshot.isEmpty {
                    DisclosureGroup(isExpanded: $archiveExpanded) {
                        if archiveExpanded {
                            ForEach(archivedSnapshot) { item in
                                switch item {
                                case .daymeld(let job): AgentCard(job: job, archived: true)
                                case .tanomi(let task): TanomiTaskCard(task: task, archived: true)
                                }
                            }
                        }
                    } label: {
                        Text("アーカイブ（\(archivedSnapshot.count)）")
                    }
                    .glassCard()
                    .agentListRow()
                }
            }
            .listStyle(.plain)
            .scrollContentBackground(.hidden)
            .background(AppBackground())
            .navigationTitle("Daymeld")
            .animation(.easeInOut(duration: 0.28), value: activeSnapshot.map(\.id))
            .refreshable { await model.refresh() }
            .onAppear { synchronizeSelection(with: activeSnapshot.map(\.id)) }
            .onChange(of: activeSnapshot.map(\.id)) { _, ids in
                expandedTaskIDs.formIntersection(ids)
                expandedTaskOrder.removeAll { !ids.contains($0) }
                if expandedTaskIDs.isEmpty { expandedTaskOrder.removeAll() }
                synchronizeSelection(with: ids)
            }
            #if os(macOS)
            .onChange(of: macAgentKeyboard.invocation) { _, invocation in
                guard let invocation else { return }
                handle(invocation.command, proxy: proxy)
            }
            #endif
        }
    }

    private var activeTasks: [AgentTaskItem] {
        (model.agents.map(AgentTaskItem.daymeld) + model.tanomiTasks.map(AgentTaskItem.tanomi))
            .sorted(by: AgentTaskItem.newestFirst)
    }

    private var archivedTasks: [AgentTaskItem] {
        (model.archivedAgents.map(AgentTaskItem.daymeld) + model.tanomiArchivedTasks.map(AgentTaskItem.tanomi))
            .sorted(by: AgentTaskItem.newestFirst)
    }

    private func displayedTasks(from activeTasks: [AgentTaskItem]) -> [AgentTaskItem] {
        guard !expandedTaskIDs.isEmpty else { return activeTasks }
        let tasksByID = Dictionary(uniqueKeysWithValues: activeTasks.map { ($0.id, $0) })
        let retained = expandedTaskOrder.compactMap { tasksByID[$0] }
        let retainedIDs = Set(retained.map(\.id))
        let newTasks = activeTasks.filter { !retainedIDs.contains($0.id) }
        return retained + newTasks
    }

    private func setExpanded(_ id: String, isExpanded: Bool) {
        if isExpanded {
            if expandedTaskIDs.isEmpty { expandedTaskOrder = activeTasks.map(\.id) }
            expandedTaskIDs.insert(id)
        } else {
            expandedTaskIDs.remove(id)
            if expandedTaskIDs.isEmpty { expandedTaskOrder.removeAll() }
        }
    }

    private func synchronizeSelection(with ids: [String]) {
        guard !ids.isEmpty else {
            selectedTaskID = nil
            return
        }
        if selectedTaskID.map({ !ids.contains($0) }) ?? true {
            selectedTaskID = ids.first
        }
    }

    private func isKeyboardSelected(_ id: String) -> Bool {
        #if os(macOS)
        selectedTaskID == id
        #else
        false
        #endif
    }

    #if os(macOS)
    private func handle(_ command: MacAgentNavigationCommand, proxy: ScrollViewProxy) {
        let tasks = displayedTasks(from: activeTasks)
        guard !tasks.isEmpty else { return }
        let currentIndex = tasks.firstIndex { $0.id == selectedTaskID } ?? 0

        switch command {
        case .move(let offset):
            select(tasks, index: currentIndex + offset, anchor: .center, proxy: proxy)
        case .page(let direction):
            select(tasks, index: currentIndex + (direction * 5), anchor: .center, proxy: proxy)
        case .first:
            select(tasks, index: 0, anchor: .top, proxy: proxy)
        case .last:
            select(tasks, index: tasks.count - 1, anchor: .bottom, proxy: proxy)
        case .open:
            let id = tasks[currentIndex].id
            selectedTaskID = id
            setExpanded(id, isExpanded: true)
            withAnimation { proxy.scrollTo(id, anchor: .center) }
        case .close:
            let id = tasks[currentIndex].id
            selectedTaskID = id
            setExpanded(id, isExpanded: false)
        case .alignTop:
            proxy.scrollTo(tasks[currentIndex].id, anchor: .top)
        case .alignCenter:
            proxy.scrollTo(tasks[currentIndex].id, anchor: .center)
        case .alignBottom:
            proxy.scrollTo(tasks[currentIndex].id, anchor: .bottom)
        case .archive(let direction):
            archive(tasks[currentIndex], at: currentIndex, direction: direction, tasks: tasks, proxy: proxy)
        }
    }

    private func select(_ tasks: [AgentTaskItem], index: Int, anchor: UnitPoint, proxy: ScrollViewProxy) {
        let boundedIndex = min(max(index, 0), tasks.count - 1)
        let id = tasks[boundedIndex].id
        selectedTaskID = id
        withAnimation { proxy.scrollTo(id, anchor: anchor) }
    }

    private func archive(
        _ item: AgentTaskItem,
        at index: Int,
        direction: MacTaskArchiveDirection,
        tasks: [AgentTaskItem],
        proxy: ScrollViewProxy
    ) {
        if case .tanomi(let task) = item, ["queued", "running"].contains(task.status) {
            NSSound.beep()
            return
        }

        let nextIndex = direction == .previous ? index - 1 : index + 1
        let remaining = tasks.filter { $0.id != item.id }
        if !remaining.isEmpty {
            let adjusted = direction == .previous ? nextIndex : min(index, remaining.count - 1)
            select(remaining, index: adjusted, anchor: .center, proxy: proxy)
        } else {
            selectedTaskID = nil
        }

        switch item {
        case .daymeld(let job):
            Task { await model.hideAgent(jobID: job.id) }
        case .tanomi(let task):
            Task { await model.hideTanomi(task) }
        }
    }
    #endif

    private func archiveButton(for job: AgentJob) -> some View {
        Button {
            Task { await model.hideAgent(jobID: job.id) }
        } label: {
            Label("非表示", systemImage: "archivebox.fill")
        }
        .tint(.orange)
    }

    private func archiveButton(for task: TanomiTask) -> some View {
        Button {
            Task { await model.hideTanomi(task) }
        } label: {
            Label("非表示", systemImage: "archivebox.fill")
        }
        .tint(.orange)
        .disabled(["queued", "running"].contains(task.status))
    }
}

private enum AgentTaskItem: Identifiable {
    case daymeld(AgentJob)
    case tanomi(TanomiTask)

    var id: String {
        switch self {
        case .daymeld(let job): "daymeld-\(job.id)"
        case .tanomi(let task): "tanomi-\(task.id)"
        }
    }

    private var updatedDate: Date {
        switch self {
        case .daymeld(let job): job.updatedAt.iso8601Date ?? .distantPast
        case .tanomi(let task): task.updatedDate ?? .distantPast
        }
    }

    static func newestFirst(_ left: AgentTaskItem, _ right: AgentTaskItem) -> Bool {
        if left.updatedDate != right.updatedDate { return left.updatedDate > right.updatedDate }
        return left.id > right.id
    }
}

struct TanomiComposer: View {
    @EnvironmentObject private var model: AppModel
    @State private var prompt = ""
    @State private var repo = ""
    @State private var sending = false
    @State private var selectedModel = "opus"
    @State private var selectedEffort = ""
    @State private var permissionMode = "acceptEdits"
    @State private var confirmBypass = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("tanomi", systemImage: "terminal")
                    .appFont(.headline)
                Spacer()
                Text(model.tanomiAvailable ? "接続中" : "接続不可")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            TextField("tanomiへ依頼する内容", text: $prompt, axis: .vertical)
                .lineLimit(2...5).textFieldStyle(.roundedBorder)
            HStack {
                Button("依頼") {
                    if permissionMode == "bypassPermissions" { confirmBypass = true; return }
                    Task {
                        sending = true
                        if await model.createTanomi(prompt: prompt, repo: repo, model: selectedModel, permissionMode: permissionMode, effort: selectedEffort.isEmpty ? nil : selectedEffort) { prompt = "" }
                        sending = false
                    }
                }.buttonStyle(.borderedProminent).disabled(!model.tanomiAvailable || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || repo.isEmpty || sending)
                Spacer(minLength: 0)
                Picker("リポジトリ", selection: $repo) {
                    ForEach(model.tanomiRepositories) { item in
                        Text(item.label ?? item.path).tag(item.path)
                    }
                }.pickerStyle(.menu).disabled(model.tanomiRepositories.isEmpty || !model.tanomiAvailable || sending)
            }
            DisclosureGroup("詳細（\(selectedModel)・Effort \(selectedEffort.isEmpty ? "既定" : selectedEffort)・\(permissionMode)）") {
                Picker("モデル", selection: $selectedModel) { ForEach(model.tanomiConfig.models, id: \.self) { Text($0).tag($0) } }
                Picker("Effort", selection: $selectedEffort) { Text("既定").tag(""); ForEach(model.tanomiConfig.efforts, id: \.self) { Text($0).tag($0) } }
                Picker("権限", selection: $permissionMode) { ForEach(model.tanomiConfig.permissionModes, id: \.self) { Text($0).tag($0) } }
            }.appFont(.caption)
            if !model.tanomiAvailable && model.tanomiTasks.isEmpty {
                Text(model.tanomiStatusMessage.map { "tanomiを利用できません：\($0)" } ?? "tanomiは現在利用できません。")
                    .appFont(.subheadline).foregroundStyle(.secondary)
            }
        }.glassCard()
        .alert("tanomiに強い権限を許可しますか？", isPresented: $confirmBypass) {
            Button("キャンセル", role: .cancel) {}
            Button("許可して依頼", role: .destructive) {
                Task { sending = true; if await model.createTanomi(prompt: prompt, repo: repo, model: selectedModel, permissionMode: permissionMode, effort: selectedEffort.isEmpty ? nil : selectedEffort) { prompt = "" }; sending = false }
            }
        }
        .onAppear { if repo.isEmpty { repo = model.tanomiRepositories.first?.path ?? "" } }
        .onChange(of: model.tanomiRepositories, initial: true) { _, values in
            if !values.contains(where: { $0.path == repo }) { repo = values.first?.path ?? "" }
        }
        .onChange(of: model.tanomiConfig, initial: true) { _, config in
            if !config.models.contains(selectedModel) { selectedModel = config.defaultModel }
            if !selectedEffort.isEmpty && !config.efforts.contains(selectedEffort) { selectedEffort = config.defaultEffort ?? "" }
            if !config.permissionModes.contains(permissionMode) { permissionMode = config.permissionModes.first ?? "acceptEdits" }
        }
    }
}

private struct TanomiTaskCard: View {
    @EnvironmentObject private var model: AppModel
    let task: TanomiTask
    var archived = false
    var requestedExpanded: Bool? = nil
    var keyboardSelected = false
    var onExpansionChange: ((Bool) -> Void)? = nil
    @State private var expanded = false
    @State private var instruction = ""
    @State private var sending = false

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            Button {
                expanded.toggle()
                onExpansionChange?(expanded)
            } label: {
                HStack(alignment: .top, spacing: 12) {
                    Image(systemName: statusIcon)
                        .foregroundStyle(statusColor)
                        .appFont(.title3)
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(spacing: 8) {
                            AgentSourceBadge(label: "tanomi", color: .purple)
                            Text(task.displayRepository)
                                .appFont(.caption).foregroundStyle(.secondary)
                                .lineLimit(1)
                        }
                        Text(task.displayTitle)
                            .appFont(.headline)
                            .foregroundStyle(.primary)
                            .lineLimit(expanded ? nil : 2)
                            .multilineTextAlignment(.leading)
                        Text(statusAndTime)
                            .appFont(.caption).foregroundStyle(statusColor)
                    }
                    Spacer()
                    Image(systemName: expanded ? "chevron.up" : "chevron.down")
                        .foregroundStyle(.tertiary)
                }
            }
            .buttonStyle(.plain)

            if expanded {
                Divider()
                if let prompt = task.prompt, !prompt.isEmpty {
                    Text("依頼内容").appFont(.caption, weight: .bold).foregroundStyle(.secondary)
                    MarkdownContentView(source: prompt)
                }
                if !task.displayResult.isEmpty {
                    Text(task.result != nil ? "結果" : "エラー")
                        .appFont(.caption, weight: .bold).foregroundStyle(.secondary)
                    if let result = task.result {
                        MarkdownContentView(source: result, collapsible: true)
                    } else {
                        Text(verbatim: task.displayResult).appFont(.body).textSelection(.enabled)
                    }
                }

                if !archived && task.canContinue {
                    TextField("このtanomiタスクへの追加指示", text: $instruction, axis: .vertical)
                        .lineLimit(2...5).textFieldStyle(.roundedBorder)
                    Button("追加指示を送信") {
                        Task {
                            sending = true
                            if await model.sendTanomiInstruction(taskID: task.id, instruction: instruction) { instruction = "" }
                            sending = false
                        }
                    }
                    .buttonStyle(.borderedProminent).tint(.purple)
                    .disabled(instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || sending)
                }
                if ["queued", "running"].contains(task.status) {
                    Button("停止") { Task { await model.stopTanomi(task) } }.appFont(.caption)
                }
                if !archived && !["queued", "running"].contains(task.status) {
                    HStack {
                        Spacer()
                        Button("非表示") { Task { await model.hideTanomi(task) } }
                            .appFont(.caption, weight: .bold)
                            .buttonStyle(.borderless)
                    }
                }
            }
        }
        .agentTaskCard(accent: .purple, selected: keyboardSelected)
        .accessibilityAddTraits(keyboardSelected ? .isSelected : [])
        .onChange(of: requestedExpanded, initial: true) { _, requested in
            guard let requested, expanded != requested else { return }
            expanded = requested
        }
    }

    private var statusAndTime: String {
        guard let date = task.updatedDate else { return statusLabel }
        return "\(statusLabel)・\(date.formatted(.relative(presentation: .named)))"
    }

    private var statusLabel: String {
        switch task.status {
        case "queued": "待機中"
        case "running": "実行中"
        case "done": "完了"
        case "error": "失敗"
        case "stopped": "停止済み"
        default: task.status
        }
    }

    private var statusIcon: String {
        switch task.status {
        case "done": "checkmark.circle.fill"
        case "error": "exclamationmark.triangle.fill"
        case "running": "bolt.circle.fill"
        case "stopped": "minus.circle.fill"
        default: "clock.fill"
        }
    }

    private var statusColor: Color {
        switch task.status {
        case "done": .green
        case "error": .red
        case "running": .cyan
        default: .secondary
        }
    }
}

private extension View {
    func agentListRow() -> some View {
        listRowInsets(EdgeInsets(top: 7, leading: 16, bottom: 7, trailing: 16))
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)
    }
}

struct AgentUsageCard: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("AI 使用状況", systemImage: "gauge.with.dots.needle.67percent")
                    .appFont(.headline)
                Spacer()
            }
            HStack {
                AgentSourceBadge(label: "Codex", color: .mint)
                Spacer()
                if let plan = model.codexUsage?.rateLimits?.planType, !plan.isEmpty {
                    Text(plan).appFont(.caption).foregroundStyle(.secondary)
                }
            }
            if model.codexUsageFailed {
                Text("使用状況を取得できませんでした。")
                    .appFont(.subheadline).foregroundStyle(.secondary)
            } else {
                let limits = sortedLimits
                if limits.isEmpty {
                    Text(model.codexUsage == nil ? "使用状況を読み込んでいます…" : "現在の利用枠はありません。")
                        .appFont(.subheadline).foregroundStyle(.secondary)
                } else {
                    ForEach(limits, id: \.id) { item in
                        CodexLimitRow(name: item.name, window: item.window)
                    }
                }
            }
            Divider()
            HStack {
                AgentSourceBadge(label: "tanomi", color: .purple)
                Spacer()
                if let running = model.tanomiUsage?.running {
                    Text("実行中 \(running)件").appFont(.caption).foregroundStyle(.secondary)
                }
            }
            if model.tanomiUsage?.stale == true {
                Text("前回取得した使用状況を表示しています。")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            if model.tanomiUsageFailed {
                Text("tanomiの使用状況を取得できませんでした。")
                    .appFont(.subheadline).foregroundStyle(.secondary)
            } else {
                let limits = sortedTanomiLimits
                if limits.isEmpty {
                    Text(model.tanomiUsage == nil ? "使用状況を読み込んでいます…" : "現在の利用枠はありません。")
                        .appFont(.subheadline).foregroundStyle(.secondary)
                } else {
                    ForEach(limits, id: \.id) { item in
                        TanomiLimitRow(name: item.name, limit: item.limit)
                    }
                }
            }
        }
        .glassCard()
    }

    private var sortedLimits: [(id: String, name: String, window: CodexLimitWindow)] {
        (model.codexUsage?.rateLimitsByLimitID ?? [:])
            .sorted {
                let leftRank = $0.key == "codex" ? 0 : 1
                let rightRank = $1.key == "codex" ? 0 : 1
                if leftRank != rightRank { return leftRank < rightRank }
                let leftName = $0.value.limitName ?? $0.key
                let rightName = $1.value.limitName ?? $1.key
                if leftName != rightName { return leftName.localizedCompare(rightName) == .orderedAscending }
                return $0.key.localizedCompare($1.key) == .orderedAscending
            }
            .flatMap { id, limit in
                let name = limit.limitName ?? (id == "codex" ? "Codex" : id)
                return [(id: "\(id)-primary", name: "\(name)・\(windowLabel(limit.primary?.windowDurationMins))", window: limit.primary),
                        (id: "\(id)-secondary", name: "\(name)・\(windowLabel(limit.secondary?.windowDurationMins))", window: limit.secondary)]
                    .compactMap { item in item.window.map { (item.id, item.name, $0) } }
            }
    }

    private func windowLabel(_ minutes: Int?) -> String {
        guard let minutes, minutes > 0 else { return "利用枠" }
        if minutes == 10080 { return "週次" }
        if minutes % 1440 == 0 { return "\(minutes / 1440)日" }
        if minutes % 60 == 0 { return "\(minutes / 60)時間" }
        return "\(minutes)分"
    }

    private var sortedTanomiLimits: [(id: String, name: String, limit: TanomiUsageLimit)] {
        (model.tanomiUsage?.limits ?? [:])
            .sorted {
                let rank = ["five_hour": 0, "seven_day": 1]
                let leftRank = rank[$0.key] ?? 2
                let rightRank = rank[$1.key] ?? 2
                if leftRank != rightRank { return leftRank < rightRank }
                return $0.key < $1.key
            }
            .map { id, limit in
                let name = switch id {
                case "five_hour": "5時間"
                case "seven_day": "週次"
                default: id.replacingOccurrences(of: "_", with: " ")
                }
                return (id, name, limit)
            }
    }
}

struct CodexLimitRow: View {
    let name: String
    let window: CodexLimitWindow

    var body: some View {
        let used = min(max(window.usedPercent ?? 0, 0), 100)
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(name).appFont(.subheadline, weight: .semibold)
                Spacer()
                Text("\(used.formatted(.number.precision(.fractionLength(0...1))) )% 使用")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            ProgressView(value: used, total: 100)
                .tint(.mint)
            HStack {
                Text("残り \(max(0, 100 - used).formatted(.number.precision(.fractionLength(0...1))) )%")
                Spacer()
                Text(resetLabel)
            }
            .appFont(.caption2).foregroundStyle(.secondary)
        }
    }

    private var resetLabel: String {
        guard let timestamp = window.resetsAt else { return "リセット時刻不明" }
        return "リセット \(Date(timeIntervalSince1970: TimeInterval(timestamp)).runtimeDisplay)"
    }
}

struct TanomiLimitRow: View {
    let name: String
    let limit: TanomiUsageLimit

    var body: some View {
        let used = min(max(limit.utilization, 0), 100)
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(name).appFont(.subheadline, weight: .semibold)
                Spacer()
                Text("\(used.formatted(.number.precision(.fractionLength(0...1))) )% 使用")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            ProgressView(value: used, total: 100)
                .tint(.purple)
            HStack {
                Text("残り \(max(0, 100 - used).formatted(.number.precision(.fractionLength(0...1))) )%")
                Spacer()
                Text(resetLabel)
            }
            .appFont(.caption2).foregroundStyle(.secondary)
        }
    }

    private var resetLabel: String {
        guard let date = limit.resetsAt?.iso8601Date else { return "リセット時刻不明" }
        return "リセット \(date.runtimeDisplay)"
    }
}

struct AgentCard: View {
    @EnvironmentObject private var model: AppModel
    let job: AgentJob
    var archived = false
    var requestedExpanded: Bool? = nil
    var keyboardSelected = false
    var onExpansionChange: ((Bool) -> Void)? = nil
    @State private var expanded = false
    @State private var showConversation = false
    @State private var fullEvents: [AgentEvent] = []
    @State private var fullEventsLoaded = false
    @State private var fullEventsError: String?
    @State private var instruction = ""
    @State private var sending = false
    @State private var actionInFlight = false

    var body: some View {
        cardContent
            .clipShape(RoundedRectangle(cornerRadius: 22))
            .accessibilityAddTraits(keyboardSelected ? .isSelected : [])
            .onChange(of: requestedExpanded, initial: true) { _, requested in
                guard let requested, expanded != requested else { return }
                expanded = requested
            }
    }

    private var cardContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button {
                expanded.toggle()
                onExpansionChange?(expanded)
            } label: {
                HStack(spacing: 12) {
                    Image(systemName: statusIcon).foregroundStyle(statusColor).appFont(.title3)
                    VStack(alignment: .leading, spacing: 5) {
                        HStack(spacing: 8) {
                            AgentSourceBadge(label: "Daymeld", color: .mint)
                            let repository = job.repositoryLabel ?? job.repository
                            if repository.caseInsensitiveCompare("Daymeld") != .orderedSame {
                                Text(repository)
                                    .appFont(.caption).foregroundStyle(.secondary)
                                    .lineLimit(1)
                            }
                        }
                        Text(job.prompt).appFont(.headline).foregroundStyle(.primary).lineLimit(expanded ? nil : 2)
                        if let implementationConfiguration {
                            Text(implementationConfiguration)
                                .appFont(.caption).foregroundStyle(.secondary)
                                .lineLimit(expanded ? nil : 2)
                        }
                        Text("\(phaseLabel)・\(job.updatedAt.relativeTime)")
                            .appFont(.caption).foregroundStyle(statusColor)
                    }
                    Spacer()
                    Image(systemName: expanded ? "chevron.up" : "chevron.down").foregroundStyle(.tertiary)
                }
            }
            .buttonStyle(.plain)

            if expanded {
                Divider()
                Text("現在の進捗").appFont(.caption, weight: .bold).foregroundStyle(statusColor)
                if let summary = job.summary, !summary.isEmpty {
                    Text(job.status == "completed" || job.followUp == 1 ? "完了サマリー" : "現在の報告").appFont(.caption, weight: .bold).foregroundStyle(.secondary)
                    Text(summary).appFont(.subheadline).textSelection(.enabled)
                }
                if ["queued", "running", "blocked"].contains(job.status) {
                    Label(job.status == "blocked" ? "回答を待っています" : "進捗を自動更新中", systemImage: "waveform.path.ecg").appFont(.caption, weight: .bold).foregroundStyle(statusColor)
                    ForEach(job.recentEvents ?? []) { event in AgentEventRow(event: event) }
                }
                Text("やりとり").appFont(.caption, weight: .bold).foregroundStyle(.secondary)
                Button(showConversation ? "やりとりを非表示" : "やりとりを表示") {
                    showConversation.toggle()
                    if showConversation && !fullEventsLoaded {
                        loadFullEvents()
                    }
                }
                .appFont(.caption, weight: .bold)
                .buttonStyle(.borderless)
                if showConversation {
                    if let fullEventsError {
                        VStack(alignment: .leading, spacing: 6) {
                            Text(fullEventsError).appFont(.caption).foregroundStyle(.orange)
                            Button("履歴を再取得") { loadFullEvents() }
                                .appFont(.caption, weight: .semibold)
                        }
                    } else if !fullEventsLoaded {
                        ProgressView("履歴を取得しています…").frame(maxWidth: .infinity)
                    } else if fullEvents.isEmpty {
                        Text("やりとりはまだありません。").appFont(.caption).foregroundStyle(.secondary)
                    } else {
                        ForEach(fullEvents) { event in AgentEventRow(event: event) }
                    }
                }
                if canAttach {
                    TextField(instructionPlaceholder, text: $instruction, axis: .vertical)
                        .lineLimit(2...5)
                        .textFieldStyle(.roundedBorder)
                    Button(sendLabel) {
                        Task {
                            sending = true
                            if await model.sendInstruction(jobID: job.id, instruction: instruction) { instruction = "" }
                            sending = false
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.mint)
                    .disabled(instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || sending)
                }
                HStack {
                    if !archived && ["queued", "running"].contains(job.status) {
                        Button("停止", role: .destructive) {
                            guard !actionInFlight else { return }
                            actionInFlight = true
                            Task {
                                await model.cancelAgent(jobID: job.id)
                                actionInFlight = false
                            }
                        }
                        .disabled(actionInFlight)
                    }
                    Spacer()
                    if !archived {
                        Button("非表示") {
                            guard !actionInFlight else { return }
                            actionInFlight = true
                            Task {
                                await model.hideAgent(jobID: job.id)
                                actionInFlight = false
                            }
                        }
                        .disabled(actionInFlight)
                    }
                }
                .appFont(.caption, weight: .bold)
                .buttonStyle(.borderless)
            }
        }
        .agentTaskCard(accent: .mint, selected: keyboardSelected)
    }

    private var canAttach: Bool {
        guard !archived else { return false }
        return ["queued", "running", "blocked", "completed"].contains(job.status)
            || (job.status == "failed" && job.worktree != nil)
    }
    private var instructionPlaceholder: String {
        if job.status == "blocked" { return "必要な判断や追加情報を入力" }
        if job.status == "completed" || job.followUp == 1 { return "完了内容について質問" }
        return "このタスクへの追加指示"
    }
    private var sendLabel: String {
        if job.status == "completed" || job.followUp == 1 { return "Agentに確認" }
        if ["blocked", "failed"].contains(job.status) { return "送信して再開" }
        return "タスクへ送信"
    }
    private var statusLabel: String {
        switch job.status { case "queued": "待機中"; case "running": "実行中"; case "blocked": "判断待ち"; case "completed": "完了"; case "failed": "失敗"; case "cancelled": "キャンセル済み"; default: job.status }
    }
    private var statusIcon: String {
        switch job.status { case "completed": "checkmark.circle.fill"; case "blocked": "questionmark.circle.fill"; case "failed": "exclamationmark.triangle.fill"; case "running": "bolt.circle.fill"; case "cancelled": "minus.circle.fill"; default: "clock.fill" }
    }
    private var statusColor: Color {
        switch job.status { case "completed": .green; case "blocked": .orange; case "failed": .red; case "running": .cyan; default: .secondary }
    }
    private var phaseLabel: String {
        job.phase == statusLabel ? statusLabel : "\(statusLabel)・\(job.phase)"
    }
    private var implementationConfiguration: String? {
        guard job.model != nil || job.reasoningEffort != nil else { return nil }
        return "実装モデル \(job.model ?? "未設定")・Effort \(job.reasoningEffort ?? "未設定")"
    }

    private func loadFullEvents() {
        fullEventsError = nil
        Task {
            guard let detail = await model.agentDetail(job.id) else {
                fullEventsError = "履歴を取得できませんでした。"
                return
            }
            fullEvents = detail.events ?? []
            fullEventsLoaded = true
        }
    }
}

private struct AgentSourceBadge: View {
    let label: String
    let color: Color

    var body: some View {
        HStack(spacing: 4) {
            Circle().fill(color).frame(width: 6, height: 6)
            Text(label)
        }
        .appFont(.caption2, weight: .bold)
        .padding(.horizontal, 8)
        .padding(.vertical, 3)
        .background(color.opacity(0.18), in: Capsule())
        .foregroundStyle(color)
    }
}

struct AgentEventRow: View {
    let event: AgentEvent
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(event.kind == "user" ? .mint : .secondary).frame(width: 7, height: 7).padding(.top, 6)
            VStack(alignment: .leading, spacing: 3) {
                Text(event.kind == "user" ? "あなた" : event.kind == "codex" ? "Agent" : "進捗")
                    .appFont(.caption2, weight: .bold).foregroundStyle(event.kind == "user" ? .mint : .secondary)
                Text(event.message).appFont(.caption).textSelection(.enabled)
                Text(event.createdAt.relativeTime).appFont(.caption2).foregroundStyle(.tertiary)
            }
        }
    }
}

struct AgentComposer: View {
    @EnvironmentObject private var model: AppModel
    @State private var prompt = ""
    @State private var repository = ""
    @State private var agentModel = AgentModelOption.fallback.slug
    @State private var reasoningEffort = AgentModelOption.fallback.defaultReasoningEffort
    @State private var sending = false
    @FocusState private var promptFocused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            TextField("実現したい結果を書いてください", text: $prompt, axis: .vertical)
                .lineLimit(3...7)
                .textFieldStyle(.roundedBorder)
                .focused($promptFocused)
                .accessibilityLabel("Agentへの依頼")
            HStack(spacing: 10) {
                Button {
                    Task {
                        sending = true
                        if await model.createAgent(prompt: prompt, repository: repository, model: agentModel, reasoningEffort: reasoningEffort) {
                            prompt = ""
                            promptFocused = false
                        }
                        sending = false
                    }
                } label: {
                    HStack(spacing: 6) {
                        if sending { ProgressView() }
                        Text("依頼")
                    }
                }
                .buttonStyle(.borderedProminent)
                .tint(.mint)
                .disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || repository.isEmpty || currentModel == nil || reasoningEffort.isEmpty || sending)
                Spacer(minLength: 0)
                Picker("リポジトリ", selection: $repository) {
                    ForEach(model.repositories) { Text($0.label).tag($0.name) }
                }
                .pickerStyle(.menu)
                .disabled(model.repositories.isEmpty || sending)
            }
            DisclosureGroup {
                VStack(alignment: .leading, spacing: 8) {
                    Picker("実装モデル", selection: $agentModel) {
                        ForEach(model.agentModels) { option in Text(option.displayName).tag(option.slug) }
                    }
                    Picker("Effort", selection: $reasoningEffort) {
                        ForEach(currentModel?.supportedReasoningEfforts ?? [], id: \.self) { effort in Text(effort).tag(effort) }
                    }
                }
            } label: {
                Text("詳細（\(currentModel?.displayName ?? "GPT-6-Astra")・\(reasoningEffort)）").appFont(.caption)
            }
            .disabled(model.agentModels.isEmpty || sending)
        }
        .glassCard()
        .onAppear { synchronizeRepositorySelection() }
        .onChange(of: model.repositories, initial: true) { _, _ in synchronizeRepositorySelection() }
        .onChange(of: model.agentModels, initial: true) { _, _ in synchronizeAgentSelection() }
    }

    private var currentModel: AgentModelOption? { model.agentModels.first(where: { $0.slug == agentModel }) }

    private func synchronizeRepositorySelection() {
        guard !model.repositories.isEmpty else {
            repository = ""
            return
        }
        if !model.repositories.contains(where: { $0.name == repository }) {
            repository = model.repositories[0].name
        }
    }

    private func synchronizeAgentSelection() {
        guard !model.agentModels.isEmpty else { return }
        if currentModel == nil { agentModel = model.agentModels[0].slug }
        let efforts = currentModel?.supportedReasoningEfforts ?? []
        if !efforts.contains(reasoningEffort) { reasoningEffort = currentModel?.defaultReasoningEffort ?? efforts.first ?? "" }
    }
}

struct TodayView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                StatusHero(title: "今日", subtitle: todaySubtitle, icon: "sun.max.fill", color: .orange)
                NavigationLink { DiaryView(store: model.diary) } label: {
                    Label("日記 · 記録から自動下書き", systemImage: "book.closed")
                        .frame(maxWidth: .infinity, alignment: .leading).glassCard()
                }
                LifeBrief(store: model.life)
                NavigationLink { PaymentHistoryView() } label: {
                    Label("PayPayの支払い明細", systemImage: "yensign.circle")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .glassCard()
                }
#if os(iOS)
                DeviceLocationCard(location: model.deviceLocation)
#endif
                NavigationLink { LocationHistoryView() } label: {
                    Label("GPSの取得履歴・マップ", systemImage: "map.fill")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .glassCard()
                }
                if model.today == nil && model.todayLoadState != .loaded {
                    ResourceStatusView(state: model.todayLoadState, label: "今日") {
                        Task { await model.refresh() }
                    }
                } else {
                    if model.todayLoadState != .loaded {
                        ResourceStatusView(state: model.todayLoadState, label: "今日") {
                            Task { await model.refresh() }
                        }
                    }
                    TaskComposer()
                    HealthCheckinCard(health: model.today?.health)
#if os(iOS)
                    if model.today?.health == nil && !model.isFixture {
                        Button { Task { await model.syncHealth() } } label: {
                            Label("HealthKitを同期", systemImage: "heart.fill")
                                .frame(maxWidth: .infinity)
                        }
                        .buttonStyle(.borderedProminent)
                        .tint(.pink)
                    }
#endif
                    if (model.today?.tasks.isEmpty ?? true) && (model.today?.routines.isEmpty ?? true) {
                        EmptyState(icon: "checkmark.circle", title: "通常タスクはありません", detail: "会話から作った用事は「暮らしのアシスタント」で確認できます。")
                    } else {
                        SectionTitle("タスク")
                        ForEach(model.today?.tasks ?? []) { task in TaskRow(task: task) }
                        SectionTitle("ルーティン")
                        ForEach(model.today?.routines ?? []) { task in TaskRow(task: task) }
                    }
                }
            }.padding()
        }.background(AppBackground()).navigationTitle("今日").refreshable { await model.refresh() }
    }
    private var remaining: Int { (model.today?.tasks.count ?? 0) + (model.today?.routines.filter { !$0.isCompleted }.count ?? 0) }
    private var todaySubtitle: String {
        switch model.todayLoadState {
        case .idle, .loading:
            return "読み込み中…"
        case .failed:
            return model.today == nil ? "読み込みに失敗しました" : "前回のデータを表示中"
        case .loaded:
            return remaining == 0 ? "予定・用事・体調を確認" : "通常タスク・ルーティンがあと\(remaining)件です"
        }
    }
}

#if os(iOS)
struct DeviceLocationCard: View {
    @EnvironmentObject private var model: AppModel
    @ObservedObject var location: DeviceLocationService

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label(location.isRecording ? "GPS記録中・新しい位置待ち" : "GPS記録・停止中", systemImage: "location.fill")
                    .appFont(.headline)
                    .foregroundStyle(.cyan)
                Spacer()
                if let reading = location.lastReading {
                    Text(reading.isApproximate ? "概算位置" : "正確な位置")
                        .badgeStyle(reading.isApproximate ? .orange : .green)
                }
            }

            locationContent
            if let reading = location.lastReading {
                VStack(alignment: .leading, spacing: 8) {
                    LabeledContent("緯度", value: reading.latitude.formatted(.number.precision(.fractionLength(6))))
                    LabeledContent("経度", value: reading.longitude.formatted(.number.precision(.fractionLength(6))))
                    LabeledContent("水平精度", value: "約\(reading.horizontalAccuracy.formatted(.number.precision(.fractionLength(0)))) m")
                }.appFont(.subheadline)
            }
            if let acquired = location.lastAcquiredAt {
                TimelineView(.periodic(from: .now, by: 60)) { _ in
                    VStack(alignment: .leading) {
                        Text("最終取得時刻 \(acquired.formatted(date: .abbreviated, time: .standard))")
                        Text("取得から \(acquired, style: .relative) 経過")
                    }.appFont(.caption)
                }
            }

            Button {
                location.requestLocation()
            } label: {
                HStack {
                    if isRequesting { ProgressView() }
                    Label(buttonTitle, systemImage: "location.circle")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.cyan)
            .disabled(isRequesting || model.isFixture)

            Button {
                if location.isRecording { location.stopRecording() }
                else { location.startRecording() }
            } label: {
                Label(location.isRecording ? "記録を停止" : "移動の記録を開始", systemImage: location.isRecording ? "stop.circle.fill" : "record.circle")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .tint(location.isRecording ? .red : .cyan)
            .disabled((isRequesting && !location.isRecording) || model.isFixture)

            Text("約100 mの移動を基準に記録します。固定間隔ではなく、静止中などは取得時刻が更新されない場合があります。時刻が古いだけでは停止とは判断できません。画面を閉じても記録しますが、アプリ終了・更新・端末再起動後は再度開始してください。取得した位置は端末に保存し、Mac miniへ同期します。")
                .appFont(.caption2)
                .foregroundStyle(.secondary)
            if !location.pending.isEmpty {
                Text("未同期あり・\(location.pending.count)件").appFont(.caption)
                Button("今すぐ同期") { Task { await location.syncPending(force: true) } }
                    .disabled(location.isSyncing)
                DisclosureGroup("未同期の取得履歴") {
                    ForEach(Array(location.pending.suffix(20).reversed())) { event in
                        Text("\(event.date?.formatted(date: .abbreviated, time: .standard) ?? event.timestamp) · 精度 約\(Int(event.horizontal_accuracy)) m")
                            .appFont(.caption)
                    }
                }
            }
            if let message = location.syncMessage { Text(message).appFont(.caption).foregroundStyle(.orange) }
            if location.isSyncing { ProgressView("Mac miniへ同期中…") }
            if let message = location.diagnosticsMessage { Text(message).appFont(.caption).foregroundStyle(.orange) }
            if let synced = location.lastSyncedAt {
                Text("最終同期成功 \(synced.formatted(date: .abbreviated, time: .standard))").appFont(.caption2)
            }
        }
        .glassCard()
    }

    @ViewBuilder
    private var locationContent: some View {
        switch location.state {
        case .idle:
            Text("ボタンを押すまで位置情報にはアクセスしません。")
                .foregroundStyle(.secondary)
        case .requestingAuthorization:
            Label("位置情報の利用許可を確認しています…", systemImage: "hand.raised.fill")
                .foregroundStyle(.secondary)
        case .locating:
            Label("現在地を取得しています…", systemImage: "location.magnifyingglass")
                .foregroundStyle(.secondary)
        case .located:
            Text(location.isRecording ? "記録を継続しています。必要なときは現在地を再取得できます。" : "保存済みの取得結果です。移動の記録は停止しています。")
                .appFont(.caption).foregroundStyle(.secondary)
        case .denied:
            Label("位置情報が許可されていません。iPhoneの設定でDaymeldの位置情報を許可してください。", systemImage: "location.slash.fill")
                .foregroundStyle(.orange)
        case .restricted:
            Label("この端末では位置情報の利用が制限されています。", systemImage: "lock.fill")
                .foregroundStyle(.orange)
        case .servicesDisabled:
            Label("iPhoneの位置情報サービスがオフです。", systemImage: "location.slash.fill")
                .foregroundStyle(.orange)
        case .failed(let message):
            Label(message, systemImage: "exclamationmark.triangle.fill")
                .foregroundStyle(.orange)
        }
    }

    private var isRequesting: Bool {
        if location.isRefreshingLocation { return true }
        switch location.state {
        case .requestingAuthorization: return true
        case .locating: return !location.isRecording
        default: return false
        }
    }

    private var buttonTitle: String {
        if location.isRecording || location.lastAcquiredAt != nil { return "現在地を再取得して保存" }
        return "現在地を一回取得して保存"
    }
}
#endif

#if os(macOS)
private typealias HistoryMapRepresentable = NSViewRepresentable
private typealias HistoryMapColor = NSColor
#else
private typealias HistoryMapRepresentable = UIViewRepresentable
private typealias HistoryMapColor = UIColor
#endif

private final class HistoryMapPoint: MKPointAnnotation {
    let event: LocationEvent
    init(_ event: LocationEvent) {
        self.event = event
        super.init()
        coordinate = CLLocationCoordinate2D(latitude: event.latitude, longitude: event.longitude)
        title = event.date?.formatted(date: .omitted, time: .standard) ?? event.timestamp
        subtitle = "水平精度 約\(Int(event.horizontal_accuracy)) m"
    }
}

private struct HistoryNativeMap: HistoryMapRepresentable {
    let events: [LocationEvent]

    func makeCoordinator() -> Coordinator { Coordinator() }
    #if os(macOS)
    func makeNSView(context: Context) -> MKMapView { makeMap(context) }
    func updateNSView(_ map: MKMapView, context: Context) { updateMap(map, context) }
    #else
    func makeUIView(context: Context) -> MKMapView { makeMap(context) }
    func updateUIView(_ map: MKMapView, context: Context) { updateMap(map, context) }
    #endif

    private func makeMap(_ context: Context) -> MKMapView {
        let map = MKMapView()
        map.delegate = context.coordinator
        map.showsUserLocation = false
        return map
    }

    private func updateMap(_ map: MKMapView, _ context: Context) {
        let coordinator = context.coordinator
        let ids = events.map(\.id)
        let changed = coordinator.ids != ids
        if changed {
            coordinator.ids = ids
            map.removeAnnotations(map.annotations)
            map.addAnnotations(events.map(HistoryMapPoint.init))
            if events.count > 1 { map.showAnnotations(map.annotations, animated: false) }
        }
        if changed, events.count == 1, let target = events.first {
            let center = CLLocationCoordinate2D(latitude: target.latitude, longitude: target.longitude)
            map.setRegion(MKCoordinateRegion(center: center, latitudinalMeters: max(1000, target.horizontal_accuracy * 4), longitudinalMeters: max(1000, target.horizontal_accuracy * 4)), animated: false)
        }
    }

    final class Coordinator: NSObject, MKMapViewDelegate {
        var ids: [String] = []

        func mapView(_ mapView: MKMapView, viewFor annotation: MKAnnotation) -> MKAnnotationView? {
            guard let point = annotation as? HistoryMapPoint else { return nil }
            let view = MKMarkerAnnotationView(annotation: point, reuseIdentifier: "location")
            view.markerTintColor = point.event.is_approximate ? HistoryMapColor.systemOrange : HistoryMapColor.systemCyan
            view.canShowCallout = true
            return view
        }

    }
}

struct LocationHistoryView: View {
    @EnvironmentObject private var model: AppModel
    @State private var day = Date.now
    @State private var events: [LocationEvent] = []
    @State private var total = 0
    @State private var hasMore = false
    @State private var nextOffset = 0
    @State private var isLoading = false
    @State private var error: String?
    @State private var requestID = UUID()

    private var startOfDay: Date { Calendar.current.startOfDay(for: day) }

    var body: some View {
        Group {
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 16) {
                    DatePicker("取得日", selection: $day, displayedComponents: .date)
                    Text("\(TimeZone.current.identifier) の日付で表示 · \(events.count) / \(total)件")
                        .appFont(.caption).foregroundStyle(.secondary)
                    if !events.isEmpty {
                        historyMap.id("history-map")
                        Text("地図に取得地点を表示しています。取得時刻と精度はピンや下の履歴で確認できます。")
                            .appFont(.caption).foregroundStyle(.secondary)
                    }
                    if let error {
                        Text(error).foregroundStyle(.orange)
                        Button("再試行") { Task { await load() } }.disabled(isLoading)
                    } else if events.isEmpty && !isLoading {
                        ContentUnavailableView("この日の取得履歴はありません", systemImage: "map", description: Text("iPhoneの「今日」でGPSの記録を開始してください。未同期の記録はMac miniへの同期後に表示されます。"))
                    }
                    if isLoading { ProgressView("履歴を読み込み中…") }
                    ForEach(events) { event in
                        readingDetails(event)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .glassCard()
                    }
                    if hasMore {
                        Button("さらに500件を表示") { Task { await load(more: true) } }.disabled(isLoading)
                    }
                }
                .padding()
            }
            .navigationTitle("GPS取得履歴")
            .toolbar { Button { Task { await load() } } label: { Image(systemName: "arrow.clockwise") }.disabled(isLoading) }
            .refreshable { await load() }
            .task(id: startOfDay) { await load() }
        }
    }

    private var historyMap: some View {
        HistoryNativeMap(events: events)
            .frame(maxWidth: .infinity)
            .frame(height: 320)
            .clipShape(RoundedRectangle(cornerRadius: 16))
    }

    private func readingDetails(_ event: LocationEvent) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(event.date?.formatted(date: .abbreviated, time: .standard) ?? event.timestamp).appFont(.headline)
            Text("緯度 \(event.latitude.formatted(.number.precision(.fractionLength(6)))) · 経度 \(event.longitude.formatted(.number.precision(.fractionLength(6))))").appFont(.caption)
            Text("水平精度 約\(Int(event.horizontal_accuracy)) m · \(event.is_approximate ? "概算位置" : "正確な位置")").appFont(.caption).foregroundStyle(.secondary)
        }
    }

    @MainActor
    private func load(more: Bool = false) async {
        let id = UUID()
        requestID = id
        isLoading = true
        error = nil
        let start = startOfDay
        let end = Calendar.current.date(byAdding: .day, value: 1, to: start)!
        let offset = more ? nextOffset : 0
        if !more { events = []; total = 0; hasMore = false }
        defer { if requestID == id { isLoading = false } }
        if model.isFixture {
            events = [LocationEvent(timestamp: start.addingTimeInterval(36000).ISO8601Format(), latitude: 35.6812, longitude: 139.7671, horizontal_accuracy: 35, is_approximate: false)]
            total = events.count
            return
        }
        do {
            let result: LocationHistoryResponse = try await APIClient.shared.get("api/locations", queryItems: [
                URLQueryItem(name: "start", value: start.ISO8601Format()),
                URLQueryItem(name: "end", value: end.ISO8601Format()),
                URLQueryItem(name: "offset", value: String(offset))
            ])
            guard requestID == id, !Task.isCancelled else { return }
            // Duplicate delivery during concurrent sync must not create duplicate SwiftUI IDs.
            let existing = Set(events.map(\.id))
            events += result.items.filter { !existing.contains($0.id) }
            total = result.total
            hasMore = result.has_more
            nextOffset = offset + result.items.count
        } catch {
            guard requestID == id, !Task.isCancelled else { return }
            self.error = "GPS履歴を取得できませんでした。Mac miniへの接続を確認してください。"
        }
    }

}

struct TaskComposer: View {
    @EnvironmentObject private var model: AppModel
    @State private var title = ""
    @State private var dueDateEnabled = false
    @State private var dueDate = Date()
    @State private var priority = 2
    @State private var recurrence = "none"
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("タスクを追加", systemImage: "plus.circle.fill")
                .appFont(.headline)
                .foregroundStyle(.orange)
            TextField("今日やること", text: $title)
                .textFieldStyle(.roundedBorder)
            HStack {
                Picker("優先度", selection: $priority) {
                    Text("高").tag(1)
                    Text("中").tag(2)
                    Text("低").tag(3)
                }
                .pickerStyle(.menu)
                Picker("繰り返し", selection: $recurrence) {
                    Text("なし").tag("none")
                    Text("毎日").tag("daily")
                    Text("平日").tag("weekdays")
                    Text("毎週").tag("weekly")
                }
                .pickerStyle(.menu)
            }
            Toggle("期限を設定", isOn: $dueDateEnabled)
                .appFont(.caption)
            if dueDateEnabled {
                DatePicker("期限", selection: $dueDate, displayedComponents: .date)
                    .appFont(.caption)
            }
            Button {
                Task {
                    saving = true
                    let success = await model.createTask(title: title, dueDate: dueDateEnabled ? Self.dateOnly(dueDate) : nil, priority: priority, recurrence: recurrence)
                    if success { title = ""; dueDateEnabled = false; recurrence = "none"; priority = 2 }
                    saving = false
                }
            } label: {
                HStack {
                    if saving { ProgressView() }
                    Text("追加")
                }
                .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.orange)
            .disabled(title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || saving)
        }
        .glassCard()
    }

    private static func dateOnly(_ date: Date) -> String {
        let components = Calendar.current.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", components.year ?? 0, components.month ?? 0, components.day ?? 0)
    }
}

struct TaskRow: View {
    @EnvironmentObject private var model: AppModel
    let task: PlannerTask
    @State private var confirmDelete = false
    @State private var actionInFlight = false

    var body: some View {
        HStack(spacing: 12) {
            Button {
                guard !actionInFlight else { return }
                actionInFlight = true
                Task {
                    await model.toggle(task)
                    actionInFlight = false
                }
            } label: {
                HStack(spacing: 14) {
                    Image(systemName: task.isCompleted ? "checkmark.circle.fill" : "circle")
                        .appFont(.title2)
                        .foregroundStyle(task.isCompleted ? .green : .secondary)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(task.title).foregroundStyle(.primary).multilineTextAlignment(.leading)
                        HStack(spacing: 6) {
                            if let due = task.dueDate {
                                Text(Self.dateLabel(due))
                                    .foregroundStyle(Self.isOverdue(due) && !task.isCompleted ? .red : .secondary)
                            } else if task.recurrence == "none" {
                                Text("期限なし").foregroundStyle(.secondary)
                            }
                            if task.priority == 1 { Text("高").badgeStyle(.red) }
                            if task.recurrence != "none" { Text(Self.recurrenceLabel(task.recurrence)).badgeStyle(.indigo) }
                        }
                        .appFont(.caption2)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                if actionInFlight { ProgressView().controlSize(.small) }
            }
            .buttonStyle(.plain)
            .disabled(actionInFlight)
            Spacer(minLength: 0)
            Menu {
                Button("削除", role: .destructive) { confirmDelete = true }
            } label: {
                Image(systemName: "ellipsis.circle")
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("タスクの操作")
            }
        }
        .glassCard()
        .confirmationDialog("このタスクを削除しますか？", isPresented: $confirmDelete) {
            Button("削除", role: .destructive) { Task { _ = await model.deleteTask(task) } }
            Button("キャンセル", role: .cancel) {}
        }
    }

    private static func dateLabel(_ value: String) -> String {
        guard let date = DateFormatter.isoDate.date(from: value) else { return value }
        return date.formatted(.dateTime.month().day())
    }

    private static func isOverdue(_ value: String) -> Bool {
        value < DateFormatter.isoDate.string(from: .now)
    }

    private static func recurrenceLabel(_ value: String) -> String {
        switch value {
        case "daily": "毎日"
        case "weekdays": "平日"
        case "weekly": "毎週"
        default: value
        }
    }
}

struct HealthCheckinCard: View {
    @EnvironmentObject private var model: AppModel
    let health: HealthSnapshot?
    @State private var fatigue = 3
    @State private var mood = 3
    @State private var note = ""
    @State private var saving = false

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Label("体調チェックイン", systemImage: "face.smiling")
                .appFont(.headline)
                .foregroundStyle(.pink)
            Picker("疲労度", selection: $fatigue) {
                ForEach(1...5, id: \.self) { Text("\($0)").tag($0) }
            }
            .pickerStyle(.segmented)
            .accessibilityLabel("疲労度 1から5")
            Picker("気分", selection: $mood) {
                ForEach(1...5, id: \.self) { Text("\($0)").tag($0) }
            }
            .pickerStyle(.segmented)
            .accessibilityLabel("気分 1から5")
            TextField("メモ（任意）", text: $note, axis: .vertical)
                .lineLimit(2...4)
                .textFieldStyle(.roundedBorder)
            Button {
                Task {
                    saving = true
                    let current = health
                    let snapshot = HealthSnapshot(
                        date: current?.date ?? model.today?.date ?? Self.dateOnly(.now),
                        sleepMinutes: current?.sleepMinutes, steps: current?.steps,
                        restingHeartRate: current?.restingHeartRate, hrvMS: current?.hrvMS,
                        respiratoryRate: current?.respiratoryRate, fatigue: fatigue,
                        mood: mood, note: note
                    )
                    _ = await model.saveHealthCheckin(snapshot)
                    saving = false
                }
            } label: {
                HStack { if saving { ProgressView() }; Text("体調を保存") }
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.bordered)
            .tint(.pink)
            .disabled(saving)
        }
        .glassCard()
        .onAppear {
            fatigue = health?.fatigue ?? 3
            mood = health?.mood ?? 3
            note = health?.note ?? ""
        }
    }

    private static func dateOnly(_ date: Date) -> String {
        let components = Calendar.current.dateComponents([.year, .month, .day], from: date)
        return String(format: "%04d-%02d-%02d", components.year ?? 0, components.month ?? 0, components.day ?? 0)
    }
}

struct HealthCard: View {
    @EnvironmentObject private var model: AppModel
    let health: HealthSnapshot
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack {
                Label("HealthKit", systemImage: "heart.fill")
                    .appFont(.headline)
                    .foregroundStyle(.pink)
                Spacer()
#if os(iOS)
                if model.isFixture {
                    Text("fixtureデータ")
                        .appFont(.caption)
                        .foregroundStyle(.secondary)
                } else {
                    Button("同期") { Task { await model.syncHealth() } }
                        .appFont(.caption, weight: .semibold)
                }
#else
                Text("iPhoneから同期済み")
                    .appFont(.caption)
                    .foregroundStyle(.secondary)
#endif
            }
            HStack { Metric(value: health.sleepMinutes.map { "\($0 / 60)h \($0 % 60)m" } ?? "—", label: "睡眠"); Metric(value: health.steps.map { $0.formatted() } ?? "—", label: "歩数"); Metric(value: health.hrvMS.map { String(format: "%.0f", $0) } ?? "—", label: "HRV") }
            HStack { Metric(value: health.restingHeartRate.map { String(format: "%.0f", $0) } ?? "—", label: "安静時心拍"); Metric(value: health.respiratoryRate.map { String(format: "%.1f", $0) } ?? "—", label: "呼吸数"); Metric(value: health.fatigue.map(String.init) ?? "—", label: "疲労度") }
            HStack { Metric(value: health.mood.map(String.init) ?? "—", label: "気分"); Spacer() }
            if let note = health.note, !note.isEmpty { Text(note).appFont(.subheadline).foregroundStyle(.secondary).textSelection(.enabled) }
        }.glassCard()
    }
}

struct EmailView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var completingEmailIDs = Set<String>()
    @State private var completionFeedback = 0

    var body: some View {
        List {
            if model.emailLoadState != .loaded {
                ResourceStatusView(state: model.emailLoadState, label: "メール") {
                    Task { await model.refresh() }
                }
                .listRowBackground(Color.clear)
            }
            if let error = model.emailSyncError {
                Text(error).appFont(.footnote).foregroundStyle(.orange)
                    .listRowBackground(Color.clear)
            }
            ForEach(model.emails) { email in
                EmailCard(email: email, completionPresented: completingEmailIDs.contains(email.threadID))
                    .listRowInsets(EdgeInsets(top: 7, leading: 0, bottom: 7, trailing: 0))
                    .listRowBackground(Color.clear)
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        Button { completeBySwipe(email) } label: {
                            Label("完了", systemImage: "checkmark.circle.fill")
                        }.tint(.mint)
                    }
            }
            if model.emails.isEmpty && model.emailLoadState == .loaded {
                EmptyState(icon: "tray", title: "未読メールはありません", detail: "迷惑メールとゴミ箱を除く未読メールを表示します。")
                    .listRowBackground(Color.clear)
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(AppBackground())
        .navigationTitle("未読メール")
        .refreshable { await model.refresh() }
        .sensoryFeedback(.success, trigger: completionFeedback)
    }

    private func completeBySwipe(_ email: EmailReminder) {
        guard !completingEmailIDs.contains(email.threadID) else { return }
        completingEmailIDs.insert(email.threadID)
        completionFeedback += 1
        Task {
            if !reduceMotion {
                try? await Task.sleep(for: .milliseconds(260))
            }
            _ = await model.act(on: email, action: "done")
            completingEmailIDs.remove(email.threadID)
        }
    }
}

struct EmailCard: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    let email: EmailReminder
    var completionPresented = false
    @State private var actionInFlight = false

    var body: some View {
        ZStack {
            VStack(alignment: .leading, spacing: 10) {
                NavigationLink(destination: EmailDetailView(email: email)) {
                    VStack(alignment: .leading, spacing: 7) {
                        HStack { Text(email.sender).appFont(.caption, weight: .semibold).foregroundStyle(.cyan); Spacer(); if email.importance == "high" { Text("重要").badgeStyle(.red) } }
                        if let received = email.receivedAt?.emailReceivedDisplay() {
                            Text(received).appFont(.caption2).foregroundStyle(.secondary)
                        }
                        Text(email.subject).appFont(.headline).foregroundStyle(.primary)
                        Text(email.requiredAction).appFont(.subheadline).foregroundStyle(.secondary)
                        if !email.reason.isEmpty { Text(email.reason).appFont(.caption).foregroundStyle(.secondary) }
                        HStack(spacing: 8) {
                            Text(Self.statusLabel(email.status))
                                .badgeStyle(email.status == "awaiting_reply" ? .orange : .secondary)
                            if let dueDate = email.dueDate { Text("期限 \(dueDate)").appFont(.caption2).foregroundStyle(.red) }
                        }
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                }
                HStack {
                    Spacer()
                    if actionInFlight { ProgressView().controlSize(.small) }
                    Menu {
                        if model.emailCanMarkRead { Button("既読") { perform("read") } }
                        Button("明日へ保留") { perform("snooze") }
                        Button("対応不要") { perform("dismiss") }
                    } label: {
                        Label("その他の操作", systemImage: "ellipsis.circle")
                    }
                    .disabled(actionInFlight)
                    Button("完了") { perform("done") }
                        .buttonStyle(.borderedProminent)
                        .tint(.mint)
                        .disabled(actionInFlight)
                }
                .appFont(.caption, weight: .semibold)
            }
            if completionPresented {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 42, weight: .semibold))
                    .foregroundStyle(.mint)
                    .symbolEffect(.bounce, value: completionPresented)
                    .transition(.scale(scale: 0.55).combined(with: .opacity))
            }
        }
        .opacity(completionPresented ? 0.72 : 1)
        .scaleEffect(completionPresented && !reduceMotion ? 0.96 : 1)
        .animation(.easeOut(duration: 0.2), value: completionPresented)
        .transition(.asymmetric(insertion: .opacity, removal: .opacity.combined(with: .scale(scale: 0.88))))
        .glassCard()
    }

    private func perform(_ action: String) {
        guard !actionInFlight else { return }
        actionInFlight = true
        Task {
            _ = await model.act(on: email, action: action)
            actionInFlight = false
        }
    }

    private static func statusLabel(_ status: String?) -> String {
        switch status {
        case "awaiting_reply": "返信待ち"
        case "snoozed": "明日へ保留"
        case "done": "完了"
        case "dismissed": "対応不要"
        default: "未対応"
        }
    }
}

struct EmailDetailView: View {
    @EnvironmentObject private var model: AppModel
    let email: EmailReminder
    @State private var content: EmailThreadContent?
    @State private var errorMessage: String?
    @State private var loading = false

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 14) {
                Text(email.subject).appFont(.title3, weight: .bold)
                if loading {
                    ProgressView("本文を取得しています…")
                } else if let errorMessage {
                    Text(errorMessage).appFont(.subheadline).foregroundStyle(.orange)
                    Button("再取得") { Task { await load() } }.buttonStyle(.borderedProminent)
                } else if let content {
                    if content.messages.isEmpty {
                        EmptyState(icon: "envelope.open", title: "本文はありません", detail: "このスレッドには表示できる本文がありません。")
                    } else {
                        ForEach(Array(content.messages.enumerated()), id: \.offset) { _, message in
                            VStack(alignment: .leading, spacing: 7) {
                                Text(message.sender).appFont(.caption, weight: .semibold).foregroundStyle(.cyan)
                                Text(message.receivedAt.relativeTime).appFont(.caption2).foregroundStyle(.secondary)
                                Text(message.body.isEmpty ? "本文を取得できませんでした。" : message.body)
                                    .appFont(.body).textSelection(.enabled)
                            }.glassCard()
                        }
                    }
                }
            }.padding()
        }
        .background(AppBackground())
        .navigationTitle("メール本文")
        .task { await load() }
    }

    private func load() async {
        guard !loading else { return }
        loading = true
        errorMessage = nil
        defer { loading = false }
        do {
            content = try await model.fetchEmailContent(threadID: email.threadID)
        } catch {
            errorMessage = error.localizedDescription
        }
    }
}

struct NewsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var query = ""
    @State private var category = "すべて"
    @State private var savedOnly = false
    @State private var visibleLimit = 50

    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                if model.newsLoadState != .loaded {
                    ResourceStatusView(state: model.newsLoadState, label: "ニュース") {
                        Task { await model.refresh() }
                    }
                }
                // A failed initial load has no snapshot to filter.  A failed
                // refresh with existing articles still keeps the previous
                // snapshot visible so the user can continue reading.
                if model.newsLoadState == .loaded || !model.articles.isEmpty {
                    NewsFilterBar(query: $query, category: $category, savedOnly: $savedOnly, categories: categories)
                    let articles = filteredArticles
                    if articles.isEmpty {
                        EmptyState(icon: savedOnly ? "bookmark" : "magnifyingglass", title: savedOnly ? "あとで読む記事はありません" : "条件に一致する記事はありません", detail: savedOnly ? "記事カードの「あとで読む」から保存できます。" : "検索語や分野を変えてお試しください。")
                    } else {
                        ForEach(articles.prefix(visibleLimit)) { article in
                            ArticleCard(article: article)
                        }
                        if articles.count > visibleLimit {
                            Button("さらに表示（残り \(articles.count - visibleLimit)件）") {
                                visibleLimit += 50
                            }
                            .frame(maxWidth: .infinity)
                            .buttonStyle(.bordered)
                        }
                    }
                }
            }
            .padding()
        }
        .background(AppBackground())
        .navigationTitle("ニュース")
        .refreshable { await model.refresh() }
        .onChange(of: query) { _, _ in visibleLimit = 50 }
        .onChange(of: category) { _, _ in visibleLimit = 50 }
        .onChange(of: savedOnly) { _, _ in visibleLimit = 50 }
    }

    private var categories: [String] {
        ["すべて"] + Array(Set(model.articles.map(\.category))).sorted()
    }

    private var filteredArticles: [Article] {
        let normalizedQuery = query.trimmingCharacters(in: .whitespacesAndNewlines).localizedLowercase
        return model.articles
            .filter { !model.hiddenArticleIDs.contains($0.id) }
            .filter { category == "すべて" || $0.category == category }
            .filter { !savedOnly || model.savedArticleIDs.contains($0.id) }
            .filter {
                guard !normalizedQuery.isEmpty else { return true }
                return [$0.title, $0.summary, $0.source, $0.category]
                    .joined(separator: " ")
                    .localizedLowercase
                    .contains(normalizedQuery)
            }
            .sorted { left, right in
                (left.publishedAt.iso8601Date ?? .distantPast) > (right.publishedAt.iso8601Date ?? .distantPast)
            }
    }
}

struct NewsFilterBar: View {
    @Binding var query: String
    @Binding var category: String
    @Binding var savedOnly: Bool
    let categories: [String]

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            TextField("タイトル・概要・情報元を検索", text: $query)
                .textFieldStyle(.roundedBorder)
                .accessibilityLabel("ニュース検索")
            HStack {
                Picker("分野", selection: $category) {
                    ForEach(categories, id: \.self) { Text($0).tag($0) }
                }
                .pickerStyle(.menu)
                Toggle("あとで読む", isOn: $savedOnly)
                    .toggleStyle(.button)
                    .tint(.indigo)
            }
        }
        .glassCard()
    }
}

struct ArticleCard: View {
    @EnvironmentObject private var model: AppModel
    let article: Article
    @State private var confirmHide = false

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            Link(destination: article.url) {
                VStack(alignment: .leading, spacing: 10) {
                    if let imageURL = article.imageURL {
                        AsyncImage(url: imageURL) { phase in
                            switch phase {
                            case .success(let image):
                                image.resizable().scaledToFill()
                                    .frame(height: 170)
                                    .clipShape(RoundedRectangle(cornerRadius: 16))
                                    .clipped()
                            case .failure:
                                EmptyView()
                            default:
                                ProgressView().frame(maxWidth: .infinity).frame(height: 80)
                            }
                        }
                    }
                    HStack {
                        Text(article.category).badgeStyle(.indigo)
                        if model.readArticleIDs.contains(article.id) { Text("既読").badgeStyle(.secondary) }
                        Spacer()
                        Text(article.source).appFont(.caption).foregroundStyle(.secondary)
                    }
                    Text(article.title).appFont(.headline).foregroundStyle(.primary).multilineTextAlignment(.leading)
                    if !article.summary.isEmpty { Text(article.summary).appFont(.subheadline).foregroundStyle(.secondary).lineLimit(3) }
                    Text(Self.dateLabel(article.publishedAt)).appFont(.caption2).foregroundStyle(.tertiary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .buttonStyle(.plain)
            .simultaneousGesture(TapGesture().onEnded {
                Task { _ = await model.markArticleRead(article) }
            })
            HStack {
                Button(model.savedArticleIDs.contains(article.id) ? "保存済み" : "あとで読む") {
                    model.toggleArticleSaved(article)
                }
                .buttonStyle(.borderless)
                .foregroundStyle(model.savedArticleIDs.contains(article.id) ? .indigo : .secondary)
                Spacer()
                Button("表示しない", role: .destructive) { confirmHide = true }
                    .buttonStyle(.borderless)
            }
            .appFont(.caption, weight: .semibold)
        }
        .glassCard()
        .confirmationDialog("この記事を今後表示しませんか？", isPresented: $confirmHide) {
            Button("表示しない", role: .destructive) { Task { _ = await model.hideArticle(article) } }
            Button("キャンセル", role: .cancel) {}
        }
    }

    private static func dateLabel(_ value: String) -> String {
        guard let date = value.iso8601Date else { return value }
        return date.formatted(.dateTime.year().month().day().hour().minute())
    }
}

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @AppStorage("serverURL") private var serverURL = "https://sk-mins-mac-mini.tailc193b2.ts.net/"
    @State private var healthToken = ""
    @State private var tokenStatus = ""
    private var versionText: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "—"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        return build.isEmpty ? version : "\(version) (\(build))"
    }
    var body: some View {
        Form {
            Section("接続") {
#if os(iOS)
                TextField("サーバーURL", text: $serverURL)
                    .textInputAutocapitalization(.never)
                    .keyboardType(.URL)
                SecureField("HealthKit同期トークン", text: $healthToken)
                Button("トークンを安全に保存") {
                    do {
                        try SecretStore.saveHealthToken(healthToken)
                        tokenStatus = "Keychainへ保存しました"
                    } catch {
                        tokenStatus = error.localizedDescription
                    }
                }
                if !tokenStatus.isEmpty {
                    Text(tokenStatus).appFont(.caption).foregroundStyle(.secondary)
                }
#else
                TextField("サーバーURL", text: $serverURL)
                    .textFieldStyle(.roundedBorder)
                Text("Agent・タスク・メール・ニュースは、このMac mini APIをiPhone版と共有します。")
                    .appFont(.caption)
                    .foregroundStyle(.secondary)
#endif
            }
            Section("プライバシー") {
#if os(iOS)
                Label("健康情報はtailnet内のMac miniだけへ送信します", systemImage: "lock.shield")
#else
                Label("健康情報はiPhoneが同期したMac mini上の集計だけを表示します", systemImage: "lock.shield")
#endif
            }
            Section("バージョン") {
                LabeledContent("Daymeld", value: versionText)
                if let info = model.deploymentInfo {
                    LabeledContent("サーバー", value: info.version)
                    if let date = info.deployedDate {
                        LabeledContent("デプロイ", value: date.runtimeDisplay)
                    }
                }
            }
        }
            .navigationTitle("設定")
#if os(iOS)
            .onAppear { healthToken = (try? SecretStore.readHealthToken()) ?? "" }
#endif
    }
}

struct StatusHero: View {
    let title: String; let subtitle: String; let icon: String; let color: Color
    var body: some View { HStack(spacing: 18) { ZStack { Circle().fill(color.gradient).frame(width: 58, height: 58); Image(systemName: icon).appFont(.title2).foregroundStyle(.black) }; VStack(alignment: .leading, spacing: 4) { Text(title).appFont(.title2, weight: .bold); Text(subtitle).foregroundStyle(.secondary) }; Spacer() }.padding(20).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 28)).overlay(RoundedRectangle(cornerRadius: 28).stroke(color.opacity(0.25))) }
}
struct Metric: View { let value: String; let label: String; var body: some View { VStack(alignment: .leading) { Text(value).appFont(.title3, weight: .bold, monospacedDigit: true); Text(label).appFont(.caption).foregroundStyle(.secondary) }.frame(maxWidth: .infinity, alignment: .leading) } }
struct SectionTitle: View { let title: String; init(_ title: String) { self.title = title }; var body: some View { Text(title).appFont(.title3, weight: .bold).frame(maxWidth: .infinity, alignment: .leading) } }
struct EmptyState: View { let icon: String; let title: String; let detail: String; var body: some View { VStack(spacing: 12) { Image(systemName: icon).appFont(.largeTitle).foregroundStyle(.secondary); Text(title).appFont(.headline); Text(detail).appFont(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center) }.frame(maxWidth: .infinity).padding(40).glassCard() } }
private enum ScreenRefreshFreshness {
    case fresh
    case aging
    case stale

    init(updatedAt: Date, now: Date) {
        let elapsed = max(0, now.timeIntervalSince(updatedAt))
        if elapsed < 5 * 60 {
            self = .fresh
        } else if elapsed < 10 * 60 {
            self = .aging
        } else {
            self = .stale
        }
    }

    var color: Color {
        switch self {
        case .fresh: .green
        case .aging: Color(red: 0.72, green: 0.86, blue: 0.35)
        case .stale: .yellow
        }
    }

    var accessibilityLabel: String {
        switch self {
        case .fresh: "更新から5分未満"
        case .aging: "更新から5分以上"
        case .stale: "更新から10分以上"
        }
    }
}

struct RuntimeInfo: View {
    let info: DeploymentInfo?
    let refreshedAt: Date?
    var body: some View {
        VStack(spacing: 8) {
            if let info {
                HStack {
                    Label("稼働 \(info.version)", systemImage: "shippingbox")
                    Spacer()
                    if let date = info.deployedDate { Text("デプロイ \(date.runtimeDisplay)") }
                }
            }
            NativeReleaseStatus(info: info)
            if let refreshedAt {
                TimelineView(.periodic(from: refreshedAt, by: 60)) { context in
                    let freshness = ScreenRefreshFreshness(updatedAt: refreshedAt, now: context.date)
                    HStack {
                        Label("画面更新", systemImage: "arrow.clockwise")
                        Spacer()
                        Text(refreshedAt.runtimeDisplay)
                    }
                    .foregroundStyle(freshness.color)
                    .accessibilityElement(children: .combine)
                    .accessibilityValue("\(refreshedAt.runtimeDisplay)、\(freshness.accessibilityLabel)")
                }
            }
        }
        .appFont(.caption)
        .foregroundStyle(.secondary)
        .padding(.horizontal, 4)
    }
}

private struct NativeReleaseStatus: View {
    let info: DeploymentInfo?

    private var updateAvailableLabel: String {
#if os(iOS)
        "SideStore更新あり"
#else
        "アプリ更新あり"
#endif
    }

    private var installedVersion: String? {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String
    }

    private var installedVersionText: String {
        let version = installedVersion ?? "—"
        let build = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? ""
        return build.isEmpty || build == version ? version : "\(version) (\(build))"
    }

    private var installedIcon: String {
#if os(iOS)
        "iphone"
#else
        "desktopcomputer"
#endif
    }

    var body: some View {
        HStack {
            Label("インストール済み", systemImage: installedIcon)
            Spacer()
            Text(installedVersionText)
        }
        .accessibilityElement(children: .combine)
        if let installedVersion, let releaseVersion = info?.nativeReleaseVersion {
            let isLatest = installedVersion == releaseVersion
            HStack {
                Label(
                    isLatest ? "アプリ最新版" : updateAvailableLabel,
                    systemImage: isLatest ? "checkmark.circle.fill" : "arrow.down.circle.fill"
                )
                Spacer()
                Text(isLatest ? installedVersion : "\(installedVersion) → \(releaseVersion)")
            }
            .foregroundStyle(isLatest ? .green : .yellow)
            .accessibilityElement(children: .combine)
        } else {
            HStack {
                Label("配布版は未確認", systemImage: "questionmark.circle")
                Spacer()
                Text("サーバー未接続")
            }
            .accessibilityElement(children: .combine)
        }
    }
}

private extension Date {
    var runtimeDisplay: String { formatted(.dateTime.month().day().hour().minute()) }
}

private extension DateFormatter {
    static let isoDate: DateFormatter = {
        let formatter = DateFormatter()
        formatter.calendar = Calendar(identifier: .gregorian)
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.timeZone = .current
        formatter.dateFormat = "yyyy-MM-dd"
        return formatter
    }()
}

struct AppBackground: View { var body: some View { LinearGradient(colors: [Color(red: 0.04, green: 0.06, blue: 0.1), Color(red: 0.07, green: 0.05, blue: 0.12)], startPoint: .topLeading, endPoint: .bottomTrailing).ignoresSafeArea() } }

extension View {
    func glassCard() -> some View { self.padding(16).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 22)).overlay(RoundedRectangle(cornerRadius: 22).stroke(.white.opacity(0.08))) }
    func agentTaskCard(accent: Color, selected: Bool = false) -> some View {
        padding(16)
            .background(.thinMaterial, in: RoundedRectangle(cornerRadius: 22))
            .overlay(alignment: .leading) {
                RoundedRectangle(cornerRadius: 2)
                    .fill(accent)
                    .frame(width: 4)
                    .padding(.vertical, 14)
                    .padding(.leading, 5)
            }
            .overlay(
                RoundedRectangle(cornerRadius: 22)
                    .stroke(selected ? accent.opacity(0.95) : accent.opacity(0.28), lineWidth: selected ? 2 : 1)
            )
    }
    func badgeStyle(_ color: Color) -> some View { self.appFont(.caption2, weight: .bold).padding(.horizontal, 8).padding(.vertical, 4).background(color.opacity(0.2), in: Capsule()).foregroundStyle(color) }

}
