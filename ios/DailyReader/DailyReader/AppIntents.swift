import AppIntents
import Foundation

struct OpenAgentIntent: AppIntent {
    static let title: LocalizedStringResource = "Agentを開く"
    static let description = IntentDescription("DaymeldのAgent画面を開きます。")
    static let openAppWhenRun = true
    @MainActor func perform() async throws -> some IntentResult { .result() }
}

struct DailyReaderShortcuts: AppShortcutsProvider {
    static var appShortcuts: [AppShortcut] {
        AppShortcut(intent: OpenAgentIntent(), phrases: ["\(.applicationName)でAgentを開く"], shortTitle: "Agentを開く", systemImageName: "terminal")
        AppShortcut(intent: ImportRecordingIntent(), phrases: ["\(.applicationName)に録音を取り込む"], shortTitle: "MP3を取り込む", systemImageName: "waveform.badge.plus")
        AppShortcut(intent: ImportSoundcoreLinkIntent(), phrases: ["\(.applicationName)にSoundcoreリンクを取り込む"], shortTitle: "Soundcoreリンク", systemImageName: "icloud.and.arrow.down")
    }
}

struct ImportSoundcoreLinkIntent: AppIntent {
    static let title: LocalizedStringResource = "SoundcoreリンクをDaymeldに取り込む"
    static let description = IntentDescription("Soundcoreの共有URLをMac miniへ送り、クラウドの音声取得を受け付けます。取得と文字起こしはMacで続き、状況はDaymeldの「会話」で確認できます。")
    static let openAppWhenRun = true

    @Parameter(title: "Soundcore共有リンク")
    var url: URL

    static var parameterSummary: some ParameterSummary {
        Summary("\(\.$url)から音声をDaymeldに取り込む")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
#if DEBUG
        if DaymeldFixture.fromProcessArguments() != nil {
            return .result(dialog: "プレビュー中は共有リンクを送信しません。")
        }
#endif
        _ = try await acceptLink(into: .shared)
        ConversationImports.shared.openConversations()
        return .result(dialog: "Mac miniで受け付けました。音声の取得・解析状況はDaymeldの「会話」で確認できます。")
    }

    @MainActor
    func acceptLink(into imports: SoundcoreImports) async throws -> SoundcoreImportJob {
        try await imports.accept(url.absoluteString)
    }
}

struct ImportRecordingIntent: AppIntent {
    static let title: LocalizedStringResource = "MP3をDaymeldに取り込む"
    static let description = IntentDescription("共有または書き出し済みのMP3を受け付け、DaymeldでMac miniへ送信します。Soundcore内の録音を直接取得する機能ではありません。")
    // Keep compatibility with iOS 17/macOS 14. Transfer continues in the app,
    // rather than keeping the intent alive for a potentially long upload.
    static let openAppWhenRun = true

    @Parameter(title: "MP3ファイル", description: "Soundcoreなどから書き出したMP3を指定してください。")
    var file: IntentFile

    static var parameterSummary: some ParameterSummary {
        Summary("\(\.$file)をDaymeldに取り込む")
    }

    @MainActor
    func perform() async throws -> some IntentResult & ProvidesDialog {
#if DEBUG
        if DaymeldFixture.fromProcessArguments() != nil {
            return .result(dialog: "プレビュー中は録音を送信しません。")
        }
#endif
        try await acceptFile(into: .shared)
        return .result(dialog: "MP3を端末に受け付けました。送信状況はDaymeldの「会話」で確認できます。")
    }

    @MainActor
    func acceptFile(into imports: ConversationImports) async throws {
        let input = file
        let (url, data, filename) = await Task.detached {
            let url = input.fileURL
            return (url, url == nil ? input.data : nil, input.filename)
        }.value
        guard (filename as NSString).pathExtension.lowercased() == "mp3" else {
            throw ConversationImportError.invalidFile
        }
        try await imports.enqueue(url: url, data: data, filename: filename)
    }
}
