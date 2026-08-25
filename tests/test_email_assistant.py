from datetime import UTC, datetime
from pathlib import Path

from daily_reader.email_assistant import (
    GmailThreadRecord,
    assess_email,
    clean_message_body,
    get_gmail_sync_state,
    gmail_thread_url,
    list_reminders,
    mark_gmail_thread_read,
    reconcile_unread_threads,
    update_status,
    upsert_thread,
)

NOW = datetime(2026, 8, 12, 3, tzinfo=UTC)


def test_gmail_thread_url_selects_the_synchronized_account() -> None:
    assert gmail_thread_url("me+reader@example.com", "thread-1") == (
        "https://mail.google.com/mail/?authuser=me%2Breader%40example.com#all/thread-1"
    )


def test_gmail_sync_state_is_empty_before_first_completed_sync(tmp_path: Path) -> None:
    assert get_gmail_sync_state(tmp_path / "assistant.sqlite3") is None


def test_clean_html_message_body_preserves_structure() -> None:
    body = clean_message_body(
        "<style>hidden</style><h1>お知らせ</h1><p>本文です。<br>次の行です。</p>"
        "<ul><li>項目1</li><li>項目2</li></ul><script>bad()</script>",
        is_html=True,
    )
    assert body == "お知らせ\n本文です。\n次の行です。\n\n・項目1\n・項目2"


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
    assert list_reminders(database, "daily", NOW)[0]["gmail_url"] == (
        "https://mail.google.com/mail/?authuser=me%40example.com#all/thread-1"
    )
    assert update_status(database, "thread-1", "done", NOW)
    assert list_reminders(database, "daily", NOW) == []


def test_read_thread_is_not_listed_as_a_reminder(tmp_path: Path) -> None:
    database = tmp_path / "assistant.sqlite3"
    record = GmailThreadRecord(
        "thread-read", "message-1", "me@example.com", "本人確認が必要です",
        "service@example.com", NOW.isoformat(), "本人確認を行ってください",
        "https://example.com", "high", 7, "本人確認", "本人確認を行う",
        None, "open", "classified", is_unread=False,
    )

    upsert_thread(database, record, NOW)

    assert list_reminders(database, "daily", NOW) == []


def test_mark_gmail_thread_read_updates_gmail_and_local_state(
    tmp_path: Path, monkeypatch,
) -> None:
    database = tmp_path / "assistant.sqlite3"
    record = GmailThreadRecord(
        "thread-1", "message-1", "me@example.com", "本人確認が必要です",
        "service@example.com", NOW.isoformat(), "本人確認を行ってください",
        "https://example.com", "high", 7, "本人確認", "本人確認を行う",
        None, "open", "classified",
    )
    upsert_thread(database, record, NOW)
    executed = []

    class Request:
        def execute(self):
            executed.append(True)

    class Threads:
        def modify(self, **kwargs):
            assert kwargs == {
                "userId": "me",
                "id": "thread-1",
                "body": {"removeLabelIds": ["UNREAD"]},
            }
            return Request()

    class Users:
        def threads(self):
            return Threads()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(
        "daily_reader.email_assistant.load_credentials",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr("daily_reader.email_assistant.build", lambda *args, **kwargs: Service())

    assert mark_gmail_thread_read(
        database, tmp_path / "client.json", tmp_path / "token.json", "thread-1", NOW
    )
    assert executed == [True]
    assert list_reminders(database, "daily", NOW) == []


def test_mark_gmail_thread_read_rejects_unknown_thread(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setattr(
        "daily_reader.email_assistant.load_credentials",
        lambda *args: (_ for _ in ()).throw(AssertionError("must not authenticate")),
    )

    assert not mark_gmail_thread_read(
        tmp_path / "assistant.sqlite3",
        tmp_path / "client.json",
        tmp_path / "token.json",
        "unknown",
        NOW,
    )


def test_threads_missing_from_latest_sync_are_not_listed(tmp_path: Path) -> None:
    database = tmp_path / "assistant.sqlite3"
    records = [
        GmailThreadRecord(
            f"thread-{index}", f"message-{index}", "me@example.com", "要確認",
            "service@example.com", NOW.isoformat(), "確認してください",
            "https://example.com", "high", 5, "ご確認ください", "確認する",
            None, "open", "classified",
        )
        for index in (1, 2)
    ]
    for record in records:
        upsert_thread(database, record, NOW)

    reconcile_unread_threads(database, {"thread-2"})

    assert [item["thread_id"] for item in list_reminders(database, "daily", NOW)] == [
        "thread-2"
    ]


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
