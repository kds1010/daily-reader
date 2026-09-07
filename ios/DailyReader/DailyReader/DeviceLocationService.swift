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

    private struct Diagnostics: Codable {
        var lastAcquiredAt: Date?
        var lastSyncedAt: Date?
    }

    private let manager = CLLocationManager()
    // A one-shot request stops its own manager after delivery. Keep it separate
    // from the continuous manager so manual refresh cannot end the recording.
    private let refreshManager = CLLocationManager()
    private let now: () -> Date
    private let upload: ([LocationEvent]) async throws -> Void
    private let queueURL: URL
    private var diagnosticsURL: URL {
        queueURL.deletingLastPathComponent().appending(path: "location-diagnostics.json")
    }
    private let isEnabled: Bool
    private var requestPending = false
    private var continuousRequested = false
    private var storageAvailable = true
    private var lastSyncAttempt: Date = .distantPast
    init(isEnabled: Bool = true,
         queueURL: URL = URL.applicationSupportDirectory.appending(path: "Daymeld/location-pending.json"),
         now: @escaping () -> Date = { .now },
         upload: @escaping ([LocationEvent]) async throws -> Void = { try await APIClient.shared.syncLocations($0) }) {
        self.isEnabled = isEnabled
        self.queueURL = queueURL
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
        do {
            if FileManager.default.fileExists(atPath: queueURL.path) {
                let saved = try JSONDecoder().decode([LocationEvent].self, from: Data(contentsOf: queueURL))
                pending = saved.map { event in
                    var repaired = event
                    repaired.timestamp = repairLegacyQueuedUTCTimestamp(event.timestamp)
                    return repaired
                }
                if pending != saved { try persist(pending) }
            }
        } catch {
            storageAvailable = false
            state = .failed("位置履歴を読み込めませんでした。既存の記録を保護するため取得を停止しています。")
        }
        if let data = try? Data(contentsOf: diagnosticsURL),
           let saved = try? JSONDecoder().decode(Diagnostics.self, from: data) {
            lastAcquiredAt = saved.lastAcquiredAt
            lastSyncedAt = saved.lastSyncedAt
        }
        lastAcquiredAt = (pending.compactMap(\.date) + [lastAcquiredAt].compactMap { $0 }).max()
    }

    func requestLocation() {
        guard isEnabled, storageAvailable, !isRefreshingLocation else { return }
        if isRecording {
            guard CLLocationManager.locationServicesEnabled() else {
                stopRecording()
                state = .servicesDisabled
                return
            }
            isRefreshingLocation = true
            refreshManager.requestLocation()
        } else {
            begin(continuous: false)
        }
    }
    func startRecording() { begin(continuous: true) }

    func stopRecording() {
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
        guard isEnabled, storageAvailable, !isRecording else { return }
        guard CLLocationManager.locationServicesEnabled() else {
            state = .servicesDisabled
            return
        }
        requestPending = true
        continuousRequested = continuous
        switch manager.authorizationStatus {
        case .notDetermined:
            state = .requestingAuthorization
            manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways:
            startAuthorizedRequest()
        case .denied:
            stopRecording()
            state = .denied
        case .restricted:
            stopRecording()
            state = .restricted
        @unknown default:
            stopRecording()
            state = .failed("位置情報の権限状態を確認できませんでした。")
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        if manager.authorizationStatus == .denied || manager.authorizationStatus == .restricted {
            stopRecording()
            state = manager.authorizationStatus == .denied ? .denied : .restricted
        } else if manager === self.manager && requestPending {
            switch manager.authorizationStatus {
            case .authorizedWhenInUse, .authorizedAlways: startAuthorizedRequest()
            default: break
            }
        }
    }

    private func startAuthorizedRequest() {
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
        var ids = Set(pending.map(\.id))
        let updated = pending + events.filter { ids.insert($0.id).inserted }
        do {
            try persist(updated)
            pending = updated
        } catch {
            stopRecording()
            state = .failed("位置履歴を保存できませんでした。空き容量などを確認してください。")
            return
        }
        if lastReading == nil || latest.timestamp >= lastReading!.timestamp {
            lastReading = DeviceLocationReading(latitude: latest.coordinate.latitude,
                longitude: latest.coordinate.longitude, horizontalAccuracy: latest.horizontalAccuracy,
                timestamp: latest.timestamp, isApproximate: manager.accuracyAuthorization == .reducedAccuracy)
        }
        lastAcquiredAt = max(lastAcquiredAt ?? latest.timestamp, latest.timestamp)
        state = .located(lastReading!)
        persistDiagnostics()
        Task { await syncPending() }
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
        if let locationError = error as? CLError, locationError.code == .denied {
            stopRecording()
            state = .denied
        } else {
            state = .failed("現在地を取得できませんでした。記録中は次の取得を待ちます。")
        }
    }

    private func persist(_ events: [LocationEvent]) throws {
        try FileManager.default.createDirectory(at: queueURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(events).write(to: queueURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
    }

    private func persistDiagnostics() {
        do {
            let data = try JSONEncoder().encode(Diagnostics(lastAcquiredAt: lastAcquiredAt, lastSyncedAt: lastSyncedAt))
            try data.write(to: diagnosticsURL, options: [.atomic, .completeFileProtectionUntilFirstUserAuthentication])
            diagnosticsMessage = nil
        } catch {
            diagnosticsMessage = "最終取得・同期時刻を保存できませんでした。再起動後の時刻表示は復元できない場合があります。"
        }
    }

    func syncPending(force: Bool = false) async {
        guard isEnabled, storageAvailable, !isSyncing, !pending.isEmpty else { return }
        guard force || now().timeIntervalSince(lastSyncAttempt) >= 30 else { return }
        lastSyncAttempt = now()
        isSyncing = true
        defer { isSyncing = false }
        do {
            while !pending.isEmpty && !Task.isCancelled {
                let batch = Array(pending.prefix(500))
                try await upload(batch)
                let ids = Set(batch.map(\.id))
                let remaining = pending.filter { !ids.contains($0.id) }
                try persist(remaining)
                pending = remaining
                lastSyncedAt = now()
                persistDiagnostics()
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
