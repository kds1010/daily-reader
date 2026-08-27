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
        ScrollView {
            LazyVStack(spacing: 14) {
                StatusHero(title: "Agent Console", subtitle: summary, icon: "terminal.fill", color: .mint)
                ForEach(model.agents) { job in AgentCard(job: job) }
                if model.agents.isEmpty { EmptyState(icon: "sparkles", title: "Agentは待機中です", detail: "新しい依頼を送ると、ここに進捗が表示されます。") }
            }.padding()
        }
        .background(AppBackground())
        .navigationTitle("Daily Reader")
        .toolbar {
            ToolbarItem(placement: .topBarLeading) { ConnectionBadge(date: model.lastUpdated) }
            ToolbarItem(placement: .primaryAction) { Button { showComposer = true } label: { Image(systemName: "plus.circle.fill").font(.title2) } }
        }
        .refreshable { await model.refresh() }
    }
    private var summary: String {
        let running = model.agents.filter { ["queued", "running"].contains($0.status) }.count
        let blocked = model.agents.filter { $0.status == "blocked" }.count
        return blocked > 0 ? "\(blocked)件の判断を待っています" : running > 0 ? "\(running)件を進めています" : "新しい依頼を受け付けられます"
    }
}

struct AgentCard: View {
    let job: AgentJob
    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Circle().fill(statusColor).frame(width: 10, height: 10).shadow(color: statusColor, radius: 5)
                Text(job.phase).font(.caption.weight(.semibold)).foregroundStyle(statusColor)
                Spacer()
                Text(job.updatedAt.relativeTime).font(.caption).foregroundStyle(.secondary)
            }
            Text(job.prompt).font(.headline).lineLimit(3)
            if let summary = job.summary, !summary.isEmpty { Text(summary).font(.subheadline).foregroundStyle(.secondary).lineLimit(3) }
            HStack { Label(job.repository, systemImage: "shippingbox"); Spacer(); Image(systemName: "chevron.right") }.font(.caption).foregroundStyle(.secondary)
        }.glassCard()
    }
    private var statusColor: Color {
        switch job.status { case "completed": .green; case "blocked": .orange; case "failed": .red; case "running": .cyan; default: .secondary }
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
            }
            .navigationTitle("Agentへ依頼")
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("閉じる") { dismiss() } }; ToolbarItem(placement: .confirmationAction) { Button("開始") { Task { sending = true; if await model.createAgent(prompt: prompt, repository: repository) { dismiss() }; sending = false } }.disabled(prompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || repository.isEmpty || sending) } }
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
    @AppStorage("serverURL") private var serverURL = "https://sk-mins-mac-mini.tailc193b2.ts.net/"
    @State private var healthToken = ""
    @State private var tokenStatus = ""
    var body: some View {
        Form { Section("接続") { TextField("サーバーURL", text: $serverURL).textInputAutocapitalization(.never).keyboardType(.URL); SecureField("HealthKit同期トークン", text: $healthToken); Button("トークンを安全に保存") { do { try SecretStore.saveHealthToken(healthToken); tokenStatus = "Keychainへ保存しました" } catch { tokenStatus = error.localizedDescription } }; if !tokenStatus.isEmpty { Text(tokenStatus).font(.caption).foregroundStyle(.secondary) } }; Section("プライバシー") { Label("健康情報はtailnet内のMac miniだけへ送信します", systemImage: "lock.shield") }; Section("バージョン") { LabeledContent("Daily Reader", value: "0.1.0") } }
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
struct ConnectionBadge: View { let date: Date?; var body: some View { HStack(spacing: 5) { Circle().fill(date == nil ? .orange : .green).frame(width: 7, height: 7); Text(date == nil ? "接続中" : "同期済み") }.font(.caption.weight(.medium)).foregroundStyle(.secondary) } }
struct AppBackground: View { var body: some View { LinearGradient(colors: [Color(red: 0.04, green: 0.06, blue: 0.1), Color(red: 0.07, green: 0.05, blue: 0.12)], startPoint: .topLeading, endPoint: .bottomTrailing).ignoresSafeArea() } }

extension View {
    func glassCard() -> some View { self.padding(16).background(.thinMaterial, in: RoundedRectangle(cornerRadius: 22)).overlay(RoundedRectangle(cornerRadius: 22).stroke(.white.opacity(0.08))) }
    func badgeStyle(_ color: Color) -> some View { self.font(.caption2.weight(.bold)).padding(.horizontal, 8).padding(.vertical, 4).background(color.opacity(0.2), in: Capsule()).foregroundStyle(color) }
}
