import Foundation

struct PaymentRecord: Decodable, Identifiable {
    let id: String
    let source: String
    let transaction_id: String?
    let occurred_at: String
    let outgoing_yen: Int64?
    let incoming_yen: Int64?
    let kind: String
    let counterparty: String
    let payment_method: String
    let row_number: Int
    let details: [String: String]
}

struct PaymentHistoryResponse: Decodable {
    let items: [PaymentRecord]
    let total: Int
    let offset: Int
    let has_more: Bool
    let last_imported_at: String?
    let missing_ids: Int
}

struct PaymentImportResult: Decodable {
    let total: Int
    let added: Int
    let duplicates: Int
    let conflicts: Int
    let missing_ids: Int
    let conflict_rows: [Int]
    let imported_at: String
    let file_duplicate: Bool
}

enum ResourceLoadState: Equatable {
    case idle
    case loading
    case loaded
    case failed(String)

    var isLoading: Bool {
        if case .loading = self { return true }
        return false
    }

    var errorMessage: String? {
        if case .failed(let message) = self { return message }
        return nil
    }

}

enum MacVimKeyStroke: Equatable {
    case character(Character)
    case shiftedG
    case controlD
    case controlU
    case enter
    case escape
}

enum MacTaskArchiveDirection: Equatable {
    case next
    case previous
}

enum MacAgentNavigationCommand: Equatable {
    case move(Int)
    case first
    case last
    case open
    case close
    case page(Int)
    case alignTop
    case alignCenter
    case alignBottom
    case archive(MacTaskArchiveDirection)
}

struct MacAgentNavigationInvocation: Equatable {
    let serial: Int
    let command: MacAgentNavigationCommand
}

struct MacVimKeyParser {
    private var pendingPrefix: Character?
    private var pendingAt: TimeInterval = 0
    private let sequenceTimeout: TimeInterval

    init(sequenceTimeout: TimeInterval = 1) {
        self.sequenceTimeout = sequenceTimeout
    }

    mutating func handle(_ stroke: MacVimKeyStroke, at timestamp: TimeInterval) -> MacAgentNavigationCommand? {
        if pendingPrefix != nil && timestamp - pendingAt > sequenceTimeout {
            pendingPrefix = nil
        }

        if let prefix = pendingPrefix {
            pendingPrefix = nil
            if let command = sequenceCommand(prefix: prefix, stroke: stroke) {
                return command
            }
        }

        switch stroke {
        case .character(let character) where character == "d" || character == "g" || character == "z":
            pendingPrefix = character
            pendingAt = timestamp
            return nil
        case .character("j"): return .move(1)
        case .character("k"): return .move(-1)
        case .character("h"), .escape: return .close
        case .character("l"), .enter: return .open
        case .shiftedG: return .last
        case .controlD: return .page(1)
        case .controlU: return .page(-1)
        default: return nil
        }
    }

    private func sequenceCommand(prefix: Character, stroke: MacVimKeyStroke) -> MacAgentNavigationCommand? {
        switch (prefix, stroke) {
        case ("g", .character("g")): .first
        case ("z", .character("t")): .alignTop
        case ("z", .character("z")): .alignCenter
        case ("z", .character("b")): .alignBottom
        case ("d", .character("d")), ("d", .character("j")): .archive(.next)
        case ("d", .character("k")): .archive(.previous)
        default: nil
        }
    }
}

private enum DateParsing {
    static let iso8601 = ISO8601DateFormatter()
    static let iso8601WithFractionalSeconds: ISO8601DateFormatter = {
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        return formatter
    }()
}

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

