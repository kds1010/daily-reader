import Foundation
import Combine
import SwiftUI
import UserNotifications

@MainActor
final class AppModel: ObservableObject {
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

    private let api = APIClient.shared
    private var fixture: DaymeldFixture?
#if os(iOS)
    private let health = HealthService()
#endif
    #if os(iOS)
    private let agentNotifications = AgentNotificationCoordinator.shared
    #else
    private let agentNotifications = AgentNotificationCoordinator()
    #endif
    private var agentRefreshGeneration = 0
    private var tanomiRefreshGeneration = 0
    private var snapshotRefreshInProgress = false
    private var pendingEmailActions: [String: (email: EmailReminder, index: Int)] = [:]
    private struct PendingArchive {
        let index: Int
    }
    private var pendingArchives: [String: PendingArchive] = [:]
    private var pendingAgentJobs: [String: AgentJob] = [:]
    private var pendingTanomiTasks: [String: TanomiTask] = [:]

    var isFixture: Bool { fixture != nil }

    init(fixture: DaymeldFixture? = nil) {
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

    func refresh() async {
        guard !isRefreshing else { return }
        if let fixture {
            isRefreshing = true
            defer { isRefreshing = false }
            applyFixture(fixture)
            return
        }
        isRefreshing = true
        snapshotRefreshInProgress = true
        errorMessage = nil
        defer {
            snapshotRefreshInProgress = false
            isRefreshing = false
        }
        var updated = false
        todayLoadState = .loading
        do {
            today = try await api.get("api/today", as: TodayEnvelope.self)
            todayLoadState = .loaded
            updated = true
        } catch { todayLoadState = .failed(error.localizedDescription) }
        emailLoadState = .loading
        do {
            let mail = try await api.get("api/emails/unread", as: EmailEnvelope.self)
            let pendingIDs = Set(pendingEmailActions.keys)
            emails = mail.items.filter { !pendingIDs.contains($0.threadID) }
            emailSyncError = mail.syncError
            emailCanMarkRead = mail.canMarkRead ?? true
            emailLoadState = .loaded
            updated = true
        } catch {
            emailLoadState = .failed(error.localizedDescription)
            emailSyncError = "メール同期結果を取得できませんでした：\(error.localizedDescription)"
        }
        if await refreshAgentSnapshot() {
            updated = true
        }
        if await refreshTanomiSnapshot() { updated = true }
        codexUsageLoadState = .loading
        do {
            codexUsage = try await api.get("api/codex-usage", as: CodexUsageEnvelope.self)
            codexUsageFailed = false
            codexUsageLoadState = .loaded
            updated = true
        } catch {
            codexUsageFailed = true
            codexUsageLoadState = .failed(error.localizedDescription)
        }
        newsLoadState = .loading
        do {
            let news = try await api.get("data/articles.json", as: ArticleEnvelope.self)
            articles = news.articles
            newsLoadState = .loaded
            updated = true
        } catch { newsLoadState = .failed(error.localizedDescription) }
        deploymentLoadState = .loading
        do {
            deploymentInfo = try await api.get("api/deployment", as: DeploymentInfo.self)
            deploymentLoadState = .loaded
        } catch { deploymentLoadState = .failed(error.localizedDescription) }
        if updated {
            lastUpdated = .now
        }
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
            await refresh()
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
            await refreshAgents()
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
            await refreshAgents()
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
            await refreshAgents()
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

    func refreshAgents() async {
        guard !isRefreshing, !snapshotRefreshInProgress else { return }
        if fixture != nil { return }
        snapshotRefreshInProgress = true
        defer { snapshotRefreshInProgress = false }
        _ = await refreshAgentSnapshot()
        _ = await refreshTanomiSnapshot()
    }

    @discardableResult
    private func refreshTanomiSnapshot() async -> Bool {
        if fixture != nil {
            tanomiLoadState = fixture?.failedResources.contains(.tanomi) == true
                ? .failed(fixture?.tanomiStatusMessage ?? "fixture: tanomiを取得できません") : .loaded
            return true
        }
        tanomiRefreshGeneration += 1
        let generation = tanomiRefreshGeneration
        do {
            async let repositories: [TanomiRepository] = api.get("api/tanomi/repos")
            async let config: TanomiConfig = api.get("api/tanomi/config")
            async let buckets: TanomiBuckets = api.get(
                "api/tanomi/tasks",
                queryItems: [URLQueryItem(name: "limit", value: "50")]
            )
            async let health: TanomiHealth = api.get("api/tanomi/health")
            async let usage: TanomiUsage? = try? api.get("api/tanomi/usage")
            let (repos, configSnapshot, tasks, status, usageSnapshot) = try await (repositories, config, buckets, health, usage)
            guard generation == tanomiRefreshGeneration else { return false }
            tanomiLoadState = .loaded
            if tanomiRepositories != repos { tanomiRepositories = repos }
            if tanomiConfig != configSnapshot { tanomiConfig = configSnapshot }
            let pending = Set(pendingArchives.keys.filter { $0.hasPrefix("tanomi-") }.map { String($0.dropFirst(7)) })
            let visibleTasks = tasks.tasks.filter { !pending.contains($0.id) }
            if tanomiTasks != visibleTasks { tanomiTasks = visibleTasks }
            if tanomiArchivedTasks != tasks.archived { tanomiArchivedTasks = tasks.archived }
            if tanomiAvailable != status.ok { tanomiAvailable = status.ok }
            if let usageSnapshot, tanomiUsage != usageSnapshot { tanomiUsage = usageSnapshot }
            let usageFailed = usageSnapshot == nil
            if tanomiUsageFailed != usageFailed { tanomiUsageFailed = usageFailed }
            let message = status.ok ? nil : "tanomiのヘルスチェックが失敗しました"
            if tanomiStatusMessage != message { tanomiStatusMessage = message }
            return true
        } catch {
            guard generation == tanomiRefreshGeneration else { return false }
            tanomiLoadState = .failed(error.localizedDescription)
            if tanomiAvailable { tanomiAvailable = false }
            if !tanomiUsageFailed { tanomiUsageFailed = true }
            let message = error.localizedDescription
            if tanomiStatusMessage != message { tanomiStatusMessage = message }
            return false
        }
    }

    @discardableResult
    private func refreshAgentSnapshot() async -> Bool {
        if fixture != nil {
            agentLoadState = fixture?.failedResources.contains(.agents) == true
                ? .failed("fixture: Agentを取得できません") : .loaded
            return true
        }
        agentRefreshGeneration += 1
        let generation = agentRefreshGeneration
        guard let envelope = try? await api.get("api/agent-jobs", as: AgentEnvelope.self),
              generation == agentRefreshGeneration else {
            agentLoadState = .failed("Agentの一覧を取得できませんでした")
            return false
        }
        agentLoadState = .loaded

        for job in agentNotifications.changedJobs(active: envelope.jobs, archived: envelope.archivedJobs) {
            await agentNotifications.schedule(for: job)
        }
        let pending = Set(pendingArchives.keys.filter { $0.hasPrefix("daymeld-") }.map { String($0.dropFirst(8)) })
        let visibleJobs = envelope.jobs.filter { !pending.contains($0.id) }
        if agents != visibleJobs { agents = visibleJobs }
        if archivedAgents != envelope.archivedJobs { archivedAgents = envelope.archivedJobs }
        if repositories != envelope.repositories { repositories = envelope.repositories }
        if agentModels != envelope.models { agentModels = envelope.models }
        return true
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
            await refreshAgents()
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
            await refreshAgents()
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    private func archiveWithEffect(id: String, index: Int, action: () async throws -> EmptyResponse) async {
        guard pendingArchives[id] == nil else { return }
        pendingArchives[id] = PendingArchive(index: index)
        do {
            _ = try await action()
            pendingArchives.removeValue(forKey: id)
            pendingAgentJobs.removeValue(forKey: String(id.dropFirst(8)))
            pendingTanomiTasks.removeValue(forKey: String(id.dropFirst(7)))
            await refreshAgents()
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
            await refresh()
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
            await refresh()
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
            await refresh()
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
            await refresh()
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
            pendingEmailActions.removeValue(forKey: email.threadID)
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
            let token = try SecretStore.readHealthToken() ?? ""
            guard !token.isEmpty else { throw APIClientError.server("設定でHealthKit同期トークンを入力してください") }
            let snapshot = try await health.readToday()
            try await api.syncHealth(snapshot, token: token)
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }
#endif

    private func requestNotifications() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
    }
}
