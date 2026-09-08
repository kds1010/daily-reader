import CoreLocation
import Combine
import Foundation

struct DeviceLocationReading: Equatable {
    let latitude: Double
    let longitude: Double
    let horizontalAccuracy: Double
    let timestamp: Date
    let isApproximate: Bool
}

enum DeviceLocationState: Equatable {
    case idle, requestingAuthorization, locating
    case located(DeviceLocationReading)
    case denied, restricted, servicesDisabled
    case failed(String)
}

// This synchronous system-wide query can involve IPC. Authorization callbacks
// drive acquisition; use the query off MainActor only to clarify a denial.
private actor LocationServiceAvailability {
    func isEnabled() -> Bool { CLLocationManager.locationServicesEnabled() }
}

// All queue transactions run away from MainActor. In particular, ACK removes
// only acknowledged IDs from the latest queue, including appends during upload.
actor LocationJournal {
    struct Diagnostics: Codable, Sendable {
        var lastAcquiredAt: Date?
        var lastSyncedAt: Date?
    }
    struct Snapshot: Sendable {
        let revision: Int
        let pending: [LocationEvent]
        let diagnostics: Diagnostics
        let diagnosticsFailed: Bool
    }
    private let url: URL
    private var loaded = false
    private var pending: [LocationEvent] = []
    private var revision = 0
    private var diagnostics = Diagnostics()
    private var diagnosticsFailed = false
    private var diagnosticsURL: URL { url.deletingLastPathComponent().appending(path: "location-diagnostics.json") }
    init(url: URL) { self.url = url }

    func load() throws -> Snapshot {
        guard !loaded else { return snapshot }
        if FileManager.default.fileExists(atPath: url.path) {
            let saved = try JSONDecoder().decode([LocationEvent].self, from: Data(contentsOf: url))
            let repaired = saved.map { event in
                var value = event
                value.timestamp = repairLegacyQueuedUTCTimestamp(event.timestamp)
                return value
            }
            if repaired != saved { try persist(repaired) }
            pending = repaired
        }
        if let data = try? Data(contentsOf: diagnosticsURL),
           let saved = try? JSONDecoder().decode(Diagnostics.self, from: data) { diagnostics = saved }
        diagnostics.lastAcquiredAt = (pending.compactMap(\.date) + [diagnostics.lastAcquiredAt].compactMap { $0 }).max()
        loaded = true
        return snapshot
    }

    func append(_ events: [LocationEvent], acquiredAt: Date) throws -> Snapshot {
        _ = try load()
        var ids = Set(pending.map(\.id))
        let updated = pending + events.filter { ids.insert($0.id).inserted }
        try persist(updated)
        pending = updated
        diagnostics.lastAcquiredAt = max(diagnostics.lastAcquiredAt ?? acquiredAt, acquiredAt)
        revision += 1
        persistDiagnostics()
        return snapshot
    }

    func acknowledge(_ ids: Set<String>, at date: Date) throws -> Snapshot {
        _ = try load()
        let remaining = pending.filter { !ids.contains($0.id) }
        try persist(remaining)
        pending = remaining
        diagnostics.lastSyncedAt = date
        revision += 1
        persistDiagnostics()
        return snapshot
    }

    private var snapshot: Snapshot {
        Snapshot(revision: revision, pending: pending, diagnostics: diagnostics, diagnosticsFailed: diagnosticsFailed)
    }
    private func persist(_ events: [LocationEvent]) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(events).write(to: url, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }
    private func persistDiagnostics() {
        do {
            try JSONEncoder().encode(diagnostics).write(to: diagnosticsURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            diagnosticsFailed = false
        } catch { diagnosticsFailed = true }
    }
}

@MainActor
final class DeviceLocationService: NSObject, ObservableObject, @preconcurrency CLLocationManagerDelegate {
    @Published private(set) var state: DeviceLocationState = .idle
    @Published private(set) var isRecording = false
    @Published private(set) var pending: [LocationEvent] = []
    @Published private(set) var syncMessage: String?
    @Published private(set) var isSyncing = false
    @Published private(set) var lastSyncedAt: Date?
    @Published private(set) var lastAcquiredAt: Date?
    @Published private(set) var lastReading: DeviceLocationReading?
    @Published private(set) var isRefreshingLocation = false
    @Published private(set) var diagnosticsMessage: String?

    private let manager = CLLocationManager()
    // A one-shot request stops its own manager after delivery. Keep it separate
    // from the continuous manager so manual refresh cannot end the recording.
    private let refreshManager = CLLocationManager()
    private let now: () -> Date
    private let upload: ([LocationEvent]) async throws -> Void
    private let journal: LocationJournal
    private let availability = LocationServiceAvailability()
    private var initialLoad: Task<Void, Never>?
    private var persistenceTask: Task<Void, Never>?
    private var deferredStart: Task<Void, Never>?
    private var journalLoaded = false
    private var journalRevision = -1
    private var acquisitionGeneration = 0
    private var syncWaiters: [CheckedContinuation<Void, Never>] = []
    private let isEnabled: Bool
    private var requestPending = false
    private var continuousRequested = false
    private var storageAvailable = false
    private var lastSyncAttempt: Date = .distantPast
    init(isEnabled: Bool = true,
         queueURL: URL = URL.applicationSupportDirectory.appending(path: "Daymeld/location-pending.json"),
         now: @escaping () -> Date = { .now },
         upload: @escaping ([LocationEvent]) async throws -> Void = { try await APIClient.shared.syncLocations($0) }) {
        self.isEnabled = isEnabled
        self.journal = LocationJournal(url: queueURL)
        self.now = now
        self.upload = upload
        super.init()
        manager.delegate = self
        refreshManager.delegate = self
        refreshManager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        manager.distanceFilter = 100
        manager.pausesLocationUpdatesAutomatically = false
        manager.showsBackgroundLocationIndicator = true
        guard isEnabled else { return }
        initialLoad = Task {
            do {
                apply(try await journal.load())
                storageAvailable = true
            } catch {
                state = .failed("位置履歴を読み込めませんでした。既存の記録を保護するため取得を停止しています。")
            }
            journalLoaded = true
        }
    }

    // Also used by deterministic tests and foreground sync. Never acquire or
    // overwrite a queue before its initial protected-file read has completed.
    func prepare() async { await initialLoad?.value }
    func flushPendingWrites() async { await persistenceTask?.value }

    private func apply(_ snapshot: LocationJournal.Snapshot) {
        guard snapshot.revision >= journalRevision else { return }
        journalRevision = snapshot.revision
        pending = snapshot.pending
        lastAcquiredAt = snapshot.diagnostics.lastAcquiredAt
        lastSyncedAt = snapshot.diagnostics.lastSyncedAt
        diagnosticsMessage = snapshot.diagnosticsFailed
            ? "最終取得・同期時刻を保存できませんでした。再起動後の時刻表示は復元できない場合があります。" : nil
    }

    private func deferUntilLoaded(_ action: @escaping @MainActor () -> Void) -> Bool {
        guard !journalLoaded else { return false }
        deferredStart?.cancel()
        deferredStart = Task {
            await prepare()
            guard !Task.isCancelled else { return }
            action()
        }
        return true
    }

    func requestLocation() {
        guard isEnabled else { return }
        if deferUntilLoaded({ self.requestLocation() }) { return }
        guard isEnabled, storageAvailable, !isRefreshingLocation else { return }
        if isRecording {
            isRefreshingLocation = true
            refreshManager.requestLocation()
        } else {
            begin(continuous: false)
        }
    }
    func startRecording() { begin(continuous: true) }

    func stopRecording() {
        acquisitionGeneration += 1
        deferredStart?.cancel()
        requestPending = false
        continuousRequested = false
        isRecording = false
        manager.stopUpdatingLocation()
        refreshManager.stopUpdatingLocation()
        isRefreshingLocation = false
        manager.allowsBackgroundLocationUpdates = false
        if state == .locating || state == .requestingAuthorization { state = .idle }
    }

    private func begin(continuous: Bool) {
        guard isEnabled else { return }
        if deferUntilLoaded({ self.begin(continuous: continuous) }) { return }
        guard isEnabled, storageAvailable, !isRecording else { return }
        requestPending = true
        continuousRequested = continuous
        switch manager.authorizationStatus {
        case .notDetermined:
            state = .requestingAuthorization
            manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways:
            startAuthorizedRequest()
        case .denied:
            showDeniedState()
        case .restricted:
            stopRecording()
            state = .restricted
        @unknown default:
            stopRecording()
            state = .failed("位置情報の権限状態を確認できませんでした。")
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        if manager.authorizationStatus == .denied {
            showDeniedState()
        } else if manager.authorizationStatus == .restricted {
            stopRecording()
            state = .restricted
        } else if manager === self.manager && requestPending {
            switch manager.authorizationStatus {
            case .authorizedWhenInUse, .authorizedAlways: startAuthorizedRequest()
            default: break
            }
        }
    }

    private func showDeniedState() {
        stopRecording()
        state = .denied
        let generation = acquisitionGeneration
        Task {
            let enabled = await availability.isEnabled()
            guard generation == acquisitionGeneration, manager.authorizationStatus == .denied else { return }
            state = enabled ? .denied : .servicesDisabled
        }
    }

    private func startAuthorizedRequest() {
        acquisitionGeneration += 1
        state = .locating
        if continuousRequested {
            requestPending = false
            manager.allowsBackgroundLocationUpdates = true
            isRecording = true
            manager.startUpdatingLocation()
        } else {
            manager.requestLocation()
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        guard isEnabled else { return }
        if manager === refreshManager {
            guard isRefreshingLocation else { return }
            isRefreshingLocation = false
        } else {
            guard isRecording || requestPending else { return }
            requestPending = false
        }
        acquisitionGeneration += 1
        let generation = acquisitionGeneration
        let capturedNow = now()
        let valid = locations.filter {
            $0.horizontalAccuracy >= 0 && $0.horizontalAccuracy <= 100000
            && CLLocationCoordinate2DIsValid($0.coordinate)
            && $0.timestamp.timeIntervalSince(capturedNow) <= 10
            && $0.timestamp.timeIntervalSince(capturedNow) >= -120
        }
        guard let latest = valid.max(by: { $0.timestamp < $1.timestamp }) else {
            state = .failed("新しい現在地を取得できませんでした。もう一度お試しください。")
            return
        }
        let events = valid.map {
            LocationEvent(timestamp: preciseUTCTimestamp($0.timestamp),
                          latitude: $0.coordinate.latitude, longitude: $0.coordinate.longitude,
                          horizontal_accuracy: $0.horizontalAccuracy,
                          is_approximate: manager.accuracyAuthorization == .reducedAccuracy,
                          speed_mps: $0.speed >= 0 && $0.speed <= 400 && $0.speedAccuracy >= 0 && $0.speedAccuracy <= 400 ? $0.speed : nil,
                          speed_accuracy_mps: $0.speed >= 0 && $0.speed <= 400 && $0.speedAccuracy >= 0 && $0.speedAccuracy <= 400 ? $0.speedAccuracy : nil,
                          is_simulated: $0.sourceInformation?.isSimulatedBySoftware ?? false)
        }
        let reading = DeviceLocationReading(latitude: latest.coordinate.latitude,
            longitude: latest.coordinate.longitude, horizontalAccuracy: latest.horizontalAccuracy,
            timestamp: latest.timestamp, isApproximate: manager.accuracyAuthorization == .reducedAccuracy)
        let previous = persistenceTask
        persistenceTask = Task {
            await previous?.value
            do {
                let snapshot = try await journal.append(events, acquiredAt: reading.timestamp)
                apply(snapshot)
                if lastReading == nil || reading.timestamp >= lastReading!.timestamp { lastReading = reading }
                if generation == acquisitionGeneration { state = .located(lastReading!) }
                Task { await syncPending() }
            } catch {
                stopRecording()
                state = .failed("位置履歴を保存できませんでした。空き容量などを確認してください。")
            }
        }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        guard isEnabled else { return }
        if manager === refreshManager {
            guard isRefreshingLocation else { return }
            isRefreshingLocation = false
        } else {
            guard isRecording || requestPending else { return }
            requestPending = false
        }
        acquisitionGeneration += 1
        if let locationError = error as? CLError, locationError.code == .denied {
            showDeniedState()
        } else {
            state = .failed("現在地を取得できませんでした。記録中は次の取得を待ちます。")
        }
    }

    func syncPending(force: Bool = false) async {
        await prepare()
        await flushPendingWrites()
        guard isEnabled, storageAvailable, !Task.isCancelled else { return }
        if isSyncing {
            await withCheckedContinuation { syncWaiters.append($0) }
            return
        }
        guard !pending.isEmpty else { return }
        guard force || now().timeIntervalSince(lastSyncAttempt) >= 30 else { return }
        lastSyncAttempt = now()
        isSyncing = true
        defer {
            isSyncing = false
            let waiters = syncWaiters
            syncWaiters.removeAll()
            for waiter in waiters { waiter.resume() }
        }
        do {
            while !pending.isEmpty && !Task.isCancelled {
                let batch = Array(pending.prefix(500))
                try await upload(batch)
                let ids = Set(batch.map(\.id))
                // Wait for callbacks already received during the upload before
                // taking the latest journal snapshot; newer callbacks remain safe
                // because acknowledge operates on IDs in the actor-owned queue.
                await flushPendingWrites()
                apply(try await journal.acknowledge(ids, at: now()))
            }
            syncMessage = nil
        } catch {
            let reason: String
            if error is URLError {
                reason = "Mac miniと通信できませんでした。Tailscaleと接続先を確認してください。"
            } else if error is APIClientError || error is DecodingError {
                reason = "Mac miniへの同期に失敗しました。アプリの更新とサーバーの状態を確認してください。"
            } else if error is CancellationError {
                reason = "同期が中断されました。次の更新時に再試行します。"
            } else {
                reason = "同期後の端末への保存処理に失敗しました。空き容量などを確認してください。"
            }
            syncMessage = reason + " 未同期の\(pending.count)件を端末に保持しています。"
        }
    }
}
