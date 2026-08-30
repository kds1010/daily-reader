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
    @Published var repositories: [Repository] = []
    @Published var agentModels: [AgentModelOption] = [.fallback]
    @Published var today: TodayEnvelope?
    @Published var emails: [EmailReminder] = []
    @Published var articles: [Article] = []
    @Published var codexUsage: CodexUsageEnvelope?
    @Published var codexUsageFailed = false
    @Published var deploymentInfo: DeploymentInfo?
    @Published var isRefreshing = false
    @Published var errorMessage: String?
    @Published var lastUpdated: Date?
    @Published var selectedTab = 0

    private let api = APIClient.shared
    private let health = HealthService()
    private let agentNotifications = AgentNotificationCoordinator()
    private var agentRefreshGeneration = 0

    func start() async {
        do {
            try SecretStore.importBootstrapHealthToken()
            try SecretStore.prepareBootstrapHealthTokenFile()
        } catch {
            errorMessage = "HealthKit同期トークンを安全に取り込めませんでした：\(error.localizedDescription)"
        }
        await requestNotifications()
        await refresh()
    }

    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        errorMessage = nil
        defer { isRefreshing = false }
        var updated = false
        if let day = try? await api.get("api/today", as: TodayEnvelope.self) {
            today = day
            updated = true
        }
        if let mail = try? await api.get("api/email-reminders/daily", as: EmailEnvelope.self) {
            emails = mail.items
            updated = true
        }
        if await refreshAgentSnapshot() {
            updated = true
        }
        if let repos = try? await api.get("api/tanomi/repos", as: [TanomiRepository].self) {
            tanomiRepositories = repos
        }
        if let buckets = try? await api.get("api/tanomi/tasks?limit=50", as: TanomiBuckets.self) {
            tanomiTasks = buckets.tasks
            tanomiArchivedTasks = buckets.archived
            tanomiAvailable = true
            updated = true
        } else {
            tanomiAvailable = false
        }
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

    func refreshAgents() async {
        _ = await refreshAgentSnapshot()
        if let buckets = try? await api.get("api/tanomi/tasks?limit=50", as: TanomiBuckets.self) {
            tanomiTasks = buckets.tasks
            tanomiArchivedTasks = buckets.archived
            tanomiAvailable = true
        } else {
            tanomiAvailable = false
        }
    }

    @discardableResult
    private func refreshAgentSnapshot() async -> Bool {
        agentRefreshGeneration += 1
        let generation = agentRefreshGeneration
        guard let envelope = try? await api.get("api/agent-jobs", as: AgentEnvelope.self),
              generation == agentRefreshGeneration else { return false }

        for job in agentNotifications.changedJobs(active: envelope.jobs, archived: envelope.archivedJobs) {
            agentNotifications.schedule(for: job)
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
        await performAgentAction("api/agent-jobs/hide", jobID: jobID)
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

    func toggle(_ task: PlannerTask) async {
        do {
            let _: EmptyResponse = try await api.post("api/task-status", body: TaskStatus(taskID: task.id, completed: !task.isCompleted), as: EmptyResponse.self)
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }

    func act(on email: EmailReminder, action: String) async {
        do {
            let _: EmptyResponse = try await api.post("api/email-status", body: EmailAction(threadID: email.threadID, action: action), as: EmptyResponse.self)
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }

    func syncHealth() async {
        do {
            let token = try SecretStore.readHealthToken() ?? ""
            guard !token.isEmpty else { throw APIClientError.server("設定でHealthKit同期トークンを入力してください") }
            let snapshot = try await health.readToday()
            try await api.syncHealth(snapshot, token: token)
            await refresh()
        } catch { errorMessage = error.localizedDescription }
    }

    private func requestNotifications() async {
        _ = try? await UNUserNotificationCenter.current().requestAuthorization(options: [.alert, .badge, .sound])
    }
}
