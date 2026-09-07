import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_ios_api_url_builder_keeps_query_out_of_path(tmp_path: Path) -> None:
    source = Path(__file__).parents[1] / "ios/DailyReader/DailyReader/APIClient.swift"
    models = Path(__file__).parents[1] / "ios/DailyReader/DailyReader/Models.swift"
    main = tmp_path / "main.swift"
    main.write_text(
        """
import Foundation

let base = URL(string: "https://example.test/")!
let noQueryURL = makeAPIURL(baseURL: base, path: "api/agent-jobs")
precondition(noQueryURL.absoluteString == "https://example.test/api/agent-jobs")
precondition(!noQueryURL.absoluteString.hasSuffix("?"))
let url = makeAPIURL(
    baseURL: base,
    path: "api/tanomi/tasks",
    queryItems: [URLQueryItem(name: "limit", value: "50")]
)
precondition(url.absoluteString == "https://example.test/api/tanomi/tasks?limit=50")
precondition(!url.absoluteString.contains("%3F"))
let recorded = LocationEvent(timestamp: "2026-09-05T15:26:25.123+09:00",
    latitude: 35, longitude: 139, horizontal_accuracy: 20, is_approximate: false)
let utc = LocationEvent(timestamp: "2026-09-05T06:26:25.123000+00:00",
    latitude: 35, longitude: 139, horizontal_accuracy: 20, is_approximate: false)
precondition(recorded.date != nil && recorded.date == utc.date)
let plain = LocationEvent(timestamp: "2026-09-05T06:26:25Z",
    latitude: 35, longitude: 139, horizontal_accuracy: 20, is_approximate: true)
precondition(plain.date != nil)
let invalid = LocationEvent(timestamp: "invalid",
    latitude: 35, longitude: 139, horizontal_accuracy: 20, is_approximate: false)
precondition(invalid.date == nil)
let roundtrip = try JSONDecoder().decode(LocationEvent.self, from: JSONEncoder().encode(recorded))
precondition(roundtrip == recorded)
print(url.absoluteString)
""",
        encoding="utf-8",
    )
    binary = tmp_path / "url-builder-test"
    module_cache = tmp_path / "module-cache"
    module_cache.mkdir()
    environment = os.environ.copy()
    environment["CLANG_MODULE_CACHE_PATH"] = str(module_cache)
    environment["SWIFT_MODULECACHE_PATH"] = str(module_cache)
    compile_result = subprocess.run(
        ["xcrun", "swiftc", str(models), str(source), str(main), "-o", str(binary)],
        capture_output=True,
        text=True,
        env=environment,
        check=False,
    )
    assert compile_result.returncode == 0, compile_result.stderr
    run_result = subprocess.run(
        [str(binary)], capture_output=True, text=True, check=False, env=environment
    )
    assert run_result.returncode == 0, run_result.stderr
    assert run_result.stdout.strip() == "https://example.test/api/tanomi/tasks?limit=50"
