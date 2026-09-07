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

    private let manager = CLLocationManager()
    private let isEnabled: Bool
    private var requestPending = false
    private var continuousRequested = false
    private var storageAvailable = true
    private var lastSyncAttempt: Date = .distantPast
    private var queueURL: URL {
        URL.applicationSupportDirectory.appending(path: "Daymeld/location-pending.json")
    }

    init(isEnabled: Bool = true) {
        self.isEnabled = isEnabled
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
        manager.distanceFilter = 100
        manager.pausesLocationUpdatesAutomatically = false
        manager.showsBackgroundLocationIndicator = true
        guard isEnabled else { return }
        do {
            if FileManager.default.fileExists(atPath: queueURL.path) {
                pending = try JSONDecoder().decode([LocationEvent].self, from: Data(contentsOf: queueURL))
            }
        } catch {
            storageAvailable = false
            state = .failed("位置履歴を読み込めませんでした。既存の記録を保護するため取得を停止しています。")
        }
    }

    func requestLocation() { begin(continuous: false) }
    func startRecording() { begin(continuous: true) }

    func stopRecording() {
        requestPending = false
        continuousRequested = false
        isRecording = false
        manager.stopUpdatingLocation()
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
        } else if requestPending {
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
        guard isEnabled, isRecording || requestPending else { return }
        requestPending = false
        let valid = locations.filter {
            $0.horizontalAccuracy >= 0 && $0.horizontalAccuracy <= 100000
            && CLLocationCoordinate2DIsValid($0.coordinate)
            && $0.timestamp.timeIntervalSinceNow <= 10
            && $0.timestamp.timeIntervalSinceNow >= -120
        }
        guard let latest = valid.last else {
            state = .failed("新しい現在地を取得できませんでした。もう一度お試しください。")
            return
        }
        let events = valid.map {
            LocationEvent(timestamp: $0.timestamp.ISO8601Format(.iso8601(timeZone: .gmt, includingFractionalSeconds: true)),
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
        state = .located(DeviceLocationReading(latitude: latest.coordinate.latitude,
            longitude: latest.coordinate.longitude, horizontalAccuracy: latest.horizontalAccuracy,
            timestamp: latest.timestamp, isApproximate: manager.accuracyAuthorization == .reducedAccuracy))
        Task { await syncPending() }
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        requestPending = false
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

    func syncPending(force: Bool = false) async {
        guard isEnabled, storageAvailable, !isSyncing, !pending.isEmpty else { return }
        guard force || Date.now.timeIntervalSince(lastSyncAttempt) >= 30 else { return }
        lastSyncAttempt = .now
        isSyncing = true
        defer { isSyncing = false }
        do {
            while !pending.isEmpty && !Task.isCancelled {
                let batch = Array(pending.prefix(500))
                try await APIClient.shared.syncLocations(batch)
                let ids = Set(batch.map(\.id))
                let remaining = pending.filter { !ids.contains($0.id) }
                try persist(remaining)
                pending = remaining
                lastSyncedAt = .now
            }
            syncMessage = nil
        } catch {
            syncMessage = "未同期の\(pending.count)件を端末に保持しています。Mac miniへ接続後に再試行します。"
        }
    }
}
