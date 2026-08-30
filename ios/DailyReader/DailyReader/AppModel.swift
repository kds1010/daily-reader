import Foundation
import Combine
import UserNotifications

@MainActor
final class AppModel: ObservableObject {
    @Published var agents: [AgentJob] = []
    @Published var archivedAgents: [AgentJob] = []
    @Published var tanomiTasks: [TanomiTask] = []
    @Published var tanomiArchivedTasks: [TanomiTask] = []
    @Published var tanomiRepositories: [TanomiRepository] = []
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
    @Published private(set) var archiveEffectIDs: Set<String> = []

    private let api = APIClient.shared
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
    private var pendingArchiveIDs: Set<String> = []

    func start() async {
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
        isRefreshing = true
        snapshotRefreshInProgress = true
        errorMessage = nil
        defer {
            snapshotRefreshInProgress = false
            isRefreshing = false
        }
        var updated = false
        if let day = try? await api.get("api/today", as: TodayEnvelope.self) {
            today = day
            updated = true
        }
        do {
            let mail = try await api.get("api/emails/unread", as: EmailEnvelope.self)
            let pendingIDs = Set(pendingEmailActions.keys)
            emails = mail.items.filter { !pendingIDs.contains($0.threadID) }
            emailSyncError = mail.syncError
            emailCanMarkRead = mail.canMarkRead ?? true
            updated = true
        } catch {
            emailSyncError = "メール同期結果を取得できませんでした：\(error.localizedDescription)"
        }
        if await refreshAgentSnapshot() {
            updated = true
        }
        if await refreshTanomiSnapshot() { updated = true }
        do {
            codexUsage = try await api.get("api/codex-usage", as: CodexUsageEnvelope.self)
            codexUsageFailed = false
            updated = true
        } catch {
            codexUsageFailed = true
        }
        if let news = try? await api.get("data/articles.json", as: ArticleEnvelope.self) {
            articles = news.articles
            updated = true
        }
        if let deployment = try? await api.get("api/deployment", as: DeploymentInfo.self) {
            deploymentInfo = deployment
        }
        if updated {
            lastUpdated = .now
        }
    }

