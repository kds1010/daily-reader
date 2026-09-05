from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios" / "DailyReader" / "DailyReader"


def test_keyboard_dismissal_does_not_delay_touches_or_mutate_during_the_tap() -> None:
    source = (IOS / "DailyReaderApp.swift").read_text()

    assert "recognizer.delaysTouchesBegan = false" in source
    assert "recognizer.delaysTouchesEnded = false" in source
    assert "DispatchQueue.main.async" in source
    assert "window?.endEditing(true)" in source


def test_soan_comment_submission_has_explicit_focus_hit_area_and_feedback() -> None:
    source = (IOS / "SoanView.swift").read_text()

    assert "@FocusState private var commentFocused: Bool" in source
    assert ".focused($commentFocused)" in source
    assert "commentFocused = false" in source
    assert ".contentShape(Rectangle())" in source
    assert 'Text(loading ? "修正案を作成中" : "修正案を作る")' in source


def test_soan_sheet_keeps_errors_and_failed_saves_visible() -> None:
    source = (IOS / "SoanView.swift").read_text()

    assert 'Label(error, systemImage: "exclamationmark.triangle")' in source
    assert "if await saveCurrent() { showingTextEditor = false }" in source
    assert "private func saveCurrent() async -> Bool" in source
