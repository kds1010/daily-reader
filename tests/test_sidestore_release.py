import plistlib
import subprocess
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts/build_sidestore_release.py"
SPEC = spec_from_file_location("build_sidestore_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = "a" * 42 + "A"
OTHER_TOKEN = "b" * 42 + "A"


def test_build_source_uses_versioned_ipa_metadata() -> None:
    source = MODULE.build_source("0.1.42", 12345, "https://example.test/sidestore/")
    app = source["apps"][0]
    version = app["versions"][0]

    assert source["identifier"] == "net.skmin.DailyReader.source"
    assert source["sourceURL"] == "https://example.test/sidestore/source.json"
    assert source["name"] == "Daymeld"
    assert app["name"] == "Daymeld"
    assert app["bundleIdentifier"] == "net.skmin.DailyReader"
    assert version["version"] == "0.1.42"
    assert version["downloadURL"] == "https://example.test/sidestore/DailyReader.ipa"
    assert version["size"] == 12345


def test_default_source_is_private_lan_url() -> None:
    assert Path(__file__).parents[1] / "data/sidestore" == MODULE.DEFAULT_OUTPUT
    assert MODULE.DEFAULT_BASE_URL == "http://sk-mins-Mac-mini.local:8788"


def test_remote_source_uses_token_and_versioned_ipa() -> None:
    token = TOKEN

    source = MODULE.build_remote_source(
        "0.1.42",
        12345,
        "https://reader.example.test:8443/",
        token,
    )
    app = source["apps"][0]
    version = app["versions"][0]

    assert source["name"] == "Daymeld Remote"
    assert source["subtitle"] == "個人用の外出先更新ソース"
    assert source["identifier"] == "net.skmin.DailyReader.remote-source"
    assert source["sourceURL"] == f"https://reader.example.test:8443/{token}/source.json"
    assert app["iconURL"] == f"https://reader.example.test:8443/{token}/icon.png"
    assert version["downloadURL"] == (
        f"https://reader.example.test:8443/{token}/DailyReader-0.1.42.ipa"
    )


def test_remote_token_is_created_once_with_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "secrets" / "sidestore-token.txt"

    token = MODULE.load_or_create_remote_token(path)

    assert len(token) >= 43
    assert path.stat().st_mode & 0o777 == 0o600
    assert MODULE.load_or_create_remote_token(path) == token


def test_existing_remote_token_requires_private_permissions(tmp_path: Path) -> None:
    path = tmp_path / "sidestore-token.txt"
    path.write_text(TOKEN + "\n", encoding="utf-8")
    path.chmod(0o644)

    with pytest.raises(ValueError, match="permissions must be 0600"):
        MODULE.load_or_create_remote_token(path)


def test_existing_remote_token_must_not_be_a_symlink(tmp_path: Path) -> None:
    real_path = tmp_path / "real-token.txt"
    real_path.write_text(TOKEN + "\n", encoding="utf-8")
    real_path.chmod(0o600)
    link_path = tmp_path / "sidestore-token.txt"
    link_path.symlink_to(real_path)

    with pytest.raises(ValueError, match="must not be a symlink"):
        MODULE.load_or_create_remote_token(link_path)


@pytest.mark.parametrize(
    "token",
    ["short", "a" * 42, "a" * 43, "a" * 42 + "/", "a" * 42 + "+"],
)
def test_remote_token_rejects_weak_or_non_url_safe_values(token: str) -> None:
    with pytest.raises(ValueError, match="32-byte URL-safe"):
        MODULE.validate_remote_token(token)


@pytest.mark.parametrize(
    "origin",
    [
        "http://reader.example.test:8443",
        "https://user@reader.example.test:8443",
        "https://reader.example.test:8443/path",
        "https://reader.example.test:8443?token=value",
        "https://reader.example.test:8443?",
        "https://reader.example.test:8443#",
        "https://exa mple.test:8443",
        "https://reader.example.test:99999",
    ],
)
def test_remote_source_rejects_unsafe_origins(origin: str) -> None:
    with pytest.raises(ValueError):
        MODULE.build_remote_source("0.1.42", 12345, origin, TOKEN)


def test_private_source_and_versioned_ipa_are_written_atomically(tmp_path: Path) -> None:
    source_path = tmp_path / "remote-source.json"
    MODULE.write_private_source(source_path, {"sourceURL": "credential"})

    ipa_source = tmp_path / "DailyReader.ipa"
    ipa_destination = tmp_path / "DailyReader-0.1.42.ipa"
    ipa_source.write_bytes(b"new ipa")
    ipa_destination.write_bytes(b"old ipa")
    MODULE.atomic_copy(ipa_source, ipa_destination)

    assert source_path.stat().st_mode & 0o777 == 0o600
    assert ipa_destination.read_bytes() == b"new ipa"
    assert not (tmp_path / ".DailyReader-0.1.42.ipa.tmp").exists()


def test_sidestore_seed_is_ad_hoc_signed_with_required_entitlements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = tmp_path / "DailyReader.app"
    app.mkdir()
    calls: list[list[str]] = []
    signed_entitlements = plistlib.dumps(
        {entitlement: True for entitlement in MODULE.REQUIRED_SIDESTORE_ENTITLEMENTS}
    )

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        calls.append(command)
        stdout = signed_entitlements if "--display" in command else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    MODULE.sign_app_for_sidestore(app)

    assert calls[0][:4] == ["/usr/bin/codesign", "--force", "--sign", "-"]
    assert str(MODULE.ENTITLEMENTS) in calls[0]
    assert calls[1][1:4] == ["--display", "--entitlements", ":-"]
    assert calls[2][1:3] == ["--verify", "--strict"]


def test_sidestore_seed_rejects_missing_healthkit_entitlements(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app = tmp_path / "DailyReader.app"
    app.mkdir()

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess:
        stdout = plistlib.dumps({}) if "--display" in command else b""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(MODULE.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="missing required entitlements"):
        MODULE.sign_app_for_sidestore(app)


def test_previous_remote_versions_are_retained_only_when_safe(tmp_path: Path) -> None:
    token = TOKEN
    origin = "https://reader.example.test:8443"
    previous = MODULE.build_remote_source("0.1.41", 8, origin, token)
    previous_version = previous["apps"][0]["versions"][0]
    (tmp_path / "DailyReader-0.1.41.ipa").write_bytes(b"previous")
    MODULE.write_private_source(tmp_path / "remote-source.json", previous)

    retained = MODULE.load_previous_remote_versions(tmp_path, origin, token)
    updated = MODULE.build_remote_source("0.1.42", 20, origin, token, retained)

    assert retained == [previous_version]
    assert [item["version"] for item in updated["apps"][0]["versions"]] == [
        "0.1.42",
        "0.1.41",
    ]


def test_previous_remote_versions_are_dropped_after_token_rotation(tmp_path: Path) -> None:
    old_token = TOKEN
    new_token = OTHER_TOKEN
    origin = "https://reader.example.test:8443"
    previous = MODULE.build_remote_source("0.1.41", 10, origin, old_token)
    (tmp_path / "DailyReader-0.1.41.ipa").write_bytes(b"previous")
    MODULE.write_private_source(tmp_path / "remote-source.json", previous)

    assert MODULE.load_previous_remote_versions(tmp_path, origin, new_token) == []


def test_invalid_previous_version_metadata_is_not_republished(tmp_path: Path) -> None:
    token = TOKEN
    origin = "https://reader.example.test:8443"
    previous = MODULE.build_remote_source("0.1.41", 10, origin, token)
    previous["apps"][0]["versions"][0]["date"] = "invalid"
    (tmp_path / "DailyReader-0.1.41.ipa").write_bytes(b"0123456789")
    MODULE.write_private_source(tmp_path / "remote-source.json", previous)

    assert MODULE.load_previous_remote_versions(tmp_path, origin, token) == []


def test_unlisted_versioned_ipas_are_pruned(tmp_path: Path) -> None:
    retained = tmp_path / "DailyReader-0.1.42.ipa"
    stale = tmp_path / "DailyReader-0.1.1.ipa"
    retained.write_bytes(b"retained")
    stale.write_bytes(b"stale")

    MODULE.prune_unlisted_versioned_ipas(
        tmp_path,
        [{"version": "0.1.42"}],
    )

    assert retained.is_file()
    assert not stale.exists()
