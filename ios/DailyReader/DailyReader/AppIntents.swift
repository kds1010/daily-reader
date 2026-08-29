import AppIntents

struct OpenAgentIntent: AppIntent {
    static let title: LocalizedStringResource = "Agentを開く"
    static let description = IntentDescription("DaymeldのAgent画面を開きます。")
    static let openAppWhenRun = true
    @MainActor func perform() async throws -> some IntentResult { .result() }
}

struct DailyReaderShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: OpenAgentIntent(), phrases: ["\(.applicationName)でAgentを開く"], shortTitle: "Agentを開く", systemImageName: "terminal")
    }
}
