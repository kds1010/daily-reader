import SwiftUI

struct RootView: View {
    @EnvironmentObject private var model: AppModel
    @State private var showComposer = false

    var body: some View {
        TabView(selection: $model.selectedTab) {
            NavigationStack { AgentView(showComposer: $showComposer) }.tabItem { Label("Agent", systemImage: "sparkles") }.tag(0)
            NavigationStack { TodayView() }.tabItem { Label("今日", systemImage: "checkmark.circle") }.tag(1)
            NavigationStack { EmailView() }.tabItem { Label("メール", systemImage: "envelope") }.badge(model.emails.count).tag(2)
            NavigationStack { NewsView() }.tabItem { Label("ニュース", systemImage: "newspaper") }.tag(3)
            NavigationStack { SettingsView() }.tabItem { Label("設定", systemImage: "gearshape") }.tag(4)
        }
        .tint(.mint)
        .sheet(isPresented: $showComposer) { AgentComposer() }
        .alert("接続できませんでした", isPresented: Binding(get: { model.errorMessage != nil }, set: { if !$0 { model.errorMessage = nil } })) {
            Button("閉じる", role: .cancel) {}
        } message: { Text(model.errorMessage ?? "") }
    }
}

struct AgentView: View {
    @EnvironmentObject private var model: AppModel
    @Binding var showComposer: Bool
    var body: some View {
        List {
            StatusHero(title: "Agent Console", subtitle: summary, icon: "terminal.fill", color: .mint)
                .agentListRow()
            RuntimeInfo(info: model.deploymentInfo, refreshedAt: model.lastUpdated)
                .agentListRow()
            CodexUsageCard()
                .agentListRow()
            Button { showComposer = true } label: {
                Label("新しいタスク", systemImage: "plus.circle.fill")
                    .frame(maxWidth: .infinity)
            }
            .buttonStyle(.borderedProminent)
            .tint(.mint)
            .agentListRow()
            ForEach(model.agents) { job in
                AgentCard(job: job)
                    .agentListRow()
                    .swipeActions(edge: .leading, allowsFullSwipe: true) {
                        archiveButton(for: job)
                    }
                    .swipeActions(edge: .trailing, allowsFullSwipe: true) {
                        archiveButton(for: job)
                    }
            }
            if model.agents.isEmpty {
                EmptyState(icon: "sparkles", title: "Agentは待機中です", detail: "新しい依頼を送ると、ここに進捗が表示されます。")
                    .agentListRow()
            }
            if !model.archivedAgents.isEmpty {
                DisclosureGroup("アーカイブ（\(model.archivedAgents.count)）") {
                    ForEach(model.archivedAgents) { job in AgentCard(job: job, archived: true) }
                }
                .glassCard()
                .agentListRow()
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(AppBackground())
        .navigationTitle("Daily Reader")
        .refreshable { await model.refresh() }
        .task {
            while !Task.isCancelled {
                try? await Task.sleep(for: .seconds(5))
                if !Task.isCancelled { await model.refreshAgents() }
            }
        }
    }
    private var summary: String {
        let running = model.agents.filter { ["queued", "running"].contains($0.status) }.count
        let blocked = model.agents.filter { $0.status == "blocked" }.count
        return blocked > 0 ? "\(blocked)件の判断を待っています" : running > 0 ? "\(running)件を進めています" : "新しい依頼を受け付けられます"
    }

    private func archiveButton(for job: AgentJob) -> some View {
        Button {
            Task { await model.hideAgent(job) }
        } label: {
            Label("非表示", systemImage: "archivebox.fill")
        }
        .tint(.orange)
    }
}

private extension View {
    func agentListRow() -> some View {
        listRowInsets(EdgeInsets(top: 7, leading: 16, bottom: 7, trailing: 16))
            .listRowSeparator(.hidden)
            .listRowBackground(Color.clear)
    }
}

struct CodexUsageCard: View {
    @EnvironmentObject private var model: AppModel

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Label("Codex 使用状況", systemImage: "gauge.with.dots.needle.67percent")
                    .font(.headline)
                Spacer()
                if let plan = model.codexUsage?.rateLimits?.planType, !plan.isEmpty {
                    Text(plan).font(.caption).foregroundStyle(.secondary)
                }
            }
            if model.codexUsageFailed {
                Text("使用状況を取得できませんでした。")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                let limits = sortedLimits
                if limits.isEmpty {
                    Text(model.codexUsage == nil ? "使用状況を読み込んでいます…" : "現在の利用枠はありません。")
                        .font(.subheadline).foregroundStyle(.secondary)
                } else {
                    ForEach(limits, id: \.id) { item in
                        CodexLimitRow(name: item.name, window: item.window)
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
}

struct CodexLimitRow: View {
    let name: String
    let window: CodexLimitWindow

    var body: some View {
        let used = min(max(window.usedPercent ?? 0, 0), 100)
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(name).font(.subheadline.weight(.semibold))
                Spacer()
                Text("\(used.formatted(.number.precision(.fractionLength(0...1))) )% 使用")
                    .font(.caption).foregroundStyle(.secondary)
            }
            ProgressView(value: used, total: 100)
                .tint(.mint)
            HStack {
                Text("残り \(max(0, 100 - used).formatted(.number.precision(.fractionLength(0...1))) )%")
                Spacer()
                Text(resetLabel)
            }
            .font(.caption2).foregroundStyle(.secondary)
        }
    }

    private var resetLabel: String {
        guard let timestamp = window.resetsAt else { return "リセット時刻不明" }
        return "リセット \(Date(timeIntervalSince1970: TimeInterval(timestamp)).formatted(date: .omitted, time: .shortened))"
    }
}

struct AgentCard: View {
    @EnvironmentObject private var model: AppModel
    let job: AgentJob
    var archived = false
    @State private var expanded = false
    @State private var showConversation = false
    @State private var fullEvents: [AgentEvent] = []
    @State private var instruction = ""
    @State private var sending = false

    var body: some View {
        cardContent
            .clipShape(RoundedRectangle(cornerRadius: 22))
    }

    private var cardContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            Button { withAnimation(.snappy) { expanded.toggle() } } label: {
                HStack(spacing: 12) {
                    Image(systemName: statusIcon).foregroundStyle(statusColor).font(.title3)
                    VStack(alignment: .leading, spacing: 5) {
                        HStack { Text(statusLabel).font(.caption.bold()).foregroundStyle(statusColor); Text(job.repository).font(.caption).foregroundStyle(.secondary) }
                        Text(job.prompt).font(.headline).foregroundStyle(.primary).lineLimit(expanded ? nil : 2)
                        Text("\(job.phase)・\(job.updatedAt.relativeTime)").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Image(systemName: expanded ? "chevron.up" : "chevron.down").foregroundStyle(.tertiary)
                }
            }
            .buttonStyle(.plain)

            if expanded {
                Divider()
                Text("現在の進捗").font(.caption.bold()).foregroundStyle(statusColor)
                if let summary = job.summary, !summary.isEmpty {
                    Text(job.status == "completed" || job.followUp == 1 ? "完了サマリー" : "現在の報告").font(.caption.bold()).foregroundStyle(.secondary)
                    Text(summary).font(.subheadline).textSelection(.enabled)
                }
                if ["queued", "running", "blocked"].contains(job.status) {
                    Label(job.status == "blocked" ? "回答を待っています" : "進捗を自動更新中", systemImage: "waveform.path.ecg").font(.caption.bold()).foregroundStyle(statusColor)
                    ForEach(job.recentEvents ?? []) { event in AgentEventRow(event: event) }
                }
                Text("やりとり").font(.caption.bold()).foregroundStyle(.secondary)
                Button(showConversation ? "やりとりを非表示" : "やりとりを表示") {
                    showConversation.toggle()
                    if showConversation && fullEvents.isEmpty {
                        Task { fullEvents = await model.agentDetail(job.id)?.events ?? [] }
                    }
                }
                .font(.caption.bold())
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
                            if await model.sendInstruction(to: job, instruction: instruction) { instruction = "" }
                            sending = false
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.mint)
                    .disabled(instruction.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || sending)
                }
                HStack {
                    if !archived && ["queued", "running"].contains(job.status) {
                        Button("停止", role: .destructive) { Task { await model.cancelAgent(job) } }
                    }
                    Spacer()
                    if !archived {
                        Button("非表示") { Task { await model.hideAgent(job) } }
                    }
                }
                .font(.caption.bold())
            }
        }
        .glassCard()
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
}