    func createAgent(prompt: String, repository: String, model: String, reasoningEffort: String) async -> Bool {
        do {
            let _: AgentJob = try await api.post("api/agent-jobs", body: NewAgentJob(repository: repository, prompt: prompt, model: model, reasoningEffort: reasoningEffort), as: AgentJob.self)
            await refresh()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func createTanomi(prompt: String, repo: String, model: String, permissionMode: String) async -> Bool {
        do {
            let _: EmptyResponse = try await api.post(
                "api/tanomi/tasks",
                body: NewTanomiTask(prompt: prompt, repo: repo, model: model, permissionMode: permissionMode),
                as: EmptyResponse.self
            )
            await refreshAgents()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func stopTanomi(_ task: TanomiTask) async {
        do {
            let _: EmptyResponse = try await api.post("api/tanomi/tasks/\(task.id)/stop", body: EmptyRequest(), as: EmptyResponse.self)
            await refreshAgents()
        } catch { errorMessage = error.localizedDescription }
    }

    func hideTanomi(_ task: TanomiTask) async {
        await archiveWithEffect(id: "tanomi-\(task.id)") {
            try await self.api.post(
                "api/tanomi/tasks/\(task.id)/archive",
                body: EmptyRequest(),
                as: EmptyResponse.self
            )
        }
    }

    func refreshAgents() async {
        guard !isRefreshing, !snapshotRefreshInProgress else { return }
        snapshotRefreshInProgress = true
        defer { snapshotRefreshInProgress = false }
        _ = await refreshAgentSnapshot()
        _ = await refreshTanomiSnapshot()
    }

    @discardableResult
    private func refreshTanomiSnapshot() async -> Bool {
        guard !pendingArchiveIDs.contains(where: { $0.hasPrefix("tanomi-") }) else { return false }
        tanomiRefreshGeneration += 1
        let generation = tanomiRefreshGeneration
        do {
            async let repositories: [TanomiRepository] = api.get("api/tanomi/repos")
            async let buckets: TanomiBuckets = api.get(
                "api/tanomi/tasks",
                queryItems: [URLQueryItem(name: "limit", value: "50")]
            )
            async let health: TanomiHealth = api.get("api/tanomi/health")
            async let usage: TanomiUsage? = try? api.get("api/tanomi/usage")
            let (repos, tasks, status, usageSnapshot) = try await (repositories, buckets, health, usage)
            guard generation == tanomiRefreshGeneration else { return false }
            if tanomiRepositories != repos { tanomiRepositories = repos }
            if tanomiTasks != tasks.tasks { tanomiTasks = tasks.tasks }
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
            if tanomiAvailable { tanomiAvailable = false }
            if !tanomiUsageFailed { tanomiUsageFailed = true }
            let message = error.localizedDescription
            if tanomiStatusMessage != message { tanomiStatusMessage = message }
            return false
        }
    }

    @discardableResult
    private func refreshAgentSnapshot() async -> Bool {
        guard !pendingArchiveIDs.contains(where: { $0.hasPrefix("daymeld-") }) else { return false }
        agentRefreshGeneration += 1
        let generation = agentRefreshGeneration
        guard let envelope = try? await api.get("api/agent-jobs", as: AgentEnvelope.self),
              generation == agentRefreshGeneration else { return false }

        for job in agentNotifications.changedJobs(active: envelope.jobs, archived: envelope.archivedJobs) {
            await agentNotifications.schedule(for: job)
        }
        if agents != envelope.jobs { agents = envelope.jobs }
        if archivedAgents != envelope.archivedJobs { archivedAgents = envelope.archivedJobs }
        if repositories != envelope.repositories { repositories = envelope.repositories }
        if agentModels != envelope.models { agentModels = envelope.models }
        return true
    }

    func agentDetail(_ jobID: String) async -> AgentJob? {
        do {
            let encoded = jobID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? jobID
            return try await api.get("api/agent-jobs/\(encoded)", as: AgentJob.self)
        } catch {
            errorMessage = error.localizedDescription
            return nil
        }
    }

    func sendInstruction(jobID: String, instruction: String) async -> Bool {
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
        await performAgentAction("api/agent-jobs/cancel", jobID: jobID)
    }

    func hideAgent(jobID: String) async {
        await archiveWithEffect(id: "daymeld-\(jobID)") {
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

    private func archiveWithEffect(id: String, action: () async throws -> EmptyResponse) async {
        guard pendingArchiveIDs.insert(id).inserted else { return }
        if id.hasPrefix("daymeld-") { agentRefreshGeneration += 1 }
        else { tanomiRefreshGeneration += 1 }
        do {
            _ = try await action()
            archiveEffectIDs.insert(id)
            try? await Task.sleep(for: .milliseconds(450))
            pendingArchiveIDs.remove(id)
            await refreshAgents()
            archiveEffectIDs.remove(id)
        } catch {
            pendingArchiveIDs.remove(id)
            archiveEffectIDs.remove(id)
            errorMessage = error.localizedDescription
        }
    }

    func toggle(_ task: PlannerTask) async {
        do {
            let _: EmptyResponse = try await api.post("api/task-status", body: TaskStatus(taskID: task.id, completed: !task.isCompleted), as: EmptyResponse.self)
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }

    func act(on email: EmailReminder, action: String) async {
        guard pendingEmailActions[email.threadID] == nil,
              let index = emails.firstIndex(where: { $0.threadID == email.threadID }) else { return }
        pendingEmailActions[email.threadID] = (email, index)
        emails.remove(at: index)
        do {
            let _: EmptyResponse = try await api.post("api/email-status", body: EmailAction(threadID: email.threadID, action: action), as: EmptyResponse.self)
            pendingEmailActions.removeValue(forKey: email.threadID)
        } catch { errorMessage = error.localizedDescription }
        if pendingEmailActions[email.threadID] != nil {
            let pending = pendingEmailActions.removeValue(forKey: email.threadID)!
            let restoredIndex = min(pending.index, emails.count)
            emails.insert(pending.email, at: restoredIndex)
        }
    }

    func fetchEmailContent(threadID: String) async throws -> EmailThreadContent {
        let encoded = threadID.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed) ?? threadID
        return try await api.get("api/email-content/\(encoded)", as: EmailThreadContent.self)
    }

#if os(iOS)
    func syncHealth() async {
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
