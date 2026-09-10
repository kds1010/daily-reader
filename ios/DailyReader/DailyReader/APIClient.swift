import Foundation

actor APIClient {
    static let shared = APIClient()

    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        return decoder
    }()

    private let session: URLSession
    private let serverURL: URL?

    init(session: URLSession = .shared, serverURL: URL? = nil) {
        self.session = session
        self.serverURL = serverURL
    }

    private var baseURL: URL {
        if let serverURL { return serverURL }
        let stored = UserDefaults.standard.string(forKey: "serverURL")
        return URL(string: stored ?? "https://sk-mins-mac-mini.tailc193b2.ts.net/")!
    }

    func get<T: Decodable>(_ path: String, queryItems: [URLQueryItem] = [], as type: T.Type = T.self) async throws -> T {
        let url = makeAPIURL(baseURL: baseURL, path: path, queryItems: queryItems)
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 15
        return try await execute(request, as: type)
    }

    func post<T: Encodable, R: Decodable>(_ path: String, body: T, as type: R.Type, timeout: TimeInterval = 20, preservingConflict: Bool = false) async throws -> R {
        let url = makeAPIURL(baseURL: baseURL, path: path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        request.timeoutInterval = timeout
        return try await execute(request, as: type, preservingConflict: preservingConflict)
    }

    func syncHealth(_ snapshot: HealthSnapshot, token: String) async throws {
        let url = baseURL.appending(path: "api/health/sync")
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        request.httpBody = try JSONEncoder().encode(snapshot)
        request.timeoutInterval = 20
        let _: EmptyResponse = try await execute(request, as: EmptyResponse.self)
    }

    func uploadConversationFile(
        _ fileURL: URL, recordedAt: Date?
    ) async throws -> ConversationRecording {
        let allowed = fileURL.startAccessingSecurityScopedResource()
        defer { if allowed { fileURL.stopAccessingSecurityScopedResource() } }
        let values = try fileURL.resourceValues(forKeys: [.fileSizeKey])
        guard let size = values.fileSize else { throw APIClientError.invalidResponse }
        let fileExtension = fileURL.pathExtension.lowercased()
        guard fileExtension == "mp3" || fileExtension == "txt" else {
            throw APIClientError.server("MP3またはUTF-8のTXTを選択してください")
        }
        let url = makeAPIURL(
            baseURL: baseURL,
            path: "api/conversations/upload",
            queryItems: [URLQueryItem(name: "filename", value: fileURL.lastPathComponent), URLQueryItem(name: "recorded_at", value: recordedAt?.ISO8601Format())]
        )
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue(
            fileExtension == "txt" ? "text/plain; charset=utf-8" : "audio/mpeg",
            forHTTPHeaderField: "Content-Type"
        )
        request.setValue(String(size), forHTTPHeaderField: "Content-Length")
        request.timeoutInterval = 3600
        let (data, response) = try await session.upload(for: request, fromFile: fileURL)
        try Task.checkCancellation()
        guard let http = response as? HTTPURLResponse else { throw APIClientError.invalidResponse }
        guard 200..<300 ~= http.statusCode else {
            let message = (try? decoder.decode(APIErrorPayload.self, from: data).error) ?? "HTTP \(http.statusCode)"
            throw APIClientError.server(message)
        }
        return try decoder.decode(ConversationRecording.self, from: data)
    }

    func syncLocations(_ events: [LocationEvent]) async throws {
        let _: LocationSyncResponse = try await post("api/locations/sync", body: LocationSyncRequest(events: events), as: LocationSyncResponse.self)
    }

    func importPayPayCSV(_ fileURL: URL) async throws -> PaymentImportResult {
        let allowed = fileURL.startAccessingSecurityScopedResource()
        defer { if allowed { fileURL.stopAccessingSecurityScopedResource() } }
        guard fileURL.pathExtension.lowercased() == "csv" else {
            throw APIClientError.server("PayPayのCSVファイルを選択してください")
        }
        // Read a bounded snapshot. Neither filenames nor financial fields enter the URL.
        let file = try FileHandle(forReadingFrom: fileURL)
        defer { try? file.close() }
        let maximum = 10 * 1024 * 1024
        let content = try file.read(upToCount: maximum + 1) ?? Data()
        guard !content.isEmpty && content.count <= maximum else {
            throw APIClientError.server("CSVは空でない10 MiB以下のファイルにしてください")
        }
        var request = URLRequest(url: makeAPIURL(baseURL: baseURL, path: "api/payments/import"))
        request.httpMethod = "POST"
        request.setValue("text/csv; charset=utf-8", forHTTPHeaderField: "Content-Type")
        request.httpBody = content
        request.timeoutInterval = 60
        return try await execute(request, as: PaymentImportResult.self)
    }

    private func execute<T: Decodable>(_ request: URLRequest, as type: T.Type, preservingConflict: Bool = false) async throws -> T {
        let (data, response) = try await session.data(for: request)
        try Task.checkCancellation()
        guard let http = response as? HTTPURLResponse else { throw APIClientError.invalidResponse }
        guard 200..<300 ~= http.statusCode else {
            let message = (try? decoder.decode(APIErrorPayload.self, from: data).error) ?? "HTTP \(http.statusCode)"
            if preservingConflict && http.statusCode == 409 {
                switch (try? decoder.decode(APIErrorPayload.self, from: data))?.code {
                case "duplicate_term": throw APIClientError.vocabularyDuplicate
                case "recording_busy": throw APIClientError.recordingBusy
                default: throw APIClientError.conflict
                }
            }
            throw APIClientError.server(message)
        }
        return try decoder.decode(type, from: data)
    }
}

