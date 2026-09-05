import Foundation

actor APIClient {
    static let shared = APIClient()

    private let decoder: JSONDecoder = {
        let decoder = JSONDecoder()
        return decoder
    }()

    private var baseURL: URL {
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

    func post<T: Encodable, R: Decodable>(_ path: String, body: T, as type: R.Type, timeout: TimeInterval = 20) async throws -> R {
        let url = makeAPIURL(baseURL: baseURL, path: path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        request.timeoutInterval = timeout
        return try await execute(request, as: type)
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
        let (data, response) = try await URLSession.shared.upload(for: request, fromFile: fileURL)
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

    private func execute<T: Decodable>(_ request: URLRequest, as type: T.Type) async throws -> T {
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse else { throw APIClientError.invalidResponse }
        guard 200..<300 ~= http.statusCode else {
            let message = (try? decoder.decode(APIErrorPayload.self, from: data).error) ?? "HTTP \(http.statusCode)"
            throw APIClientError.server(message)
        }
        return try decoder.decode(type, from: data)
    }
}

func makeAPIURL(baseURL: URL, path: String, queryItems: [URLQueryItem] = []) -> URL {
    let url = baseURL.appending(path: path)
    guard !queryItems.isEmpty else { return url }
    return url.appending(queryItems: queryItems)
}

struct EmptyResponse: Decodable {}
struct EmptyRequest: Encodable {}
struct LocationEvent: Codable { let timestamp: String; let latitude: Double; let longitude: Double; let horizontal_accuracy: Double; let is_approximate: Bool }
struct LocationSyncRequest: Encodable { let events: [LocationEvent] }
struct LocationSyncResponse: Decodable { let stored: Int }
enum APIClientError: LocalizedError {
    case invalidResponse
    case server(String)
    var errorDescription: String? {
        switch self { case .invalidResponse: "サーバーの応答を確認できませんでした"; case .server(let message): message }
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
