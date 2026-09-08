"""Run the actual service with deterministic Core Location callbacks, never GPS hardware."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

IOS = Path(__file__).resolve().parents[1] / "ios/DailyReader/DailyReader"


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_location_session_refresh_and_durable_retry(tmp_path):
    # Only replace the SDK import. All service state, serialization and async retry
    # code below is the production implementation; the SDK boundary is a fake.
    service = (IOS / "DeviceLocationService.swift").read_text()
    (tmp_path / "Service.swift").write_text(service.replace("import CoreLocation", ""))
    (tmp_path / "CoreLocationFake.swift").write_text('''
import Foundation
let kCLLocationAccuracyHundredMeters = 100.0
struct CLLocationCoordinate2D { let latitude: Double; let longitude: Double }
func CLLocationCoordinate2DIsValid(_ c: CLLocationCoordinate2D) -> Bool {
    (-90...90).contains(c.latitude) && (-180...180).contains(c.longitude)
}
enum CLAuthorizationStatus {
    case notDetermined, authorizedWhenInUse, authorizedAlways, denied, restricted
}
enum CLAccuracyAuthorization { case fullAccuracy, reducedAccuracy }
struct CLError: Error { enum Code { case denied, locationUnknown }; let code: Code }
struct SourceInformation { let isSimulatedBySoftware: Bool }
struct CLLocation {
    let timestamp: Date
    let coordinate = CLLocationCoordinate2D(latitude: 35, longitude: 139)
    let horizontalAccuracy = 10.0
    let speed = -1.0, speedAccuracy = -1.0
    let sourceInformation: SourceInformation? = nil
}
protocol CLLocationManagerDelegate: AnyObject {}
@MainActor final class CLLocationManager {
    static var instances: [CLLocationManager] = []
    nonisolated(unsafe) static var servicesEnabled = true
    nonisolated static func locationServicesEnabled() -> Bool { servicesEnabled }
    weak var delegate: (any CLLocationManagerDelegate)?
    var desiredAccuracy = 0.0, distanceFilter = 0.0
    var pausesLocationUpdatesAutomatically = true
    var showsBackgroundLocationIndicator = false, allowsBackgroundLocationUpdates = false
    var authorizationStatus: CLAuthorizationStatus = .authorizedWhenInUse
    var accuracyAuthorization: CLAccuracyAuthorization = .fullAccuracy
    var starts = 0, stops = 0, requests = 0
    init() { Self.instances.append(self) }
    func requestWhenInUseAuthorization() {}
    func startUpdatingLocation() { starts += 1 }
    func stopUpdatingLocation() { stops += 1 }
    func requestLocation() { requests += 1 }
}
''')
    (tmp_path / "Runner.swift").write_text('''
import Foundation
@main struct Runner {
    @MainActor static func main() async throws {
        let directory = URL(fileURLWithPath: CommandLine.arguments[1])
        let queue = directory.appending(path: "location-pending.json")
        var clock = Date(timeIntervalSince1970: 1788770000)
        var offline = true
        var batches: [[LocationEvent]] = []
        var duringUpload: (() -> Void)?
        let service = DeviceLocationService(queueURL: queue, now: { clock }, upload: { batch in
            batches.append(batch)
            if offline { throw URLError(.notConnectedToInternet) }
            let callback = duringUpload; duringUpload = nil
            callback?()
        })
        await service.prepare()
        let continuous = CLLocationManager.instances[0], refresh = CLLocationManager.instances[1]
        precondition(!service.isRecording && service.lastAcquiredAt == nil)
        service.startRecording()
        precondition(service.isRecording && continuous.starts == 1)
        precondition(continuous.distanceFilter == 100)
        service.locationManager(continuous, didUpdateLocations: [CLLocation(timestamp: clock)])
        await service.syncPending(force: true)
        precondition(service.pending.count == 1 && service.lastSyncedAt == nil)
        precondition(service.syncMessage!.contains("Tailscale"))
        let attempts = batches.count
        await service.syncPending()
        precondition(batches.count == attempts) // respect the 30-second retry gate
        let reading = service.lastReading
        service.requestLocation()
        service.requestLocation() // coalesce repeated taps
        precondition(refresh.requests == 1 && continuous.stops == 0 && service.isRefreshingLocation)
        service.locationManager(refresh, didFailWithError: CLError(code: .locationUnknown))
        precondition(service.isRecording && !service.isRefreshingLocation)
        precondition(service.lastReading == reading)
        if case .failed = service.state {} else { fatalError("missing acquisition error") }
        clock += 60
        service.requestLocation()
        service.locationManager(refresh, didUpdateLocations: [CLLocation(timestamp: clock)])
        await service.flushPendingWrites()
        precondition(service.isRecording && continuous.stops == 0 && !service.isRefreshingLocation)
        if case .located = service.state {} else { fatalError("failed to recover") }
        precondition(service.pending.count == 2 && service.lastAcquiredAt == clock)
        let acquired = clock
        clock += 7200
        precondition(service.lastAcquiredAt == acquired) // passage of time is not a new fix
        service.locationManager(continuous, didUpdateLocations: [CLLocation(timestamp: acquired)])
        await service.flushPendingWrites()
        precondition(service.lastAcquiredAt == acquired && service.pending.count == 2)
        offline = false
        duringUpload = {
            service.locationManager(continuous, didUpdateLocations: [CLLocation(timestamp: clock)])
        }
        await service.syncPending(force: true)
        precondition(service.pending.isEmpty && service.lastSyncedAt == clock)
        precondition(batches.suffix(2).map(\\.count) == [2, 1])
        let disk = try JSONDecoder().decode([LocationEvent].self, from: Data(contentsOf: queue))
        precondition(disk.isEmpty && service.syncMessage == nil)
        service.requestLocation()
        service.stopRecording()
        precondition(!service.isRecording && !service.isRefreshingLocation)
        let finalTime = service.lastAcquiredAt
        clock += 10
        service.locationManager(refresh, didUpdateLocations: [CLLocation(timestamp: clock)])
        service.locationManager(continuous, didFailWithError: CLError(code: .locationUnknown))
        precondition(service.pending.isEmpty && service.lastAcquiredAt == finalTime)
        let restored = DeviceLocationService(queueURL: queue, now: { clock }, upload: { _ in })
        await restored.prepare()
        precondition(!restored.isRecording && restored.lastReading == nil)
        precondition(restored.lastAcquiredAt == finalTime && restored.lastSyncedAt == finalTime)
        // Old queue format remains valid and supplies the acquisition time even
        // if the optional diagnostic file has never existed.
        let oldQueue = directory.appending(path: "legacy/location-pending.json")
        try FileManager.default.createDirectory(at: oldQueue.deletingLastPathComponent(),
            withIntermediateDirectories: true)
        let legacy = LocationEvent(timestamp: String(preciseUTCTimestamp(clock).dropLast()),
            latitude: 35, longitude: 139, horizontal_accuracy: 10, is_approximate: false)
        try JSONEncoder().encode([legacy]).write(to: oldQueue)
        let old = DeviceLocationService(queueURL: oldQueue, now: { clock }, upload: { _ in })
        await old.prepare()
        precondition(old.pending.count == 1 && old.pending[0].timestamp.hasSuffix("Z"))
        precondition(old.lastAcquiredAt == clock && !old.isRecording)
        await old.syncPending(force: true)
        precondition(old.pending.isEmpty)
        // A queue write failure must retain unacknowledged events in memory.
        let failureQueue = directory.appending(path: "failure/location-pending.json")
        try FileManager.default.createDirectory(at: failureQueue.deletingLastPathComponent(),
            withIntermediateDirectories: true)
        try JSONEncoder().encode([legacy]).write(to: failureQueue)
        let failing = DeviceLocationService(queueURL: failureQueue, now: { clock }, upload: { _ in
            try FileManager.default.removeItem(at: failureQueue)
            try FileManager.default.createDirectory(at: failureQueue,
            withIntermediateDirectories: true)
        })
        await failing.syncPending(force: true)
        precondition(failing.pending.count == 1 && failing.lastSyncedAt == nil)
        precondition(failing.syncMessage!.contains("保存処理"))
        let bulkQueue = directory.appending(path: "bulk/location-pending.json")
        try FileManager.default.createDirectory(at: bulkQueue.deletingLastPathComponent(),
            withIntermediateDirectories: true)
        let bulk = (0..<501).map { index in
            LocationEvent(timestamp: preciseUTCTimestamp(clock.addingTimeInterval(Double(index))),
                latitude: 35, longitude: 139, horizontal_accuracy: 10, is_approximate: false)
        }
        try JSONEncoder().encode(bulk).write(to: bulkQueue)
        var sizes: [Int] = []
        let bulkService = DeviceLocationService(queueURL: bulkQueue,
            upload: { sizes.append($0.count) })
        await bulkService.syncPending(force: true)
        precondition(sizes == [500, 1] && bulkService.pending.isEmpty)
        let deniedManager = CLLocationManager.instances[CLLocationManager.instances.count - 2]
        bulkService.startRecording()
        deniedManager.authorizationStatus = .denied
        bulkService.locationManagerDidChangeAuthorization(deniedManager)
        precondition(!bulkService.isRecording && bulkService.state == .denied)
        // Late disk completion must not overwrite a subsequent permission error.
        let stateService = DeviceLocationService(
            queueURL: directory.appending(path: "state/location-pending.json"),
            now: { clock }, upload: { _ in throw URLError(.notConnectedToInternet) })
        await stateService.prepare()
        let stateManager = CLLocationManager.instances[CLLocationManager.instances.count - 2]
        stateService.startRecording()
        stateService.locationManager(stateManager,
            didUpdateLocations: [CLLocation(timestamp: clock)])
        stateManager.authorizationStatus = .denied
        stateService.locationManagerDidChangeAuthorization(stateManager)
        await stateService.flushPendingWrites()
        precondition(stateService.state == .denied && !stateService.isRecording
            && stateService.pending.count == 1)
        CLLocationManager.servicesEnabled = false
        stateService.locationManagerDidChangeAuthorization(stateManager)
        for _ in 0..<1000 {
            if stateService.state == .servicesDisabled { break }
            try await Task.sleep(for: .milliseconds(10))
        }
        precondition(stateService.state == .servicesDisabled)
        CLLocationManager.servicesEnabled = true
        // The actor transaction must preserve a new append when an older batch
        // is acknowledged, even if the UI has not received the append snapshot.
        let journal = LocationJournal(url: directory.appending(path: "actor/location-pending.json"))
        let first = try await journal.append([bulk[0]], acquiredAt: clock)
        let second = try await journal.append([bulk[1]], acquiredAt: clock)
        let ack = try await journal.acknowledge(Set(first.pending.map(\\.id)), at: clock)
        precondition(ack.pending == [bulk[1]] && ack.revision > second.revision)
        let journalDisk = try JSONDecoder().decode([LocationEvent].self,
            from: Data(contentsOf: directory.appending(path: "actor/location-pending.json")))
        precondition(journalDisk == ack.pending)
        // Failed appends do not publish data that has never been persisted.
        let blockedURL = directory.appending(path: "actor/location-pending.json")
        try FileManager.default.removeItem(at: blockedURL)
        try FileManager.default.createDirectory(at: blockedURL, withIntermediateDirectories: true)
        do {
            _ = try await journal.append([bulk[2]], acquiredAt: clock)
            fatalError("write should fail")
        }
        catch {}
        let unchanged = try await journal.load()
        precondition(unchanged.pending == ack.pending && unchanged.revision == ack.revision)
        print("GPS session, manual refresh, recovery, retry and persistence passed")
    }
}
''')
    env = os.environ.copy()
    module_cache = os.environ.get("DAYMELD_SWIFT_TEST_CACHE", str(tmp_path / "module-cache"))
    env["CLANG_MODULE_CACHE_PATH"] = module_cache
    env["SWIFT_MODULECACHE_PATH"] = module_cache
    binary = tmp_path / "location-test"
    result = subprocess.run(
        ["xcrun", "swiftc", "-whole-module-optimization",
         str(IOS / "Models.swift"), str(IOS / "APIClient.swift"),
         str(tmp_path / "CoreLocationFake.swift"), str(tmp_path / "Service.swift"),
         str(tmp_path / "Runner.swift"), "-o", str(binary)],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary), str(tmp_path)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    assert "persistence passed" in result.stdout
