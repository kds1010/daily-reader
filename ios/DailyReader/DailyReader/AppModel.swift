import Foundation
import Combine
import SwiftUI
import UserNotifications
#if os(iOS)
import UniformTypeIdentifiers
#endif

// Coalesce reads of the same resource. A successful mutation replaces the
// in-flight read, so a response issued before that mutation cannot undo it.
@MainActor
final class ResourceRefreshes {
    private struct Flight { let generation: Int; let task: Task<Void, Never> }
    private var flights: [String: Flight] = [:]
    private var nextGeneration = 0

    func isCurrent(_ key: String, _ generation: Int, includingCancelled: Bool = false) -> Bool {
        flights[key]?.generation == generation && (includingCancelled || !Task.isCancelled)
    }

    func invalidate(_ key: String) { flights.removeValue(forKey: key)?.task.cancel() }

    func run(_ key: String, replacing: Bool = false,
             operation: @escaping @MainActor (Int) async -> Void) async {
        guard !Task.isCancelled else { return }
        if let existing = flights[key], !replacing, !existing.task.isCancelled {
            await existing.task.value
            return
        }
        flights[key]?.task.cancel()
        nextGeneration += 1
        let generation = nextGeneration
        let task = Task { await operation(generation) }
        flights[key] = Flight(generation: generation, task: task)
        await withTaskCancellationHandler {
            await task.value
        } onCancel: {
            task.cancel()
        }
        if flights[key]?.generation == generation { flights.removeValue(forKey: key) }
    }
}

@MainActor
final class AppModel: ObservableObject {
    let life = LifeStore()
    let diary: DiaryStore
    @Published var agents: [AgentJob] = []
    @Published var archivedAgents: [AgentJob] = []
    @Published var tanomiTasks: [TanomiTask] = []
    @Published var tanomiArchivedTasks: [TanomiTask] = []
    @Published var tanomiRepositories: [TanomiRepository] = []
    @Published var tanomiConfig = TanomiConfig(models: ["opus"], defaultModel: "opus", efforts: ["low", "medium", "high", "xhigh", "max"], defaultEffort: nil, permissionModes: ["acceptEdits", "plan", "manual", "bypassPermissions"])
    @Published var tanomiAvailable = false
    @Published var tanomiStatusMessage: String?
    @Published var tanomiUsage: TanomiUsage?
    @Published var tanomiUsageFailed = false
    @Published var repositories: [Repository] = []
    @Published var agentModels: [AgentModelOption] = [.fallback]
    @Published var today: TodayEnvelope?
    @Published var emails: [EmailReminder] = []
    @Published var emailSyncError: String?
    @Published var emailCanMarkRead = true
    @Published var articles: [Article] = []
    @Published var codexUsage: CodexUsageEnvelope?
    @Published var codexUsageFailed = false
    @Published var deploymentInfo: DeploymentInfo?
    @Published var conversations: [ConversationRecording] = []
    @Published private(set) var conversationDetailErrors: [String: String] = [:]
    @Published var conversationLLMAvailable = false
    @Published var conversationLoadState: ResourceLoadState = .idle
    @Published var isRefreshing = false
    @Published var errorMessage: String?
    @Published var lastUpdated: Date?
    @Published var selectedTab = 0
    @Published private(set) var todayLoadState: ResourceLoadState = .idle
    @Published private(set) var emailLoadState: ResourceLoadState = .idle
    @Published private(set) var agentLoadState: ResourceLoadState = .idle
    @Published private(set) var tanomiLoadState: ResourceLoadState = .idle
    @Published private(set) var codexUsageLoadState: ResourceLoadState = .idle
    @Published private(set) var newsLoadState: ResourceLoadState = .idle
    @Published private(set) var deploymentLoadState: ResourceLoadState = .idle
    @Published private(set) var readArticleIDs: Set<String>
    @Published private(set) var savedArticleIDs: Set<String>
    @Published private(set) var hiddenArticleIDs: Set<String>

    private let api: APIClient
    private var fixture: DaymeldFixture?
#if os(iOS)
    lazy var deviceLocation = DeviceLocationService(isEnabled: !isFixture)
#endif
    #if os(iOS)
    private let agentNotifications = AgentNotificationCoordinator.shared
    #else
    private let agentNotifications = AgentNotificationCoordinator()
    #endif
    private let refreshes = ResourceRefreshes()
    private var conversationDetails: [String: ConversationRecording] = [:]
    private var fullRefreshCount = 0
    private var lastTanomiMetadataRefresh: Date = .distantPast
    private var pendingEmailActions: [String: (email: EmailReminder, index: Int)] = [:]
    private struct PendingArchive {
        let index: Int
    }
    private var pendingArchives: [String: PendingArchive] = [:]
    private var pendingAgentJobs: [String: AgentJob] = [:]
    private var pendingTanomiTasks: [String: TanomiTask] = [:]

