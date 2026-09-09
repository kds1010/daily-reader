import Foundation
import Combine
import UserNotifications

enum ConnectionNotificationPermission: Equatable {
    case unknown, notDetermined, denied, allowed
}

@MainActor
protocol ConnectionNotificationDelivery {
    func permission() async -> ConnectionNotificationPermission
    func requestPermission() async
    func add(_ alert: ConnectionAlert) async throws
    func remove(ids: [String])
}

@MainActor
private final class SystemConnectionNotificationDelivery: ConnectionNotificationDelivery {
    private let center = UNUserNotificationCenter.current()

    func permission() async -> ConnectionNotificationPermission {
        switch await center.notificationSettings().authorizationStatus {
        case .authorized, .provisional, .ephemeral: return .allowed
        case .denied: return .denied
        case .notDetermined: return .notDetermined
        @unknown default: return .unknown
        }
    }

    func requestPermission() async {
        _ = try? await center.requestAuthorization(options: [.alert, .badge, .sound])
    }

    func add(_ alert: ConnectionAlert) async throws {
        let content = UNMutableNotificationContent()
        content.title = String(alert.title.prefix(100))
        content.body = String(alert.message.prefix(240))
        content.sound = .default
        content.userInfo = ["connection_alert_id": alert.id]
        try await center.add(UNNotificationRequest(
            identifier: Self.identifier(alert.id), content: content, trigger: nil))
    }

    func remove(ids: [String]) {
        let identifiers = ids.map(Self.identifier)
        center.removePendingNotificationRequests(withIdentifiers: identifiers)
        center.removeDeliveredNotifications(withIdentifiers: identifiers)
    }

    private static func identifier(_ id: String) -> String { "connection-\(id)" }
}

// One instance owns foreground and BGAppRefresh snapshots and delivery. Episode
// IDs come from the server; polling timestamps are never deduplication keys.
@MainActor
final class ConnectionAlerts: ObservableObject {
    static let shared = ConnectionAlerts()
    private static let storageKey = "connection-notified-episodes-v1"

    struct Destination: Identifiable {
        let id = UUID()
        let alertID: String?
    }

    @Published private(set) var alerts: [ConnectionAlert] = []
    @Published private(set) var hasLoaded = false
    @Published private(set) var loadError: String?
    @Published private(set) var deliveryError: String?
    @Published private(set) var notificationPermission: ConnectionNotificationPermission = .unknown
    @Published var destination: Destination?

    private let defaults: UserDefaults
    private let delivery: any ConnectionNotificationDelivery
    private var notified: Set<String>
    private var inFlight: Set<String> = []
    private var generation = 0

    init(defaults: UserDefaults = .standard, delivery: (any ConnectionNotificationDelivery)? = nil) {
        self.defaults = defaults
        self.delivery = delivery ?? SystemConnectionNotificationDelivery()
        notified = Set(defaults.stringArray(forKey: Self.storageKey) ?? [])
    }

    func open(alertID: String? = nil) { destination = Destination(alertID: alertID) }

    @discardableResult
    func handleNotification(userInfo: [AnyHashable: Any]) -> Bool {
        guard let id = userInfo["connection_alert_id"] as? String, !id.isEmpty else { return false }
        // Retained even when the notification launches the app before RootView.
        open(alertID: id)
        return true
    }

    func beginSnapshot() -> Int {
        generation += 1
        return generation
    }

    func refresh(api: APIClient = .shared) async {
        guard !Task.isCancelled else { return }
        let request = beginSnapshot()
        do {
            let envelope: ConnectionAlertEnvelope = try await api.get("api/connection-alerts")
            await accept(envelope.alerts, generation: request)
        } catch {
            failed(generation: request)
        }
    }

    func failed(generation request: Int) {
        guard request == generation, !Task.isCancelled else { return }
        loadError = hasLoaded
            ? "接続状況を取得できませんでした。前回の情報を表示しています。"
            : "接続状況を取得できませんでした。Mac miniへの接続を確認してください。"
        // An unavailable server is not evidence that a connection recovered.
    }

    func accept(_ snapshot: [ConnectionAlert]?, generation request: Int) async {
        guard request == generation, !Task.isCancelled, let snapshot else { return }
        var ids: Set<String> = []
        let current = snapshot.filter { !$0.id.isEmpty && ids.insert($0.id).inserted }
        let removed = Set(alerts.map(\.id)).union(notified).subtracting(ids)
        delivery.remove(ids: Array(removed))
        notified.formIntersection(ids)
        persist()
        if alerts != current { alerts = current }
        if current.allSatisfy({ notified.contains($0.id) }) { deliveryError = nil }
        hasLoaded = true
        loadError = nil
        await refreshPermission()
        guard request == generation, !Task.isCancelled else { return }
        await deliverPending()
    }

    func refreshPermission() async {
        let permission = await delivery.permission()
        guard !Task.isCancelled else { return }
        notificationPermission = permission
    }

    func requestPermission() async {
        await delivery.requestPermission()
        await refreshPermission()
        await deliverPending()
    }

    private func deliverPending() async {
        guard !Task.isCancelled, notificationPermission == .allowed else { return }
        for alert in alerts {
            guard !Task.isCancelled else { return }
            guard !notified.contains(alert.id), !inFlight.contains(alert.id),
                  alerts.contains(where: { $0.id == alert.id }) else { continue }
            inFlight.insert(alert.id)
            do {
                try await delivery.add(alert)
                if Task.isCancelled || notificationPermission != .allowed || !alerts.contains(where: { $0.id == alert.id }) {
                    delivery.remove(ids: [alert.id])
                } else {
                    notified.insert(alert.id)
                    persist()
                    deliveryError = nil
                }
            } catch {
                if Task.isCancelled { delivery.remove(ids: [alert.id]) }
                if !Task.isCancelled { deliveryError = "通知を送れませんでした。次の更新で再試行します。" }
            }
            inFlight.remove(alert.id)
        }
    }

    private func persist() {
        let value = notified.sorted()
        if defaults.stringArray(forKey: Self.storageKey) != value {
            defaults.set(value, forKey: Self.storageKey)
        }
    }
}
