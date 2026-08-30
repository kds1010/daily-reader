import AppKit
import SwiftUI
import UserNotifications

extension Notification.Name {
    static let openAgentFromNotification = Notification.Name("Daymeld.openAgentFromNotification")
}

final class DaymeldMacAppDelegate: NSObject, NSApplicationDelegate, UNUserNotificationCenterDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        UNUserNotificationCenter.current().delegate = self
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        willPresent notification: UNNotification,
        withCompletionHandler completionHandler: @escaping (UNNotificationPresentationOptions) -> Void
    ) {
        completionHandler([.banner, .list, .sound])
    }

    func userNotificationCenter(
        _ center: UNUserNotificationCenter,
        didReceive response: UNNotificationResponse,
        withCompletionHandler completionHandler: @escaping () -> Void
    ) {
        NotificationCenter.default.post(name: .openAgentFromNotification, object: nil)
        completionHandler()
    }
}

@main
struct DaymeldMacApp: App {
    @NSApplicationDelegateAdaptor(DaymeldMacAppDelegate.self) private var appDelegate
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environmentObject(model)
                .preferredColorScheme(.dark)
                .frame(minWidth: 760, minHeight: 560)
                .task { await model.start() }
        }
        .defaultSize(width: 1120, height: 760)
    }
}
