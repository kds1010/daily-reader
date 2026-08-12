from datetime import UTC, datetime
from pathlib import Path

from daily_reader.email_assistant import (
    GmailThreadRecord,
    assess_email,
    list_reminders,
    update_status,
    upsert_thread,
)

NOW = datetime(2026, 8, 12, 3, tzinfo=UTC)


def test_assess_email_detects_action_and_due_date() -> None:
    result = assess_email(
        "契約の自動更新について",
        "料金改定があります。2026年8月20日までにご確認ください。",
        {},
        NOW,
    )
    assert result.importance == "high"
    assert result.due_date == "2026-08-20"
    assert result.required_action == "内容を確認する"


def test_bulk_email_is_downgraded() -> None:
    result = assess_email(
        "今週のお知らせ", "新商品をご確認ください。",
        {"list-unsubscribe": "<https://example.com/unsubscribe>"}, NOW,
    )
    assert result.importance == "low"


def test_reminder_status_workflow(tmp_path: Path) -> None:
    database = tmp_path / "assistant.sqlite3"
    record = GmailThreadRecord(
        "thread-1", "message-1", "me@example.com", "本人確認が必要です",
        "service@example.com", NOW.isoformat(), "本人確認を行ってください",
        "https://mail.google.com/mail/u/0/#all/thread-1", "high", 7, "本人確認",
        "本人確認を行う", None, "open", "classified",
    )
    upsert_thread(database, record, NOW)
    assert [item["thread_id"] for item in list_reminders(database, "daily", NOW)] == [
        "thread-1"
    ]
    assert update_status(database, "thread-1", "done", NOW)
    assert list_reminders(database, "daily", NOW) == []


def test_manual_done_is_preserved_until_a_new_message_arrives(tmp_path: Path) -> None:
    database = tmp_path / "assistant.sqlite3"
    original = GmailThreadRecord(
        "thread-1", "message-1", "me@example.com", "件名", "a@example.com",
        NOW.isoformat(), "本文", "https://example.com", "high", 8, "要対応",
        "確認する", None, "open", "classified",
    )
    upsert_thread(database, original, NOW)
    update_status(database, "thread-1", "done", NOW)
    upsert_thread(database, original, NOW)
    assert list_reminders(database, "weekly", NOW) == []

    refreshed = GmailThreadRecord(
        **{**original.__dict__, "latest_message_id": "message-2", "status": "open"}
    )
    upsert_thread(database, refreshed, NOW)
    assert len(list_reminders(database, "weekly", NOW)) == 1
