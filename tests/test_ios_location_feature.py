import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios" / "DailyReader" / "DailyReader"


def test_location_usage_description_covers_persistence_and_background_recording() -> None:
    with (IOS / "Info.plist").open("rb") as file:
        info = plistlib.load(file)

    description = info["NSLocationWhenInUseUsageDescription"]
    assert "Mac mini" in description
    assert "保存" in description
    assert "バックグラウンド" in description
    assert "NSLocationAlwaysAndWhenInUseUsageDescription" not in info
    assert "location" in info.get("UIBackgroundModes", [])


def test_location_service_supports_explicit_sessions_and_durable_retry() -> None:
    source = (IOS / "DeviceLocationService.swift").read_text()

    assert "requestWhenInUseAuthorization()" in source
    assert "manager.requestLocation()" in source
    assert "case .authorizedWhenInUse, .authorizedAlways:" in source
    assert "case .denied:" in source
    assert "case .restricted:" in source
    assert "accuracyAuthorization == .reducedAccuracy" in source
    assert "requestPending = true" in source
    assert "manager.startUpdatingLocation()" in source
    assert "manager.stopUpdatingLocation()" in source
    assert "manager.showsBackgroundLocationIndicator = true" in source
    assert "guard isEnabled, storageAvailable, !isRecording" in source
    assert source.index("try persist(updated)") < source.index("Task { await syncPending() }")
    assert source.index("try await APIClient.shared.syncLocations(batch)") < source.index(
        "try persist(remaining)"
    )
    assert "requestAlwaysAuthorization" not in source
    assert "URLSession" not in source
    assert "UserDefaults" not in source
    assert "FileManager" in source
    assert "print(" not in source


def test_location_card_displays_required_values_and_supports_retry() -> None:
    source = (IOS / "RootView.swift").read_text()

    assert "struct DeviceLocationCard: View" in source
    assert 'return "現在地を再取得して保存"' in source
    for label in ("緯度", "経度", "水平精度", "取得時刻", "概算位置"):
        assert label in source


def test_location_service_is_built_only_into_the_iphone_target() -> None:
    project_path = (
        ROOT / "ios" / "DailyReader" / "DailyReader.xcodeproj" / "project.pbxproj"
    )
    project = project_path.read_text()

    assert project.count("DeviceLocationService.swift in Sources") == 1
    iphone_sources = project.split("/* Begin PBXSourcesBuildPhase section */", 1)[1].split(
        "/* End PBXSourcesBuildPhase section */", 1
    )[0]
    assert iphone_sources.count("A1000000000000000000000D") == 1
