from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

spec = spec_from_file_location(
    "build_keyhints_release", Path(__file__).parents[1] / "scripts/build_keyhints_release.py"
)
assert spec and spec.loader
mod = module_from_spec(spec)
spec.loader.exec_module(mod)


def test_release_version():
    assert mod.release_version(7) == "0.1.7"
    with pytest.raises(ValueError):
        mod.release_version(0)


def test_target_and_info_plist():
    project = (
        Path(__file__).parents[1] / "ios/DailyReader/DailyReader.xcodeproj/project.pbxproj"
    ).read_text()
    assert "KeyHintsMac" in project
    assert "net.skmin.KeyHints" in project
    assert (
        "<key>LSUIElement</key>"
        in (Path(__file__).parents[1] / "ios/DailyReader/KeyHints/Info.plist").read_text()
    )
