from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/build_macos_release.py"
SPEC = spec_from_file_location("build_macos_release", SCRIPT)
assert SPEC and SPEC.loader
macos_release = module_from_spec(SPEC)
SPEC.loader.exec_module(macos_release)


def test_release_version_matches_sidestore_version_shape() -> None:
    assert macos_release.release_version(42) == "0.1.42"
    with pytest.raises(ValueError, match="positive"):
        macos_release.release_version(0)


def test_macos_entitlements_allow_network_without_healthkit() -> None:
    macos_release.validate_entitlements(
        {
            "com.apple.security.app-sandbox": True,
            "com.apple.security.network.client": True,
        }
    )


@pytest.mark.parametrize(
    "entitlements, message",
    [
        ({"com.apple.security.app-sandbox": True}, "missing entitlements"),
        (
            {
                "com.apple.security.app-sandbox": True,
                "com.apple.security.network.client": True,
                "com.apple.developer.healthkit": True,
            },
            "iPhone entitlements",
        ),
        (
            {
                "com.apple.security.app-sandbox": True,
                "com.apple.security.network.client": True,
                "com.apple.security.files.user-selected.read-write": True,
            },
            "unexpected entitlements",
        ),
    ],
)
def test_macos_entitlements_reject_invalid_payloads(
    entitlements: dict[str, object], message: str
) -> None:
    with pytest.raises(RuntimeError, match=message):
        macos_release.validate_entitlements(entitlements)
