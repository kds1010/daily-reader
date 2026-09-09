import Foundation

@MainActor
final class FakeNotifications: ConnectionNotificationDelivery {
    enum Failure: Error { case injected }
    var authorization: ConnectionNotificationPermission = .allowed
    var shouldFail = false
    var suspend = false
    var continuation: CheckedContinuation<Void, Never>?
    var added: [String] = []
    var visible: Set<String> = []
    var removed: Set<String> = []
    var permissionRequests = 0

    func permission() async -> ConnectionNotificationPermission { authorization }
    func requestPermission() async { permissionRequests += 1; authorization = .allowed }
    func add(_ alert: ConnectionAlert) async throws {
        added.append(alert.id)
        if suspend { await withCheckedContinuation { continuation = $0 } }
        if shouldFail { throw Failure.injected }
        visible.insert(alert.id)
    }
    func remove(ids: [String]) {
        removed.formUnion(ids)
        visible.subtract(ids)
    }
    func resume() { suspend = false; continuation?.resume(); continuation = nil }
}

@main struct ConnectionAlertTests {
    @MainActor static func until(_ condition: () -> Bool) async throws {
        for _ in 0..<1000 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(1))
        }
        fatalError("anonymous notification gate timed out")
    }

    @MainActor static func main() async throws {
        let suite = "daymeld.connection-alert-test.\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: suite)!
        defer { defaults.removePersistentDomain(forName: suite) }
        let delivery = FakeNotifications()
        var coordinator = ConnectionAlerts(defaults: defaults, delivery: delivery)
        let first = ConnectionAlert(id: "episode-1", provider: "soundcore", title: "接続の確認", message: "匿名の確認案内", occurredAt: "2026-09-09T00:00:00Z")
        let recurrence = ConnectionAlert(id: "episode-2", provider: "soundcore", title: "接続の確認", message: "匿名の確認案内", occurredAt: "2026-09-09T01:00:00Z")
        let drive = ConnectionAlert(id: "drive-1", provider: "google_drive", title: "共有の確認", message: "匿名の確認案内", occurredAt: "2026-09-09T00:00:00Z")

        // Old BG servers omit the optional field. Absence is not recovery.
        let old = try JSONDecoder().decode(AgentNotificationEnvelope.self, from: Data(#"{"jobs":[]}"#.utf8))
        precondition(old.connectionAlerts == nil)
        let new = try JSONDecoder().decode(AgentNotificationEnvelope.self, from: Data(#"{"jobs":[],"connection_alerts":[{"id":"episode-1","provider":"soundcore","title":"接続の確認","message":"匿名の確認案内","occurred_at":"2026-09-09T00:00:00Z"}]}"#.utf8))
        precondition(new.connectionAlerts == [first])
        let empty = try JSONDecoder().decode(ConnectionAlertEnvelope.self, from: Data(#"{"alerts":[]}"#.utf8))
        precondition(empty.alerts.isEmpty)

        await coordinator.accept([first, first], generation: coordinator.beginSnapshot())
        precondition(delivery.added == [first.id] && coordinator.alerts == [first], "initial auth alert must notify, duplicated rows only once")
        await coordinator.accept([first], generation: coordinator.beginSnapshot())
        precondition(delivery.added.count == 1)
        coordinator = ConnectionAlerts(defaults: defaults, delivery: delivery)
        await coordinator.accept([first], generation: coordinator.beginSnapshot())
        precondition(delivery.added.count == 1, "restart must retain episode acknowledgement")

        await coordinator.accept(nil, generation: coordinator.beginSnapshot())
        coordinator.failed(generation: coordinator.beginSnapshot())
        precondition(coordinator.alerts == [first] && delivery.visible == [first.id])
        let obsolete = coordinator.beginSnapshot()
        await coordinator.accept([], generation: coordinator.beginSnapshot())
        await coordinator.accept([first], generation: obsolete)
        precondition(coordinator.alerts.isEmpty && delivery.visible.isEmpty, "late response cannot undo recovery")
        await coordinator.accept([recurrence], generation: coordinator.beginSnapshot())
        precondition(delivery.added == [first.id, recurrence.id], "recovered provider may notify on new episode")

        // No permission and add failure both remain eligible for later delivery.
        delivery.authorization = .denied
        await coordinator.accept([recurrence, drive], generation: coordinator.beginSnapshot())
        precondition(delivery.added.count == 2)
        delivery.authorization = .notDetermined
        await coordinator.accept([recurrence, drive], generation: coordinator.beginSnapshot())
        precondition(delivery.added.count == 2)
        delivery.shouldFail = true
        await coordinator.requestPermission()
        precondition(delivery.permissionRequests == 1 && delivery.added.last == drive.id && coordinator.deliveryError != nil)
        delivery.shouldFail = false
        await coordinator.accept([recurrence, drive], generation: coordinator.beginSnapshot())
        precondition(delivery.added.filter { $0 == drive.id }.count == 2 && coordinator.deliveryError == nil)

        // A foreground and BG snapshot may overlap while center.add awaits.
        await coordinator.accept([], generation: coordinator.beginSnapshot())
        delivery.suspend = true
        let overlap = Task { await coordinator.accept([first], generation: coordinator.beginSnapshot()) }
        try await until { delivery.continuation != nil }
        let count = delivery.added.count
        await coordinator.accept([first], generation: coordinator.beginSnapshot())
        precondition(delivery.added.count == count)
        delivery.resume()
        await overlap.value
        await coordinator.accept([first], generation: coordinator.beginSnapshot())
        precondition(delivery.added.count == count, "in-flight delivery is acknowledged once even when newer snapshot keeps same episode")

        // Recovery while delivery is awaiting removes its eventual notification.
        await coordinator.accept([], generation: coordinator.beginSnapshot())
        delivery.suspend = true
        let recovered = Task { await coordinator.accept([drive], generation: coordinator.beginSnapshot()) }
        try await until { delivery.continuation != nil }
        await coordinator.accept([], generation: coordinator.beginSnapshot())
        delivery.resume()
        await recovered.value
        precondition(delivery.visible.isEmpty)

        // Cancellation must not acknowledge delivery, including a transport
        // that finishes successfully after cancellation was requested.
        delivery.suspend = true
        let cancelled = Task { await coordinator.accept([drive], generation: coordinator.beginSnapshot()) }
        try await until { delivery.continuation != nil }
        cancelled.cancel()
        delivery.resume()
        await cancelled.value
        precondition(delivery.visible.isEmpty)
        let beforeRetry = delivery.added.count
        await coordinator.accept([drive], generation: coordinator.beginSnapshot())
        precondition(delivery.added.count == beforeRetry + 1 && delivery.visible == [drive.id])

        // Routing persists until a view consumes it, including a cold launch.
        precondition(!coordinator.handleNotification(userInfo: ["agent_job_id": "unrelated"]))
        precondition(coordinator.handleNotification(userInfo: ["connection_alert_id": drive.id]))
        precondition(coordinator.destination?.alertID == drive.id)
        coordinator.destination = nil
        precondition(coordinator.destination == nil)
        precondition(drive.guidance.contains("サービスアカウント"))
        print("Connection alert delivery and snapshot races passed")
    }
}
