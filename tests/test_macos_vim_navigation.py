import os
import shutil
import subprocess
from pathlib import Path

import pytest


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_macos_vim_key_parser_maps_navigation_sequences(tmp_path: Path) -> None:
    models = Path(__file__).parents[1] / "ios/DailyReader/DailyReader/Models.swift"
    main = tmp_path / "main.swift"
    main.write_text(
        r'''
import Foundation

var parser = MacVimKeyParser(sequenceTimeout: 1)
var now: TimeInterval = 10

func press(
    _ stroke: MacVimKeyStroke,
    after delay: TimeInterval = 0.05
) -> MacAgentNavigationCommand? {
    now += delay
    return parser.handle(stroke, at: now)
}

precondition(press(.character("j")) == .move(1))
precondition(press(.character("k")) == .move(-1))
precondition(press(.character("h")) == .close)
precondition(press(.character("l")) == .open)
precondition(press(.enter) == .open)
precondition(press(.escape) == .close)
precondition(press(.controlD) == .page(1))
precondition(press(.controlU) == .page(-1))
precondition(press(.shiftedG) == .last)

precondition(press(.character("g")) == nil)
precondition(press(.character("g")) == .first)
precondition(press(.character("z")) == nil)
precondition(press(.character("t")) == .alignTop)
precondition(press(.character("z")) == nil)
precondition(press(.character("z")) == .alignCenter)
precondition(press(.character("z")) == nil)
precondition(press(.character("b")) == .alignBottom)
precondition(press(.character("d")) == nil)
precondition(press(.character("d")) == .archive(.next))
precondition(press(.character("d")) == nil)
precondition(press(.character("j")) == .archive(.next))
precondition(press(.character("d")) == nil)
precondition(press(.character("k")) == .archive(.previous))

precondition(press(.character("d")) == nil)
precondition(press(.character("j"), after: 1.1) == .move(1))
print("macOS Vim key sequences mapped")
''',
        encoding="utf-8",
    )
    binary = tmp_path / "macos-vim-navigation-test"
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
    assert run_result.stdout.strip() == "macOS Vim key sequences mapped"