// Explicitly include the UTC designator: ISO8601FormatStyle's initializer
// alone emits a timezone-less string even when its timeZone is GMT.
func preciseUTCTimestamp(_ date: Date) -> String {
    date.ISO8601Format(.iso8601(timeZone: .gmt, includingFractionalSeconds: true).timeZone(separator: .colon))
}

func parseISOTimestamp(_ value: String) -> Date? {
    (try? Date(value, strategy: .iso8601.year().month().day().time(includingFractionalSeconds: true).timeZone(separator: .colon)))
    ?? (try? Date(value, strategy: .iso8601))
}

func makePhoneMotionInterval(start: Date, end: Date, activity: String, confidence: String) -> PhoneMotionInterval? {
    let a = preciseUTCTimestamp(start), b = preciseUTCTimestamp(end)
    guard let parsedStart = parseISOTimestamp(a), let parsedEnd = parseISOTimestamp(b), parsedStart < parsedEnd else { return nil }
    return PhoneMotionInterval(start_at: a, end_at: b, activity: activity, confidence: confidence)
}

// Old clients rounded subsecond motion transitions to the same second. Those
// intervals have no recoverable duration; a fresh Core Motion query follows.
func repairLegacyQueuedMotionIntervals(_ values: [PhoneMotionInterval]) -> [PhoneMotionInterval] {
    values.filter { !($0.start_at == $0.end_at && parseISOTimestamp($0.start_at) != nil) }
}

// Only migrate our private queues, which older clients explicitly formatted
// in GMT. Never infer a timezone for arbitrary user-provided timestamps.
func repairLegacyQueuedUTCTimestamp(_ value: String) -> String {
    guard value.range(of: #"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}$"#, options: .regularExpression) != nil,
          (try? Date(value + "Z", strategy: .iso8601.year().month().day().time(includingFractionalSeconds: true).timeZone(separator: .colon))) != nil
    else { return value }
    return value + "Z"
}

func makeAPIURL(baseURL: URL, path: String, queryItems: [URLQueryItem] = []) -> URL {
    let url = baseURL.appending(path: path)
    guard !queryItems.isEmpty else { return url }
    return url.appending(queryItems: queryItems)
}

struct EmptyResponse: Decodable {}
struct EmptyRequest: Encodable {}
struct LocationEvent: Codable, Identifiable, Equatable, Sendable {
    var timestamp: String
    let latitude: Double
    let longitude: Double
    let horizontal_accuracy: Double
    let is_approximate: Bool
    var speed_mps: Double? = nil
    var speed_accuracy_mps: Double? = nil
    var is_simulated: Bool? = nil
    var id: String { "\(timestamp)|\(latitude)|\(longitude)" }
    var date: Date? {
        (try? Date(timestamp, strategy: .iso8601.year().month().day().time(includingFractionalSeconds: true).timeZone(separator: .colon)))
        ?? (try? Date(timestamp, strategy: .iso8601))
    }
}
struct LocationHistoryResponse: Decodable {
    let items: [LocationEvent]
    let total: Int
    let has_more: Bool
}
struct LocationSyncRequest: Encodable { let events: [LocationEvent] }
struct LocationSyncResponse: Decodable { let stored: Int }
enum APIClientError: LocalizedError {
    case conflict
    case vocabularyDuplicate
    case recordingBusy
    case invalidResponse
    case server(String)
    var errorDescription: String? {
        switch self {
        case .vocabularyDuplicate: "この表記は用語辞書に登録済みです。発言訂正では用語登録のチェックを外して保存し、用語の変更は辞書から編集してください。"
        case .recordingBusy: "この録音は処理中です。入力は保持しています。処理が完了してから保存を再試行してください。"
        case .conflict: "ほかの操作で内容が更新されています。入力は保持しています。最新の内容を確認してから編集し直してください。"; case .invalidResponse: "サーバーの応答を確認できませんでした"; case .server(let message): message }
    }
}

struct NewAgentJob: Encodable {
    let repository: String
    let prompt: String
    let model: String
    let reasoningEffort: String
    let mode = "execute"

    enum CodingKeys: String, CodingKey {
        case repository, prompt, model, mode
        case reasoningEffort = "reasoning_effort"
    }
}
struct NewTanomiTask: Encodable {
    let prompt: String
    let repo: String
    let model: String
    let permissionMode: String
    let effort: String?
    enum CodingKeys: String, CodingKey { case prompt, repo, model, effort; case permissionMode = "permission_mode" }
}
struct TanomiFollowUp: Encodable {
    let prompt: String
    let parentID: String
    enum CodingKeys: String, CodingKey { case prompt; case parentID = "parent_id" }
}
struct NewTask: Encodable { let title: String; let dueDate: String?; let priority: Int; let recurrence: String
    enum CodingKeys: String, CodingKey { case title, priority, recurrence; case dueDate = "due_date" }
}
struct TaskAction: Encodable { let taskID: String
    enum CodingKeys: String, CodingKey { case taskID = "task_id" }
}
struct TaskStatus: Encodable { let taskID: String; let completed: Bool
    enum CodingKeys: String, CodingKey { case completed; case taskID = "task_id" }
}
struct AgentInstruction: Encodable { let jobID: String; let instruction: String
    enum CodingKeys: String, CodingKey { case instruction; case jobID = "job_id" }
}
struct AgentJobAction: Encodable { let jobID: String
    enum CodingKeys: String, CodingKey { case jobID = "job_id" }
}
struct EmailAction: Encodable { let threadID: String; let action: String
    enum CodingKeys: String, CodingKey { case action; case threadID = "thread_id" }
}
struct ArticleInteraction: Encodable { let articleID: String; let surface: String
    enum CodingKeys: String, CodingKey { case surface; case articleID = "article_id" }
}
