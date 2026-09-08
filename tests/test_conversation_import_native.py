"""Run the native import queue and real URLSession uploads against an anonymous server."""

import json
import os
import shutil
import subprocess
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

IOS = Path(__file__).resolve().parents[1] / "ios/DailyReader/DailyReader"


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_native_import_queue_and_upload(tmp_path):
    seen = Counter()
    uploads = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            parsed = urlsplit(self.path)
            assert parsed.path == "/api/conversations/upload"
            query = parse_qs(parsed.query)
            filename = query["filename"][0]
            assert "recorded_at" not in query
            content = self.rfile.read(int(self.headers["Content-Length"]))
            assert self.headers["Content-Type"] == "audio/mpeg"
            uploads.append((filename, content))
            seen[filename] += 1
            time.sleep(0.15)
            failed = filename in {"2026-09-08_2026-09-08 15:26:25.mp3", "retry.mp3"}
            failed = failed and seen[filename] == 1
            body = {"error": "fixture failure"} if failed else {
                "id": filename, "filename": filename, "byte_size": len(content),
                "status": "pending", "created_at": "2026-09-08T00:00:00Z",
            }
            encoded = json.dumps(body).encode()
            self.send_response(503 if failed else 201)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    life_types = tmp_path / "LifeModels.swift"
    source = (IOS / "LifeAssistant.swift").read_text().split("@MainActor", 1)[0]
    for module in ("SwiftUI", "Combine", "EventKit", "UserNotifications"):
        source = source.replace(f"import {module}\n", "")
    life_types.write_text("import Foundation\n" + source)
    env = os.environ.copy()
    env["CLANG_MODULE_CACHE_PATH"] = str(tmp_path / "module-cache")
    env["SWIFT_MODULECACHE_PATH"] = str(tmp_path / "module-cache")
    binary = tmp_path / "import-contract"
    result = subprocess.run(
        ["xcrun", "swiftc", "-whole-module-optimization",
         str(IOS / "Models.swift"), str(IOS / "APIClient.swift"),
         str(IOS / "ConversationImports.swift"), str(IOS / "AppIntents.swift"),
         str(life_types), str(Path(__file__).parent / "swift/ConversationImportHarness.swift"),
         "-o", str(binary)], capture_output=True, text=True, env=env, timeout=600,
    )
    assert result.returncode == 0, result.stderr
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [str(binary), str(tmp_path / "files"), f"http://127.0.0.1:{server.server_port}"],
            capture_output=True, text=True, timeout=60, env=env,
        )
        assert result.returncode == 0, f"{result.stderr}\n{result.stdout}"
        print(result.stdout)
        assert len(uploads) == 7
        assert seen["2026-09-08_2026-09-08 15:26:25.mp3"] == 2
        assert uploads[0][1] == uploads[1][1] == b"ID3-anonymous-fixture"
        assert seen["retry.mp3"] == 2
        assert seen["shortcut-original.mp3"] == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
