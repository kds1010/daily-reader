import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_tanomi_models_decode_numeric_upstream_response(tmp_path: Path) -> None:
    models = Path(__file__).parents[1] / "ios/DailyReader/DailyReader/Models.swift"
    main = tmp_path / "main.swift"
    main.write_text(
        r'''
import Foundation

let decoder = JSONDecoder()
let health = try decoder.decode(
    TanomiHealth.self,
    from: Data(#"{"ok":true,"running":0,"log_dir":"/var/empty"}"#.utf8)
)
precondition(health.ok)
precondition(health.running == 0)

let usage = try decoder.decode(
    TanomiUsage.self,
    from: Data(#"""
{
  "limits": {
    "five_hour": {"utilization": 5.0, "resets_at": "2026-08-30T15:30:00.298366+00:00"},
    "seven_day": {"utilization": 76.0, "resets_at": "2026-09-01T00:00:00.298386+00:00"}
  },
  "running": 0
}
"""#.utf8)
)
precondition(usage.running == 0)
precondition(usage.limits["five_hour"]?.utilization == 5.0)
precondition(usage.limits["seven_day"]?.utilization == 76.0)
precondition(usage.limits["seven_day"]?.resetsAt?.iso8601Date != nil)

let repositories = try decoder.decode(
    [TanomiRepository].self,
    from: Data(#"[{"path":"/workspace/tonoi","label":"tonoi"}]"#.utf8)
)
precondition(repositories.count == 1)
precondition(repositories[0].path == "/workspace/tonoi")
precondition(repositories[0].label == "tonoi")

let buckets = try decoder.decode(
    TanomiBuckets.self,
    from: Data(#"""
{
  "tasks":[{
    "id":"0123456789ab",
    "title":"確認",
    "prompt":"確認",
    "repo_path":"/workspace/tonoi",
    "status":"done",
    "result":"完了",
    "created_at":1788070912.0,
    "started_at":1788070913.5,
    "ended_at":1788070920.25
  }],
  "archived":[{
    "id":"abcdefabcdef",
    "status":"stopped",
    "created_at":null,
    "started_at":null,
    "ended_at":null
  }],
  "deleted":[{"id":"fedcba654321","status":"done"}]
}
"""#.utf8)
)
precondition(buckets.tasks.count == 1)
precondition(buckets.archived.count == 1)
precondition(buckets.deleted.count == 1)
precondition(buckets.archived[0].createdAt == nil)
precondition(buckets.archived[0].startedAt == nil)
precondition(buckets.archived[0].endedAt == nil)
let task = buckets.tasks[0]
precondition(task.createdAt == 1788070912.0)
precondition(task.startedAt == 1788070913.5)
precondition(task.endedAt == 1788070920.25)
    precondition(task.updatedDate == Date(timeIntervalSince1970: 1788070920.25))
    precondition(task.displayRepository == "tonoi")
    precondition(task.canContinue == false)
let followUp = try decoder.decode(
    TanomiTask.self,
    from: Data(#"{"id":"0123456789ab","status":"done","session_id":"session"}"#.utf8)
)
precondition(followUp.canContinue)
let sameBuckets = try decoder.decode(TanomiBuckets.self, from: Data(#"""
{
  "tasks":[{"id":"0123456789ab","status":"done","result":"完了"}],
  "archived":[],
  "deleted":[]
}
"""#.utf8))
precondition(buckets.tasks[0] == buckets.tasks[0])
precondition(buckets.tasks[0] != sameBuckets.tasks[0])
print("tanomi contract decoded")
''',
        encoding="utf-8",
    )
    binary = tmp_path / "tanomi-models-test"
    module_cache = tmp_path / "module-cache"
    module_cache.mkdir()
    environment = os.environ.copy()
    environment["CLANG_MODULE_CACHE_PATH"] = str(module_cache)
    environment["SWIFT_MODULECACHE_PATH"] = str(module_cache)
    compile_result = subprocess.run(
        ["xcrun", "swiftc", str(models), str(main), "-o", str(binary)],
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
    assert run_result.stdout.strip() == "tanomi contract decoded"
