import Foundation
import Combine
import UserNotifications

@MainActor
final class AppModel: ObservableObject {
    @Published var agents: [AgentJob] = []
    @Published var archivedAgents: [AgentJob] = []
    @Published var repositories: [Repository] = []
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
    private var previousStates: [String: String] = [:]

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
        if let agent = try? await api.get("api/agent-jobs", as: AgentEnvelope.self) {
            notifyAgentChanges(agent.jobs)
            agents = agent.jobs
            archivedAgents = agent.archivedJobs
            repositories = agent.repositories
            updated = true
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

    func createAgent(prompt: String, repository: String) async -> Bool {
        do {
            let _: AgentJob = try await api.post("api/agent-jobs", body: NewAgentJob(repository: repository, prompt: prompt), as: AgentJob.self)
            await refresh()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
    }

    func refreshAgents() async {
        do {
            let envelope = try await api.get("api/agent-jobs", as: AgentEnvelope.self)
            notifyAgentChanges(envelope.jobs)
            agents = envelope.jobs
            archivedAgents = envelope.archivedJobs
            repositories = envelope.repositories
        } catch {
        }
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

    func sendInstruction(to job: AgentJob, instruction: String) async -> Bool {
        do {
            let _: EmptyResponse = try await api.post(
                "api/agent-jobs/attach",
                body: AgentInstruction(jobID: job.id, instruction: instruction),
                as: EmptyResponse.self
            )
            await refreshAgents()
            return true
        } catch {
            errorMessage = error.localizedDescription
            return false
        }
    }

    func cancelAgent(_ job: AgentJob) async {
        await performAgentAction("api/agent-jobs/cancel", job: job)
    }

    func hideAgent(_ job: AgentJob) async {
        await performAgentAction("api/agent-jobs/hide", job: job)
    }

    private func performAgentAction(_ path: String, job: AgentJob) async {
        do {
            let _: EmptyResponse = try await api.post(
                path, body: AgentJobAction(jobID: job.id), as: EmptyResponse.self
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

    private func notifyAgentChanges(_ jobs: [AgentJob]) {
        defer { previousStates = Dictionary(uniqueKeysWithValues: jobs.map { ($0.id, $0.status) }) }
        guard !previousStates.isEmpty else { return }
        for job in jobs where previousStates[job.id] != job.status && ["completed", "blocked", "failed"].contains(job.status) {
            let content = UNMutableNotificationContent()
            content.title = job.status == "completed" ? "Agentが完了しました" : job.status == "blocked" ? "Agentが判断を待っています" : "Agentが失敗しました"
            content.body = job.summary?.isEmpty == false ? job.summary! : job.prompt
            content.sound = .default
            UNUserNotificationCenter.current().add(UNNotificationRequest(identifier: job.id, content: content, trigger: nil))
        }
    }
}
