import Foundation
import Combine

final class Responses: @unchecked Sendable {
    static let shared = Responses()
    let lock = NSLock()
    var delays: [String: Double] = ["/api/emails/unread": 2, "/api/tanomi/repos": 2, "/api/diary": 2]
    var failures: Set<String> = []
    var counts: [String: Int] = [:]
    var completed: Set<String> = []
    var hidden = false
    var includeEmail = false
    var emailHidden = false
    func locked<T>(_ action: () -> T) -> T { lock.lock(); defer { lock.unlock() }; return action() }
    func reply(_ request: URLRequest) -> (String, Double, Int) {
        locked {
            let path = request.url!.path
            counts[path, default: 0] += 1
            let delay = delays[path] ?? 0.02
            if path == "/api/agent-jobs/hide" { hidden = true; return ("{}", 0, 200) }
            if path == "/api/email-status" { emailHidden = true; return ("{}", 0, 200) }
            if failures.contains(path) { return (#"{"error":"injected failure"}"#, delay, 503) }
            let payload: String
            switch path {
            case "/api/agent-jobs":
                let jobs = hidden ? "[]" : #"[{"id":"job","repository":"daily-reader","prompt":"anonymous","status":"running","phase":"test","updated_at":"2026-09-08T00:00:00Z"}]"#
                payload = #"{"repositories":[],"jobs":\#(jobs),"archived_jobs":[]}"#
            case "/api/tanomi/tasks": payload = #"{"tasks":[{"id":"task","status":"running"}],"archived":[],"deleted":[]}"#
            case "/api/tanomi/repos": payload = "[]"
            case "/api/tanomi/config": payload = #"{"models":["opus"],"default_model":"opus","efforts":["low"],"permission_modes":["acceptEdits"]}"#
            case "/api/tanomi/health": payload = #"{"ok":true}"#
            case "/api/tanomi/usage": payload = #"{"limits":{},"running":0}"#
            case "/api/diary":
                let day = URLComponents(url: request.url!, resolvingAgainstBaseURL: false)!.queryItems!.first { $0.name == "date" }!.value!
                payload = #"{"date":"\#(day)","timezone":"Asia/Tokyo","state":"missing","entry":null,"history":[]}"#
            case "/api/diary/settings": payload = #"{"enabled":true,"since":"2026-09-01T00:00:00Z","timezone":"Asia/Tokyo"}"#
            case "/api/today": payload = #"{"date":"2026-09-08","tasks":[],"routines":[]}"#
            case "/api/emails/unread":
                payload = includeEmail && !emailHidden
                    ? #"{"items":[{"thread_id":"mail","sender":"fixture","subject":"fixture","importance":"high","reason":"fixture","required_action":"review"}]}"#
                    : #"{"items":[]}"#
            case "/api/conversation-items": payload = #"{"items":[]}"#
            case "/data/articles.json": payload = #"{"articles":[]}"#
            case "/api/conversations": payload = #"{"recordings":[]}"#
            case "/api/codex-usage": payload = #"{"rateLimitsByLimitId":{}}"#
            case "/api/deployment": payload = #"{"version":"test","deployed_at":"2026-09-08T00:00:00Z"}"#
            default: payload = "{}"
            }
            return (payload, delay, 200)
        }
    }
}

final class DelayedProtocol: URLProtocol, @unchecked Sendable {
    private let queue = DispatchQueue(label: "daymeld.test.response")
    private var stopped = false
    override class func canInit(with request: URLRequest) -> Bool { true }
    override class func canonicalRequest(for request: URLRequest) -> URLRequest { request }
    override func startLoading() {
        let (payload, delay, status) = Responses.shared.reply(request)
        queue.asyncAfter(deadline: .now() + delay) {
            guard !self.stopped else { return }
            Responses.shared.locked { _ = Responses.shared.completed.insert(self.request.url!.path) }
            self.client?.urlProtocol(self, didReceive: HTTPURLResponse(url: self.request.url!, statusCode: status,
                httpVersion: nil, headerFields: ["Content-Type": "application/json"])!, cacheStoragePolicy: .notAllowed)
            self.client?.urlProtocol(self, didLoad: Data(payload.utf8))
            self.client?.urlProtocolDidFinishLoading(self)
        }
    }
    override func stopLoading() { queue.async { self.stopped = true } }
}

@main struct Runner {
    @MainActor static func until(_ condition: () -> Bool) async throws {
        for _ in 0..<3000 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        fatalError("condition timed out")
    }
    @MainActor static func main() async throws {
        let baseline = CommandLine.arguments.contains("--baseline")
        let configuration = URLSessionConfiguration.ephemeral
        configuration.protocolClasses = [DelayedProtocol.self]
        let client = APIClient(session: URLSession(configuration: configuration), serverURL: URL(string: "https://fixture.invalid")!)
        let model = AppModel(api: client)
        let server = Responses.shared
        let start = Date()
        var firstAgent: Double?
        var agentBeforeMail = false
        var agentBeforeDiary = false
        var tanomiBeforeMetadata = false
        let agentSubscription = model.$agents.sink { values in
            if !values.isEmpty && firstAgent == nil {
                firstAgent = Date().timeIntervalSince(start)
                agentBeforeMail = server.locked { !server.completed.contains("/api/emails/unread") }
                agentBeforeDiary = server.locked { !server.completed.contains("/api/diary") }
            }
        }
        let tanomiSubscription = model.$tanomiTasks.sink { values in
            if !values.isEmpty { tanomiBeforeMetadata = server.locked { !server.completed.contains("/api/tanomi/repos") } }
        }
        await model.refresh()
        precondition(firstAgent != nil && model.agentLoadState == .loaded && model.tanomiLoadState == .loaded)
        agentSubscription.cancel(); tanomiSubscription.cancel()
        if !baseline {
            precondition(agentBeforeDiary, "diary delay blocked initial Agent display")
            #if !BASELINE
            precondition(model.diary.current?.state == "missing" && model.diary.policy?.enabled == true)
            #endif
            precondition(agentBeforeMail, "mail delay blocked initial Agent display")
            precondition(tanomiBeforeMetadata, "metadata delay blocked tanomi tasks")
        }
        server.locked { server.delays = [:]; server.counts = [:] }
        var publications = 0
        let changes = model.objectWillChange.sink { publications += 1 }
        await model.refreshAgents()
        changes.cancel()
        let requests = server.locked { server.counts.values.reduce(0, +) }
        print("first_agent_seconds=\(firstAgent!) poll_requests=\(requests) unchanged_publications=\(publications)")
        if baseline { return }
        precondition(requests == 2 && publications == 0)

        server.locked { server.counts = [:]; server.delays["/api/agent-jobs"] = 0.3; server.delays["/api/tanomi/tasks"] = 0.3 }
        let readers = (0..<10).map { _ in Task { await model.refreshAgents() } }
        for reader in readers { await reader.value }
        precondition(server.locked { server.counts["/api/agent-jobs"] == 1 && server.counts["/api/tanomi/tasks"] == 1 })

        // A POST completing during a read must replace that read with a fresh
        // generation. The old response captured the still-visible job.
        server.locked { server.counts = [:] }
        let staleRead = Task { await model.refreshAgents() }
        try await until { server.locked { server.counts["/api/agent-jobs"] == 1 } }
        await model.hideAgent(jobID: "job")
        await staleRead.value
        precondition(model.agents.isEmpty && server.locked { server.counts["/api/agent-jobs"] == 2 })

        server.locked { server.delays = [:]; server.includeEmail = true }
        await model.refresh()
        let email = model.emails[0]
        server.locked { server.delays["/api/emails/unread"] = 0.3; server.counts = [:] }
        var wasEmpty = false
        var mailReappeared = false
        let mailChanges = model.$emails.sink { values in
            if values.isEmpty { wasEmpty = true }
            else if wasEmpty { mailReappeared = true }
        }
        let oldMailRead = Task { await model.refresh() }
        try await until { server.locked { server.counts["/api/emails/unread"] == 1 } }
        await model.act(on: email, action: "done")
        await oldMailRead.value
        mailChanges.cancel()
        precondition(model.emails.isEmpty && !mailReappeared)

        server.locked { server.delays = [:]; server.failures = ["/api/tanomi/config"] }
        await model.refresh()
        precondition(model.tanomiLoadState == .loaded && model.tanomiTasks.count == 1)
        precondition(model.tanomiConfig.models == ["opus"])

        server.locked { server.delays["/api/emails/unread"] = 2; server.counts = [:] }
        let cancelled = Task { await model.refresh() }
        try await until { server.locked { server.counts["/api/emails/unread"] == 1 } }
        cancelled.cancel()
        await cancelled.value
        precondition(!model.isRefreshing)
        server.locked { server.delays = [:] }
        await model.refresh()
        precondition(model.emailLoadState == .loaded && !model.isRefreshing)
        #if !BASELINE
        // Even a transport that ignores cancellation cannot publish an obsolete
        // generation. No scheduler timing assumption is needed for this check.
        let gate = ResourceRefreshes()
        var releaseOld: CheckedContinuation<Void, Never>?
        var observed = 0
        let old = Task {
            await gate.run("resource") { generation in
                await withCheckedContinuation { releaseOld = $0 }
                if gate.isCurrent("resource", generation) { observed = 1 }
            }
        }
        try await until { releaseOld != nil }
        await gate.run("resource", replacing: true) { generation in
            if gate.isCurrent("resource", generation) { observed = 2 }
        }
        releaseOld?.resume()
        await old.value
        precondition(observed == 2)

        // A slow tanomi read must not prevent a second Daymeld polling cycle.
        server.locked {
            server.counts = [:]
            server.delays["/api/tanomi/tasks"] = 0.5
        }
        let slowTanomi = Task { await model.pollTanomiTasks() }
        try await until { server.locked { server.counts["/api/tanomi/tasks"] == 1 } }
        await model.pollDaymeldAgents()
        await model.pollDaymeldAgents()
        precondition(server.locked { server.counts["/api/agent-jobs"] == 2 })
        await slowTanomi.value
        #endif
        print("AppModel delay, unchanged polls, coalescing, archive race, partial failure and cancellation passed")
    }
}
