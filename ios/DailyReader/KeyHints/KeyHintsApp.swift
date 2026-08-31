import AppKit
import ApplicationServices
import SwiftUI

@main
struct KeyHintsApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    var body: some Scene { Settings { EmptyView() } }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let scanner = AccessibilityScanner()
    private let overlay = OverlayController()
    private var hotKey: HotKeyController!
    private var status: NSStatusItem!
    private var targets: [HintTarget] = []
    private var buffer = ""
    private var monitor: Any?

    func applicationDidFinishLaunching(_ notification: Notification) {
        status = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        status.button?.title = "⌨︎"
        let menu = NSMenu(); menu.addItem(NSMenuItem(title: "Accessibility設定を開く", action: #selector(openPrivacy), keyEquivalent: "")); menu.addItem(NSMenuItem.separator()); menu.addItem(NSMenuItem(title: "終了", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")); status.menu = menu
        hotKey = HotKeyController(); hotKey.onPress = { [weak self] in self?.activate() }; hotKey.start()
        monitor = NSEvent.addLocalMonitorForEvents(matching: .keyDown) { [weak self] event in self?.handle(event) ?? event }
    }

    private func activate() {
        guard AXIsProcessTrusted() else { openPrivacy(); return }
        scanner.scan { [weak self] snapshots, _ in
            guard let self else { return }
            self.targets = zip(snapshots, HintCodeGenerator.codes(count: snapshots.count)).map { HintTarget(snapshot: $0.0, code: $0.1) }
            self.buffer = ""; self.overlay.show(self.targets)
        }
    }

    private func handle(_ event: NSEvent) -> NSEvent? {
        guard !targets.isEmpty else { return event }
        if event.keyCode == 53 { dismiss(); return nil }
        guard let character = event.charactersIgnoringModifiers?.lowercased(), character.count == 1 else { return nil }
        buffer += character
        let matches = targets.filter { $0.code.hasPrefix(buffer) }
        if matches.count == 1 && matches[0].code == buffer { click(matches[0]); dismiss() }
        else if matches.isEmpty { buffer = "" }
        return nil
    }

    private func click(_ target: HintTarget) {
        if AXUIElementPerformAction(target.snapshot.element, kAXPressAction as CFString) != .success {
            let point = CGPoint(x: target.snapshot.frame.midX, y: target.snapshot.frame.midY)
            CGEvent(mouseEventSource: nil, mouseType: .leftMouseDown, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
            CGEvent(mouseEventSource: nil, mouseType: .leftMouseUp, mouseCursorPosition: point, mouseButton: .left)?.post(tap: .cghidEventTap)
        }
    }
    private func dismiss() { overlay.hide(); targets.removeAll(); buffer = "" }
    @objc private func openPrivacy() { NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility")!) }
}