    var isFixture: Bool { fixture != nil }
    var conversationAPI: APIClient { api }

    init(fixture: DaymeldFixture? = nil, api: APIClient = .shared) {
        self.api = api
        self.diary = DiaryStore(api: api)
        if fixture == nil {
            readArticleIDs = Self.loadArticleIDs(forKey: "daily-reader.native.read")
            savedArticleIDs = Self.loadArticleIDs(forKey: "daily-reader.native.saved")
            hiddenArticleIDs = Self.loadArticleIDs(forKey: "daily-reader.native.hidden")
        } else {
            readArticleIDs = []
            savedArticleIDs = []
            hiddenArticleIDs = []
        }
        self.fixture = fixture
        if let fixture {
            applyFixture(fixture)
        }
    }

    private func applyFixture(_ fixture: DaymeldFixture) {
        diary.configureFixture(fixture.diaryScenario)
        repositories = fixture.repositories
        agentModels = fixture.agentModels
        agents = fixture.agents
        archivedAgents = fixture.archivedAgents
        tanomiRepositories = fixture.tanomiRepositories
        tanomiConfig = fixture.tanomiConfig
        tanomiTasks = fixture.tanomiTasks
        tanomiArchivedTasks = fixture.tanomiArchivedTasks
        tanomiUsage = fixture.tanomiUsage
        tanomiAvailable = fixture.tanomiAvailable
        tanomiStatusMessage = fixture.tanomiStatusMessage
        life.isFixture = true
        life.snapshot = fixture.lifeSnapshot
        conversations = fixture.conversations
        conversationLoadState = .loaded
        today = fixture.today
        emails = fixture.emails
        articles = fixture.articles
        codexUsage = fixture.codexUsage
        deploymentInfo = fixture.deploymentInfo
        let errors = fixture.failedResources
        todayLoadState = errors.contains(.today) ? .failed("fixture: 今日のデータを取得できません") : .loaded
        emailLoadState = errors.contains(.email) ? .failed("fixture: メールを取得できません") : .loaded
        agentLoadState = errors.contains(.agents) ? .failed("fixture: Agentを取得できません") : .loaded
        tanomiLoadState = errors.contains(.tanomi) ? .failed(fixture.tanomiStatusMessage ?? "fixture: tanomiを取得できません") : .loaded
        codexUsageLoadState = errors.contains(.codexUsage) ? .failed("fixture: 使用状況を取得できません") : .loaded
        newsLoadState = errors.contains(.news) ? .failed("fixture: ニュースを取得できません") : .loaded
        deploymentLoadState = errors.contains(.deployment) ? .failed("fixture: バージョン情報を取得できません") : .loaded
        lastUpdated = fixture.referenceDate ?? .now
    }

    private static func loadArticleIDs(forKey key: String) -> Set<String> {
        Set(UserDefaults.standard.stringArray(forKey: key) ?? [])
    }

    private func persistArticleIDs(_ values: Set<String>, forKey key: String) {
        UserDefaults.standard.set(Array(values).sorted(), forKey: key)
    }

    func start() async {
        if let fixture {
            applyFixture(fixture)
            return
        }
#if os(iOS)
        do {
            try SecretStore.importBootstrapHealthToken()
            try SecretStore.prepareBootstrapHealthTokenFile()
        } catch {
            errorMessage = "HealthKit同期トークンを安全に取り込めませんでした：\(error.localizedDescription)"
        }
#endif
        await requestNotifications()
        await refresh()
    }

    func refresh(afterMutation: Bool = false) async {
        guard !Task.isCancelled else { return }
        if let fixture { applyFixture(fixture); return }
        fullRefreshCount += 1
        if !isRefreshing { isRefreshing = true }
        if errorMessage != nil { errorMessage = nil }
        defer {
            fullRefreshCount -= 1
            if fullRefreshCount == 0 { isRefreshing = false }
        }
        // Three independent lanes bound fan-out while keeping the initial Agent
        // screen independent of mail, news, device context and tanomi metadata.
        async let agent: Void = refreshAgents(afterMutation: afterMutation)
        async let content: Void = refreshContent(afterMutation: afterMutation)
        async let supplemental: Void = refreshSupplemental(afterMutation: afterMutation)
        _ = await (agent, content, supplemental)
    }

    private func refreshContent(afterMutation: Bool) async {
        await refreshToday(force: afterMutation)
        await refreshEmails(force: afterMutation)
        await refreshResource("news", path: "data/articles.json", state: \.newsLoadState,
                              force: afterMutation, as: ArticleEnvelope.self) { self.articles = $0.articles }
        await refreshConversations(afterMutation: afterMutation)
    }

    private func refreshToday(force: Bool) async {
        await refreshResource("today", path: "api/today", state: \.todayLoadState,
                              force: force, as: TodayEnvelope.self) { self.today = $0 }
    }

