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

    func get<T: Decodable>(_ path: String, as type: T.Type = T.self) async throws -> T {
        let url = baseURL.appending(path: path)
        var request = URLRequest(url: url)
        request.cachePolicy = .reloadIgnoringLocalCacheData
        request.timeoutInterval = 15
        return try await execute(request, as: type)
    }

    func post<T: Encodable, R: Decodable>(_ path: String, body: T, as type: R.Type) async throws -> R {
        let url = baseURL.appending(path: path)
        var request = URLRequest(url: url)
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONEncoder().encode(body)
        request.timeoutInterval = 20
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

struct EmptyResponse: Decodable {}
struct EmptyRequest: Encodable {}
enum APIClientError: LocalizedError {
    case invalidResponse
    case server(String)
    var errorDescription: String? {
        switch self { case .invalidResponse: "サーバーの応答を確認できませんでした"; case .server(let message): message }
    }
}

struct NewAgentJob: Encodable { let repository: String; let prompt: String; let mode = "execute" }
struct NewTanomiTask: Encodable {
    let prompt: String
    let repo: String
    let model: String
    let permissionMode: String
    enum CodingKeys: String, CodingKey { case prompt, repo, model; case permissionMode = "permission_mode" }
}
struct NewTask: Encodable { let title: String; let dueDate: String?; let priority: Int; let recurrence: String
    enum CodingKeys: String, CodingKey { case title, priority, recurrence; case dueDate = "due_date" }
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
