import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from daily_reader import device_context
from daily_reader.conversations import list_location_events, store_location_events


@pytest.mark.skipif(shutil.which('xcrun') is None, reason='Xcode is unavailable')
def test_native_utc_timestamps_and_recovered_queues_reach_backend(tmp_path):
    root = Path(__file__).parents[1] / 'ios/DailyReader/DailyReader'
    main = tmp_path / 'main.swift'
    main.write_text('''import Foundation
let now = Date.now
let current = preciseUTCTimestamp(now)
precondition(current.hasSuffix("Z"))
let legacy = String(current.dropLast())
let recovered = repairLegacyQueuedUTCTimestamp(legacy)
precondition(recovered == current)
for existing in ["2026-09-07T17:41:42.123+09:00", "2026-09-07T08:41:42Z",
                 "invalid", "2026-09-07", "2026-09-07T08:41:42"] {
    precondition(repairLegacyQueuedUTCTimestamp(existing) == existing)
}
let event = LocationEvent(timestamp: recovered, latitude: 35, longitude: 139,
    horizontal_accuracy: 10, is_approximate: false)
precondition(event.date != nil)
precondition(abs(event.date!.timeIntervalSince(now)) < 0.001)
let output = ["current": current, "recovered": recovered, "legacy": legacy]
print(String(data: try JSONEncoder().encode(output), encoding: .utf8)!)
''')
    env = os.environ.copy()
    env['CLANG_MODULE_CACHE_PATH'] = str(tmp_path / 'module-cache')
    env['SWIFT_MODULECACHE_PATH'] = str(tmp_path / 'module-cache')
    binary = tmp_path / 'timestamps'
    result = subprocess.run(
        ['xcrun', 'swiftc', str(root / 'Models.swift'), str(root / 'APIClient.swift'),
         str(main), '-o', str(binary)], capture_output=True, text=True, env=env,
    )
    assert result.returncode == 0, result.stderr
    result = subprocess.run([str(binary)], capture_output=True, text=True, env=env)
    assert result.returncode == 0, result.stderr
    stamps = json.loads(result.stdout)
    database = tmp_path / 'context.sqlite3'
    for timestamp in [stamps['current'], stamps['recovered']]:
        device_context.ingest(database, {
            'device_id': 'test-phone', 'captured_at': timestamp, 'timezone': 'Asia/Tokyo',
            'calendar': {'state': 'denied'}, 'motion': {'state': 'denied'},
        })
        store_location_events(database, [{
            'timestamp': timestamp, 'latitude': 35, 'longitude': 139,
            'horizontal_accuracy': 10, 'is_approximate': False,
        }])
    captured = datetime.fromisoformat(stamps['current'])
    locations = list_location_events(
        database, (captured - timedelta(seconds=1)).isoformat(),
        (captured + timedelta(seconds=1)).isoformat(),
    )
    assert locations['total'] == 1
    with pytest.raises(ValueError, match='日時が不正'):
        device_context._date(stamps['legacy'])
