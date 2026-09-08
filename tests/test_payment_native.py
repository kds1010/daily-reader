import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

from daily_reader.payment_history import import_csv, list_payments

ROOT = Path(__file__).resolve().parents[1]
IOS = ROOT / "ios/DailyReader/DailyReader"


def test_mac_allows_reading_selected_csv_without_write_access():
    entitlement_file = IOS.parent / "DailyReaderMac/DailyReaderMac.entitlements"
    entitlements = plistlib.loads(entitlement_file.read_bytes())
    assert entitlements["com.apple.security.files.user-selected.read-only"] is True
    assert "com.apple.security.files.user-selected.read-write" not in entitlements


@pytest.mark.skipif(shutil.which("xcrun") is None, reason="Xcode is unavailable")
def test_swift_decodes_real_payment_responses_and_nullable_fields(tmp_path):
    db = tmp_path / "payments.db"
    imported = import_csv(db, (ROOT / "tests/fixtures/paypay.csv").read_bytes())
    snapshot = list_payments(db)
    wire = tmp_path / "response.json"
    wire.write_text(json.dumps({"history": snapshot, "result": imported}))
    main = tmp_path / "main.swift"
    main.write_text('''import Foundation
struct Wire: Decodable {
    let history: PaymentHistoryResponse
    let result: PaymentImportResult
}
let data = try Data(contentsOf: URL(fileURLWithPath: CommandLine.arguments[1]))
let wire = try JSONDecoder().decode(Wire.self, from: data)
precondition(wire.result.added == 6 && wire.result.conflicts == 0)
precondition(wire.result.conflict_rows.isEmpty && !wire.result.file_duplicate)
precondition(wire.history.total == 6 && wire.history.offset == 0 && !wire.history.has_more)
precondition(wire.history.last_imported_at == wire.result.imported_at)
let row = wire.history.items.first { $0.transaction_id == "000000000000000000001" }!
precondition(row.outgoing_yen == 1200 && row.incoming_yen == nil)
precondition(row.occurred_at == "2026-09-08T10:15:03+09:00")
precondition(row.source == "paypay" && row.counterparty == "架空喫茶")
precondition(row.details["出金金額（円）"] == "1,200")
precondition(wire.history.items.contains { $0.transaction_id == "lp-demo-points" })
precondition(wire.history.items.contains { $0.details["通貨"] == "USD" })
print("PayPay API JSON decoded: 6 rows, exact IDs, nullable amounts, original metadata")
''')
    env = {**os.environ, "CLANG_MODULE_CACHE_PATH": str(tmp_path / "cache"),
           "SWIFT_MODULECACHE_PATH": str(tmp_path / "cache")}
    binary = tmp_path / "wire-test"
    compiled = subprocess.run(
        ["xcrun", "swiftc", str(IOS / "Models.swift"), str(main), "-o", str(binary)],
        capture_output=True, text=True, env=env,
    )
    assert compiled.returncode == 0, compiled.stderr
    ran = subprocess.run([str(binary), str(wire)], capture_output=True, text=True, env=env)
    assert ran.returncode == 0, ran.stderr
    assert "6 rows, exact IDs, nullable amounts" in ran.stdout
