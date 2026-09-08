"""Run the actual Swift wire models and edit state, without any personal data or network."""

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

import pytest

from daily_reader import diary, life_assistant
from daily_reader.daily_planner import create_task, set_task_completion

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "ios/DailyReader/DailyReader/Diary.swift"


def swift(tmp_path, source, main, arguments=()):
    source_path, main_path = tmp_path / "Diary.swift", tmp_path / "Run.swift"
    source_path.write_text(source)
    main_path.write_text(main)
    output = tmp_path / "check"
    subprocess.run(
        [
            "xcrun",
            "swiftc",
            str(source_path),
            str(main_path),
            "-o",
            str(output),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        [str(output), *map(str, arguments)],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_diary_swift_decodes_real_api_and_encodes_edit(tmp_path):
    planner, conversations = tmp_path / "planner.db", tmp_path / "conversations.db"
    now = datetime.fromisoformat("2026-09-08T09:00:00+09:00")
    diary.settings(planner, now)
    with life_assistant.connect(conversations):
        pass
    task = create_task(planner, {"title": "本を返す"}, now)
    set_task_completion(planner, task["id"], True, now.date(), now)
    entry = diary.generate(planner, conversations, now.date(), revision=0, now=now)
    entry = diary.save(planner, {"date": str(now.date()), "revision": 1, "body": "本人の追記"})
    wire = tmp_path / "response.json"
    wire.write_text(json.dumps(entry, ensure_ascii=False))
    source = SOURCE.read_text().split("// MARK: - Native state")[0]
    result = swift(
        tmp_path,
        source,
        """import Foundation
@main struct Run {
    static func main() throws {
        let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
        let response = try JSONDecoder().decode(DiaryEnvelope.self, from: data)
        precondition(response.state == "ready")
        precondition(response.entry!.body == "本人の追記")
        precondition(response.entry!.edited_sources!.items.first!.title == "本を返す")
        precondition(response.history.first!.revision == 1)
        let request = DiaryRequest(
            date: response.date, revision: response.entry!.revision, body: "次の追記")
        print(String(data: try JSONEncoder().encode(request), encoding: .utf8)!)
    }
}
""",
        [wire],
    )
    saved = diary.save(planner, json.loads(result.stdout))
    assert saved["entry"]["body"] == "次の追記"
    assert saved["entry"]["revision"] == 3


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_native_edit_buffers_conflict_delete_and_fixtures_never_call_api(tmp_path):
    source = SOURCE.read_text().split("struct DiaryView: View")[0]
    result = swift(
        tmp_path,
        source,
        """import Foundation
actor APIClient {
    static let shared = APIClient()
    func get<T: Decodable>(_ path: String, queryItems: [URLQueryItem] = []) async throws -> T {
        fatalError("Fixture called the real API")
    }
    func post<T: Encodable, R: Decodable>(
        _ path: String, body: T, as type: R.Type
    ) async throws -> R {
        fatalError("Fixture called the real API")
    }
}
enum APIClientError: LocalizedError {
    case server(String)
    var errorDescription: String? { if case .server(let value) = self { return value }; return nil }
}
@main struct Run {
    @MainActor static func main() async {
        let store = DiaryStore()
        store.configureFixture("in-flight")
        await store.refresh()
        let firstDay = store.selectedDate
        store.text = "未保存の入力"
        await store.refresh()
        precondition(store.text == "未保存の入力")
        store.selectedDate = firstDay.addingTimeInterval(-86400)
        await store.refresh()
        precondition(!store.hasEdits)
        store.text = "別の日の入力"
        store.selectedDate = firstDay
        await store.refresh()
        precondition(store.text == "未保存の入力")
        await store.save()
        precondition(store.conflict && store.hasEdits && store.error != nil)
        let confirmed = store.current!.entry!.revision
        await store.save(confirmedRevision: confirmed)
        precondition(!store.hasEdits && !store.conflict)
        precondition(store.text == "未保存の入力")
        await store.setAutomatic(false)
        precondition(store.policy?.enabled == false)
        await store.delete()
        precondition(store.current?.state == "deleted")
        await store.refresh()
        precondition(store.current?.state == "deleted")
        await store.generate()
        precondition(store.current?.state == "ready")
        let empty = DiaryStore()
        empty.configureFixture("empty")
        await empty.refresh()
        precondition(empty.current?.state == "missing")
        await empty.generate()
        precondition(empty.current?.state == "ready")
        let failure = DiaryStore()
        failure.configureFixture("partial-failure")
        await failure.refresh()
        precondition(failure.error != nil)
        print("diary native state verified")
    }
}
""",
    )
    assert "diary native state verified" in result.stdout
