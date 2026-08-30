import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from daily_reader.email_assistant import (
    GMAIL_READONLY_SCOPE,
    GmailAuthorizationRequired,
    GmailThreadRecord,
    assess_email,
    clean_message_body,
    fetch_gmail_thread_content,
    get_gmail_sync_state,
    get_gmail_sync_status,
    gmail_thread_url,
    list_reminders,
    list_unread_threads,
    load_credentials,
    mark_gmail_thread_read,
    reconcile_unread_threads,
    record_gmail_sync_status,
    sync_gmail,
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


def test_unread_inbox_includes_low_and_old_threads_but_not_completed_or_read(
    tmp_path: Path,
) -> None:
    database = tmp_path / "assistant.sqlite3"
    records = [
        GmailThreadRecord(
            "low-old", "message-1", "me@example.com", "お知らせ", "a@example.com",
            "2020-01-01T00:00:00+00:00", "本文", "https://example.com", "low", 0,
            "明確な期限・依頼・警告を検出していません", "対応不要の可能性", None,
            "open", "classified",
        ),
        GmailThreadRecord(
            "done", "message-2", "me@example.com", "完了", "a@example.com",
            NOW.isoformat(), "本文", "https://example.com", "high", 8, "要対応", "確認する",
            None, "done", "marked_done",
        ),
        GmailThreadRecord(
            "read", "message-3", "me@example.com", "既読", "a@example.com",
            NOW.isoformat(), "本文", "https://example.com", "high", 8, "要対応", "確認する",
            None, "open", "classified", is_unread=False,
        ),
    ]
    for record in records:
        upsert_thread(database, record, NOW)

    items = list_unread_threads(database, NOW)

    assert [item["thread_id"] for item in items] == ["low-old"]


def test_sync_status_is_recorded_without_exposing_error_details(tmp_path: Path) -> None:
    database = tmp_path / "assistant.sqlite3"

    record_gmail_sync_status(database, NOW, "authorization_required", True, False)

    assert get_gmail_sync_status(database) == {
        "last_attempt_at": NOW.isoformat(),
        "last_error": "authorization_required",
        "authorization_required": 1,
        "can_mark_read": 0,
    }


def test_thread_listing_follows_all_pages() -> None:
    pages = {
        None: {"threads": [{"id": "thread-1"}], "nextPageToken": "next"},
        "next": {"threads": [{"id": "thread-2"}]},
    }

    class Request:
        def __init__(self, token):
            self.token = token

        def execute(self):
            return pages[self.token]

    class Threads:
        def list(self, **kwargs):
            assert kwargs["maxResults"] == 500
            return Request(kwargs.get("pageToken"))

    class Users:
        def threads(self):
            return Threads()

    class Service:
        def users(self):
            return Users()

    from daily_reader.email_assistant import _list_thread_ids

    assert _list_thread_ids(Service(), "is:unread") == ["thread-1", "thread-2"]


def test_readonly_credentials_are_accepted_but_modify_is_rejected(
    tmp_path: Path, monkeypatch,
) -> None:
    token_path = tmp_path / "token.json"
    token_path.write_text(json.dumps({"scopes": [GMAIL_READONLY_SCOPE]}), encoding="utf-8")
    requested = []

    class CredentialsStub:
        valid = True
        expired = False
        refresh_token = None
        scopes = [GMAIL_READONLY_SCOPE]

        def to_json(self):
            return json.dumps({"scopes": self.scopes})

    monkeypatch.setattr(
        "daily_reader.email_assistant.Credentials.from_authorized_user_info",
        lambda token, scopes: (requested.append(scopes), CredentialsStub())[1],
    )

    assert load_credentials(tmp_path / "client.json", token_path, False).valid
    assert requested == [[GMAIL_READONLY_SCOPE]]
    with pytest.raises(GmailAuthorizationRequired):
        load_credentials(tmp_path / "client.json", token_path, False, require_modify=True)


def test_sync_failure_does_not_reconcile_existing_unread_threads(
    tmp_path: Path, monkeypatch,
) -> None:
    database = tmp_path / "assistant.sqlite3"
    upsert_thread(
        database,
        GmailThreadRecord(
            "existing", "message-1", "me@example.com", "件名", "a@example.com",
            NOW.isoformat(), "本文", "https://example.com", "low", 0, "理由",
            "確認する", None, "open", "classified",
        ),
        NOW,
    )

    class CredentialsStub:
        scopes = [GMAIL_READONLY_SCOPE]

    monkeypatch.setattr(
        "daily_reader.email_assistant.load_credentials", lambda *args, **kwargs: CredentialsStub()
    )
    class ProfileRequest:
        def execute(self):
            return {"emailAddress": "me@example.com"}

    class Users:
        def getProfile(self, **kwargs):
            return ProfileRequest()

    class Service:
        def users(self):
            return Users()

    monkeypatch.setattr(
        "daily_reader.email_assistant.build", lambda *args, **kwargs: Service()
    )
    monkeypatch.setattr(
        "daily_reader.email_assistant._list_thread_ids",
        lambda *args: (_ for _ in ()).throw(RuntimeError("page failed")),
    )

    with pytest.raises(RuntimeError, match="page failed"):
        sync_gmail(database, tmp_path / "client.json", tmp_path / "token.json")

    assert [item["thread_id"] for item in list_unread_threads(database, NOW)] == ["existing"]


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


def test_fetch_gmail_thread_content_returns_messages_in_chronological_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "assistant.sqlite3"
    upsert_thread(
        database,
        GmailThreadRecord(
            "thread-1", "message-2", "me@example.com", "件名", "a@example.com",
            NOW.isoformat(), "本文", "https://example.com", "high", 5, "理由",
            "確認する", None, "open", "classified",
        ),
        NOW,
    )

    class Request:
        def __init__(self, payload):
            self.payload = payload

        def execute(self):
            return self.payload

    class Threads:
        def get(self, **kwargs):
            assert kwargs == {"userId": "me", "id": "thread-1", "format": "full"}
            return Request(
                {"messages": [
                    {
                        "id": "message-2", "internalDate": "2000",
                        "payload": {
                            "headers": [{"name": "From", "value": "b@example.com"}],
                            "body": {"data": ""},
                        },
                        "snippet": "新",
                    },
                    {
                        "id": "message-1", "internalDate": "1000",
                        "payload": {
                            "headers": [{"name": "From", "value": "a@example.com"}],
                            "parts": [{
                                "mimeType": "text/plain",
                                "body": {"data": "b2xk"},
                            }],
                        },
                    },
                ]}
            )

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
    monkeypatch.setattr(
        "daily_reader.email_assistant.build", lambda *args, **kwargs: Service()
    )

    content = fetch_gmail_thread_content(
        database, tmp_path / "client.json", tmp_path / "token.json", "thread-1"
    )

    assert content is not None
    assert [message["body"] for message in content["messages"]] == ["old", "新"]