struct AgentNotificationEnvelope: Decodable {
    let jobs: [AgentJob]
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
        slug: "gpt-6-astra",
        displayName: "GPT-6-Astra",
        defaultReasoningEffort: "low",
        supportedReasoningEfforts: ["low", "medium", "high", "xhigh", "max", "ultra"]
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

struct TanomiConfig: Decodable, Equatable {
    let models: [String]
    let defaultModel: String
    let efforts: [String]
    let defaultEffort: String?
    let permissionModes: [String]
    enum CodingKeys: String, CodingKey {
        case models, efforts
        case defaultModel = "default_model"
        case defaultEffort = "default_effort"
        case permissionModes = "permission_modes"
    }
}

struct TanomiBuckets: Decodable {
    let tasks: [TanomiTask]
    let archived: [TanomiTask]
    let deleted: [TanomiTask]
}

struct TanomiHealth: Decodable {
    let ok: Bool
    let running: Int?
}

struct TanomiUsage: Decodable, Equatable {
    let limits: [String: TanomiUsageLimit]
    let running: Int
    let stale: Bool?
}

struct TanomiUsageLimit: Decodable, Equatable {
    let utilization: Double
    let resetsAt: String?

    enum CodingKeys: String, CodingKey {
        case utilization
        case resetsAt = "resets_at"
    }
}

struct TanomiTask: Decodable, Identifiable, Equatable {
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
    let createdAt: TimeInterval?
    let startedAt: TimeInterval?
    let endedAt: TimeInterval?
    let sessionID: String?

    enum CodingKeys: String, CodingKey {
        case id, title, prompt, cwd, status, result, error, model, sessionID = "session_id"
        case repoPath = "repo_path"
        case permissionMode = "permission_mode"
        case createdAt = "created_at"
        case startedAt = "started_at"
        case endedAt = "ended_at"
    }

