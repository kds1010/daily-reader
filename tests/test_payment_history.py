import csv
import io
import json
import sqlite3
import stat
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from daily_reader import payment_history as payments

FIXTURE = Path(__file__).parent / "fixtures/paypay.csv"


def csv_bytes(*rows, headers=payments.HEADERS):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(headers)
    writer.writerows(rows)
    return output.getvalue().encode()


def row(identifier="123456789012345678901", **overrides):
    value = dict.fromkeys(payments.HEADERS, "-")
    value.update({"取引日": "2026/09/08 10:15:03", "出金金額（円）": "1,200",
                  "取引内容": "支払い", "取引先": "架空喫茶", "取引方法": "PayPay残高",
                  "取引番号": identifier, **overrides})
    return [value[h] for h in payments.HEADERS]


def test_official_format_fixture_and_private_storage(tmp_path):
    db = tmp_path / "payments.sqlite3"
    result = payments.import_csv(db, FIXTURE.read_bytes())
    assert (result["total"], result["added"], result["conflicts"]) == (6, 6, 0)
    snapshot = payments.list_payments(db)
    assert snapshot["total"] == 6
    assert snapshot["last_imported_at"] == result["imported_at"]
    items = {item["transaction_id"]: item for item in snapshot["items"]}
    assert items["000000000000000000001"]["outgoing_yen"] == 1200
    assert items["000000000000000000001"]["incoming_yen"] is None
    assert items["000000000000000000003"]["incoming_yen"] == 5000
    assert items["000000000000000000003"]["occurred_at"] == "2026-09-08T11:00:00+09:00"
    assert items["000000000000000000004"]["kind"] == "返金"
    assert items["lp-demo-points"]["kind"] == "ポイント獲得"
    assert items["000000000000000000005"]["details"]["通貨"] == "USD"
    assert stat.S_IMODE(db.stat().st_mode) == 0o600


def test_resend_overlap_and_semantic_formatting(tmp_path):
    db = tmp_path / "db"
    data = csv_bytes(row("one"), row("two"))
    first = payments.import_csv(db, data)
    repeat = payments.import_csv(db, data)
    assert repeat["added"] == 0 and repeat["duplicates"] == 2
    assert repeat["file_duplicate"]
    assert repeat["imported_at"] == first["imported_at"]
    overlap = payments.import_csv(db, csv_bytes(row("two"), row("three")))
    assert overlap["added"] == overlap["duplicates"] == 1
    reformatted = payments.import_csv(db, csv_bytes(row("one", **{"出金金額（円）": "1200"})))
    assert reformatted["duplicates"] == 1 and reformatted["conflicts"] == 0
    assert payments.list_payments(db)["total"] == 3


def test_conflicts_do_not_overwrite_and_repeat_preserves_warning(tmp_path):
    db = tmp_path / "db"
    payments.import_csv(db, csv_bytes(row("one")))
    data = csv_bytes(row("one", **{"出金金額（円）": "500"}), row("two"))
    result = payments.import_csv(db, data)
    assert result["added"] == result["conflicts"] == 1
    assert result["conflict_rows"] == [2]
    repeat = payments.import_csv(db, data)
    assert repeat["conflicts"] == repeat["duplicates"] == 1
    original = next(x for x in payments.list_payments(db)["items"] if x["transaction_id"] == "one")
    assert original["outgoing_yen"] == 1200


def test_idless_rows_are_preserved_with_warning_across_files(tmp_path):
    db = tmp_path / "db"
    data = csv_bytes(row("-"), row("-"))
    result = payments.import_csv(db, data)
    assert result["added"] == result["missing_ids"] == 2
    assert payments.import_csv(db, data)["duplicates"] == 2
    result = payments.import_csv(db, csv_bytes(row("-")))
    assert result["added"] == result["missing_ids"] == 1
    snapshot = payments.list_payments(db)
    assert snapshot["total"] == snapshot["missing_ids"] == 3
    assert len({item["id"] for item in snapshot["items"]}) == 3


def test_duplicates_and_conflicts_within_one_file(tmp_path):
    result = payments.import_csv(tmp_path / "db", csv_bytes(
        row("one"), row("one"), row("one", **{"取引先": "別のお店"}),
    ))
    assert (result["added"], result["duplicates"], result["conflicts"]) == (1, 1, 1)


