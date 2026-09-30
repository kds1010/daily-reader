import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from daily_reader.gmail_oauth import ACTIVE, MESSAGES, ORIGIN, PREFIX

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_native_oauth_wire_states_and_browser_destination_validation(tmp_path):
    states = [{"status": status, "message": message} for status, message in MESSAGES.items()]
    start = {"status": "pending", "message": MESSAGES["pending"],
             "session_id": "test-session", "browser_url": ORIGIN + PREFIX + "open?ticket=test"}
    urls = ["http://sk-mins-mac-mini.tailc193b2.ts.net/api/gmail-auth/open",
            ORIGIN + ":8443" + PREFIX + "open", "https://evil.example/api/gmail-auth/open",
            ORIGIN + PREFIX + "callback", ORIGIN + PREFIX + "open#secret",
            "https://user@sk-mins-mac-mini.tailc193b2.ts.net/api/gmail-auth/open"]
    wire = tmp_path / "wire.json"
    wire.write_text(json.dumps({"states": states, "start": start, "active": sorted(ACTIVE),
                               "bad": [{**start, "browser_url": url} for url in urls]}))
    main = tmp_path / "main.swift"
    main.write_text('''import Foundation
struct Wire: Decodable {
    let states: [GmailAuthState]
    let start: GmailAuthState
    let active: [String]
    let bad: [GmailAuthState]
}
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let wire = try JSONDecoder().decode(Wire.self, from: data)
precondition(wire.start.session_id == "test-session")
precondition(wire.start.browserURL != nil)
precondition(wire.states.filter { $0.isActive }.map { $0.status }.sorted() == wire.active)
precondition(wire.states.filter { $0.canCancel }.map { $0.status } == ["pending"])
precondition(wire.states.allSatisfy { $0.session_id == nil && $0.browserURL == nil })
precondition(wire.bad.allSatisfy { $0.browserURL == nil })
print("OAuth wire states and browser destinations validated")
''')
    env = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(tmp_path / "cache"),
           "SWIFT_MODULECACHE_PATH": str(tmp_path / "cache")}
    binary = tmp_path / "oauth-wire"
    built = subprocess.run(["xcrun", "swiftc",
        str(ROOT / "ios/DailyReader/DailyReader/Models.swift"), str(main), "-o", str(binary)],
        capture_output=True, text=True, env=env)
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(binary), str(wire)], capture_output=True, text=True, env=env)
    assert ran.returncode == 0, ran.stderr