    private func refreshEmails(force: Bool) async {
        await refreshResource("email", path: "api/emails/unread", state: \.emailLoadState,
                              force: force, as: EmailEnvelope.self) { mail in
            let pendingIDs = Set(self.pendingEmailActions.keys)
            self.emails = mail.items.filter { !pendingIDs.contains($0.threadID) }
            self.emailSyncError = mail.authorizationRequired == true
                ? "Gmailの再認証が必要です。Mac miniで再接続してください。"
                : mail.syncError != nil ? "Gmailの同期に失敗したため、保存済みのメールを表示しています。" : nil
            self.emailCanMarkRead = mail.canMarkRead ?? true
        }
    }

    private func refreshSupplemental(afterMutation: Bool) async {
        // Metadata and usage are independent of the fast task-list lane.
        async let metadata: Void = refreshTanomiMetadata(force: true)
        async let lifeData: Void = life.refresh()
        async let diaryData: Void = diary.refresh()
        await refreshResource("usage", path: "api/codex-usage", state: \.codexUsageLoadState,
                              force: afterMutation, as: CodexUsageEnvelope.self) {
            self.codexUsage = $0
            self.codexUsageFailed = false
        }
        codexUsageFailed = codexUsageLoadState != .loaded
        await refreshResource("deployment", path: "api/deployment", state: \.deploymentLoadState,
                              force: afterMutation, as: DeploymentInfo.self) { self.deploymentInfo = $0 }
        _ = await (metadata, lifeData, diaryData)
    }

    private func setLoadState(_ key: ReferenceWritableKeyPath<AppModel, ResourceLoadState>, _ value: ResourceLoadState) {
        if self[keyPath: key] != value { self[keyPath: key] = value }
    }

    private func refreshResource<T: Decodable>(
        _ key: String, path: String, state: ReferenceWritableKeyPath<AppModel, ResourceLoadState>,
        force: Bool, as type: T.Type, apply: @escaping (T) -> Void
    ) async {
        await refreshes.run(key, replacing: force) { generation in
            let previous = self[keyPath: state]
            if previous == .idle { self.setLoadState(state, .loading) }
            do {
                let value = try await self.api.get(path, as: type)
                guard self.refreshes.isCurrent(key, generation) else { return }
                apply(value)
                self.setLoadState(state, .loaded)
                self.lastUpdated = .now
            } catch {
                guard self.refreshes.isCurrent(key, generation, includingCancelled: true) else { return }
                self.setLoadState(state, Task.isCancelled ? (previous == .loading ? .idle : previous) : .failed(error.localizedDescription))
            }
        }
    }

    func refreshConversations(afterMutation: Bool = false) async {
        guard !isFixture else { return }
        await refreshes.run("conversations", replacing: afterMutation) { generation in
            let previous = self.conversationLoadState
            if previous == .idle { self.conversationLoadState = .loading }
            do {
                let envelope = try await self.api.get("api/conversations", as: ConversationEnvelope.self)
                guard self.refreshes.isCurrent("conversations", generation) else { return }
                self.conversations = envelope.recordings
                self.conversationLLMAvailable = envelope.llmAvailable ?? false
                self.conversationLoadState = .loaded
            } catch {
                guard self.refreshes.isCurrent("conversations", generation, includingCancelled: true) else { return }
                self.conversationLoadState = Task.isCancelled ? (previous == .loading ? .idle : previous) : .failed(error.localizedDescription)
            }
        }
    }

    @discardableResult
    func importConversationFile(_ url: URL) async -> Bool {
        guard !isFixture else { return false }
        do {
            try await ConversationImports.shared.enqueue(url: url, filename: url.lastPathComponent)
            return true
        } catch {
            errorMessage = "会話データを送信できませんでした：\(error.localizedDescription)"
            return false
        }
    }

#if os(iOS)
    func importSharedRecording(_ url: URL) async {
        guard
            url.isFileURL,
            UTType(filenameExtension: url.pathExtension.lowercased())?.conforms(to: .mp3) == true
        else {
            errorMessage = "共有されたファイルはMP3ではありません。"
            return
        }

        guard !isFixture else { return }
        selectedTab = 4
        do {
            try await ConversationImports.shared.enqueue(url: url, filename: url.lastPathComponent, shared: true)
        } catch {
            errorMessage = "共有されたMP3を端末に保存できませんでした。元のファイルから再度共有してください。"
        }
    }
#endif