    var displayTitle: String { title ?? prompt ?? id }
    var displayRepository: String {
        let value = repoPath ?? cwd ?? "tanomi"
        let name = URL(fileURLWithPath: value).lastPathComponent
        return name.isEmpty ? value : name
    }
    var displayResult: String { result ?? error ?? "" }
    var canContinue: Bool { sessionID?.isEmpty == false && !["queued", "running"].contains(status) }
    var updatedDate: Date? {
        [endedAt, startedAt, createdAt]
            .compactMap { $0 }
            .first
            .map(Date.init(timeIntervalSince1970:))
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

struct DeploymentInfo: Decodable {
    let version: String
    let deployedAt: String
    let iOSReleaseVersion: String?
    let macOSReleaseVersion: String?

    enum CodingKeys: String, CodingKey {
        case version
        case deployedAt = "deployed_at"
        case iOSReleaseVersion = "ios_release_version"
        case macOSReleaseVersion = "macos_release_version"
    }

    var nativeReleaseVersion: String? {
#if os(iOS)
        iOSReleaseVersion
#else
        macOSReleaseVersion
#endif
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
    let model: String?
    let reasoningEffort: String?
    let updatedAt: String
    let recentEvents: [AgentEvent]?
    let events: [AgentEvent]?
    let mode: String?
    let followUp: Int?
    let worktree: String?

    enum CodingKeys: String, CodingKey {
        case id, repository, prompt, status, phase, summary, model, events, mode, worktree
        case repositoryLabel = "repository_label"
        case reasoningEffort = "reasoning_effort"
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
    var tasks: [PlannerTask]
    var routines: [PlannerTask]
    var health: HealthSnapshot?
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

struct ConversationEnvelope: Decodable {
    let recordings: [ConversationRecording]
    let llmAvailable: Bool?
    enum CodingKeys: String, CodingKey {
        case recordings
        case llmAvailable = "llm_available"
    }
}

struct ConversationItemsEnvelope: Decodable { let items: [ConversationInsightItem] }

struct SoundcoreImportJob: Decodable, Identifiable, Equatable, Sendable {
    let id: String
    let status: String
    let recordingID: String?
    let error: String?
    var createdAt: String? = nil
    var updatedAt: String? = nil
    var isPending: Bool { ["queued", "downloading", "saved"].contains(status) }
    var statusLabel: String {
        switch status {
        case "queued": "Mac miniで取得待ち"
        case "downloading": "Soundcoreクラウドから取得中"
        case "saved": "音声を保存済み・解析の受付待ち"
        case "completed": "音声の取り込み済み"
        case "failed": "取得に失敗"
        default: "取得状況を確認してください"
        }
    }
    enum CodingKeys: String, CodingKey {
        case id, status, error
        case recordingID = "recording_id", createdAt = "created_at", updatedAt = "updated_at"
    }
}

struct SoundcoreImportsEnvelope: Decodable { let items: [SoundcoreImportJob] }
struct SoundcoreImportRequest: Encodable { let url: String }

enum SoundcoreLinkError: LocalizedError {
    case unsupported
    var errorDescription: String? {
        "対応するSoundcore共有リンク（https://speaker-eu.eufylife.com/knowledge/sharelink/…）を入力してください。ほかの地域・形式には未対応です。"
    }
}

// This exact share-link format has been verified against Soundcore. The Mac
// validates it again; clients never follow the cloud URL themselves.
func normalizedSoundcoreShareURL(_ value: String) throws -> String {
    let trimmed = value.trimmingCharacters(in: .whitespacesAndNewlines)
    guard trimmed.count <= 2048,
          trimmed.range(of: #"^https://speaker-eu\.eufylife\.com/"#, options: [.regularExpression, .caseInsensitive]) != nil,
          var parts = URLComponents(string: trimmed),
          parts.scheme?.lowercased() == "https",
          parts.host?.lowercased() == "speaker-eu.eufylife.com",
          parts.user == nil, parts.password == nil, parts.port == nil,
          parts.percentEncodedPath.range(of: #"^/knowledge/sharelink/[A-Za-z0-9]{6,64}$"#, options: .regularExpression) != nil,
          (parts.queryItems ?? []).allSatisfy({ $0.name == "language" })
    else { throw SoundcoreLinkError.unsupported }
    parts.scheme = "https"
    parts.host = "speaker-eu.eufylife.com"
    parts.query = nil
    parts.fragment = nil
    guard let normalized = parts.url?.absoluteString else { throw SoundcoreLinkError.unsupported }
    return normalized
}

struct ConversationTranscriptionMetadata: Decodable {
    let model: String?
    let warnings: [String]?
}

struct ConversationRecording: Decodable, Identifiable {
    let id: String
    let filename: String
    let byteSize: Int
    let status: String
    let error: String?
    let createdAt: String
    let analyzedAt: String?
    let sourceType: String?
    let speakers: [ConversationSpeaker]?
    let utterances: [ConversationUtterance]?
    let topics: [ConversationTopic]?
    let taskProposals: [ConversationTaskProposal]?
    let insightItems: [ConversationInsightItem]?
    let insightStatus: String?
    let insightError: String?
    let insightAnalyzedAt: String?
    let insightItemCount: Int?
    let recordedAt: String?
    let locationLatitude: Double?
    let locationLongitude: Double?
    let locationAccuracy: Double?
    let locationTimestamp: String?
    let locationTimeDelta: Double?
    var transcriptionMetadata: ConversationTranscriptionMetadata? = nil
    var transcriptionNeedsReview: Int? = nil
    var locationContexts: [ConversationLocationContext]? = nil
    var isTranscript: Bool { sourceType == "transcript" }
    enum CodingKeys: String, CodingKey {
        case id, filename, status, error, speakers, utterances, topics
        case byteSize = "byte_size"; case createdAt = "created_at"; case analyzedAt = "analyzed_at"
        case sourceType = "source_type"
        case taskProposals = "task_proposals"
        case insightItems = "insight_items"
        case insightStatus = "insight_status"
        case insightError = "insight_error"
        case insightAnalyzedAt = "insight_analyzed_at"
        case insightItemCount = "insight_item_count"
        case recordedAt = "recorded_at", locationLatitude = "location_latitude", locationLongitude = "location_longitude", locationAccuracy = "location_accuracy", locationTimestamp = "location_timestamp", locationTimeDelta = "location_time_delta"
        case transcriptionMetadata = "transcription_metadata"
        case transcriptionNeedsReview = "transcription_needs_review"
        case locationContexts = "location_contexts"
    }
}

struct ConversationLocationContext: Decodable, Identifiable {
    var deviceContext: PhoneContextEvidence? = nil
    let subjectID: String
    let utteranceID: String?
    let locationEventID: String?
    let targetTimestamp: String?
    let timeBasis: String
    let dateSource: String
    let state: String
    let timeDeltaSeconds: Double?
    let methodVersion: String
    let location: ConversationContextLocation?
    var id: String { subjectID }
    enum CodingKeys: String, CodingKey {
        case state, location
        case deviceContext = "device_context"
        case subjectID = "subject_id", utteranceID = "utterance_id"
        case locationEventID = "location_event_id", targetTimestamp = "target_timestamp"
        case timeBasis = "time_basis", dateSource = "date_source"
        case timeDeltaSeconds = "time_delta_seconds", methodVersion = "method_version"
    }
}

struct ConversationContextLocation: Decodable {
    var speedMPS: Double? = nil
    var speedAccuracyMPS: Double? = nil
    let timestamp: String
    let latitude: Double
    let longitude: Double
    let horizontalAccuracy: Double
    let isApproximate: Bool
    enum CodingKeys: String, CodingKey {
        case timestamp, latitude, longitude
        case speedMPS = "speed_mps", speedAccuracyMPS = "speed_accuracy_mps"
        case horizontalAccuracy = "horizontal_accuracy", isApproximate = "is_approximate"
    }
}

struct ConversationSpeaker: Decodable, Identifiable {
    let id: String; let label: String; let displayName: String?
    enum CodingKeys: String, CodingKey { case id, label; case displayName = "display_name" }
}

struct ConversationUtterance: Decodable, Identifiable {
    let id: String; let speaker: String?; let startSeconds: Double; let endSeconds: Double
    let text: String; let confidence: Double?; let context: String; let topic: String
    enum CodingKeys: String, CodingKey {
        case id, speaker, text, confidence, context, topic
        case startSeconds = "start_seconds"; case endSeconds = "end_seconds"
    }
}

struct ConversationTopic: Decodable, Identifiable {
    let id: String; let name: String; let context: String; let summary: String
}

struct ConversationTaskProposal: Decodable, Identifiable {
    let id: String; let title: String; let instruction: String; let status: String
}

struct ConversationTaskApproval: Encodable {
    let target: String; let instruction: String; let repository: String?
}

struct ConversationInsightItem: Decodable, Identifiable {
    let id: String
    let recordingID: String
    let kind: String
    let title: String
    let detail: String
    let assignee: String?
    let dueDate: String?
    let dueDateOriginal: String?
    let certainty: String
    let status: String
    let source: String
    let recordingFilename: String?
    let recordedAt: String?
    let recordingSourceType: String?
    let evidence: [ConversationInsightEvidence]

    var isActionable: Bool { kind == "task" || kind == "follow_up" }

    enum CodingKeys: String, CodingKey {
        case id, kind, title, detail, assignee, certainty, status, source, evidence
        case recordingID = "recording_id"
        case dueDate = "due_date"
        case dueDateOriginal = "due_date_original"
        case recordingFilename = "recording_filename"
        case recordedAt = "recorded_at"
        case recordingSourceType = "recording_source_type"
    }
}

struct ConversationInsightEvidence: Decodable {
    let position: Int
    let utteranceID: String?
    let quote: String
    let speaker: String?
    let startSeconds: Double?
    let endSeconds: Double?

    enum CodingKeys: String, CodingKey {
        case position, quote, speaker
        case utteranceID = "utterance_id"
        case startSeconds = "start_seconds"
        case endSeconds = "end_seconds"
    }
}

struct ConversationItemReviewRequest: Encodable {
    let action: String
    let title: String
    let detail: String
    let assignee: String
    let dueDate: String

    enum CodingKeys: String, CodingKey {
        case action, title, detail, assignee
        case dueDate = "due_date"
    }
}

struct ConversationItemDispatchRequest: Encodable {
    let target: String
    let title: String
    let detail: String
    let assignee: String
    let dueDate: String
    let repository: String?

    enum CodingKeys: String, CodingKey {
        case target, title, detail, assignee, repository
        case dueDate = "due_date"
    }
}

struct ConversationExtractionResponse: Decodable { let queued: Bool }

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
    let status: String?
    let receivedAt: String?

    enum CodingKeys: String, CodingKey {
        case sender, subject, importance, reason
        case threadID = "thread_id"
        case requiredAction = "required_action"
        case dueDate = "due_date"
        case status
        case receivedAt = "received_at"
    }
}

struct EmailThreadContent: Decodable {
    let threadID: String
    let subject: String
    let accountEmail: String
    let messages: [EmailMessage]

    enum CodingKeys: String, CodingKey {
        case threadID = "thread_id"
        case subject
        case accountEmail = "account_email"
        case messages
    }
}

struct EmailMessage: Decodable {
    let sender: String
    let receivedAt: String
    let body: String

    enum CodingKeys: String, CodingKey {
        case sender
        case receivedAt = "received_at"
        case body
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
    var iso8601Date: Date? {
        DateParsing.iso8601WithFractionalSeconds.date(from: self)
            ?? DateParsing.iso8601.date(from: self)
    }

    var relativeTime: String {
        guard let date = iso8601Date else { return self }
        return date.formatted(.relative(presentation: .named))
    }

    func emailReceivedDisplay(now: Date = .now) -> String? {
        guard let date = iso8601Date else { return nil }
        let calendar = Calendar.current
        var dateFormat = Date.FormatStyle().month(.defaultDigits).day(.defaultDigits)
        if calendar.component(.year, from: date) != calendar.component(.year, from: now) {
            dateFormat = dateFormat.year()
        }
        dateFormat = dateFormat.hour(.twoDigits(amPM: .omitted)).minute(.twoDigits)
        let receivedText = date.formatted(dateFormat)
        let days = calendar.dateComponents([.day], from: calendar.startOfDay(for: date), to: calendar.startOfDay(for: now)).day ?? 0
        let relative = days == 0 ? "今日" : days > 0 ? "\(days)日前" : "未来"
        return "受信 \(receivedText)（\(relative)）"
    }
}

struct PhoneCalendarEvent: Sendable, Codable, Identifiable {
    let id: String; let title: String; let location: String
    let start_at: String; let end_at: String; let busy: Bool; let all_day: Bool
    let daymeld_entry_id: String
}
struct PhoneMotionInterval: Sendable, Codable, Identifiable {
    let start_at: String; let end_at: String; let activity: String; let confidence: String
    var id: String { start_at + activity }
}
struct PhoneContextEvidence: Decodable {
    let calendar: [PhoneCalendarEvent]; let motion: [PhoneMotionInterval]; let basis: String
}
struct PhoneCollectionState: Codable {
    var state: String; var start_at: String?; var end_at: String?; var updated_at: String?
}
struct PhoneDevice: Decodable, Identifiable {
    let id: String; let captured_at: String; let timezone: String
    let calendar: PhoneCollectionState; let motion: PhoneCollectionState; let calendar_fresh: Bool
}
struct PhoneTaskWindow: Decodable, Identifiable {
    let task_id: String; let title: String; let start_at: String; let end_at: String
    var id: String { task_id }
}
struct PhoneCalendarConflict: Decodable, Identifiable {
    let entry_id: String; let title: String; let calendar_title: String
    var id: String { entry_id + calendar_title }
}
struct PhoneContextOverview: Decodable {
    let devices: [PhoneDevice]; let calendar_ready: Bool; let timezone: String
    let agenda: [PhoneCalendarEvent]; let suggestions: [PhoneTaskWindow]
    let conflicts: [PhoneCalendarConflict]
}

// Combine overlapping devices/stages and clip samples to the requested observation window.
func mergedSleepMinutes(_ intervals: [DateInterval], window: DateInterval) -> Double? {
    let clipped = intervals.compactMap { value -> DateInterval? in
        let start = max(value.start, window.start), end = min(value.end, window.end)
        return end > start ? DateInterval(start: start, end: end) : nil
    }.sorted { $0.start < $1.start }
    guard var current = clipped.first else { return nil }
    var total: TimeInterval = 0
    for next in clipped.dropFirst() {
        if next.start <= current.end {
            current = DateInterval(start: current.start, end: max(current.end, next.end))
        } else { total += current.duration; current = next }
    }
    return (total + current.duration) / 60
}