@pytest.mark.parametrize("overrides", [
    {"取引日": "2026/02/30 12:30"}, {"取引日": "2026-09-08T10:15:03Z"},
    {"出金金額（円）": "1,20"}, {"出金金額（円）": "-100"},
    {"入金金額（円）": "¥100"}, {"入金金額（円）": "10.5"},
    {"入金金額（円）": "1e5"}, {"取引内容": ""},
    {"取引先": "a" * (payments.MAX_FIELD + 1)},
])
def test_invalid_row_rejects_entire_import_without_echoing_private_values(tmp_path, overrides):
    db = tmp_path / "db"
    payments.import_csv(db, csv_bytes(row("existing")))
    before = payments.list_payments(db)
    with pytest.raises(ValueError) as failure:
        payments.import_csv(db, csv_bytes(row("new"), row("bad", **overrides)))
    assert "架空喫茶" not in str(failure.value)
    assert payments.list_payments(db) == before
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM payment_imports").fetchone()[0] == 1


@pytest.mark.parametrize("data", [
    b"", b"\xff", b"a,b\nx,y", b"\x00", csv_bytes(row()[:-1]),
    csv_bytes(row(), headers=payments.HEADERS[:-1]),
    csv_bytes(row(), headers=(*payments.HEADERS, "取引日")),
    (",".join(payments.HEADERS) + '\n"unterminated').encode(),
])
def test_malformed_files_never_create_database(tmp_path, data):
    db = tmp_path / "db"
    with pytest.raises(ValueError):
        payments.import_csv(db, data)
    assert not db.exists()


def test_limits_bom_multiline_unknown_metadata_and_sql_literals(tmp_path, monkeypatch):
    db = tmp_path / "db"
    merchant = "架空,'店舗\n二階"
    data = csv_bytes([*row("'; DROP TABLE payments;--", **{"取引先": merchant}), "将来の情報"],
                     headers=(*payments.HEADERS, "追加項目"))
    payments.import_csv(db, b"\xef\xbb\xbf" + data)
    item = payments.list_payments(db)["items"][0]
    assert item["counterparty"] == merchant
    assert item["details"]["追加項目"] == "将来の情報"
    assert json.loads(json.dumps(item))["transaction_id"] == "'; DROP TABLE payments;--"
    monkeypatch.setattr(payments, "MAX_ROWS", 1)
    with pytest.raises(ValueError, match="50,000"):
        payments.parse_csv(csv_bytes(row("1"), row("2")))
    monkeypatch.setattr(payments, "MAX_BYTES", 10)
    with pytest.raises(ValueError, match="10 MiB"):
        payments.parse_csv(data)


def test_inclusive_japanese_dates_stable_pages_and_no_filter(tmp_path):
    db = tmp_path / "db"
    payments.import_csv(db, FIXTURE.read_bytes())
    first = payments.list_payments(db, start="2026-09-08", end="2026-09-08", limit="2")
    second = payments.list_payments(db, start="2026-09-08", end="2026-09-08", limit="2", offset="2")
    assert first["total"] == second["total"] == 4
    assert first["has_more"] and not second["has_more"]
    assert len({i["id"] for i in first["items"] + second["items"]}) == 4
    assert payments.list_payments(db, end="2026-09-07")["total"] == 2
    assert payments.list_payments(db, start="2026-09-09")["total"] == 0
    assert payments.list_payments(db, offset="100")["items"] == []


@pytest.mark.parametrize("query", [
    {"limit": "0"}, {"limit": "501"}, {"offset": "-1"}, {"offset": "1000001"},
    {"limit": "nan"}, {"start": "20260908"}, {"end": "2026-02-30"},
    {"start": "2026-09-09", "end": "2026-09-08"}, {"end": "9999-12-31"},
])
def test_invalid_queries(tmp_path, query):
    with pytest.raises(ValueError):
        payments.list_payments(tmp_path / "db", **query)


def test_simultaneous_resends_are_atomic(tmp_path):
    db = tmp_path / "db"
    data = FIXTURE.read_bytes()
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: payments.import_csv(db, data), range(2)))
    assert sum(r["added"] for r in results) == 6
    assert payments.list_payments(db)["total"] == 6
