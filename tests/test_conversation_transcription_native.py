from __future__ import annotations

import io
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from daily_reader.conversations import get_recording, store_transcript


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_native_decodes_old_and_new_transcription_metadata(tmp_path):
    db = tmp_path / "db"
    record = store_transcript(db, io.BytesIO(b"text"), 4, "test.txt")
    payload = get_recording(db, record["id"])
    old = {k: v for k, v in payload.items() if not k.startswith("transcription_")}
    payload["transcription_metadata"] = {"model": "large-v3", "warnings": ["話者未判定です"]}
    payload["transcription_needs_review"] = 1
    wire = tmp_path / "recordings.json"
    wire.write_text(json.dumps([old, payload]))
    main = tmp_path / "main.swift"
    main.write_text("""
import Foundation
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let rows = try JSONDecoder().decode([ConversationRecording].self, from: data)
precondition(rows[0].transcriptionMetadata == nil)
precondition(rows[0].transcriptionNeedsReview == nil)
precondition(rows[1].transcriptionMetadata?.model == "large-v3")
precondition(rows[1].transcriptionMetadata?.warnings == ["話者未判定です"])
precondition(rows[1].transcriptionNeedsReview == 1)
print("transcription wire compatibility passed")
""")
    models = Path(__file__).resolve().parents[1] / "ios/DailyReader/DailyReader/Models.swift"
    binary = tmp_path / "verify"
    subprocess.run(
        ["xcrun", "swiftc", str(models), str(main), "-o", str(binary)],
        check=True,
        capture_output=True,
        text=True,
    )
    result = subprocess.run([str(binary), str(wire)], check=True, capture_output=True, text=True)
    assert "compatibility passed" in result.stdout
