import Foundation
import Combine
import UserNotifications

@MainActor
final class AppModel: ObservableObject {
    @Published var agents: [AgentJob] = []
    @Published var repositories: [Repository] = []
    @Published var today: TodayEnvelope?
    @Published var emails: [EmailReminder] = []
    @Published var articles: [Article] = []
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
        } catch {
            errorMessage = "HealthKit同期トークンを安全に取り込めませんでした：\(error.localizedDescription)"
        }
        await requestNotifications()
        await refresh()
    }

    func refresh() async {
        guard !isRefreshing else { return }
        isRefreshing = true
        defer { isRefreshing = false }
        do {
            async let agentRequest = api.get("api/agent-jobs", as: AgentEnvelope.self)
            async let todayRequest = api.get("api/today", as: TodayEnvelope.self)
            async let emailRequest = api.get("api/email-reminders/daily", as: EmailEnvelope.self)
            async let articleRequest = api.get("data/articles.json", as: ArticleEnvelope.self)
            let (agent, day, mail, news) = try await (agentRequest, todayRequest, emailRequest, articleRequest)
            notifyAgentChanges(agent.jobs)
            agents = agent.jobs
            repositories = agent.repositories
            today = day
            emails = mail.items
            articles = news.articles
            lastUpdated = .now
            errorMessage = nil
        } catch {
            errorMessage = error.localizedDescription
        }
    }

    func createAgent(prompt: String, repository: String) async -> Bool {
        do {
            let _: AgentJob = try await api.post("api/agent-jobs", body: NewAgentJob(repository: repository, prompt: prompt), as: AgentJob.self)
            await refresh()
            return true
        } catch { errorMessage = error.localizedDescription; return false }
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
