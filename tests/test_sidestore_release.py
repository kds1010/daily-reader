from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts/build_sidestore_release.py"
SPEC = spec_from_file_location("build_sidestore_release", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def test_build_source_uses_versioned_ipa_metadata() -> None:
    source = MODULE.build_source("0.1.42", 12345, "https://example.test/sidestore/")
    app = source["apps"][0]
    version = app["versions"][0]

    assert source["identifier"] == "net.skmin.DailyReader.source"
    assert source["sourceURL"] == "https://example.test/sidestore/source.json"
    assert app["bundleIdentifier"] == "net.skmin.DailyReader"
    assert version["version"] == "0.1.42"
    assert version["downloadURL"] == "https://example.test/sidestore/DailyReader.ipa"
    assert version["size"] == 12345


def test_default_source_is_private_lan_url() -> None:
    assert Path(__file__).parents[1] / "data/sidestore" == MODULE.DEFAULT_OUTPUT
    assert MODULE.DEFAULT_BASE_URL == "http://sk-mins-Mac-mini.local:8788"
