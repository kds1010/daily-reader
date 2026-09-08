"""Explicit, local imports of PayPay's consumer CSV; never infer spending totals."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

MAX_BYTES = 10 * 1024 * 1024
MAX_ROWS = 50_000
MAX_FIELD = 2048
JST = ZoneInfo("Asia/Tokyo")
HEADERS = (
    "取引日", "出金金額（円）", "入金金額（円）", "海外出金金額", "通貨",
    "変換レート（円）", "利用国", "取引内容", "取引先", "取引方法",
    "支払い区分", "利用者", "取引番号",
)


def _hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _amount(value: str) -> int | None:
    if value in {"", "-"}:
        return None
    if not re.fullmatch(r"(?:[0-9]{1,12}|[0-9]{1,3}(?:,[0-9]{3}){1,3})", value):
        raise ValueError("円金額の形式が未対応です")
    return int(value.replace(",", ""))


def _timestamp(value: str) -> str:
    for pattern, fmt in (
        (r"[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}", "%Y/%m/%d %H:%M:%S"),
        (r"[0-9]{4}/[0-9]{2}/[0-9]{2} [0-9]{2}:[0-9]{2}", "%Y/%m/%d %H:%M"),
    ):
        if re.fullmatch(pattern, value):
            try:
                parsed = datetime.strptime(value, fmt).replace(tzinfo=JST)
                if 2000 <= parsed.year <= 2200:
                    return parsed.isoformat()
            except ValueError:
                break
    raise ValueError("取引日の形式が未対応または不正です")


def parse_csv(content: bytes) -> list[dict]:
    """Validate the whole file before any DB mutation. Errors never echo cells."""
    if not 0 < len(content) <= MAX_BYTES:
        raise ValueError("CSVは空でない10 MiB以下のファイルにしてください")
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("UTF-8のPayPay CSVを選択してください") from None
    if "\x00" in text:
        raise ValueError("CSVに不正な文字が含まれています")
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    try:
        headers = next(reader, [])
        if (
            len(headers) != len(set(headers)) or not set(HEADERS).issubset(headers)
            or len(headers) > 64 or any(not h or len(h) > 100 for h in headers)
        ):
            raise ValueError("PayPay個人向けCSVの13列が必要です。列名を変更せず選択してください")
        rows = []
        for row_number, cells in enumerate(reader, start=2):
            if not cells:
                continue
            if len(rows) >= MAX_ROWS:
                raise ValueError("CSVは50,000件以下に分割してください")
            if len(cells) != len(headers) or any(len(c) > MAX_FIELD for c in cells):
                raise ValueError(f"CSVの{row_number}行目の列数または文字数が不正です")
            raw = dict(zip(headers, cells, strict=True))
            values = {k: v.strip() for k, v in raw.items()}
            try:
                occurred_at = _timestamp(values["取引日"])
                outgoing = _amount(values["出金金額（円）"])
                incoming = _amount(values["入金金額（円）"])
            except ValueError as error:
                raise ValueError(f"CSVの{row_number}行目: {error}") from None
            if values["取引内容"] in {"", "-"}:
                raise ValueError(f"CSVの{row_number}行目に取引内容がありません")
            transaction_id = values["取引番号"]
            transaction_id = None if transaction_id in {"", "-"} else transaction_id
            # All columns participate, including future metadata. Formatting of the
            # known numeric/date fields does not turn an unchanged row into a conflict.
            canonical = dict(values)
            canonical.update({
                "取引日": occurred_at, "出金金額（円）": outgoing,
                "入金金額（円）": incoming, "取引番号": transaction_id,
            })
            rows.append({
                "row_number": row_number, "occurred_at": occurred_at,
                "outgoing_yen": outgoing, "incoming_yen": incoming,
                "kind": values["取引内容"], "counterparty": values["取引先"],
                "payment_method": values["取引方法"], "transaction_id": transaction_id,
                "details": raw, "content_hash": _hash(_json(canonical).encode()),
            })
    except csv.Error:
        raise ValueError("CSVの引用符またはフィールド長が不正です") from None
    return rows


@contextmanager
def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Create privately before sqlite opens it; rollback journals inherit DB mode.
    path.touch(mode=0o600, exist_ok=True)
    path.chmod(0o600)
    connection = sqlite3.connect(path, timeout=15)
    connection.row_factory = sqlite3.Row
    try:
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS payment_imports (
                file_hash TEXT PRIMARY KEY, imported_at TEXT NOT NULL,
                total INTEGER NOT NULL, added INTEGER NOT NULL, duplicates INTEGER NOT NULL,
                conflicts INTEGER NOT NULL, missing_ids INTEGER NOT NULL,
                conflict_rows TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS payments (
                id TEXT PRIMARY KEY, source TEXT NOT NULL, transaction_id TEXT,
                occurred_at TEXT NOT NULL, outgoing_yen INTEGER, incoming_yen INTEGER,
                kind TEXT NOT NULL, counterparty TEXT NOT NULL, payment_method TEXT NOT NULL,
                details TEXT NOT NULL, content_hash TEXT NOT NULL,
                file_hash TEXT NOT NULL, row_number INTEGER NOT NULL
            );
            CREATE INDEX IF NOT EXISTS payments_date ON payments(occurred_at DESC, id DESC);
        """)
        with connection:
            yield connection
    finally:
        connection.close()