struct AgentEventRow: View {
    let event: AgentEvent
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Circle().fill(event.kind == "user" ? .mint : .secondary).frame(width: 7, height: 7).padding(.top, 6)
            VStack(alignment: .leading, spacing: 3) {
                Text(event.kind == "user" ? "あなた" : event.kind == "codex" ? "Agent" : "進捗")
                    .font(.caption2.bold()).foregroundStyle(event.kind == "user" ? .mint : .secondary)
                Text(event.message).font(.caption).textSelection(.enabled)
                Text(event.createdAt.relativeTime).font(.caption2).foregroundStyle(.tertiary)
            }
        }
    }
}

struct AgentComposer: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var prompt = ""
    @State private var repository = ""
    @State private var sending = false
    var body: some View {
        NavigationStack {
            Form {
                Section("依頼") { TextEditor(text: $prompt).frame(minHeight: 180); Text("目的、制約、完了条件を自然な言葉で入力してください。").font(.caption).foregroundStyle(.secondary) }
                Section("リポジトリ") { Picker("対象", selection: $repository) { ForEach(model.repositories) { Text($0.label).tag($0.name) } } }
                Section {
                    Button {
                        Task {
                            sending = true
                            if await model.createAgent(prompt: prompt, repository: repository) { dismiss() }
                            sending = false
                        }
                    } label: {
                        HStack {
                            Spacer()
                            if sending { ProgressView().tint(.white) }
                            Text("タスクを開始")
                            Spacer()
                        }
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(.mint)
                    .disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || repository.isEmpty || sending)
                }
            }
            .navigationTitle("Agentへ依頼")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("閉じる") { dismiss() } } }
            .onAppear { repository = repository.isEmpty ? model.repositories.first?.name ?? "" : repository }
        }.presentationDetents([.large])
    }
}

