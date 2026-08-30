import Foundation

struct AgentEnvelope: Decodable {
    let repositories: [Repository]
    let models: [AgentModelOption]
    let defaultModel: String
    let defaultReasoningEffort: String
    let jobs: [AgentJob]
    let archivedJobs: [AgentJob]

    enum CodingKeys: String, CodingKey {
        case repositories, jobs, models
        case defaultModel = "default_model"
        case defaultReasoningEffort = "default_reasoning_effort"
        case archivedJobs = "archived_jobs"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        repositories = try container.decode([Repository].self, forKey: .repositories)
        models = try container.decodeIfPresent([AgentModelOption].self, forKey: .models) ?? [.fallback]
        defaultModel = try container.decodeIfPresent(String.self, forKey: .defaultModel) ?? AgentModelOption.fallback.slug
        defaultReasoningEffort = try container.decodeIfPresent(String.self, forKey: .defaultReasoningEffort) ?? AgentModelOption.fallback.defaultReasoningEffort
        jobs = try container.decode([AgentJob].self, forKey: .jobs)
        archivedJobs = try container.decodeIfPresent([AgentJob].self, forKey: .archivedJobs) ?? []
    }
}

struct AgentModelOption: Decodable, Identifiable, Hashable {
    let slug: String
    let displayName: String
    let defaultReasoningEffort: String
    let supportedReasoningEfforts: [String]

    var id: String { slug }

    enum CodingKeys: String, CodingKey {
        case slug
        case displayName = "display_name"
        case defaultReasoningEffort = "default_reasoning_effort"
        case supportedReasoningEfforts = "supported_reasoning_efforts"
    }

    static let fallback = AgentModelOption(
        slug: "gpt-5.6-luna",
        displayName: "GPT-5.6-Luna",
        defaultReasoningEffort: "low",
        supportedReasoningEfforts: ["low", "medium", "high", "xhigh", "max"]
    )
}

struct TanomiRepository: Decodable, Identifiable, Hashable {
    let path: String
    let label: String?
    var id: String { path }

    init(from decoder: Decoder) throws {
        if let value = try? decoder.singleValueContainer().decode(String.self) {
            path = value
            label = nil
            return
        }
        let container = try decoder.container(keyedBy: CodingKeys.self)
        if let value = try container.decodeIfPresent(String.self, forKey: .path) {
            path = value
        } else {
            path = try container.decode(String.self, forKey: .name)
        }
        label = try container.decodeIfPresent(String.self, forKey: .label)
    }

    enum CodingKeys: String, CodingKey { case path, label, name }
}

struct TanomiBuckets: Decodable {
    let tasks: [TanomiTask]
    let archived: [TanomiTask]
    let deleted: [TanomiTask]
}

struct TanomiTask: Decodable, Identifiable {
    let id: String
    let title: String?
    let prompt: String?
    let repoPath: String?
    let cwd: String?
    let status: String
    let result: String?
    let error: String?
    let model: String?
    let permissionMode: String?
    let createdAt: String?
    let startedAt: String?
    let endedAt: String?

    enum CodingKeys: String, CodingKey {
        case id, title, prompt, cwd, status, result, error, model
        case repoPath = "repo_path"
        case permissionMode = "permission_mode"
        case createdAt = "created_at"
        case startedAt = "started_at"
        case endedAt = "ended_at"
    }

    var displayTitle: String { title ?? prompt ?? id }
    var displayRepository: String { repoPath ?? cwd ?? "tanomi" }
    var displayResult: String { result ?? error ?? "" }
}

struct CodexUsageEnvelope: Decodable {
    let rateLimits: CodexRateLimits?
    let rateLimitsByLimitID: [String: CodexLimit]

    enum CodingKeys: String, CodingKey {
        case rateLimits
        case rateLimitsByLimitID = "rateLimitsByLimitId"
    }
}

struct CodexRateLimits: Decodable {
    let planType: String?
}

struct CodexLimit: Decodable {
    let limitName: String?
    let primary: CodexLimitWindow?
    let secondary: CodexLimitWindow?
}

struct CodexLimitWindow: Decodable {
    let usedPercent: Double?
    let windowDurationMins: Int?
    let resetsAt: Int64?
}

