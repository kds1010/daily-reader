import Foundation
import UserNotifications

@MainActor
final class AgentNotificationCoordinator {
    private struct StoredState: Codable {
        var initialized: Bool
        var statuses: [String: String]
    }

    private static let storageKey = "agent-notification-state-v1"
    private static let notificationStatuses: Set<String> = ["completed", "blocked", "failed"]

    private let defaults: UserDefaults
    private let center: UNUserNotificationCenter
    private var state: StoredState

    init(
        defaults: UserDefaults = .standard,
        center: UNUserNotificationCenter = .current()
    ) {
        self.defaults = defaults
        self.center = center
        if let data = defaults.data(forKey: Self.storageKey),
           let stored = try? JSONDecoder().decode(StoredState.self, from: data) {
            state = stored
        } else {
            state = StoredState(initialized: false, statuses: [:])
        }
    }

    func changedJobs(active: [AgentJob], archived: [AgentJob]) -> [AgentJob] {
        let previous = state.statuses
        let allJobs = active + archived
        let changed: [AgentJob]
        if state.initialized {
            changed = active.filter { job in
                previous[job.id] != job.status
                    && Self.notificationStatuses.contains(job.status)
            }
        } else {
            changed = []
        }

        for job in allJobs {
            state.statuses[job.id] = job.status
        }
        state.initialized = true
        persist()
        return changed
    }

    func schedule(for job: AgentJob) {
        let content = UNMutableNotificationContent()
        switch job.status {
        case "completed":
            content.title = "Agentが完了しました"
        case "blocked":
            content.title = "Agentが判断を待っています"
        default:
            content.title = "Agentが失敗しました"
        }
        let text = (job.summary?.isEmpty == false ? job.summary! : job.prompt)
            .replacingOccurrences(of: "\\s+", with: " ", options: .regularExpression)
            .trimmingCharacters(in: .whitespacesAndNewlines)
        content.body = String(text.prefix(180))
        content.sound = .default
        content.userInfo = ["agent_job_id": job.id]
        let identifier = "agent-\(job.id)-\(job.status)-\(job.updatedAt)"
        center.add(UNNotificationRequest(identifier: identifier, content: content, trigger: nil)) { error in
            if let error {
                NSLog("Daymeld agent notification failed: %@", error.localizedDescription)
            }
        }
    }

    private func persist() {
        guard let data = try? JSONEncoder().encode(state) else { return }
        defaults.set(data, forKey: Self.storageKey)
    }
}
