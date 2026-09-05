import plistlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios" / "DailyReader" / "DailyReader"


def test_iphone_registers_as_an_alternate_mp3_viewer() -> None:
    with (IOS / "Info.plist").open("rb") as file:
        info = plistlib.load(file)

    mp3_types = [
        item
        for item in info["CFBundleDocumentTypes"]
        if "public.mp3" in item.get("LSItemContentTypes", [])
    ]
    assert mp3_types == [
        {
            "CFBundleTypeName": "MP3 Audio",
            "CFBundleTypeRole": "Viewer",
            "LSHandlerRank": "Alternate",
            "LSItemContentTypes": ["public.mp3"],
        }
    ]
    assert info["LSSupportsOpeningDocumentsInPlace"] is False
    assert "UISupportsDocumentBrowser" not in info
    assert "UIFileSharingEnabled" not in info


def test_shared_mp3_uses_the_existing_recording_upload_flow() -> None:
    app_source = (IOS / "DailyReaderApp.swift").read_text(encoding="utf-8")
    model_source = (IOS / "AppModel.swift").read_text(encoding="utf-8")

    assert ".onOpenURL { url in" in app_source
    assert "await model.importSharedRecording(url)" in app_source
    assert "func importSharedRecording(_ url: URL) async" in model_source
    assert "url.isFileURL" in model_source
    assert "conforms(to: .mp3) == true" in model_source
    assert "selectedTab = 4" in model_source
    assert "if await importConversationFile(url), removesInboxCopy" in model_source
    assert "FileManager.default.removeItem(at: url)" in model_source
    assert 'appending(path: "Inbox", directoryHint: .isDirectory)' in model_source