    func loadConversation(_ id: String, afterMutation: Bool = false) async -> ConversationRecording? {
        if let fixture { return fixture.conversations.first { $0.id == id } }
        let key = "conversation-detail-\(id)"
        await refreshes.run(key, replacing: afterMutation) { generation in
            do {
                let value = try await self.api.get("api/conversations/\(id)", as: ConversationRecording.self)
                guard self.refreshes.isCurrent(key, generation) else { return }
                self.conversationDetails[id] = value
                self.conversationDetailErrors.removeValue(forKey: id)
            } catch {
                guard self.refreshes.isCurrent(key, generation) else { return }
                self.conversationDetailErrors[id] = self.conversationDetails[id] == nil
                    ? "会話を取得できませんでした。再試行してください。"
                    : "更新できませんでした。前回取得した会話を表示しています。"
            }
        }
        return conversationDetails[id]
    }

    func summarizeConversation(_ id: String) async -> Bool {
        guard !isFixture else { return false }
        do {
            let _: EmptyResponse = try await api.post("api/conversations/\(id)/overview", body: EmptyRequest(), as: EmptyResponse.self)
            await refreshConversations(afterMutation: true)
            return true
        } catch {
            errorMessage = "要約を開始できませんでした。Mac miniの接続と処理状況を確認してください。"
            return false
        }
    }

    func correctConversation(_ id: String) async -> Bool {
        guard !isFixture else { return false }
        do {
            let _: EmptyResponse = try await api.post("api/conversations/\(id)/corrections", body: EmptyRequest(), as: EmptyResponse.self)
            await refreshConversations(afterMutation: true)
            return true
        } catch {
            errorMessage = "補正を開始できませんでした。Mac miniの接続と処理状況を確認してください。"
            return false
        }
    }

    func analyzeConversation(_ id: String) async {
        guard !isFixture else { return }
        do {
            let _: EmptyResponse = try await api.post("api/conversations/\(id)/analyze", body: EmptyRequest(), as: EmptyResponse.self)
            await refreshConversations(afterMutation: true)
        } catch { errorMessage = "解析を開始できませんでした：\(error.localizedDescription)" }
    }

    func extractConversationInsights(_ id: String) async -> Bool {
        guard !isFixture else { return false }
        guard conversationLLMAvailable else {
            errorMessage = "Mac miniでCodexへChatGPTログインしていません。"
            return false
        }
        do {
            let _: ConversationExtractionResponse = try await api.post(
                "api/conversations/\(id)/insights",
                body: EmptyRequest(),
                as: ConversationExtractionResponse.self
            )
            await refreshConversations(afterMutation: true)
            return true
        } catch {
            errorMessage = "会話をCodexで整理できませんでした：\(error.localizedDescription)"
            return false
        }
    }

    func reviewConversationItem(
        _ item: ConversationInsightItem,
        action: String,
        title: String,
        detail: String,
        assignee: String,
        dueDate: String
    ) async -> Bool {
        do {
            let _: ConversationInsightItem = try await api.post(
                "api/conversation-items/\(item.id)/review",
                body: ConversationItemReviewRequest(
                    action: action,
                    title: title,
                    detail: detail,
                    assignee: assignee,
                    dueDate: dueDate
                ),
                as: ConversationInsightItem.self
            )
            await refreshConversations(afterMutation: true)
            return true
        } catch {
            errorMessage = "会話の候補を更新できませんでした：\(error.localizedDescription)"
            return false
        }
    }

    func dispatchConversationItem(
        _ item: ConversationInsightItem,
        target: String,
        title: String,
        detail: String,
        assignee: String,
        dueDate: String,
        repository: String?
    ) async -> Bool {
        do {
            let _: EmptyResponse = try await api.post(
                "api/conversation-items/\(item.id)/dispatch",
                body: ConversationItemDispatchRequest(
                    target: target,
                    title: title,
                    detail: detail,
                    assignee: assignee,
                    dueDate: dueDate,
                    repository: repository
                ),
                as: EmptyResponse.self
            )
            await refresh(afterMutation: true)
            return true
        } catch {
            errorMessage = "会話の候補を送信できませんでした：\(error.localizedDescription)"
            return false
        }
    }

    func approveConversationTask(_ id: String, target: String, instruction: String, repository: String?) async {
        do {
            let _: EmptyResponse = try await api.post(
                "api/conversation-tasks/\(id)/approve",
                body: ConversationTaskApproval(target: target, instruction: instruction, repository: repository),
                as: EmptyResponse.self
            )
            await refresh(afterMutation: true)
        } catch { errorMessage = "タスクを承認できませんでした：\(error.localizedDescription)" }
    }

