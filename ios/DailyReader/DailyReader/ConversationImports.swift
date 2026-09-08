import Foundation
import Combine
import CryptoKit

struct PendingConversationImport: Codable, Identifiable, Sendable {
    let id: String
    let filename: String
    var inboxCopies: [URL]
}

// Cloud imports are durable jobs on the Mac, so no second local URL queue is
// needed. A failed request keeps the input, while accepted jobs survive app exit.
@MainActor
final class SoundcoreImports: ObservableObject {
    static let shared = SoundcoreImports()
    @Published var draft = ""
    @Published private(set) var jobs: [SoundcoreImportJob] = []
    @Published private(set) var loadState: ResourceLoadState = .idle
    @Published private(set) var submissionError: String?
    @Published private(set) var acceptedMessage: String?
    @Published private(set) var isSubmitting = false
    @Published private(set) var retryingIDs: Set<String> = []
    @Published private(set) var retryErrors: [String: String] = [:]
    @Published private(set) var pollRevision = 0
    private let api: APIClient
    private var readGeneration = 0
    private var readTask: Task<Void, Never>?

    var hasPendingJobs: Bool { jobs.contains(where: \.isPending) }

    init(api: APIClient = .shared) { self.api = api }

    static func submitURL(_ value: String, api: APIClient = .shared) async throws -> SoundcoreImportJob {
        let normalized = try normalizedSoundcoreShareURL(value)
        return try await api.post("api/conversations/soundcore-imports",
                                  body: SoundcoreImportRequest(url: normalized), as: SoundcoreImportJob.self)
    }

    func accept(_ value: String) async throws -> SoundcoreImportJob {
        let job = try await Self.submitURL(value, api: api)
        applyMutation(job)
        return job
    }

    func refresh() async {
        if let readTask { await readTask.value; return }
        let generation = readGeneration
        let task = Task {
            if loadState == .idle { loadState = .loading }
            do {
                let response = try await api.get("api/conversations/soundcore-imports", as: SoundcoreImportsEnvelope.self)
                guard !Task.isCancelled, generation == readGeneration else { return }
                let discoveredPendingJobs = !hasPendingJobs && response.items.contains(where: \.isPending)
                if jobs != response.items { jobs = response.items }
                loadState = .loaded
                // Manual recovery or a job accepted on another device also
                // resumes the view's bounded, foreground-only polling loop.
                if discoveredPendingJobs { pollRevision += 1 }
            } catch {
                guard !Task.isCancelled, generation == readGeneration else { return }
                loadState = .failed("クラウド取り込みの状況を取得できませんでした。接続を確認して再読み込みしてください。")
            }
        }
        readTask = task
        await task.value
        if generation == readGeneration { readTask = nil }
    }

    @discardableResult
    func submit() async -> Bool {
        guard !isSubmitting else { return false }
        isSubmitting = true
        submissionError = nil
        acceptedMessage = nil
        defer { isSubmitting = false }
        let submitted = draft
        do {
            _ = try await accept(submitted)
            if draft == submitted { draft = "" }
            acceptedMessage = "Mac miniが受け付けました。音声の取得と解析はMacで続きます。状況は下の一覧で確認できます。"
            return true
        } catch {
            submissionError = error is SoundcoreLinkError ? error.localizedDescription
                : "Mac miniでの受付を確認できませんでした。接続を確認して再送してください。入力したリンクは保持しています。"
            return false
        }
    }

    func retry(_ job: SoundcoreImportJob) async {
        guard job.status == "failed", !retryingIDs.contains(job.id) else { return }
        retryingIDs.insert(job.id)
        retryErrors[job.id] = nil
        defer { retryingIDs.remove(job.id) }
        do {
            let updated = try await api.post("api/conversations/soundcore-imports/\(job.id)/retry",
                                             body: EmptyRequest(), as: SoundcoreImportJob.self)
            applyMutation(updated)
        } catch {
            retryErrors[job.id] = "再試行を受け付けられませんでした。接続を確認して、もう一度お試しください。"
        }
    }

    private func applyMutation(_ job: SoundcoreImportJob) {
        // An older GET must not remove a just-accepted job or undo a retry.
        readGeneration += 1
        readTask?.cancel()
        readTask = nil
        if let index = jobs.firstIndex(where: { $0.id == job.id }) { jobs[index] = job }
        else { jobs.insert(job, at: 0) }
        loadState = .loaded
        pollRevision += 1
    }
}

struct RestoredConversationImports: Sendable {
    let entries: [PendingConversationImport]
    let unreadableCount: Int
}

