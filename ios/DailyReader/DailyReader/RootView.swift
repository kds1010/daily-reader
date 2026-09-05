import SwiftUI
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
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    #if os(macOS)
    @EnvironmentObject private var macAgentKeyboard: MacAgentKeyboardController
    #endif

    var body: some View {
        TabView(selection: $model.selectedTab) {
            NavigationStack { AgentView() }.tabItem { Label("Agent", systemImage: "sparkles") }.tag(0)
            NavigationStack { SoanView() }.tabItem { Label("資料", systemImage: "doc.text") }.tag(5)
            NavigationStack { TodayView() }.tabItem { Label("今日", systemImage: "checkmark.circle") }.tag(1)
            NavigationStack { EmailView() }.tabItem { Label("メール", systemImage: "envelope") }.badge(model.emails.count).tag(2)
            NavigationStack { NewsView() }.tabItem { Label("ニュース", systemImage: "newspaper") }.tag(3)
            NavigationStack { ConversationsView() }.tabItem { Label("会話", systemImage: "waveform") }.tag(4)
            NavigationStack { SettingsView() }.tabItem { Label("設定", systemImage: "gearshape") }.tag(6)
        }
        .tint(.mint)
        .alert("接続できませんでした", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("閉じる", role: .cancel) {}
        } message: { Text(model.errorMessage ?? "") }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                if !Task.isCancelled {
                    await model.refreshAgents()
                }
            }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active {
                Task { await model.refreshAgents() }
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: .openAgentFromNotification)) { _ in
            model.selectedTab = 0
        }
        #if os(macOS)
        .onAppear { macAgentKeyboard.isEnabled = model.selectedTab == 0 }
        .onChange(of: model.selectedTab) { _, tab in
            macAgentKeyboard.isEnabled = tab == 0
        }
        #endif
    }
}

struct ConversationsView: View {
    @EnvironmentObject private var model: AppModel
    @State private var importing = false

