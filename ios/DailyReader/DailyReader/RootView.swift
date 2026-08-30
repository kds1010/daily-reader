import SwiftUI
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

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    #if os(macOS)
    @EnvironmentObject private var macAgentKeyboard: MacAgentKeyboardController
    #endif

    var body: some View {
        TabView(selection: $model.selectedTab) {
            NavigationStack { AgentView() }.tabItem { Label("Agent", systemImage: "sparkles") }.tag(0)
            NavigationStack { TodayView() }.tabItem { Label("今日", systemImage: "checkmark.circle") }.tag(1)
            NavigationStack { EmailView() }.tabItem { Label("メール", systemImage: "envelope") }.badge(model.emails.count).tag(2)
            NavigationStack { NewsView() }.tabItem { Label("ニュース", systemImage: "newspaper") }.tag(3)
            NavigationStack { SettingsView() }.tabItem { Label("設定", systemImage: "gearshape") }.tag(4)
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
                AgentUsageCard()
                    .agentListRow()
                TanomiComposer()
                    .agentListRow()
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
                if activeSnapshot.isEmpty {
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
                Picker("リポジトリ", selection: $repo) {
                    ForEach(model.tanomiRepositories) { item in
                        Text(item.label ?? item.path).tag(item.path)
                    }
                }.pickerStyle(.menu).disabled(model.tanomiRepositories.isEmpty || !model.tanomiAvailable || sending)
                Spacer()
                Button("依頼") {
                    Task {
                        sending = true
                        if await model.createTanomi(prompt: prompt, repo: repo, model: "opus", permissionMode: "acceptEdits") { prompt = "" }
                        sending = false
                    }
                }.buttonStyle(.borderedProminent).disabled(!model.tanomiAvailable || prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || repo.isEmpty || sending)
            }
            if !model.tanomiAvailable && model.tanomiTasks.isEmpty {
                Text(model.tanomiStatusMessage.map { "tanomiを利用できません：\($0)" } ?? "tanomiは現在利用できません。")
                    .appFont(.subheadline).foregroundStyle(.secondary)
            }
        }.glassCard()
        .onAppear { if repo.isEmpty { repo = model.tanomiRepositories.first?.path ?? "" } }
        .onChange(of: model.tanomiRepositories, initial: true) { _, values in
            if !values.contains(where: { $0.path == repo }) { repo = values.first?.path ?? "" }
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
                    Text(task.displayResult).appFont(.caption).textSelection(.enabled)
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
                    if showConversation && fullEvents.isEmpty {
                        Task { fullEvents = await model.agentDetail(job.id)?.events ?? [] }
                    }
                }
                .appFont(.caption, weight: .bold)
                .buttonStyle(.borderless)
                if showConversation {
                    if fullEvents.isEmpty { ProgressView().frame(maxWidth: .infinity) }
                    ForEach(fullEvents) { event in AgentEventRow(event: event) }
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
                StatusHero(title: "今日", subtitle: remaining == 0 ? "すべて完了しました" : "あと\(remaining)件です", icon: "sun.max.fill", color: .orange)
                if let health = model.today?.health {
                    HealthCard(health: health)
                } else {
#if os(iOS)
                    Button { Task { await model.syncHealth() } } label: {
                        Label("HealthKitを同期", systemImage: "heart.fill")
                            .frame(maxWidth: .infinity)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.pink)
#else
                    Label("健康情報はiPhoneから同期すると表示されます", systemImage: "iphone")
                        .frame(maxWidth: .infinity, alignment: .leading)
                        .foregroundStyle(.secondary)
                        .glassCard()
#endif
                }
                SectionTitle("タスク")
                ForEach(model.today?.tasks ?? []) { task in TaskRow(task: task) }
                SectionTitle("ルーティン")
                ForEach(model.today?.routines ?? []) { task in TaskRow(task: task) }
            }.padding()
        }.background(AppBackground()).navigationTitle("今日").refreshable { await model.refresh() }
    }
    private var remaining: Int { (model.today?.tasks.count ?? 0) + (model.today?.routines.filter { !$0.isCompleted }.count ?? 0) }
}

