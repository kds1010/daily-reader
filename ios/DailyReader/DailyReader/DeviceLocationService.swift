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
    case idle
    case requestingAuthorization
    case locating
    case located(DeviceLocationReading)
    case denied
    case restricted
    case servicesDisabled
    case failed(String)
}

@MainActor
final class DeviceLocationService: NSObject, ObservableObject, @preconcurrency CLLocationManagerDelegate {
    @Published private(set) var state: DeviceLocationState = .idle

    private let manager: CLLocationManager
    private var requestPending = false

    override init() {
        manager = CLLocationManager()
        super.init()
        manager.delegate = self
        manager.desiredAccuracy = kCLLocationAccuracyHundredMeters
    }

    func requestLocation() {
        guard CLLocationManager.locationServicesEnabled() else {
            requestPending = false
            state = .servicesDisabled
            return
        }

        requestPending = true
        switch manager.authorizationStatus {
        case .notDetermined:
            state = .requestingAuthorization
            manager.requestWhenInUseAuthorization()
        case .authorizedWhenInUse, .authorizedAlways:
            startOneShotRequest()
        case .denied:
            requestPending = false
            state = .denied
        case .restricted:
            requestPending = false
            state = .restricted
        @unknown default:
            requestPending = false
            state = .failed("位置情報の権限状態を確認できませんでした。")
        }
    }

    func locationManagerDidChangeAuthorization(_ manager: CLLocationManager) {
        guard requestPending else { return }
        switch manager.authorizationStatus {
        case .authorizedWhenInUse, .authorizedAlways:
            startOneShotRequest()
        case .denied:
            requestPending = false
            state = .denied
        case .restricted:
            requestPending = false
            state = .restricted
        case .notDetermined:
            state = .requestingAuthorization
        @unknown default:
            requestPending = false
            state = .failed("位置情報の権限状態を確認できませんでした。")
        }
    }

    func locationManager(_ manager: CLLocationManager, didUpdateLocations locations: [CLLocation]) {
        requestPending = false
        guard let location = locations.last, location.horizontalAccuracy >= 0 else {
            state = .failed("有効な現在地を取得できませんでした。")
            return
        }
        state = .located(
            DeviceLocationReading(
                latitude: location.coordinate.latitude,
                longitude: location.coordinate.longitude,
                horizontalAccuracy: location.horizontalAccuracy,
                timestamp: location.timestamp,
                isApproximate: manager.accuracyAuthorization == .reducedAccuracy
            )
        )
    }

    func locationManager(_ manager: CLLocationManager, didFailWithError error: Error) {
        requestPending = false
        if let locationError = error as? CLError, locationError.code == .denied {
            state = .denied
        } else {
            state = .failed("現在地を取得できませんでした。もう一度お試しください。")
        }
    }

    private func startOneShotRequest() {
        state = .locating
        manager.requestLocation()
    }
}
