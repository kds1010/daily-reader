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
    private struct PendingArchive {
        let index: Int
    }
    private var pendingArchives: [String: PendingArchive] = [:]
    private var pendingAgentJobs: [String: AgentJob] = [:]
    private var pendingTanomiTasks: [String: TanomiTask] = [:]

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

    func createTanomi(prompt: String, repo: String, model: String, permissionMode: String, effort: String?) async -> Bool {
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
        do {
            let _: EmptyResponse = try await api.post("api/tanomi/tasks/\(task.id)/stop", body: EmptyRequest(), as: EmptyResponse.self)
            await refreshAgents()
        } catch { errorMessage = error.localizedDescription }
    }

    func hideTanomi(_ task: TanomiTask) async {
        guard let index = tanomiTasks.firstIndex(where: { $0.id == task.id }) else { return }
        _ = withAnimation(.easeInOut(duration: 0.24)) { tanomiTasks.remove(at: index) }
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
        snapshotRefreshInProgress = true
        defer { snapshotRefreshInProgress = false }
        _ = await refreshAgentSnapshot()
        _ = await refreshTanomiSnapshot()
    }

    @discardableResult
    private func refreshTanomiSnapshot() async -> Bool {
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
            if tanomiAvailable { tanomiAvailable = false }
            if !tanomiUsageFailed { tanomiUsageFailed = true }
            let message = error.localizedDescription
            if tanomiStatusMessage != message { tanomiStatusMessage = message }
            return false
        }
    }

    @discardableResult
    private func refreshAgentSnapshot() async -> Bool {
        agentRefreshGeneration += 1
        let generation = agentRefreshGeneration
        guard let envelope = try? await api.get("api/agent-jobs", as: AgentEnvelope.self),
              generation == agentRefreshGeneration else { return false }

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
        guard let index = agents.firstIndex(where: { $0.id == jobID }) else { return }
        let job = agents[index]
        _ = withAnimation(.easeInOut(duration: 0.24)) { agents.remove(at: index) }
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
        do {
            let _: EmptyResponse = try await api.post("api/task-status", body: TaskStatus(taskID: task.id, completed: !task.isCompleted), as: EmptyResponse.self)
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }

    @discardableResult
    func act(on email: EmailReminder, action: String) async -> Bool {
        guard pendingEmailActions[email.threadID] == nil,
              let index = emails.firstIndex(where: { $0.threadID == email.threadID }) else { return false }
        pendingEmailActions[email.threadID] = (email, index)
        _ = withAnimation(.easeInOut(duration: 0.24)) {
            emails.remove(at: index)
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
