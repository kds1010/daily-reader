import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from daily_reader.email_assistant import (
    GMAIL_READONLY_SCOPE,
    GmailAuthorizationFailed,
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


def test_interactive_sync_requires_modify_scope(tmp_path: Path, monkeypatch) -> None:
    requested = []

    def fake_load_credentials(
        client_secret, token_path, interactive, require_modify=False, force=False,
    ):
        requested.append((interactive, require_modify))
        raise GmailAuthorizationRequired("authorization required")

    monkeypatch.setattr(
        "daily_reader.email_assistant.load_credentials", fake_load_credentials
    )

    with pytest.raises(GmailAuthorizationRequired):
        sync_gmail(
            tmp_path / "assistant.sqlite3",
            tmp_path / "client.json",
            tmp_path / "token.json",
            interactive=True,
        )

    assert requested == [(True, True)]


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


@pytest.mark.parametrize("interactive", [False, True])
def test_revoked_token_recovers_only_with_interactive_authorization(
    tmp_path: Path, monkeypatch, interactive: bool,
) -> None:
    from google.auth.exceptions import RefreshError

    token_path = tmp_path / "token.json"
    original = json.dumps({"scopes": [GMAIL_READONLY_SCOPE]})
    token_path.write_text(original)

    class ExpiredCredentials:
        expired = True
        refresh_token = "private"

        def refresh(self, request):
            raise RefreshError("private response", {"error": "invalid_grant"})

    class FreshCredentials:
        valid = True
        refresh_token = "replacement-refresh"
        scopes = ["https://www.googleapis.com/auth/gmail.modify"]
        granted_scopes = None

        def to_json(self):
            return '{"replacement": true}'

    class Flow:
        def run_local_server(self, **kwargs):
            assert token_path.read_text() == original
            assert kwargs["host"] == "127.0.0.1"
            return FreshCredentials()

    monkeypatch.setattr(
        "daily_reader.email_assistant.Credentials.from_authorized_user_info",
        lambda *args: ExpiredCredentials(),
    )
    monkeypatch.setattr(
        "daily_reader.email_assistant.InstalledAppFlow.from_client_secrets_file",
        lambda *args: Flow(),
    )
    if interactive:
        load_credentials(tmp_path / "client.json", token_path, True)
        assert json.loads(token_path.read_text()) == {"replacement": True}
        assert token_path.stat().st_mode & 0o777 == 0o600
    else:
        with pytest.raises(GmailAuthorizationRequired, match="再認証") as caught:
            sync_gmail(tmp_path / "db.sqlite3", tmp_path / "client.json", token_path)
        assert "private" not in str(caught.value)
        assert token_path.read_text() == original
        status = get_gmail_sync_status(tmp_path / "db.sqlite3")
        assert status["authorization_required"] == 1
        assert status["can_mark_read"] == 0


@pytest.mark.parametrize("failure", ["cancel", "temporary", "transport", "unknown"])
def test_failed_authorization_preserves_saved_token(tmp_path: Path, monkeypatch, failure):
    from google.auth.exceptions import RefreshError, TransportError

    token_path = tmp_path / "token.json"
    original = json.dumps({"scopes": [GMAIL_READONLY_SCOPE]})
    token_path.write_text(original)
    expected = TransportError if failure == "transport" else RefreshError

    class ExpiredCredentials:
        expired = True
        refresh_token = "private"

        def refresh(self, request):
            if failure == "transport":
                raise TransportError("offline")
            raise RefreshError(
                "failed", {"error": "other" if failure == "unknown" else "invalid_grant"},
                retryable=failure == "temporary",
            )

    class Flow:
        def run_local_server(self, **kwargs):
            assert failure == "cancel"
            raise ValueError("cancelled")

    monkeypatch.setattr(
        "daily_reader.email_assistant.Credentials.from_authorized_user_info",
        lambda *args: ExpiredCredentials(),
    )
    monkeypatch.setattr(
        "daily_reader.email_assistant.InstalledAppFlow.from_client_secrets_file",
        lambda *args: Flow(),
    )
    with pytest.raises(ValueError if failure == "cancel" else expected):
        load_credentials(tmp_path / "client.json", token_path, True)
    assert token_path.read_text() == original


def test_successful_sync_clears_authorization_failure(tmp_path: Path, monkeypatch):
    database = tmp_path / "db.sqlite3"
    record_gmail_sync_status(database, NOW, "authorization_required", True, False)

    class CredentialsStub:
        scopes = ["https://www.googleapis.com/auth/gmail.modify"]

    class Service:
        def users(self):
            return self

        def getProfile(self, **kwargs):
            return self

        def execute(self):
            return {"emailAddress": "me@example.com"}

    monkeypatch.setattr(
        "daily_reader.email_assistant.load_credentials", lambda *a, **k: CredentialsStub()
    )
    monkeypatch.setattr("daily_reader.email_assistant.build", lambda *a, **k: Service())
    monkeypatch.setattr("daily_reader.email_assistant._list_thread_ids", lambda *a: [])
    assert sync_gmail(database, tmp_path / "client.json", tmp_path / "token.json") == 0
    status = get_gmail_sync_status(database)
    assert status["last_error"] is None
    assert status["authorization_required"] == 0
    assert status["can_mark_read"] == 1
    assert get_gmail_sync_state(database)["completed_at"] is not None


def _oauth_credentials(*, token="private-access", refresh_token="private-refresh",
                       granted_scopes=None, expired=False):
    from datetime import timedelta

    from google.oauth2.credentials import Credentials

    return Credentials(
        token=token, refresh_token=refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id="test-client", client_secret="private-client-secret",
        scopes=["https://www.googleapis.com/auth/gmail.modify"],
        granted_scopes=granted_scopes,
        expiry=(datetime.now(UTC) + timedelta(hours=-1 if expired else 1)).replace(tzinfo=None),
    )


def _install_consent(monkeypatch, callback):
    class Flow:
        def run_local_server(self, **kwargs):
            assert kwargs == {
                "host": "127.0.0.1", "port": 0, "open_browser": True, "prompt": "consent",
            }
            return callback()

    monkeypatch.setattr(
        "daily_reader.email_assistant.InstalledAppFlow.from_client_secrets_file",
        lambda *a: Flow(),
    )


def test_valid_token_is_not_rewritten_or_reauthorized(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(_oauth_credentials().to_json())
    original = token.read_bytes(), token.stat().st_mtime_ns
    _install_consent(monkeypatch, lambda: pytest.fail("unexpected consent"))
    assert load_credentials(tmp_path / "client.json", token, False).valid
    assert (token.read_bytes(), token.stat().st_mtime_ns) == original


def test_expired_access_token_refreshes_without_consent(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(_oauth_credentials(expired=True).to_json())
    _install_consent(monkeypatch, lambda: pytest.fail("unexpected consent"))

    def refresh(credentials, request):
        credentials.token = "updated-access"
        credentials.expiry = _oauth_credentials().expiry

    monkeypatch.setattr("daily_reader.email_assistant.Credentials.refresh", refresh)
    credentials = load_credentials(tmp_path / "client.json", token, False)
    assert credentials.token == "updated-access"
    saved = json.loads(token.read_text())
    assert saved["token"] == "updated-access"
    assert saved["refresh_token"] == "private-refresh"
    assert token.stat().st_mode & 0o777 == 0o600


def test_force_auth_replaces_valid_token_after_consent(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(_oauth_credentials().to_json())
    original = token.read_text()

    def consent():
        assert token.read_text() == original
        return _oauth_credentials(refresh_token="new-refresh")

    _install_consent(monkeypatch, consent)
    monkeypatch.setattr("daily_reader.email_assistant.Credentials.refresh",
                        lambda *a: pytest.fail("force must skip old credentials"))
    load_credentials(tmp_path / "client.json", token, True, force=True)
    assert json.loads(token.read_text())["refresh_token"] == "new-refresh"
    assert token.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("failure", ["cancel", "no-refresh", "scope", "empty-scope", "expired"])
def test_force_auth_failure_preserves_token(tmp_path, monkeypatch, failure):
    token = tmp_path / "token.json"
    token.write_text(_oauth_credentials().to_json())
    original = token.read_bytes()

    def consent():
        if failure == "cancel":
            raise ValueError("cancelled")
        return _oauth_credentials(
            refresh_token=None if failure == "no-refresh" else "new-refresh",
            granted_scopes=([GMAIL_READONLY_SCOPE] if failure == "scope"
                            else [] if failure == "empty-scope" else None),
            expired=failure == "expired",
        )

    _install_consent(monkeypatch, consent)
    with pytest.raises(ValueError if failure == "cancel" else GmailAuthorizationFailed):
        load_credentials(tmp_path / "client.json", token, True, force=True)
    assert token.read_bytes() == original


def test_refresh_rejects_reduced_granted_scope_without_saving(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(_oauth_credentials(expired=True).to_json())
    original = token.read_bytes()

    def refresh(credentials, request):
        credentials.expiry = _oauth_credentials().expiry
        credentials._granted_scopes = [GMAIL_READONLY_SCOPE]

    monkeypatch.setattr("daily_reader.email_assistant.Credentials.refresh", refresh)
    with pytest.raises(GmailAuthorizationRequired):
        load_credentials(tmp_path / "client.json", token, False)
    assert token.read_bytes() == original


def test_failed_atomic_replace_keeps_original_and_cleans_private_temp(tmp_path, monkeypatch):
    token = tmp_path / "token.json"
    token.write_text(_oauth_credentials().to_json())
    original = token.read_bytes()
    _install_consent(monkeypatch, lambda: _oauth_credentials(refresh_token="new-refresh"))

    def fail_replace(source, destination):
        assert Path(source).stat().st_mode & 0o777 == 0o600
        assert Path(source).parent == token.parent
        assert destination == token
        raise OSError("disk failure")

    monkeypatch.setattr("daily_reader.email_assistant.os.replace", fail_replace)
    with pytest.raises(OSError, match="disk failure"):
        load_credentials(tmp_path / "client.json", token, True, force=True)
    assert token.read_bytes() == original
    assert not list(tmp_path.glob(".gmail-token-*"))


def test_concurrent_auth_does_not_block_reader_or_overwrite_newer_auth(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    token = tmp_path / "token.json"
    token.write_text(_oauth_credentials().to_json())
    started, finish = Event(), Event()

    def consent():
        started.set()
        assert finish.wait(5)
        return _oauth_credentials(refresh_token="stale-consent")

    _install_consent(monkeypatch, consent)
    with ThreadPoolExecutor(max_workers=2) as pool:
        pending = pool.submit(load_credentials, tmp_path / "client.json", token, True, False, True)
        try:
            assert started.wait(5)
            # A consent dialog must not prevent API callers from loading credentials.
            reader = pool.submit(load_credentials, tmp_path / "client.json", token, False)
            assert reader.result(timeout=5).valid
            _install_consent(monkeypatch, lambda: _oauth_credentials(refresh_token="newest"))
            load_credentials(tmp_path / "client.json", token, True, force=True)
        finally:
            finish.set()
        with pytest.raises(GmailAuthorizationFailed, match="別の処理"):
            pending.result(timeout=5)
    assert json.loads(token.read_text())["refresh_token"] == "newest"


def test_parallel_process_refreshes_reload_under_lock(tmp_path, monkeypatch):
    import multiprocessing

    token = tmp_path / "token.json"
    token.write_text(_oauth_credentials(expired=True).to_json())
    # fork gives both workers the same deterministic, network-free refresh stub.
    context = multiprocessing.get_context("fork")
    entered, release, second_started = context.Event(), context.Event(), context.Event()
    results = context.Queue()

    def refresh(credentials, request):
        entered.set()
        assert release.wait(5)
        credentials.token = "refreshed-once"
        credentials.expiry = _oauth_credentials().expiry
        results.put("refresh")

    def worker(second=False):
        if second:
            second_started.set()
        credentials = load_credentials(tmp_path / "client.json", token, False)
        results.put(credentials.token)

    monkeypatch.setattr("daily_reader.email_assistant.Credentials.refresh", refresh)
    first = context.Process(target=worker)
    second = context.Process(target=worker, args=(True,))
    first.start()
    try:
        assert entered.wait(5)
        second.start()
        assert second_started.wait(5)
    finally:
        release.set()
        for process in (first, second):
            if process.pid is not None:
                process.join(timeout=5)
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
    assert first.exitcode == second.exitcode == 0
    assert sorted(results.get(timeout=5) for _ in range(3)) == [
        "refresh", "refreshed-once", "refreshed-once",
    ]
    results.close()
    assert json.loads(token.read_text())["token"] == "refreshed-once"


def test_doctor_is_read_only_and_reports_only_safe_metadata(tmp_path, monkeypatch, capsys):
    from daily_reader.email_assistant import main

    database = tmp_path / "db.sqlite3"
    token, client = tmp_path / "token.json", tmp_path / "client.json"
    record_gmail_sync_status(database, NOW, "private-error-response", True, False)
    import sqlite3

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO gmail_sync_state VALUES (1, ?, 0)", (NOW.isoformat(),),
        )
    payload = json.loads(_oauth_credentials().to_json())
    payload["scopes"].append("private-unknown-scope")
    token.write_text(json.dumps(payload))
    token.chmod(0o600)
    client.write_text("private-client-contents")
    before = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()}
    monkeypatch.setattr("sys.argv", ["daily-reader-gmail", "doctor", "--database", str(database),
                                     "--token", str(token), "--client-secret", str(client)])
    monkeypatch.setattr("daily_reader.email_assistant.load_credentials",
                        lambda *a, **k: pytest.fail("doctor must not load/refresh credentials"))
    main()
    output = capsys.readouterr().out
    report = json.loads(output)
    assert "private" not in output
    assert report["has_refresh_token"]
    assert report["token_permissions"] == "0600"
    assert report["authorization_required"]
    assert report["sync_error"] == "unknown"
    assert report["refresh_token_expiry"] == "unknown"
    assert report["last_sync_completed_at"] == NOW.isoformat()
    assert report["last_sync_attempt_at"] == NOW.isoformat()
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in tmp_path.iterdir()} == before


@pytest.mark.parametrize("contents", [None, "private-invalid-json", "[]", '{"expiry":"private"}'])
def test_doctor_handles_missing_and_invalid_files_without_creating_database(tmp_path, contents):
    from daily_reader.email_assistant import gmail_doctor

    token = tmp_path / "token.json"
    if contents is not None:
        token.write_text(contents)
    original_files = list(tmp_path.iterdir())
    report = gmail_doctor(tmp_path / "missing.sqlite3", tmp_path / "missing-client.json", token)
    assert report["database_status"] == "missing"
    assert report["access_token_expiry"] is None
    assert "private" not in json.dumps(report)
    assert list(tmp_path.iterdir()) == original_files


@pytest.mark.parametrize("command", ["sync", "doctor"])
def test_force_is_only_accepted_for_auth(monkeypatch, command):
    from daily_reader.email_assistant import main

    monkeypatch.setattr("sys.argv", ["daily-reader-gmail", command, "--force"])
    monkeypatch.setattr("daily_reader.email_assistant.sync_gmail",
                        lambda *a, **k: pytest.fail("invalid arguments must not synchronize"))
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_auth_cli_passes_force_and_requires_modify(tmp_path, monkeypatch, capsys):
    from daily_reader.email_assistant import main

    calls = []
    monkeypatch.setattr("sys.argv", ["daily-reader-gmail", "auth", "--force"])
    monkeypatch.setattr("daily_reader.email_assistant.sync_gmail",
                        lambda *a, **k: calls.append(k) or 0)
    main()
    assert calls == [{"interactive": True, "force": True}]
    assert "Synchronized 0" in capsys.readouterr().out


@pytest.mark.parametrize("failure", ["cancel", "scope", "conflict", "save"])
def test_failed_force_auth_preserves_current_sync_status(tmp_path, monkeypatch, failure):
    database, token = tmp_path / "db.sqlite3", tmp_path / "token.json"
    record_gmail_sync_status(database, NOW, None, False, True)
    token.write_text(_oauth_credentials().to_json())
    original = token.read_bytes()
    expected_status = get_gmail_sync_status(database)

    def consent():
        if failure == "cancel":
            raise ValueError("cancelled")
        if failure == "conflict":
            # Another successful auth/sync finishes while this consent is open.
            from daily_reader.email_assistant import _save_credentials, _token_lock

            with _token_lock(token):
                _save_credentials(token, _oauth_credentials(refresh_token="newest"))
            record_gmail_sync_status(database, NOW, None, False, True)
        return _oauth_credentials(granted_scopes=[] if failure == "scope" else None)

    _install_consent(monkeypatch, consent)
    if failure == "save":
        def fail_replace(*args):
            raise OSError("disk failure")

        monkeypatch.setattr("daily_reader.email_assistant.os.replace", fail_replace)
    expected = (ValueError if failure == "cancel" else OSError if failure == "save"
                else GmailAuthorizationFailed)
    with pytest.raises(expected):
        sync_gmail(database, tmp_path / "client.json", token, interactive=True, force=True)
    assert get_gmail_sync_status(database) == expected_status
    if failure == "conflict":
        assert json.loads(token.read_text())["refresh_token"] == "newest"
    else:
        assert token.read_bytes() == original