enum ConversationImportError: LocalizedError {
    case invalidFile, invalidSize
    var errorDescription: String? {
        switch self {
        case .invalidFile: "MP3またはUTF-8のTXTファイルを選択してください。"
        case .invalidSize: "空でないMP3（2 GiB以下）またはTXT（10 MiB以下）を選択してください。"
        }
    }
}

// Disk work runs off the main actor. A complete directory is atomically moved
// into the queue before an intent is allowed to return to Shortcuts.
actor ConversationImportStore {
    let directory: URL
    let inbox: URL?
    private let fm = FileManager.default

    init(directory: URL, inbox: URL? = nil) {
        self.directory = directory
        self.inbox = inbox?.standardizedFileURL.resolvingSymlinksInPath()
    }

    func stage(url: URL?, data: Data? = nil, filename: String, shared: Bool = false) throws -> PendingConversationImport {
        let ext = (filename as NSString).pathExtension.lowercased()
        guard ["mp3", "txt"].contains(ext), filename == (filename as NSString).lastPathComponent,
              !filename.contains("\\"), !filename.contains("\0") else { throw ConversationImportError.invalidFile }
        let maximum = ext == "mp3" ? 2 * 1024 * 1024 * 1024 : 10 * 1024 * 1024
        let scoped = url?.startAccessingSecurityScopedResource() ?? false
        defer { if scoped { url?.stopAccessingSecurityScopedResource() } }
        if let url {
            guard url.isFileURL,
                  try url.resourceValues(forKeys: [.isRegularFileKey]).isRegularFile == true
            else { throw ConversationImportError.invalidFile }
        }
        try fm.createDirectory(at: directory, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        var excluded = directory
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try excluded.setResourceValues(values)
        let temporary = directory.appending(path: ".stage-\(UUID().uuidString)")
        try fm.createDirectory(at: temporary, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        defer { try? fm.removeItem(at: temporary) }
        let destination = temporary.appending(path: filename)
        guard fm.createFile(atPath: destination.path, contents: nil, attributes: [.posixPermissions: 0o600]) else {
            throw CocoaError(.fileWriteUnknown)
        }
#if os(iOS)
        try fm.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: temporary.path)
        try fm.setAttributes([.protectionKey: FileProtectionType.completeUntilFirstUserAuthentication], ofItemAtPath: destination.path)
#endif
        let output = try FileHandle(forWritingTo: destination)
        defer { try? output.close() }
        var hash = SHA256()
        var size = 0
        func write(_ chunk: Data) throws {
            size += chunk.count
            guard size <= maximum else { throw ConversationImportError.invalidSize }
            try output.write(contentsOf: chunk)
            hash.update(data: chunk)
        }
        if let url {
            let input = try FileHandle(forReadingFrom: url)
            defer { try? input.close() }
            while let chunk = try input.read(upToCount: 1024 * 1024), !chunk.isEmpty {
                try Task.checkCancellation()
                try write(chunk)
            }
        } else if let data { try write(data) }
        guard size > 0 else { throw ConversationImportError.invalidSize }
        try output.synchronize()
        let id = ext + "-" + hash.finalize().map { String(format: "%02x", $0) }.joined()
        let target = directory.appending(path: id)
        let inboxCopies = shared && url.map(isInboxCopy) == true ? [url!] : []
        if fm.fileExists(atPath: target.path) {
            if var entry = try? read(target) {
                entry.inboxCopies = Array(Set(entry.inboxCopies + inboxCopies))
                try save(entry, at: target)
                return entry
            }
            // Retain unreadable copies while allowing a fresh explicit share.
            try fm.moveItem(at: target, to: directory.appending(path: ".unreadable-\(UUID().uuidString)"))
        }
        let entry = PendingConversationImport(id: id, filename: filename, inboxCopies: inboxCopies)
        try save(entry, at: temporary)
        try fm.moveItem(at: temporary, to: target)
        return entry
    }

    func pending() throws -> RestoredConversationImports {
        guard fm.fileExists(atPath: directory.path) else { return .init(entries: [], unreadableCount: 0) }
        var entries: [PendingConversationImport] = []
        var unreadable = 0
        for folder in try fm.contentsOfDirectory(at: directory, includingPropertiesForKeys: nil) {
            let name = folder.lastPathComponent
            // Actor serialization guarantees no stage() is writing here.
            if name.hasPrefix(".stage-"), UUID(uuidString: String(name.dropFirst(7))) != nil {
                try fm.removeItem(at: folder)
            } else if name.hasPrefix(".unreadable-") {
                unreadable += 1
            } else if !name.hasPrefix(".") {
                if let entry = try? read(folder) { entries.append(entry) }
                else { unreadable += 1 }
            }
        }
        return .init(entries: entries.sorted { $0.id < $1.id }, unreadableCount: unreadable)
    }

    func fileURL(_ entry: PendingConversationImport) -> URL {
        directory.appending(path: entry.id).appending(path: entry.filename)
    }

    // Only called after the server acknowledged the upload. Recheck content so
    // a later share that reused an Inbox filename cannot be deleted by mistake.
    func finish(_ entry: PendingConversationImport) throws {
        let current = try read(directory.appending(path: entry.id))
        for url in current.inboxCopies where isInboxCopy(url) {
            guard let checksum = try? checksum(url), entry.id.hasSuffix(checksum) else { continue }
            try? fm.removeItem(at: url)
        }
        try fm.removeItem(at: directory.appending(path: entry.id))
    }

    private func isInboxCopy(_ url: URL) -> Bool {
        guard url.isFileURL, let inbox else { return false }
        let candidate = url.standardizedFileURL.resolvingSymlinksInPath()
        return candidate.path.hasPrefix(inbox.path + "/")
    }

    private func checksum(_ url: URL) throws -> String {
        let input = try FileHandle(forReadingFrom: url)
        defer { try? input.close() }
        var hash = SHA256()
        while let data = try input.read(upToCount: 1024 * 1024), !data.isEmpty { hash.update(data: data) }
        return hash.finalize().map { String(format: "%02x", $0) }.joined()
    }

    private func read(_ folder: URL) throws -> PendingConversationImport {
        let entry = try JSONDecoder().decode(PendingConversationImport.self, from: Data(contentsOf: folder.appending(path: "entry.json")))
        guard entry.id == folder.lastPathComponent,
              entry.filename == (entry.filename as NSString).lastPathComponent,
              ["mp3", "txt"].contains((entry.filename as NSString).pathExtension.lowercased())
        else { throw ConversationImportError.invalidFile }
        return entry
    }

    private func save(_ entry: PendingConversationImport, at folder: URL) throws {
        let url = folder.appending(path: "entry.json")
        try JSONEncoder().encode(entry).write(to: url, options: .atomic)
        try fm.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}

@MainActor
final class ConversationImports: ObservableObject {
    static let shared = ConversationImports(
        store: ConversationImportStore(
            directory: FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
                .appending(path: "Daymeld/ConversationImports"),
            inbox: FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first?.appending(path: "Inbox")
        ), api: .shared
    )
    @Published private(set) var pending: [PendingConversationImport] = []
    @Published private(set) var activeID: String?
    @Published private(set) var failures: [String: String] = [:]
    @Published private(set) var completedCount = 0
    @Published private(set) var openRequest = 0
    @Published private(set) var storageError: String?
    @Published private(set) var unreadableCount = 0
    private let store: ConversationImportStore
    private let api: APIClient
    private var worker: Task<Void, Never>?
    private var restored = false
    private var restoration: Task<RestoredConversationImports, Error>?

    func openConversations() { openRequest += 1 }

    init(store: ConversationImportStore, api: APIClient) {
        self.store = store
        self.api = api
    }

    func enqueue(url: URL?, data: Data? = nil, filename: String, shared: Bool = false) async throws {
        let entry = try await store.stage(url: url, data: data, filename: filename, shared: shared)
        if !pending.contains(where: { $0.id == entry.id }) { pending.append(entry) }
        openRequest += 1
        await resume()
        startWorker()
    }

    func resume() async {
        guard !restored else { return }
        if restoration == nil { restoration = Task { try await store.pending() } }
        do {
            let snapshot = try await restoration!.value
            guard !restored else { return }
            for entry in snapshot.entries where !pending.contains(where: { $0.id == entry.id }) {
                pending.append(entry)
            }
            restored = true
            unreadableCount = snapshot.unreadableCount
            restoration = nil
            storageError = nil
            startWorker()
        } catch {
            restoration = nil
            storageError = "送信待ちのファイルを読み込めませんでした。再読み込みしてください。"
        }
    }

    func retry() {
        failures = [:]
        startWorker()
    }

    private func startWorker() {
        guard restored, worker == nil else { return }
        worker = Task {
            while let entry = pending.first(where: { failures[$0.id] == nil }) {
                activeID = entry.id
                do {
                    let url = await store.fileURL(entry)
                    _ = try await api.uploadConversationFile(url, recordedAt: nil)
                    try await store.finish(entry)
                    pending.removeAll { $0.id == entry.id }
                    completedCount += 1
                } catch {
                    // Do not expose paths or filenames from file-provider errors.
                    failures[entry.id] = "送信または端末コピーの整理に失敗しました。接続・空き容量を確認して再送してください。"
                }
            }
            activeID = nil
            worker = nil
        }
    }
}
