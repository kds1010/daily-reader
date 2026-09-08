"""Run shared native cloud-import state and AppIntent against anonymous HTTP."""

import json
import os
import shutil
import subprocess
import threading
import time
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

IOS = Path(__file__).resolve().parents[1] / "ios/DailyReader/DailyReader"


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_native_soundcore_import_contract(tmp_path):
    requests = []
    jobs = []
    reads = 0
    attempts = 0
    retries = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def respond(self, status, body):
            encoded = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            # Native client intentionally cancels the stale GET.
            with suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(encoded)

        def do_GET(self):
            nonlocal reads
            assert self.path == "/api/conversations/soundcore-imports"
            reads += 1
            current = reads
            if current == 3:
                jobs[1]["status"] = "downloading"
            elif current == 4:
                jobs[0].update(status="failed", error="匿名の取得失敗")
                jobs[1].update(status="completed", recording_id="recording-2")
            elif current == 5:
                jobs[0].update(status="completed", recording_id="recording-1")
            if current == 6:
                self.respond(503, {"error": "匿名の状況取得失敗"})
                return
            snapshot = json.loads(json.dumps(jobs))
            time.sleep(0.15)
            self.respond(200, {"items": snapshot})

        def do_POST(self):
            nonlocal attempts, retries
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append((self.path, body))
            assert self.headers["Content-Type"] == "application/json"
            if self.path == "/api/conversations/soundcore-imports":
                attempts += 1
                if attempts == 1:
                    self.respond(503, {"error": "匿名の受付失敗"})
                    return
                expected = "ABC12345" if attempts == 2 else "DEF67890"
                assert body == {"url": f"https://speaker-eu.eufylife.com/knowledge/sharelink/{expected}"}
                job = {
                    "id": f"job-{len(jobs) + 1}", "status": "queued",
                    "recording_id": None, "error": None,
                    "created_at": "2026-09-09T00:00:00Z", "updated_at": None,
                }
                jobs.append(job)
                self.respond(202, job)
            elif self.path == "/api/conversations/soundcore-imports/job-1/retry":
                assert body == {}
                retries += 1
                time.sleep(0.15)
                if retries == 1:
                    self.respond(503, {"error": "匿名の再試行失敗"})
                    return
                jobs[0].update(status="queued", error=None)
                self.respond(202, jobs[0])
            else:
                raise AssertionError("unexpected endpoint")

    life = tmp_path / "LifeModels.swift"
    source = (IOS / "LifeAssistant.swift").read_text().split("@MainActor", 1)[0]
    for module in ("SwiftUI", "Combine", "EventKit", "UserNotifications"):
        source = source.replace(f"import {module}\n", "")
    life.write_text("import Foundation\n" + source)
    env = os.environ.copy()
    env["CLANG_MODULE_CACHE_PATH"] = str(tmp_path / "module-cache")
    env["SWIFT_MODULECACHE_PATH"] = str(tmp_path / "module-cache")
    binary = tmp_path / "soundcore-import-contract"
    result = subprocess.run(
        ["xcrun", "swiftc", "-whole-module-optimization",
         str(IOS / "Models.swift"), str(IOS / "APIClient.swift"),
         str(IOS / "ConversationImports.swift"), str(IOS / "AppIntents.swift"),
         str(life), str(Path(__file__).parent / "swift/SoundcoreImportHarness.swift"),
         "-o", str(binary)], capture_output=True, text=True, env=env, timeout=600,
    )
    assert result.returncode == 0, result.stderr
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = subprocess.run(
            [str(binary), f"http://127.0.0.1:{server.server_port}"],
            capture_output=True, text=True, env=env, timeout=60,
        )
        assert result.returncode == 0, f"{result.stderr}\n{result.stdout}"
        print(result.stdout)
        assert len(requests) == 5
        assert reads == 6 and attempts == 3 and retries == 2
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_cloud_import_view_preserves_manual_import_and_foreground_polling():
    source = (IOS / "RootView.swift").read_text()
    assert "SoundcoreImportSection(imports: soundcore, isFixture: model.isFixture)" in source
    assert '"soundcore_cloud_timestamp": "Soundcoreクラウドの日時"' in source
    assert 'scenePhase == .active && model.selectedTab == 4 && !model.isFixture' in source
    assert "while !Task.isCancelled, hasPendingProcessing" in source
    assert "async let recordings: Void = model.refreshConversations()" in source
    assert "allowedContentTypes: [.mp3, .plainText]" in source
    assert "UIPasteboard" not in source and "NSPasteboard.general.string" not in source