    var body: some View {
        List {
            Section {
                Button { importing = true } label: {
                    Label("MP3または文字起こしTXTを取り込む", systemImage: "square.and.arrow.down")
                }
                Text("MP3の原音とTXTの原文はMac miniに保存され、自動削除されません。")
                    .appFont(.caption).foregroundStyle(.secondary)
            }
            Section("音声インボックス") {
                if model.conversationItems.isEmpty {
                    Text("確認待ちの候補はありません。録音の詳細からCodex整理を開始できます。")
                        .appFont(.subheadline).foregroundStyle(.secondary)
                } else {
                    ForEach(model.conversationItems) { item in
                        ConversationInsightCard(item: item, showsRecordingLink: true) {
                            await model.refreshConversations()
                        }
                    }
                }
            }
            if !model.keptConversationItems.isEmpty {
                Section("保存した気づき") {
                    ForEach(model.keptConversationItems) { item in
                        VStack(alignment: .leading, spacing: 7) {
                            Label(item.title, systemImage: insightKindIcon(item.kind))
                                .appFont(.headline)
                            if !item.detail.isEmpty {
                                Text(item.detail).appFont(.subheadline).foregroundStyle(.secondary)
                            }
                            NavigationLink {
                                ConversationDetailView(recordingID: item.recordingID)
                            } label: {
                                Label(item.recordingFilename ?? "録音を表示", systemImage: "waveform")
                                    .appFont(.caption)
                            }
                        }
                    }
                }
            }
            Section("取り込み履歴") {
                ResourceStatusView(state: model.conversationLoadState, label: "会話") {
                    Task { await model.refreshConversations() }
                }
                ForEach(model.conversations) { recording in
                    NavigationLink {
                        ConversationDetailView(recordingID: recording.id)
                    } label: {
                        VStack(alignment: .leading, spacing: 5) {
                            Text(recording.filename).appFont(.headline)
                            HStack {
                                Text(recording.isTranscript ? "テキスト" : "音声")
                                Text(recording.status == "completed" ? "解析済み" : recording.status == "failed" ? "要確認" : "解析中")
                                if let count = recording.insightItemCount, count > 0 {
                                    Text("候補 \(count)件")
                                }
                                Text(ByteCountFormatter.string(fromByteCount: Int64(recording.byteSize), countStyle: .file))
                            }.appFont(.caption).foregroundStyle(.secondary)
                            if let error = recording.error { Text(error).appFont(.caption).foregroundStyle(.orange) }
                            if let error = recording.insightError { Text(error).appFont(.caption).foregroundStyle(.orange) }
                        }
                    }
                }
            }
        }
        .navigationTitle("会話")
        .refreshable { await model.refreshConversations() }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.mp3, .plainText], allowsMultipleSelection: false) { result in
            if case .success(let urls) = result, let url = urls.first {
                Task { await model.importConversationFile(url) }
            } else if case .failure(let error) = result {
                model.errorMessage = error.localizedDescription
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
    @State private var extractionInFlight = false

    var body: some View {
        List {
            if let recording {
                Section("概要") {
                    Text(recording.filename)
                    ForEach(recording.topics ?? []) { topic in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(topic.name).appFont(.headline)
                            Text(topic.summary).appFont(.subheadline).foregroundStyle(.secondary)
                        }
                    }
                    if recording.status == "failed" {
                        Text(recording.error ?? "解析に失敗しました").foregroundStyle(.orange)
                        if !recording.isTranscript {
                            Button("解析を再実行") { Task { await model.analyzeConversation(recordingID); await reload() } }
                        }
                    }
                    insightExtractionControls(recording)
                }
                let awaitingItems = (recording.insightItems ?? []).filter { $0.status == "awaiting_review" }
                if !awaitingItems.isEmpty {
                    Section("確認待ちの候補") {
                        ForEach(awaitingItems) { item in
                            ConversationInsightCard(item: item) { await reload() }
                        }
                    }
                }
                Section("会話") {
                    ForEach(recording.utterances ?? []) { utterance in
                        VStack(alignment: .leading, spacing: 4) {
                            HStack { Text(utterance.speaker ?? "話者").bold(); Spacer(); Text(utterance.topic) }
                                .appFont(.caption).foregroundStyle(.secondary)
                            Text(utterance.text).textSelection(.enabled)
                        }
                    }
                }
            } else { ProgressView("会話を読み込んでいます…") }
        }
        .navigationTitle("解析結果")
        .task { await reload() }
        .confirmationDialog(
            "文字起こしをCodexで整理しますか？",
            isPresented: $showExtractionConfirmation,
            titleVisibility: .visible
        ) {
            Button("Codexで整理") { Task { await extractInsights() } }
            Button("キャンセル", role: .cancel) {}
        } message: {
            Text("この録音の日時、話者、発話時刻、文字起こしだけをCodexへ渡します。原音、GPS、ファイル名、ほかの録音は渡しません。候補が自動でタスク化・実行されることもありません。")
        }
    }

    private func reload() async { recording = await model.loadConversation(recordingID) }

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
                .disabled(!model.conversationLLMAvailable || extractionInFlight)
        default:
            Button("Codexでタスク・決定・アイデアを整理") { showExtractionConfirmation = true }
                .disabled(recording.status != "completed" || !model.conversationLLMAvailable || extractionInFlight)
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
        await reload()
        for _ in 0..<60 {
            guard recording?.insightStatus == "queued" || recording?.insightStatus == "extracting" else { break }
            try? await Task.sleep(for: .seconds(2))
            guard !Task.isCancelled else { break }
            await reload()
        }
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
    @State private var showFullResult = false

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
                    Text(prompt).appFont(.caption).textSelection(.enabled)
                }
                if !task.displayResult.isEmpty {
                    Text(task.error != nil ? "エラー" : "結果")
                        .appFont(.caption, weight: .bold).foregroundStyle(.secondary)
                    Text(task.displayResult)
                        .appFont(.caption)
                        .lineLimit(showFullResult ? nil : 8)
                        .textSelection(.enabled)
                    if task.displayResult.count > 600 {
                        Button(showFullResult ? "結果を折りたたむ" : "結果を全文表示") {
                            showFullResult.toggle()
                        }
                        .appFont(.caption, weight: .semibold)
                        .buttonStyle(.borderless)
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
                Text("詳細（\(currentModel?.displayName ?? "GPT-5.6-Luna")・\(reasoningEffort)）").appFont(.caption)
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
#if os(iOS)
                DeviceLocationCard()
#endif
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
                        EmptyState(icon: "checkmark.circle", title: "今日のタスクはありません", detail: "上のフォームから、今日やることを追加できます。")
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
            return remaining == 0 ? "すべて完了しました" : "あと\(remaining)件です"
        }
    }
}

#if os(iOS)
struct DeviceLocationCard: View {
    @StateObject private var location = DeviceLocationService()

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("現在地", systemImage: "location.fill")
                    .appFont(.headline)
                    .foregroundStyle(.cyan)
                Spacer()
                if case .located(let reading) = location.state {
                    Text(reading.isApproximate ? "概算位置" : "正確な位置")
                        .badgeStyle(reading.isApproximate ? .orange : .green)
                }
            }

            locationContent

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
            .disabled(isRequesting)

            Text("位置情報はこの画面の表示にだけ使用し、送信・保存しません。")
                .appFont(.caption2)
                .foregroundStyle(.secondary)
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
            Label("現在地を一回だけ取得しています…", systemImage: "location.magnifyingglass")
                .foregroundStyle(.secondary)
        case .located(let reading):
            VStack(alignment: .leading, spacing: 8) {
                LabeledContent("緯度", value: reading.latitude.formatted(.number.precision(.fractionLength(6))))
                LabeledContent("経度", value: reading.longitude.formatted(.number.precision(.fractionLength(6))))
                LabeledContent("水平精度", value: "約\(reading.horizontalAccuracy.formatted(.number.precision(.fractionLength(0)))) m")
                LabeledContent("取得時刻", value: reading.timestamp.formatted(date: .omitted, time: .standard))
            }
            .appFont(.subheadline)
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
        switch location.state {
        case .requestingAuthorization, .locating: true
        default: false
        }
    }

    private var buttonTitle: String {
        if case .located = location.state { return "現在地を再取得" }
        return "現在地を取得"
    }
}
#endif

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
