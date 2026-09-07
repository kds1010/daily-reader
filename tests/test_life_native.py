import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from daily_reader import life_automation as auto
from daily_reader.life_assistant import create_entry, snapshot

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios/DailyReader"


def test_calendar_permission_is_declared_on_both_platforms():
    for target in ("DailyReader", "DailyReaderMac"):
        info = plistlib.loads((IOS / target / "Info.plist").read_bytes())
        assert info["NSCalendarsFullAccessUsageDescription"]
    entitlements = plistlib.loads((IOS / "DailyReaderMac/DailyReaderMac.entitlements").read_bytes())
    assert entitlements["com.apple.security.personal-information.calendars"] is True
    assert "com.apple.developer.healthkit" not in entitlements


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_native_models_decode_actual_life_api_and_encode_confirmed_request(tmp_path):
    db = tmp_path / "db"
    create_entry(db, {"kind": "task", "title": "確認する", "due_at": "2026-10-01T10:00:00+09:00"})
    profile = create_entry(db, {"kind": "profile", "title": "写真", "person_id": "self"})
    wire = tmp_path / "response.json"
    auto._put_draft(
        db,
        "test-draft",
        {
            "kind": "event",
            "title": "終了時間の確認",
            "detail": "会話の根拠",
            "start_at": "2027-10-10T01:00:00Z",
            "end_at": None,
            "timezone": "Asia/Tokyo",
            "source_type": "research",
            "source_id": "job",
            "source_index": 2,
        },
        {"type": "research", "entry_id": "job"},
        "終了日時を確認してください",
        False,
    )
    wire.write_text(
        json.dumps(
            {**snapshot(db), "automation": auto.settings(db), "drafts": auto.drafts(db)},
            ensure_ascii=False,
        )
    )
    source = (IOS / "DailyReader/LifeAssistant.swift").read_text().split("@MainActor", 1)[0]
    types = tmp_path / "LifeModels.swift"
    types.write_text(source)
    main = tmp_path / "main.swift"
    main.write_text("""import Foundation
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let snapshot = try JSONDecoder().decode(LifeSnapshot.self, from: data)
precondition(snapshot.entries.count == 2)
precondition(snapshot.automation?.enabled == true)
let draft = snapshot.drafts!.first!
precondition(draft.data.title == "終了時間の確認")
precondition(draft.source_index == 2)
let draftRequest = LifeRequest(draft.data)
precondition(draftRequest.start_at != nil && draftRequest.end_at == nil)
let task = snapshot.entries.first { $0.kind == "task" }!
precondition(lifeDate(task.due_at) != nil)
precondition(snapshot.people.first { $0.id == "self" }?.name == "自分")
var request = LifeRequest(task)
request.status = "completed"
let value = try JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as! [String: Any]
precondition(value["revision"] as? Int == 1)
precondition(value["remind_at"] is NSNull)
precondition(value["expires_at"] is NSNull)
precondition(value["status"] as? String == "completed")
precondition(value["due_at"] as? String == "2026-10-01T01:00:00+00:00")
precondition(LifeRequest(kind: "profile").person_id.isEmpty)
print("decoded and round-tripped")
""")
    binary = tmp_path / "test"
    env = {
        **os.environ,
        "CLANG_MODULE_CACHE_PATH": str(tmp_path / "cache"),
        "SWIFT_MODULECACHE_PATH": str(tmp_path / "cache"),
    }
    compiled = subprocess.run(
        [
            "xcrun",
            "swiftc",
            str(IOS / "DailyReader/Models.swift"),
            str(types),
            str(main),
            "-o",
            str(binary),
        ],
        capture_output=True,
        text=True,
        env=env,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run([str(binary), str(wire)], capture_output=True, text=True, env=env)
    assert ran.returncode == 0, ran.stderr
    assert "decoded and round-tripped" in ran.stdout
    assert profile["person_id"] == "self"
