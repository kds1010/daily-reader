import Foundation

struct AgentEnvelope: Decodable {
    let repositories: [Repository]
    let jobs: [AgentJob]
    let archivedJobs: [AgentJob]

    enum CodingKeys: String, CodingKey {
        case repositories, jobs
        case archivedJobs = "archived_jobs"
    }
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

struct Repository: Codable, Identifiable, Hashable {
    var id: String { name }
    let name: String
    let label: String
}

struct AgentJob: Decodable, Identifiable {
    let id: String
    let repository: String
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
        case updatedAt = "updated_at"
        case recentEvents = "recent_events"
        case followUp = "follow_up"
    }
}

struct AgentEvent: Decodable, Identifiable {
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

    enum CodingKeys: String, CodingKey {
        case sender, subject, importance, reason
        case threadID = "thread_id"
        case requiredAction = "required_action"
        case dueDate = "due_date"
        case gmailURL = "gmail_url"
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
