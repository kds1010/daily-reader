import json
import os
import subprocess
import traceback
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

SCRIPT = Path(__file__).parents[1] / "scripts/open_sidestore_remote_source.py"
SPEC = spec_from_file_location("open_sidestore_remote_source", SCRIPT)
assert SPEC and SPEC.loader
MODULE = module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
TOKEN = "a" * 42 + "A"


def test_load_source_and_open_it_without_printing_credential(
    tmp_path: Path,
    monkeypatch,
) -> None:
    token = TOKEN
    token_path = tmp_path / "token.txt"
    token_path.write_text(token + "\n", encoding="utf-8")
    os.chmod(token_path, 0o600)
    source_url = f"https://reader.example.test:8443/{token}/source.json"
    (tmp_path / "remote-source.json").write_text(
        json.dumps(
            {
                "subtitle": "個人用の外出先更新ソース",
                "sourceURL": source_url,
            }
        ),
        encoding="utf-8",
    )
    commands = []
    monkeypatch.setattr(
        MODULE.subprocess,
        "run",
        lambda command, **_kwargs: commands.append(command),
    )

    loaded_url = MODULE.load_source_url(tmp_path, token_path)
    MODULE.open_source("Test iPhone", "com.example.SideStore", loaded_url)

    assert len(commands) == 1
    command = commands[0]
    assert command[:7] == [
        "xcrun",
        "devicectl",
        "device",
        "process",
        "launch",
        "--quiet",
        "--device",
    ]
    deep_link = command[command.index("--payload-url") + 1]
    parsed = urlsplit(deep_link)
    assert parsed.scheme == "sidestore"
    assert parsed.netloc == "source"
    assert parse_qs(parsed.query) == {"url": [source_url]}
    assert command[-1] == "com.example.SideStore"


def test_open_source_redacts_credential_from_failure_traceback(monkeypatch) -> None:
    token = TOKEN
    source_url = f"https://reader.example.test:8443/{token}/source.json"

    def fail(command, **_kwargs):
        raise subprocess.CalledProcessError(1, command, stderr="device failed")

    monkeypatch.setattr(MODULE.subprocess, "run", fail)

    try:
        MODULE.open_source("Missing iPhone", "com.example.SideStore", source_url)
    except RuntimeError as error:
        rendered = "".join(traceback.format_exception(error))
    else:
        raise AssertionError("open_source should fail")

    assert token not in rendered
    assert "sidestore://" not in rendered