struct DeploymentInfo: Decodable {
    let version: String
    let deployedAt: String

    enum CodingKeys: String, CodingKey {
        case version
        case deployedAt = "deployed_at"
    }

    var deployedDate: Date? {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter.date(from: deployedAt) ?? ISO8601DateFormatter().date(from: deployedAt)
    }
}

struct Repository: Codable, Identifiable, Hashable {
    var id: String { name }
    let name: String
    let label: String
}

struct AgentJob: Decodable, Identifiable, Equatable {
    let id: String
    let repository: String
    let repositoryLabel: String?
    let prompt: String
    let status: String
    let phase: String
    let summary: String?
    let updatedAt: String
    let recentEvents: [AgentEvent]?
    let events: [AgentEvent]?
    let mode: String?
    let followUp: Int?
    let worktree: String?

    enum CodingKeys: String, CodingKey {
        case id, repository, prompt, status, phase, summary, events, mode, worktree
        case repositoryLabel = "repository_label"
        case updatedAt = "updated_at"
        case recentEvents = "recent_events"
        case followUp = "follow_up"
    }
}

struct AgentEvent: Decodable, Identifiable, Equatable {
    var id: String { "\(createdAt)-\(kind)-\(message)" }
    let createdAt: String
    let kind: String
    let message: String

    enum CodingKeys: String, CodingKey {
        case kind, message
        case createdAt = "created_at"
    }
}

struct TodayEnvelope: Decodable {
    let date: String
    let tasks: [PlannerTask]
    let routines: [PlannerTask]
    let health: HealthSnapshot?
}

struct PlannerTask: Decodable, Identifiable {
    let id: String
    let title: String
    let dueDate: String?
    let priority: Int
    let recurrence: String
    let completedToday: Int?

    var isCompleted: Bool { completedToday == 1 }

    enum CodingKeys: String, CodingKey {
        case id, title, priority, recurrence
        case dueDate = "due_date"
        case completedToday = "completed_today"
    }
}

struct HealthSnapshot: Codable {
    var date: String?
    var sleepMinutes: Int?
    var steps: Int?
    var restingHeartRate: Double?
    var hrvMS: Double?
    var respiratoryRate: Double?
    var fatigue: Int?
    var mood: Int?
    var note: String?

    enum CodingKeys: String, CodingKey {
        case date
        case sleepMinutes = "sleep_minutes"
        case steps
        case restingHeartRate = "resting_heart_rate"
        case hrvMS = "hrv_ms"
        case respiratoryRate = "respiratory_rate"
        case fatigue, mood, note
    }
}

struct EmailEnvelope: Decodable {
    let items: [EmailReminder]
    let lastSyncAt: String?
    let syncError: String?
    let authorizationRequired: Bool?
    let canMarkRead: Bool?

    enum CodingKeys: String, CodingKey {
        case items
        case lastSyncAt = "last_sync_at"
        case syncError = "sync_error"
        case authorizationRequired = "authorization_required"
        case canMarkRead = "can_mark_read"
    }
}

struct EmailReminder: Decodable, Identifiable {
    var id: String { threadID }
    let threadID: String
    let sender: String
    let subject: String
    let importance: String
    let reason: String
    let requiredAction: String
    let dueDate: String?
    let gmailURL: URL?
    let status: String?
    let receivedAt: String?

    enum CodingKeys: String, CodingKey {
        case sender, subject, importance, reason
        case threadID = "thread_id"
        case requiredAction = "required_action"
        case dueDate = "due_date"
        case gmailURL = "gmail_url"
        case status
        case receivedAt = "received_at"
    }
}

struct ArticleEnvelope: Decodable { let articles: [Article] }

struct Article: Decodable, Identifiable {
    let id: String
    let title: String
    let summary: String
    let source: String
    let category: String
    let publishedAt: String
    let url: URL
    let imageURL: URL?

    enum CodingKeys: String, CodingKey {
        case id, title, summary, source, category, url
        case publishedAt = "published_at"
        case imageURL = "image_url"
    }
}

struct APIErrorPayload: Decodable { let error: String }

extension String {
    var relativeTime: String {
        guard let date = ISO8601DateFormatter().date(from: self) else { return self }
        return date.formatted(.relative(presentation: .named))
    }
}
