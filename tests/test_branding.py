import plistlib
import struct
from pathlib import Path

ROOT = Path(__file__).parents[1]


def png_info(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    width, height, bit_depth = struct.unpack(">IIB", data[16:25])
    return width, height, data[25]


def test_brand_assets_are_consistent() -> None:
    manifest = (ROOT / "site/manifest.webmanifest").read_text(encoding="utf-8")
    assert '"name": "Daymeld"' in manifest
    assert '"short_name": "Daymeld"' in manifest
    for name, size in (("icon-192.png", 192), ("icon-512.png", 512)):
        width, height, color_type = png_info(ROOT / "site/icons" / name)
        assert (width, height, color_type) == (size, size, 2)


def test_ios_app_icon_and_display_name() -> None:
    icon = ROOT / "ios/DailyReader/DailyReader/Assets.xcassets/AppIcon.appiconset/AppIcon.png"
    assert png_info(icon) == (1024, 1024, 2)
    info = plistlib.loads((ROOT / "ios/DailyReader/DailyReader/Info.plist").read_bytes())
    assert info["CFBundleDisplayName"] == "Daymeld"
    assert "Daymeld" in info["NSHealthShareUsageDescription"]
