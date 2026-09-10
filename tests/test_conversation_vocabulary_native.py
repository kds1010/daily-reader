"""Exercise vocabulary/feedback state and the actual API client with anonymous responses."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios/DailyReader/DailyReader"


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_vocabulary_feedback_native_contract(tmp_path):
    state = tmp_path / "VocabularyState.swift"
    state.write_text(
        (IOS / "ConversationVocabulary.swift").read_text().split("// MARK: - Views")[0]
        .replace("import SwiftUI", "import Combine")
    )
    env = os.environ.copy()
    env["CLANG_MODULE_CACHE_PATH"] = str(tmp_path / "cache")
    binary = tmp_path / "vocabulary-contract"
    compiled = subprocess.run(
        ["xcrun", "swiftc", "-whole-module-optimization", str(IOS / "Models.swift"),
         str(IOS / "APIClient.swift"), str(state),
         str(ROOT / "tests/swift/ConversationVocabularyHarness.swift"), "-o", str(binary)],
        capture_output=True, text=True, env=env, timeout=600,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "PASS vocabulary and feedback" in result.stdout


def test_vocabulary_ui_preserves_original_and_limits_scope():
    source = (IOS / "RootView.swift").read_text()
    controls = (IOS / "ConversationVocabulary.swift").read_text()
    assert 'Label("文字起こしの用語辞書"' in source
    assert 'Text(verbatim: utterance.text)' in source
    assert 'if let corrected = utterance.userCorrectedText' in source
    assert 'await reload(afterMutation: true)' in source
    assert 'if model.isFixture {' in source
    assert '既存の録音を再解析' in controls
    assert 'すべての登録語が毎回使われるとは限りません' in controls
    assert '発言全文ではなく' in controls
    assert '最新の内容から編集し直す' in controls


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_real_vocabulary_feedback_wire_decodes_in_swift(tmp_path):
    import io
    import json

    # The storage/engine change is integrated separately from native UI work.
    vocabulary = pytest.importorskip("daily_reader.conversation_vocabulary")
    from daily_reader import conversations

    database = tmp_path / "anonymous.sqlite3"
    term = vocabulary.save_term(database, {
        "canonical": "星見計画", "reading": "ほしみけいかく",
        "aliases": ["星身計画"], "enabled": True,
    })
    raw = "星身計画を確認します。".encode()
    recording = conversations.store_transcript(
        database, io.BytesIO(raw), len(raw), "example.txt")
    original = conversations.get_recording(database, recording["id"])
    utterance = original["utterances"][0]
    saved = vocabulary.save_feedback(database, recording["id"], utterance["id"], {
        "expected_original_text": utterance["text"], "expected_feedback_revision": 0,
        "corrected_text": "星見計画を確認します。",
    })
    after_save = conversations.get_recording(database, recording["id"])
    reset = vocabulary.save_feedback(database, recording["id"], utterance["id"], {
        "expected_original_text": utterance["text"],
        "expected_feedback_revision": saved["feedback_revision"], "reset": True,
    })
    after_reset = conversations.get_recording(database, recording["id"])
    wire = tmp_path / "anonymous-wire.json"
    wire.write_text(json.dumps({
        "vocabulary": vocabulary.list_terms(database), "term": {"term": term},
        "saved": saved, "reset": reset, "recordings": [original, after_save, after_reset],
    }, ensure_ascii=False))
    harness = tmp_path / "main.swift"
    harness.write_text('''import Foundation
struct Wire: Decodable {
    let vocabulary: ConversationVocabularyEnvelope
    let term: ConversationVocabularyResponse
    let saved: ConversationFeedbackResponse
    let reset: ConversationFeedbackResponse
    let recordings: [ConversationRecording]
}
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let wire = try JSONDecoder().decode(Wire.self, from: data)
precondition(wire.vocabulary.terms[0].id == wire.term.term.id)
precondition(wire.term.term.canonical == "星見計画" && wire.term.term.aliases == ["星身計画"])
let rows = wire.recordings.map { $0.utterances![0] }
precondition(rows[0].userCorrection == nil && rows[0].feedbackRevision == 0)
precondition(rows[1].userCorrectedText == "星見計画を確認します。")
precondition(rows[1].feedbackRevision == wire.saved.feedback_revision)
precondition(rows[2].userCorrection == nil)
precondition(rows[2].feedbackRevision == wire.reset.feedback_revision)
precondition(rows[2].feedbackRevision > rows[1].feedbackRevision)
precondition(rows.allSatisfy {
    $0.text == rows[0].text && $0.id == rows[0].id
    && $0.startSeconds == rows[0].startSeconds && $0.endSeconds == rows[0].endSeconds
})
print("PASS real vocabulary feedback wire")
''')
    binary = tmp_path / "wire-check"
    env = os.environ.copy()
    env["CLANG_MODULE_CACHE_PATH"] = str(tmp_path / "wire-cache")
    compiled = subprocess.run(
        ["xcrun", "swiftc", str(IOS / "Models.swift"), str(harness), "-o", str(binary)],
        capture_output=True, text=True, env=env, timeout=600,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run([str(binary), str(wire)], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert "PASS real vocabulary feedback wire" in result.stdout