    func createAgent(prompt: String, repository: String, model: String, reasoningEffort: String) async -> Bool {
        if fixture != nil {
            let job = AgentJob(
                id: "fixture-created-\(UUID().uuidString)", repository: repository,
                repositoryLabel: repositories.first(where: { $0.name == repository })?.label,
                prompt: prompt, status: "queued", phase: "キュー待ち", summary: nil,
                model: model, reasoningEffort: reasoningEffort,
                updatedAt: ISO8601DateFormatter().string(from: .now), recentEvents: nil,
                events: [AgentEvent(createdAt: ISO8601DateFormatter().string(from: .now), kind: "user", message: prompt)],
                mode: "execute", followUp: nil, worktree: nil
            )
            agents.insert(job, at: 0)
            agentLoadState = .loaded
            return true
        }
        do {
            let _: AgentJob = try await api.post("api/agent-jobs", body: NewAgentJob(repository: repository, prompt: prompt, model: model, reasoningEffort: reasoningEffort), as: AgentJob.self)
            await refreshAgentSnapshot(force: true)
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func createTanomi(prompt: String, repo: String, model: String, permissionMode: String, effort: String?) async -> Bool {
        if fixture != nil {
            let task = TanomiTask(
                id: "fixture-created-\(UUID().uuidString)", title: prompt,
                prompt: prompt, repoPath: repo, cwd: nil, status: "queued", result: nil,
                error: nil, model: model, permissionMode: permissionMode,
                createdAt: Date.now.timeIntervalSince1970, startedAt: nil, endedAt: nil,
                sessionID: "fixture-session-created"
            )
            tanomiTasks.insert(task, at: 0)
            tanomiLoadState = .loaded
            return true
        }
        do {
            let _: EmptyResponse = try await api.post(
                "api/tanomi/tasks",
                body: NewTanomiTask(prompt: prompt, repo: repo, model: model, permissionMode: permissionMode, effort: effort),
                as: EmptyResponse.self
            )
            await refreshTanomiSnapshot(force: true)
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func stopTanomi(_ task: TanomiTask) async {
        if fixture != nil {
            let stopped = TanomiTask(
                id: task.id, title: task.title, prompt: task.prompt, repoPath: task.repoPath,
                cwd: task.cwd, status: "stopped", result: task.result, error: task.error,
                model: task.model, permissionMode: task.permissionMode, createdAt: task.createdAt,
                startedAt: task.startedAt, endedAt: Date.now.timeIntervalSince1970, sessionID: task.sessionID
            )
            tanomiTasks = tanomiTasks.map { $0.id == task.id ? stopped : $0 }
            return
        }
        do {
            let _: EmptyResponse = try await api.post("api/tanomi/tasks/\(task.id)/stop", body: EmptyRequest(), as: EmptyResponse.self)
            await refreshTanomiSnapshot(force: true)
        } catch { errorMessage = error.localizedDescription }
    }

    func sendTanomiInstruction(taskID: String, instruction: String) async -> Bool {
        if fixture != nil { return true }
        do {
            let _: EmptyResponse = try await api.post(
                "api/tanomi/tasks",
                body: TanomiFollowUp(prompt: instruction, parentID: taskID),
                as: EmptyResponse.self
            )
            await refreshTanomiSnapshot(force: true)
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func hideTanomi(_ task: TanomiTask) async {
        guard let index = tanomiTasks.firstIndex(where: { $0.id == task.id }) else { return }
        _ = withAnimation(.easeInOut(duration: 0.24)) { tanomiTasks.remove(at: index) }
        if fixture != nil {
            tanomiArchivedTasks.insert(task, at: 0)
            pendingTanomiTasks.removeValue(forKey: task.id)
            return
        }
        pendingTanomiTasks[task.id] = task
        await archiveWithEffect(id: "tanomi-\(task.id)", index: index) {
            try await self.api.post(
                "api/tanomi/tasks/\(task.id)/archive",
                body: EmptyRequest(),
                as: EmptyResponse.self
            )
        }
    }

    func refreshAgents(afterMutation: Bool = false) async {
        guard !isFixture, !Task.isCancelled else { return }
        if afterMutation {
            refreshes.invalidate("agents")
            refreshes.invalidate("tanomi")
        }
        async let daymeld: Void = refreshAgentSnapshot(force: afterMutation)
        async let tanomi: Void = refreshTanomiSnapshot(force: afterMutation)
        _ = await (daymeld, tanomi)
    }

    func pollDaymeldAgents() async {
        guard !isFixture else { return }
        await refreshAgentSnapshot(force: false)
    }

    func pollTanomiTasks() async {
        guard !isFixture else { return }
        await refreshTanomiSnapshot(force: false)
    }

    func refreshTanomiMetadata(force: Bool = false) async {
        guard !isFixture, !Task.isCancelled else { return }
        guard force || Date.now.timeIntervalSince(lastTanomiMetadataRefresh) >= 60 else { return }
        await refreshes.run("tanomi-metadata") { generation in
            self.lastTanomiMetadataRefresh = .now
            // This low-frequency lane never gates usable task results. Partial
            // failures keep the previous configuration and usage snapshot.
            if let repos = try? await self.api.get("api/tanomi/repos", as: [TanomiRepository].self),
               self.refreshes.isCurrent("tanomi-metadata", generation), self.tanomiRepositories != repos {
                self.tanomiRepositories = repos
            }
            if let config = try? await self.api.get("api/tanomi/config", as: TanomiConfig.self),
               self.refreshes.isCurrent("tanomi-metadata", generation), self.tanomiConfig != config {
                self.tanomiConfig = config
            }
            let usage = try? await self.api.get("api/tanomi/usage", as: TanomiUsage.self)
            guard self.refreshes.isCurrent("tanomi-metadata", generation) else {
                self.lastTanomiMetadataRefresh = .distantPast
                return
            }
            if let usage, self.tanomiUsage != usage { self.tanomiUsage = usage }
            if self.tanomiUsageFailed != (usage == nil) { self.tanomiUsageFailed = usage == nil }
        }
    }

    private func refreshTanomiSnapshot(force: Bool) async {
        await refreshes.run("tanomi", replacing: force) { generation in
            do {
                let tasks = try await self.api.get("api/tanomi/tasks",
                    queryItems: [URLQueryItem(name: "limit", value: "50")], as: TanomiBuckets.self)
                guard self.refreshes.isCurrent("tanomi", generation) else { return }
                self.setLoadState(\.tanomiLoadState, .loaded)
                let pending = Set(self.pendingArchives.keys.filter { $0.hasPrefix("tanomi-") }.map { String($0.dropFirst(7)) })
                let visibleTasks = tasks.tasks.filter { !pending.contains($0.id) }
                if self.tanomiTasks != visibleTasks { self.tanomiTasks = visibleTasks }
                if self.tanomiArchivedTasks != tasks.archived { self.tanomiArchivedTasks = tasks.archived }
                if !self.tanomiAvailable { self.tanomiAvailable = true }
                if self.tanomiStatusMessage != nil { self.tanomiStatusMessage = nil }
            } catch {
                guard self.refreshes.isCurrent("tanomi", generation) else { return }
                self.setLoadState(\.tanomiLoadState, .failed(error.localizedDescription))
                if self.tanomiAvailable { self.tanomiAvailable = false }
                if self.tanomiStatusMessage != error.localizedDescription { self.tanomiStatusMessage = error.localizedDescription }
            }
        }
    }

    private func refreshAgentSnapshot(force: Bool) async {
        await refreshes.run("agents", replacing: force) { generation in
            do {
                let envelope = try await self.api.get("api/agent-jobs", as: AgentEnvelope.self)
                guard self.refreshes.isCurrent("agents", generation) else { return }
                self.setLoadState(\.agentLoadState, .loaded)
                let pending = Set(self.pendingArchives.keys.filter { $0.hasPrefix("daymeld-") }.map { String($0.dropFirst(8)) })
                let visibleJobs = envelope.jobs.filter { !pending.contains($0.id) }
                if self.agents != visibleJobs { self.agents = visibleJobs }
                if self.archivedAgents != envelope.archivedJobs { self.archivedAgents = envelope.archivedJobs }
                if self.repositories != envelope.repositories { self.repositories = envelope.repositories }
                if self.agentModels != envelope.models { self.agentModels = envelope.models }
                for job in self.agentNotifications.changedJobs(active: envelope.jobs, archived: envelope.archivedJobs) {
                    await self.agentNotifications.schedule(for: job)
                }
            } catch {
                guard self.refreshes.isCurrent("agents", generation) else { return }
                self.setLoadState(\.agentLoadState, .failed("Agentの一覧を取得できませんでした"))
            }
        }
    }

    func agentDetail(_ jobID: String) async -> AgentJob? {
        if fixture != nil { return agents.first(where: { $0.id == jobID }) ?? archivedAgents.first(where: { $0.id == jobID }) }
        do {
            let encoded = jobID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? jobID
            return try await api.get("api/agent-jobs/\(encoded)", as: AgentJob.self)
        } catch {
            return nil
        }
    }

    func sendInstruction(jobID: String, instruction: String) async -> Bool {
        if fixture != nil {
            guard let index = agents.firstIndex(where: { $0.id == jobID }) else { return false }
            let job = agents[index]
            let events = (job.events ?? []) + [AgentEvent(createdAt: ISO8601DateFormatter().string(from: .now), kind: "user", message: instruction)]
            agents[index] = AgentJob(id: job.id, repository: job.repository, repositoryLabel: job.repositoryLabel, prompt: job.prompt, status: job.status, phase: job.phase, summary: job.summary, model: job.model, reasoningEffort: job.reasoningEffort, updatedAt: ISO8601DateFormatter().string(from: .now), recentEvents: events.suffix(3).map { $0 }, events: events, mode: job.mode, followUp: job.followUp, worktree: job.worktree)
            return true
        }
        do {
            let _: EmptyResponse = try await api.post(
                "api/agent-jobs/attach",
                body: AgentInstruction(jobID: jobID, instruction: instruction),
                as: EmptyResponse.self
            )
            await refreshAgentSnapshot(force: true)
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func cancelAgent(jobID: String) async {
        if fixture != nil {
            guard let index = agents.firstIndex(where: { $0.id == jobID }) else { return }
            let job = agents[index]
            agents[index] = AgentJob(id: job.id, repository: job.repository, repositoryLabel: job.repositoryLabel, prompt: job.prompt, status: "cancelled", phase: "キャンセル済み", summary: job.summary, model: job.model, reasoningEffort: job.reasoningEffort, updatedAt: ISO8601DateFormatter().string(from: .now), recentEvents: job.recentEvents, events: job.events, mode: job.mode, followUp: job.followUp, worktree: job.worktree)
            return
        }
        await performAgentAction("api/agent-jobs/cancel", jobID: jobID)
    }

    func hideAgent(jobID: String) async {
        guard let index = agents.firstIndex(where: { $0.id == jobID }) else { return }
        let job = agents[index]
        _ = withAnimation(.easeInOut(duration: 0.24)) { agents.remove(at: index) }
        if fixture != nil {
            archivedAgents.insert(job, at: 0)
            pendingAgentJobs.removeValue(forKey: jobID)
            return
        }
        pendingAgentJobs[jobID] = job
        await archiveWithEffect(id: "daymeld-\(jobID)", index: index) {
            try await self.api.post("api/agent-jobs/hide", body: AgentJobAction(jobID: jobID), as: EmptyResponse.self)
        }
    }

    private func performAgentAction(_ path: String, jobID: String) async {
        do {
            let _: EmptyResponse = try await api.post(
                path, body: AgentJobAction(jobID: jobID), as: EmptyResponse.self
            )
            await refreshAgentSnapshot(force: true)
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func archiveWithEffect(id: String, index: Int, action: () async throws -> EmptyResponse) async {
        guard pendingArchives[id] == nil else { return }
        pendingArchives[id] = PendingArchive(index: index)
        do {
            _ = try await action()
            // Invalidate synchronously before lifting the optimistic filter.
            refreshes.invalidate(id.hasPrefix("daymeld-") ? "agents" : "tanomi")
            pendingArchives.removeValue(forKey: id)
            pendingAgentJobs.removeValue(forKey: String(id.dropFirst(8)))
            pendingTanomiTasks.removeValue(forKey: String(id.dropFirst(7)))
            if id.hasPrefix("daymeld-") { await refreshAgentSnapshot(force: true) }
            else { await refreshTanomiSnapshot(force: true) }
        } catch {
            let pending = pendingArchives.removeValue(forKey: id)
            if id.hasPrefix("daymeld-"), let job = pendingAgentJobs.removeValue(forKey: String(id.dropFirst(8))) {
                _ = withAnimation(.spring(response: 0.42, dampingFraction: 0.78)) {
                    agents.insert(job, at: min(pending?.index ?? agents.count, agents.count))
                }
            }
            if id.hasPrefix("tanomi-"), let task = pendingTanomiTasks.removeValue(forKey: String(id.dropFirst(7))) {
                _ = withAnimation(.spring(response: 0.42, dampingFraction: 0.78)) {
                    tanomiTasks.insert(task, at: min(pending?.index ?? tanomiTasks.count, tanomiTasks.count))
                }
            }
            errorMessage = error.localizedDescription
        }
    }

    func toggle(_ task: PlannerTask) async {
        if fixture != nil {
            guard var snapshot = today else { return }
            if snapshot.tasks.contains(where: { $0.id == task.id }) {
                snapshot.tasks = snapshot.tasks.filter { $0.id != task.id || task.isCompleted }
            } else {
                snapshot.tasks = snapshot.tasks.map { $0.id == task.id ? PlannerTask(id: $0.id, title: $0.title, dueDate: $0.dueDate, priority: $0.priority, recurrence: $0.recurrence, completedToday: task.isCompleted ? nil : 1) : $0 }
            }
            snapshot.routines = snapshot.routines.map { $0.id == task.id ? PlannerTask(id: $0.id, title: $0.title, dueDate: $0.dueDate, priority: $0.priority, recurrence: $0.recurrence, completedToday: $0.isCompleted ? 0 : 1) : $0 }
            today = snapshot
            return
        }
        do {
            let _: EmptyResponse = try await api.post("api/task-status", body: TaskStatus(taskID: task.id, completed: !task.isCompleted), as: EmptyResponse.self)
            await refreshToday(force: true)
        } catch { errorMessage = error.localizedDescription }
    }

    func createTask(title: String, dueDate: String?, priority: Int, recurrence: String) async -> Bool {
        if fixture != nil {
            guard !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  let snapshot = today else { return false }
            let task = PlannerTask(id: "fixture-created-task-\(UUID().uuidString)", title: title, dueDate: dueDate, priority: priority, recurrence: recurrence, completedToday: recurrence == "none" ? nil : 0)
            var updated = snapshot
            if recurrence == "none" { updated.tasks.append(task) } else { updated.routines.append(task) }
            today = updated
            return true
        }
        do {
            let _: PlannerTask = try await api.post("api/tasks", body: NewTask(title: title, dueDate: dueDate, priority: priority, recurrence: recurrence), as: PlannerTask.self)
            await refreshToday(force: true)
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func deleteTask(_ task: PlannerTask) async -> Bool {
        if fixture != nil {
            guard var snapshot = today else { return false }
            snapshot.tasks.removeAll { $0.id == task.id }
            snapshot.routines.removeAll { $0.id == task.id }
            today = snapshot
            return true
        }
        do {
            let _: EmptyResponse = try await api.post("api/tasks/delete", body: TaskAction(taskID: task.id), as: EmptyResponse.self)
            await refreshToday(force: true)
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    func markArticleRead(_ article: Article, surface: String = "article_feed") async -> Bool {
        guard !readArticleIDs.contains(article.id) else { return true }
        readArticleIDs.insert(article.id)
        if fixture != nil { return true }
        persistArticleIDs(readArticleIDs, forKey: "daily-reader.native.read")
        do {
            let _: EmptyResponse = try await api.post("api/read", body: ArticleInteraction(articleID: article.id, surface: surface), as: EmptyResponse.self)
            return true
        } catch {
            readArticleIDs.remove(article.id)
            persistArticleIDs(readArticleIDs, forKey: "daily-reader.native.read")
            errorMessage = error.localizedDescription
            return false
        }
    }

    func toggleArticleSaved(_ article: Article) {
        if savedArticleIDs.contains(article.id) { savedArticleIDs.remove(article.id) }
        else { savedArticleIDs.insert(article.id) }
        if fixture == nil { persistArticleIDs(savedArticleIDs, forKey: "daily-reader.native.saved") }
    }

    @discardableResult
    func hideArticle(_ article: Article, surface: String = "article_feed") async -> Bool {
        hiddenArticleIDs.insert(article.id)
        if fixture != nil { return true }
        persistArticleIDs(hiddenArticleIDs, forKey: "daily-reader.native.hidden")
        do {
            let _: EmptyResponse = try await api.post("api/feedback", body: ArticleInteraction(articleID: article.id, surface: surface), as: EmptyResponse.self)
            return true
        } catch {
            hiddenArticleIDs.remove(article.id)
            persistArticleIDs(hiddenArticleIDs, forKey: "daily-reader.native.hidden")
            errorMessage = error.localizedDescription
            return false
        }
    }

    func saveHealthCheckin(_ snapshot: HealthSnapshot) async -> Bool {
        if fixture != nil {
            guard var current = today else { return false }
            current.health = snapshot
            today = current
            return true
        }
        do {
            let _: EmptyResponse = try await api.post("api/health/checkin", body: snapshot, as: EmptyResponse.self)
            await refresh(afterMutation: true)
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    @discardableResult
    func act(on email: EmailReminder, action: String) async -> Bool {
        guard pendingEmailActions[email.threadID] == nil,
              let index = emails.firstIndex(where: { $0.threadID == email.threadID }) else { return false }
        pendingEmailActions[email.threadID] = (email, index)
        _ = withAnimation(.easeInOut(duration: 0.24)) {
            emails.remove(at: index)
        }
        if fixture != nil {
            pendingEmailActions.removeValue(forKey: email.threadID)
            return true
        }
        do {
            let _: EmptyResponse = try await api.post("api/email-status", body: EmailAction(threadID: email.threadID, action: action), as: EmptyResponse.self)
            refreshes.invalidate("email")
            pendingEmailActions.removeValue(forKey: email.threadID)
            await refreshEmails(force: true)
            return true
        } catch { errorMessage = error.localizedDescription }
        if pendingEmailActions[email.threadID] != nil {
            let pending = pendingEmailActions.removeValue(forKey: email.threadID)!
            let restoredIndex = min(pending.index, emails.count)
            _ = withAnimation(.spring(response: 0.42, dampingFraction: 0.78)) {
                emails.insert(pending.email, at: restoredIndex)
            }
        }
        return false
    }

    func fetchEmailContent(threadID: String) async throws -> EmailThreadContent {
        if let fixture {
            if let content = fixture.emailContents[threadID] { return content }
            return EmailThreadContent(threadID: threadID, subject: "fixtureメール", accountEmail: "fixture@example.invalid", messages: [])
        }
        let encoded = threadID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? threadID
        return try await api.get("api/email-content/\(encoded)", as: EmailThreadContent.self)
    }

#if os(iOS)
    func syncHealth() async {
        if fixture != nil { return }
        do {
            PhoneContextSync.shared.healthMessage = try await HealthAutoSync.shared.synchronize(force: true, requestAuthorization: true)
            await refresh(afterMutation: true)
        } catch { errorMessage = error.localizedDescription }
    }
#endif

    private func requestNotifications() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
    }
}
