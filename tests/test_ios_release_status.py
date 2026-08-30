import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_deployment_model_decodes_platform_release_versions(tmp_path: Path) -> None:
    models = Path(__file__).parents[1] / "ios/DailyReader/DailyReader/Models.swift"
    main = tmp_path / "main.swift"
    main.write_text(
        r'''
import Foundation

let payload = Data(#"""
{
  "version":"0.1.0+abc123def456",
  "deployed_at":"2026-08-30T10:00:00+00:00",
  "ios_release_version":"0.1.41",
  "macos_release_version":"0.1.42"
}
"""#.utf8)
let deployment = try JSONDecoder().decode(DeploymentInfo.self, from: payload)
precondition(deployment.iOSReleaseVersion == "0.1.41")
precondition(deployment.macOSReleaseVersion == "0.1.42")
#if os(macOS)
precondition(deployment.nativeReleaseVersion == "0.1.42")
#endif
print("native release versions decoded")
''',
        encoding="utf-8",
    )
    binary = tmp_path / "release-status-test"
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
    assert run_result.stdout.strip() == "native release versions decoded"
