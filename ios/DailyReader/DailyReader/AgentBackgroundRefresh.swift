import BackgroundTasks
import Foundation

@MainActor
enum AgentBackgroundRefresh {
    static let identifier = "net.skmin.DailyReader.agent-refresh"
    private static let interval: TimeInterval = 15 * 60

    static func register() {
        _ = BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: nil) { task in
            guard let refreshTask = task as? BGAppRefreshTask else {
                task.setTaskCompleted(success: false)
                return
            }
            Task { @MainActor in handle(refreshTask) }
        }
    }

    static func schedule() {
        let request = BGAppRefreshTaskRequest(identifier: identifier)
        request.earliestBeginDate = Date(timeIntervalSinceNow: interval)
        do { try BGTaskScheduler.shared.submit(request) }
        catch { NSLog("Daymeld background refresh scheduling failed: %@", error.localizedDescription) }
    }

    private static func handle(_ task: BGAppRefreshTask) {
        schedule()
        let operation = Task { @MainActor in
            var success = false
            defer { task.setTaskCompleted(success: success) }
            do {
                let envelope: AgentNotificationEnvelope = try await APIClient.shared.get("/api/agent-notifications")
                for job in AgentNotificationCoordinator.shared.changedJobs(active: envelope.jobs, archived: []) {
                    await AgentNotificationCoordinator.shared.schedule(for: job)
                }
                success = !Task.isCancelled
            } catch {
                NSLog("Daymeld background refresh failed: %@", error.localizedDescription)
            }
        }
        task.expirationHandler = { operation.cancel() }
    }
}
