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
    @StateObject private var model: AppModel
    @StateObject private var contentZoom = MacContentZoomController()
    @StateObject private var agentKeyboard = MacAgentKeyboardController()

    init() {
#if DEBUG
        _model = StateObject(wrappedValue: AppModel(fixture: DaymeldFixture.fromProcessArguments()))
#else
        _model = StateObject(wrappedValue: AppModel())
#endif
    }

    var body: some Scene {
        WindowGroup {
            MacContentZoomView(scale: MacContentZoom.scale(for: contentZoom.level)) {
                RootView()
                    .environmentObject(model)
                    .environmentObject(agentKeyboard)
                    .preferredColorScheme(.dark)
                    .task { await model.start() }
            }
            .frame(minWidth: 760, minHeight: 560)
        }
        .defaultSize(width: 1120, height: 760)
        .commands {
            CommandGroup(before: .sidebar) {
                Button("再読み込み") {
                    Task { await model.refresh() }
                }
                .keyboardShortcut("r", modifiers: .command)
                .disabled(model.isRefreshing)

                Divider()

                Button("拡大") {
                    contentZoom.zoomIn()
                }
                .keyboardShortcut("=", modifiers: .command)
                .disabled(contentZoom.level == MacContentZoom.maximumLevel)

                Button("縮小") {
                    contentZoom.zoomOut()
                }
                .keyboardShortcut("-", modifiers: .command)
                .disabled(contentZoom.level == MacContentZoom.minimumLevel)

                Button("実際のサイズ") {
                    contentZoom.reset()
                }
                .keyboardShortcut("0", modifiers: .command)
                .disabled(contentZoom.level == MacContentZoom.defaultLevel)
            }
        }
    }
}

@MainActor
final class MacAgentKeyboardController: ObservableObject {
    @Published private(set) var invocation: MacAgentNavigationInvocation?
    var isEnabled = false

    private var parser = MacVimKeyParser()
    private var keyboardMonitor: Any?
    private var serial = 0

    init() {
        keyboardMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard let self, self.isEnabled, !Self.isEditingText else { return event }
            guard let stroke = Self.stroke(for: event) else { return event }
            if let command = self.parser.handle(stroke, at: event.timestamp) {
                self.serial += 1
                self.invocation = MacAgentNavigationInvocation(serial: self.serial, command: command)
            }
            return nil
        }
    }

    deinit {
        if let keyboardMonitor {
            NSEvent.removeMonitor(keyboardMonitor)
        }
    }

    private static var isEditingText: Bool {
        guard let responder = NSApp.keyWindow?.firstResponder else { return false }
        return responder is NSTextView || responder is NSTextField
    }

    private static func stroke(for event: NSEvent) -> MacVimKeyStroke? {
        let modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        guard !modifiers.contains(.command), !modifiers.contains(.option) else { return nil }

        if event.keyCode == 53 { return .escape }
        if event.keyCode == 36 || event.keyCode == 76 { return .enter }

        let character = event.charactersIgnoringModifiers?.lowercased().first
        if modifiers.contains(.control) {
            if character == "d" { return .controlD }
            if character == "u" { return .controlU }
            return nil
        }
        guard !modifiers.contains(.function) else { return nil }
        if modifiers.contains(.shift), character == "g" { return .shiftedG }
        guard let character, "hjkldgztb".contains(character) else { return nil }
        return .character(character)
    }
}

private enum MacContentZoom {
    static let storageKey = "daymeld.mac.contentZoomLevel"
    static let minimumLevel = -2
    static let defaultLevel = 0
    static let maximumLevel = 4

    static func clampedLevel(_ level: Int) -> Int {
        min(max(level, minimumLevel), maximumLevel)
    }

    static func scale(for level: Int) -> CGFloat {
        CGFloat(10 + clampedLevel(level)) / 10
    }

    static func zoomedInLevel(from level: Int) -> Int {
        min(clampedLevel(level) + 1, maximumLevel)
    }

    static func zoomedOutLevel(from level: Int) -> Int {
        max(clampedLevel(level) - 1, minimumLevel)
    }
}

@MainActor
private final class MacContentZoomController: ObservableObject {
    @Published private(set) var level: Int {
        didSet { defaults.set(level, forKey: MacContentZoom.storageKey) }
    }

    private let defaults: UserDefaults
    private var keyboardMonitor: Any?

    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        level = MacContentZoom.clampedLevel(defaults.integer(forKey: MacContentZoom.storageKey))
        keyboardMonitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in
            guard Self.isZoomInShortcut(event) else { return event }
            self?.zoomIn()
            return nil
        }
    }

    deinit {
        if let keyboardMonitor {
            NSEvent.removeMonitor(keyboardMonitor)
        }
    }

    func zoomIn() {
        level = MacContentZoom.zoomedInLevel(from: level)
    }

    func zoomOut() {
        level = MacContentZoom.zoomedOutLevel(from: level)
    }

    func reset() {
        level = MacContentZoom.defaultLevel
    }

    private static func isZoomInShortcut(_ event: NSEvent) -> Bool {
        let modifiers = event.modifierFlags.intersection(.deviceIndependentFlagsMask)
        guard modifiers.contains(.command),
              !modifiers.contains(.control),
              !modifiers.contains(.option)
        else { return false }

        let characters = [event.charactersIgnoringModifiers, event.characters].compactMap { $0 }
        return characters.contains(where: { $0 == "=" || $0 == "+" })
    }
}

private struct MacContentZoomView<Content: View>: View {
    let scale: CGFloat
    private let content: Content

    init(scale: CGFloat, @ViewBuilder content: () -> Content) {
        self.scale = scale
        self.content = content()
    }

    var body: some View {
        content
            .environment(\.appContentScale, scale)
            .environment(\.font, AppTypography.font(for: .body, scale: scale))
    }
}