def import_csv(path: Path, content: bytes) -> dict:
    rows = parse_csv(content)
    file_hash = _hash(content)
    with _connect(path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT * FROM payment_imports WHERE file_hash = ?", (file_hash,)
        ).fetchone()
        if previous:
            return {
                "total": previous["total"], "added": 0,
                "duplicates": previous["added"] + previous["duplicates"],
                "conflicts": previous["conflicts"], "missing_ids": previous["missing_ids"],
                "conflict_rows": json.loads(previous["conflict_rows"]),
                "imported_at": previous["imported_at"], "file_duplicate": True,
            }
        added = duplicates = conflicts = missing_ids = 0
        conflict_rows = []
        for row in rows:
            transaction_id = row["transaction_id"]
            if transaction_id is None:
                missing_ids += 1
            key = (
                "paypay:id:" + transaction_id if transaction_id is not None
                else f"paypay:file:{file_hash}:{row['row_number']}"
            )
            identifier = _hash(key.encode())
            existing = conn.execute(
                "SELECT content_hash FROM payments WHERE id = ?", (identifier,)
            ).fetchone()
            if existing:
                if existing["content_hash"] == row["content_hash"]:
                    duplicates += 1
                else:
                    conflicts += 1
                    if len(conflict_rows) < 100:
                        conflict_rows.append(row["row_number"])
                continue
            conn.execute(
                """INSERT INTO payments VALUES (?, 'paypay', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (identifier, transaction_id, row["occurred_at"], row["outgoing_yen"],
                 row["incoming_yen"], row["kind"], row["counterparty"], row["payment_method"],
                 _json(row["details"]), row["content_hash"], file_hash, row["row_number"]),
            )
            added += 1
        imported_at = datetime.now(UTC).isoformat()
        conn.execute(
            "INSERT INTO payment_imports VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (file_hash, imported_at, len(rows), added, duplicates, conflicts,
             missing_ids, _json(conflict_rows)),
        )
    return {
        "total": len(rows), "added": added, "duplicates": duplicates, "conflicts": conflicts,
        "missing_ids": missing_ids, "conflict_rows": conflict_rows,
        "imported_at": imported_at, "file_duplicate": False,
    }


def list_payments(
    path: Path, *, start: str | None = None, end: str | None = None,
    limit: str = "100", offset: str = "0",
) -> dict:
    limit_number, offset_number = int(limit), int(offset)
    if not 1 <= limit_number <= 500 or not 0 <= offset_number <= 1_000_000:
        raise ValueError("invalid page")
    conditions, parameters = [], []
    bounds = []
    for column, value, exclusive in (("occurred_at >= ?", start, False),
                                     ("occurred_at < ?", end, True)):
        if value is not None:
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
                raise ValueError("invalid date")
            day = date.fromisoformat(value)
            if not 2000 <= day.year <= 2200:
                raise ValueError("invalid year")
            bounds.append(day)
            if exclusive:
                day += timedelta(days=1)
            conditions.append(column)
            parameters.append(datetime.combine(day, time(), JST).isoformat())
    if len(bounds) == 2 and bounds[0] > bounds[1]:
        raise ValueError("reversed dates")
    where = " WHERE " + " AND ".join(conditions) if conditions else ""
    with _connect(path) as conn:
        # Count and page see the same DB snapshot even if an import commits meanwhile.
        conn.execute("BEGIN")
        total = conn.execute("SELECT count(*) FROM payments" + where, parameters).fetchone()[0]
        records = conn.execute(
            "SELECT * FROM payments" + where
            + " ORDER BY occurred_at DESC, id DESC LIMIT ? OFFSET ?",
            [*parameters, limit_number, offset_number],
        ).fetchall()
        last = conn.execute(
            "SELECT imported_at FROM payment_imports ORDER BY imported_at DESC LIMIT 1"
        ).fetchone()
        missing_ids = conn.execute(
            "SELECT count(*) FROM payments WHERE transaction_id IS NULL"
        ).fetchone()[0]
    items = []
    for record in records:
        item = {k: record[k] for k in (
            "id", "source", "transaction_id", "occurred_at", "outgoing_yen", "incoming_yen",
            "kind", "counterparty", "payment_method", "row_number",
        )}
        item["details"] = json.loads(record["details"])
        items.append(item)
    return {
        "items": items, "total": total, "offset": offset_number,
        "has_more": offset_number + len(items) < total,
        "last_imported_at": last[0] if last else None, "missing_ids": missing_ids,
    }
