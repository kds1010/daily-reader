import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios" / "DailyReader" / "DailyReader"


def test_location_usage_description_is_foreground_only_and_explicit() -> None:
    with (IOS / "Info.plist").open("rb") as file:
        info = plistlib.load(file)

    description = info["NSLocationWhenInUseUsageDescription"]
    assert "端末内" in description
    assert "送信・保存しません" in description
    assert "NSLocationAlwaysAndWhenInUseUsageDescription" not in info
    assert "location" not in info.get("UIBackgroundModes", [])


def test_location_service_is_one_shot_and_has_no_persistence_or_networking() -> None:
    source = (IOS / "DeviceLocationService.swift").read_text()

    assert "requestWhenInUseAuthorization()" in source
    assert "manager.requestLocation()" in source
    assert "case .authorizedWhenInUse, .authorizedAlways:" in source
    assert "case .denied:" in source
    assert "case .restricted:" in source
    assert "accuracyAuthorization == .reducedAccuracy" in source
    assert "requestPending = true" in source
    assert "startUpdatingLocation" not in source
    assert "requestAlwaysAuthorization" not in source
    assert "URLSession" not in source
    assert "UserDefaults" not in source
    assert "FileManager" not in source
    assert "print(" not in source


def test_location_card_displays_required_values_and_supports_retry() -> None:
    source = (IOS / "RootView.swift").read_text()

    assert "struct DeviceLocationCard: View" in source
    assert 'return "現在地を再取得"' in source
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