struct TodayView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        ScrollView {
            LazyVStack(alignment: .leading, spacing: 16) {
                StatusHero(title: "今日", subtitle: remaining == 0 ? "すべて完了しました" : "あと\(remaining)件です", icon: "sun.max.fill", color: .orange)
                if let health = model.today?.health { HealthCard(health: health) } else { Button { Task { await model.syncHealth() } } label: { Label("HealthKitを同期", systemImage: "heart.fill").frame(maxWidth: .infinity) }.buttonStyle(.borderedProminent).tint(.pink) }
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
            HStack(spacing: 14) { Image(systemName: task.isCompleted ? "checkmark.circle.fill" : "circle").font(.title2).foregroundStyle(task.isCompleted ? .green : .secondary); VStack(alignment: .leading) { Text(task.title).foregroundStyle(.primary); if let due = task.dueDate { Text(due).font(.caption).foregroundStyle(.secondary) } }; Spacer() }
        }.glassCard()
    }
}

struct HealthCard: View {
    @EnvironmentObject private var model: AppModel
    let health: HealthSnapshot
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack { Label("HealthKit", systemImage: "heart.fill").font(.headline).foregroundStyle(.pink); Spacer(); Button("同期") { Task { await model.syncHealth() } }.font(.caption.weight(.semibold)) }
            HStack { Metric(value: health.sleepMinutes.map { "\($0 / 60)h \($0 % 60)m" } ?? "—", label: "睡眠"); Metric(value: health.steps.map { $0.formatted() } ?? "—", label: "歩数"); Metric(value: health.hrvMS.map { String(format: "%.0f", $0) } ?? "—", label: "HRV") }
        }.glassCard()
    }
}

struct EmailView: View {
    @EnvironmentObject private var model: AppModel
    var body: some View {
        ScrollView { LazyVStack(spacing: 14) { ForEach(model.emails) { email in EmailCard(email: email) }; if model.emails.isEmpty { EmptyState(icon: "tray", title: "未対応メールはありません", detail: "重要な未読メールだけを表示します。") } }.padding() }
            .background(AppBackground()).navigationTitle("重要メール").refreshable { await model.refresh() }
    }
}

