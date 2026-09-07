import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from daily_reader import device_context
from daily_reader.conversations import _connect, list_location_events, store_location_events


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
precondition(parseISOTimestamp(recovered) != nil)
let base = Date(timeIntervalSince1970: floor(now.timeIntervalSince1970) - 120)
let a = base.addingTimeInterval(0.1), b = base.addingTimeInterval(0.8)
precondition(a.ISO8601Format() == b.ISO8601Format())
let precise = makePhoneMotionInterval(start: a, end: b, activity: "walking", confidence: "high")!
precondition(precise.start_at != precise.end_at)
precondition(makePhoneMotionInterval(start: b, end: a,
    activity: "walking", confidence: "high") == nil)
precondition(makePhoneMotionInterval(start: a, end: a.addingTimeInterval(0.00001),
    activity: "walking", confidence: "high") == nil)
let collapsed = PhoneMotionInterval(start_at: a.ISO8601Format(), end_at: b.ISO8601Format(),
    activity: "walking", confidence: "high")
let retained = PhoneMotionInterval(start_at: base.addingTimeInterval(-3).ISO8601Format(),
    end_at: base.addingTimeInterval(-1).ISO8601Format(), activity: "stationary", confidence: "high")
let repaired = repairLegacyQueuedMotionIntervals([collapsed, retained])
precondition(repaired.count == 1 && repaired.first?.start_at == retained.start_at)
let motionJSON = String(data: try JSONEncoder().encode(repaired + [precise]), encoding: .utf8)!
let output = ["current": current, "recovered": recovered,
    "legacy": legacy, "motion_json": motionJSON]
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
    captured = datetime.fromisoformat(stamps['current'])
    for timestamp in [stamps['current'], stamps['recovered']]:
        device_context.ingest(database, {
            'device_id': 'test-phone', 'captured_at': timestamp, 'timezone': 'Asia/Tokyo',
            'calendar': {'state': 'denied'},
            'motion': {
                'state': 'available',
                'start_at': (captured - timedelta(days=7)).isoformat(),
                'end_at': timestamp, 'items': json.loads(stamps['motion_json']),
            },
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
    with _connect(database) as connection:
        rows = connection.execute('SELECT start_at,end_at FROM device_motion_intervals').fetchall()
    assert len(rows) == 2
    durations = sorted(
        (datetime.fromisoformat(r[1]) - datetime.fromisoformat(r[0])).total_seconds()
        for r in rows
    )
    assert durations == pytest.approx([0.7, 2.0], abs=0.0011)
    with pytest.raises(ValueError, match='日時が不正'):
        device_context._date(stamps['legacy'])