struct TaskRow: View {
    @EnvironmentObject private var model: AppModel
    let task: PlannerTask
    var body: some View {
        Button { Task { await model.toggle(task) } } label: {
            HStack(spacing: 14) { Image(systemName: task.isCompleted ? "checkmark.circle.fill" : "circle").appFont(.title2).foregroundStyle(task.isCompleted ? .green : .secondary); VStack(alignment: .leading) { Text(task.title).foregroundStyle(.primary); if let due = task.dueDate { Text(due).appFont(.caption).foregroundStyle(.secondary) } }; Spacer() }
        }.glassCard()
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
                Button("同期") { Task { await model.syncHealth() } }
                    .appFont(.caption, weight: .semibold)
#else
                Text("iPhoneから同期済み")
                    .appFont(.caption)
                    .foregroundStyle(.secondary)
#endif
            }
            HStack { Metric(value: health.sleepMinutes.map { "\($0 / 60)h \($0 % 60)m" } ?? "—", label: "睡眠"); Metric(value: health.steps.map { $0.formatted() } ?? "—", label: "歩数"); Metric(value: health.hrvMS.map { String(format: "%.0f", $0) } ?? "—", label: "HRV") }
        }.glassCard()
    }
}

struct EmailView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        ScrollView {
            LazyVStack(spacing: 14) {
                if let error = model.emailSyncError {
                    Text(error).appFont(.footnote).foregroundStyle(.orange).frame(maxWidth: .infinity, alignment: .leading).glassCard()
                }
                ForEach(model.emails) { email in EmailCard(email: email) }
                if model.emails.isEmpty { EmptyState(icon: "tray", title: "未読メールはありません", detail: "迷惑メールとゴミ箱を除く未読メールを表示します。") }
            }.padding()
        }
            .background(AppBackground()).navigationTitle("未読メール").refreshable { await model.refresh() }
    }
}

struct EmailCard: View {
    @EnvironmentObject private var model: AppModel
    let email: EmailReminder
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Text(email.sender).appFont(.caption, weight: .semibold).foregroundStyle(.cyan); Spacer(); if email.importance == "high" { Text("重要").badgeStyle(.red) } }
            Text(email.subject).appFont(.headline)
            Text(email.requiredAction).appFont(.subheadline).foregroundStyle(.secondary)
            HStack { if let url = email.gmailURL { Link("Gmailで開く", destination: url) }; Spacer(); if model.emailCanMarkRead { Button("既読") { Task { await model.act(on: email, action: "read") } } }; Button("対応不要") { Task { await model.act(on: email, action: "dismiss") } }; Button("保留") { Task { await model.act(on: email, action: "snooze") } }; Button("完了") { Task { await model.act(on: email, action: "done") } }.buttonStyle(.borderedProminent).tint(.mint) }.appFont(.caption, weight: .semibold)
        }.glassCard()
    }
}

struct NewsView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        ScrollView { LazyVStack(spacing: 16) { ForEach(model.articles.prefix(80)) { article in Link(destination: article.url) { ArticleCard(article: article) }.buttonStyle(.plain) } }.padding() }
            .background(AppBackground()).navigationTitle("ニュース").refreshable { await model.refresh() }
    }
}

struct ArticleCard: View {
    let article: Article
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            if let image = article.imageURL { AsyncImage(url: image) { phase in if let loaded = phase.image { loaded.resizable().scaledToFill() } else { Rectangle().fill(.quaternary) } }.frame(height: 170).clipShape(RoundedRectangle(cornerRadius: 16)).clipped() }
            HStack { Text(article.category).badgeStyle(.indigo); Spacer(); Text(article.source).appFont(.caption).foregroundStyle(.secondary) }
            Text(article.title).appFont(.headline).foregroundStyle(.primary)
            Text(article.summary).appFont(.subheadline).foregroundStyle(.secondary).lineLimit(3)
        }.glassCard()
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
        if info != nil || refreshedAt != nil {
            VStack(spacing: 8) {
                if let info {
                    HStack {
                        Label("稼働 \(info.version)", systemImage: "shippingbox")
                        Spacer()
                        if let date = info.deployedDate { Text("デプロイ \(date.runtimeDisplay)") }
                    }
                    NativeReleaseStatus(info: info)
                }
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
}

private struct NativeReleaseStatus: View {
    let info: DeploymentInfo

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

    var body: some View {
        if let installedVersion, let releaseVersion = info.nativeReleaseVersion {
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
        }
    }
}

private extension Date {
    var runtimeDisplay: String { formatted(.dateTime.month().day().hour().minute()) }
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