struct EmailCard: View {
    @EnvironmentObject private var model: AppModel
    let email: EmailReminder
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack { Text(email.sender).font(.caption.weight(.semibold)).foregroundStyle(.cyan); Spacer(); if email.importance == "high" { Text("重要").badgeStyle(.red) } }
            Text(email.subject).font(.headline)
            Text(email.requiredAction).font(.subheadline).foregroundStyle(.secondary)
            HStack { if let url = email.gmailURL { Link("Gmailで開く", destination: url) }; Spacer(); Button("保留") { Task { await model.act(on: email, action: "snooze") } }; Button("完了") { Task { await model.act(on: email, action: "done") } }.buttonStyle(.borderedProminent).tint(.mint) }.font(.caption.weight(.semibold))
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
            HStack { Text(article.category).badgeStyle(.indigo); Spacer(); Text(article.source).font(.caption).foregroundStyle(.secondary) }
            Text(article.title).font(.headline).foregroundStyle(.primary)
            Text(article.summary).font(.subheadline).foregroundStyle(.secondary).lineLimit(3)
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
        Form { Section("接続") { TextField("サーバーURL", text: $serverURL).textInputAutocapitalization(.never).keyboardType(.URL); SecureField("HealthKit同期トークン", text: $healthToken); Button("トークンを安全に保存") { do { try SecretStore.saveHealthToken(healthToken); tokenStatus = "Keychainへ保存しました" } catch { tokenStatus = error.localizedDescription } }; if !tokenStatus.isEmpty { Text(tokenStatus).font(.caption).foregroundStyle(.secondary) } }; Section("プライバシー") { Label("健康情報はtailnet内のMac miniだけへ送信します", systemImage: "lock.shield") }; Section("バージョン") { LabeledContent("Daily Reader", value: versionText); if let info = model.deploymentInfo { LabeledContent("サーバー", value: info.version); if let date = info.deployedDate { LabeledContent("デプロイ", value: date.runtimeDisplay) } } } }
            .navigationTitle("設定")
            .onAppear { healthToken = (try? SecretStore.readHealthToken()) ?? "" }
    }
}

struct StatusHero: View {
    let title: String; let subtitle: String; let icon: String; let color: Color
    var body: some View { HStack(spacing: 18) { ZStack { Circle().fill(color.gradient).frame(width: 58, height: 58); Image(systemName: icon).font(.title2).foregroundStyle(.black) }; VStack(alignment: .leading, spacing: 4) { Text(title).font(.title2.bold()); Text(subtitle).foregroundStyle(.secondary) }; Spacer() }.padding(20).background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 28)).overlay(RoundedRectangle(cornerRadius: 28).stroke(color.opacity(0.25))) }
}
struct Metric: View { let value: String; let label: String; var body: some View { VStack(alignment: .leading) { Text(value).font(.title3.bold().monospacedDigit()); Text(label).font(.caption).foregroundStyle(.secondary) }.frame(maxWidth: .infinity, alignment: .leading) } }
struct SectionTitle: View { let title: String; init(_ title: String) { self.title = title }; var body: some View { Text(title).font(.title3.bold()).frame(maxWidth: .infinity, alignment: .leading) } }
struct EmptyState: View { let icon: String; let title: String; let detail: String; var body: some View { VStack(spacing: 12) { Image(systemName: icon).font(.largeTitle).foregroundStyle(.secondary); Text(title).font(.headline); Text(detail).font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center) }.frame(maxWidth: .infinity).padding(40).glassCard() } }
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
                }
                if let refreshedAt {
                    HStack {
                        Label("画面更新", systemImage: "arrow.clockwise")
                        Spacer()
                        Text(refreshedAt.runtimeDisplay)
                    }
                }
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .padding(.horizontal, 4)
        }
    }
}
private extension Date {
    var runtimeDisplay: String { formatted(.dateTime.month().day().hour().minute()) }
}
struct AppBackground: View { var body: some View { LinearGradient(colors: [Color(red: 0.04, green: 0.06, blue: 0.1), Color(red: 0.07, green: 0.05, blue: 0.12)], startPoint: .topLeading, endPoint: .bottomTrailing).ignoresSafeArea() } }

extension View {
    func glassCard() -> some View { self.padding(16).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 22)).overlay(RoundedRectangle(cornerRadius: 22).stroke(.white.opacity(0.08))) }
    func badgeStyle(_ color: Color) -> some View { self.font(.caption2.weight(.bold)).padding(.horizontal, 8).padding(.vertical, 4).background(color.opacity(0.2), in: Capsule()).foregroundStyle(color) }
}
