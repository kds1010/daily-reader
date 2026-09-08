import Foundation
import AppIntents

@main struct ConversationImportHarness {
    @MainActor static func waitFor(_ condition: () -> Bool) async throws {
        for _ in 0..<1000 {
            if condition() { return }
            try await Task.sleep(for: .milliseconds(10))
        }
        fatalError("import timed out")
    }

    @MainActor static func main() async throws {
        let root = URL(fileURLWithPath: CommandLine.arguments[1])
        let server = URL(string: CommandLine.arguments[2])!
        let fm = FileManager.default
        let inbox = root.appending(path: "Inbox")
        try fm.createDirectory(at: inbox, withIntermediateDirectories: true)
        let file = inbox.appending(path: "2026-09-08_2026-09-08 15:26:25.mp3")
        let original = Data("ID3-anonymous-fixture".utf8)
        try original.write(to: file)
        let store = ConversationImportStore(directory: root.appending(path: "queue"), inbox: inbox)
        let imports = ConversationImports(store: store, api: APIClient(serverURL: server))
        // Two concurrent deliveries must use one pending item and one upload.
        async let first: Void = imports.enqueue(url: file, filename: file.lastPathComponent, shared: true)
        async let second: Void = imports.enqueue(url: file, filename: file.lastPathComponent, shared: true)
        _ = try await (first, second)
        try await waitFor { imports.failures.count == 1 && imports.activeID == nil }
        precondition(imports.pending.count == 1)
        precondition(try! Data(contentsOf: file) == original)
        let entry = imports.pending[0]
        let staged = await store.fileURL(entry)
        precondition(try! Data(contentsOf: staged) == original)
        precondition(entry.filename == file.lastPathComponent)
        // Server failure leaves a real disk queue that a new app instance reads.
        let restoredStore = ConversationImportStore(directory: root.appending(path: "queue"), inbox: inbox)
        let restored = ConversationImports(store: restoredStore, api: APIClient(serverURL: server))
        await restored.resume()
        try await waitFor { restored.completedCount == 1 }
        precondition(restored.pending.isEmpty)
        precondition(!fm.fileExists(atPath: file.path))
        precondition(!fm.fileExists(atPath: staged.path))

        // Regular picker/shortcut sources must survive successful upload.
        let outside = root.appending(path: "outside.mp3")
        try Data("ID3-outside-fixture".utf8).write(to: outside)
        var fileIntent = ImportRecordingIntent()
        fileIntent.file = IntentFile(fileURL: outside, filename: "shortcut-original.mp3")
        try await fileIntent.acceptFile(into: restored)
        try await waitFor { restored.completedCount == 2 }
        precondition(fm.fileExists(atPath: outside.path))

        // A new file arrives during an upload; both are drained serially.
        var dataIntent = ImportRecordingIntent()
        dataIntent.file = IntentFile(data: Data("ID3-first".utf8), filename: "first.mp3", type: .mp3)
        try await dataIntent.acceptFile(into: restored)
        try await restored.enqueue(url: nil, data: Data("ID3-second".utf8), filename: "second.mp3")
        try await waitFor { restored.completedCount == 4 }
        precondition(restored.pending.isEmpty)

        // Replaced Inbox filenames and files outside Inbox are never removed.
        let changed = inbox.appending(path: "changed.mp3")
        try original.write(to: changed)
        let changedEntry = try await store.stage(url: changed, filename: changed.lastPathComponent, shared: true)
        try Data("new-share".utf8).write(to: changed)
        try await store.finish(changedEntry)
        precondition(try! String(contentsOf: changed, encoding: .utf8) == "new-share")
        let externalEntry = try await store.stage(url: outside, filename: outside.lastPathComponent, shared: true)
        try await store.finish(externalEntry)
        precondition(fm.fileExists(atPath: outside.path))
        let linked = inbox.appending(path: "link.mp3")
        try fm.createSymbolicLink(at: linked, withDestinationURL: outside)
        do {
            let linkedEntry = try await store.stage(url: linked, filename: linked.lastPathComponent, shared: true)
            try await store.finish(linkedEntry)
        } catch is ConversationImportError {}
        precondition(fm.fileExists(atPath: outside.path))

        // Reject invalid names, type and size before any HTTP request.
        for (name, bytes) in [("../escape.mp3", original), ("bad.wav", original),
                              ("empty.mp3", Data()), ("large.txt", Data(repeating: 1, count: 10 * 1024 * 1024 + 1))] {
            do {
                _ = try await store.stage(url: nil, data: bytes, filename: name)
                fatalError("accepted invalid input")
            } catch is ConversationImportError {}
        }
        // Manual retry only sends failed entries; a second tap cannot launch a worker.
        try await restored.enqueue(url: nil, data: Data("ID3-retry".utf8), filename: "retry.mp3")
        try await waitFor { restored.failures.count == 1 && restored.activeID == nil }
        restored.retry()
        restored.retry()
        try await waitFor { restored.completedCount == 5 }
        precondition(restored.pending.isEmpty)
        let pending = try await restoredStore.pending()
        precondition(pending.entries.isEmpty)
        // A damaged entry cannot stop other recordings. Abandoned staging is
        // never shown as accepted input and is reclaimed after interruption.
        let recoveryRoot = root.appending(path: "recovery")
        let recoveryStore = ConversationImportStore(directory: recoveryRoot)
        let damaged = try await recoveryStore.stage(url: nil, data: original, filename: "damaged.mp3")
        try Data("broken".utf8).write(to: recoveryRoot.appending(path: damaged.id).appending(path: "entry.json"))
        _ = try await recoveryStore.stage(url: nil, data: Data("ID3-valid".utf8), filename: "valid.mp3")
        let abandoned = recoveryRoot.appending(path: ".stage-\(UUID().uuidString)")
        try fm.createDirectory(at: abandoned, withIntermediateDirectories: false)
        try original.write(to: abandoned.appending(path: "partial.mp3"))
        let snapshot = try await recoveryStore.pending()
        precondition(snapshot.entries.count == 1 && snapshot.unreadableCount == 1)
        precondition(!fm.fileExists(atPath: abandoned.path))
        _ = try await recoveryStore.stage(url: nil, data: original, filename: "damaged.mp3")
        let repaired = try await recoveryStore.pending()
        precondition(repaired.entries.count == 2 && repaired.unreadableCount == 1)
        print("PASS: byte-preserving upload, duplicate coalescing, disk recovery, retry, source retention, safe Inbox cleanup, invalid inputs")
    }
}
