from __future__ import annotations

import argparse
import base64
import email.utils
import html
import json
import os
import re
import sqlite3
import urllib.parse
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

GMAIL_READONLY_SCOPE = "https://www.googleapis.com/auth/gmail.readonly"
GMAIL_MODIFY_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
GMAIL_UNREAD_QUERY = "is:unread -in:spam -in:trash"
GMAIL_RECENT_QUERY = "newer_than:7d -in:spam -in:trash"
TAG_PATTERN = re.compile(r"<[^>]+>")
SPACE_PATTERN = re.compile(r"\s+")
HTML_IGNORED_PATTERN = re.compile(
    r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL
)
HTML_BREAK_PATTERN = re.compile(
    r"<(?:br\s*/?|/p|/div|/h[1-6]|/tr|/table|/blockquote)\s*>", re.IGNORECASE
)
HTML_LIST_ITEM_PATTERN = re.compile(r"<li(?:\s[^>]*)?>", re.IGNORECASE)
DATE_PATTERNS = (
    re.compile(r"(?P<year>20\d{2})[年/-](?P<month>\d{1,2})[月/-](?P<day>\d{1,2})日?"),
    re.compile(r"(?P<month>\d{1,2})月(?P<day>\d{1,2})日"),
)
HIGH_SIGNALS = {
    "不正利用": 6,
    "セキュリティ警告": 6,
    "本人確認": 5,
    "支払期限": 5,
    "お支払い期限": 5,
    "期限切れ": 5,
    "至急": 5,
    "要対応": 5,
    "料金改定": 4,
    "自動更新": 4,
    "予約変更": 4,
    "キャンセル": 4,
    "請求書": 3,
    "返金": 3,
    "ご確認ください": 3,
    "ご返信": 3,
    "回答ください": 3,
}
ACTION_SIGNALS = (
    "確認してください",
    "ご確認ください",
    "返信ください",
    "ご返信",
    "回答ください",
    "お手続き",
    "更新してください",
    "支払",
    "本人確認",
    "対応ください",
    "要対応",
)


@contextmanager
def database_connection(path: Path):
    connection = sqlite3.connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@dataclass(frozen=True)
class EmailAssessment:
    importance: str
    score: int
    reason: str
    required_action: str
    due_date: str | None


@dataclass(frozen=True)
class GmailThreadRecord:
    thread_id: str
    latest_message_id: str
    account_email: str
    subject: str
    sender: str
    received_at: str
    snippet: str
    gmail_url: str
    importance: str
    importance_score: int
    reason: str
    required_action: str
    due_date: str | None
    status: str
    status_source: str
    is_unread: bool = True


class GmailAuthorizationRequired(RuntimeError):
    """Raised when the stored Gmail token cannot perform the requested operation."""


def clean_message_text(value: str, max_length: int = 1200) -> str:
    cleaned = html.unescape(TAG_PATTERN.sub(" ", value))
    cleaned = SPACE_PATTERN.sub(" ", cleaned).strip()
    return cleaned[:max_length]


def clean_message_body(value: str, is_html: bool, max_length: int = 8000) -> str:
    if is_html:
        value = HTML_IGNORED_PATTERN.sub("", value)
        value = HTML_BREAK_PATTERN.sub("\n", value)
        value = HTML_LIST_ITEM_PATTERN.sub("\n・", value)
        value = TAG_PATTERN.sub(" ", value)
    value = html.unescape(value).replace("\r\n", "\n").replace("\r", "\n")
    lines = []
    for line in value.splitlines():
        normalized = re.sub(r"[ \t\f\v]+", " ", line).strip()
        if normalized:
            lines.append(normalized)
        elif lines and lines[-1] != "":
            lines.append("")
    cleaned = "\n".join(lines).strip()
    return cleaned[:max_length]


def extract_due_date(text: str, now: datetime) -> str | None:
    for pattern in DATE_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        year = int(match.groupdict().get("year") or now.year)
        try:
            candidate = datetime(
                year,
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=now.tzinfo or UTC,
            )
        except ValueError:
            continue
        if "year" not in match.groupdict() and candidate < now - timedelta(days=30):
            candidate = candidate.replace(year=year + 1)
        return candidate.date().isoformat()
    return None


def assess_email(
    subject: str, body: str, headers: dict[str, str], now: datetime
) -> EmailAssessment:
    searchable = f"{subject}\n{body}".casefold()
    matched = [(signal, weight) for signal, weight in HIGH_SIGNALS.items() if signal in searchable]
    score = sum(weight for _, weight in matched)
    is_bulk = any(name in headers for name in ("list-unsubscribe", "list-id"))
    if is_bulk or headers.get("precedence", "").casefold() in {"bulk", "list", "junk"}:
        score -= 4
    has_action = any(signal in searchable for signal in ACTION_SIGNALS)
    if has_action:
        score += 2
    due_date = extract_due_date(searchable, now)
    if due_date:
        score += 2
    importance = "high" if score >= 6 else "medium" if score >= 3 else "low"
    if matched:
        reason = "、".join(signal for signal, _ in matched[:3])
    elif has_action:
        reason = "確認または対応を求める表現があります"
    elif is_bulk:
        reason = "メーリングリストまたは一括配信です"
    else:
        reason = "明確な期限・依頼・警告を検出していません"
    required_action = "内容を確認する" if importance != "low" else "対応不要の可能性"
    if "返信" in searchable or "回答" in searchable:
        required_action = "返信する"
    elif "支払" in searchable:
        required_action = "支払い内容を確認する"
    elif "本人確認" in searchable:
        required_action = "本人確認を行う"
    return EmailAssessment(importance, score, reason, required_action, due_date)


def initialize_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with database_connection(path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS email_threads (
                thread_id TEXT PRIMARY KEY, latest_message_id TEXT NOT NULL,
                account_email TEXT NOT NULL, subject TEXT NOT NULL, sender TEXT NOT NULL,
                received_at TEXT NOT NULL, snippet TEXT NOT NULL, gmail_url TEXT NOT NULL,
                importance TEXT NOT NULL, importance_score INTEGER NOT NULL,
                reason TEXT NOT NULL, required_action TEXT NOT NULL, due_date TEXT,
                status TEXT NOT NULL, status_source TEXT NOT NULL, remind_after TEXT,
                is_unread INTEGER NOT NULL DEFAULT 1,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gmail_sync_status (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                last_attempt_at TEXT NOT NULL,
                last_error TEXT,
                authorization_required INTEGER NOT NULL DEFAULT 0,
                can_mark_read INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        columns = {
            row[1] for row in connection.execute("PRAGMA table_info(email_threads)")
        }
        if "is_unread" not in columns:
            connection.execute(
                "ALTER TABLE email_threads ADD COLUMN is_unread INTEGER NOT NULL DEFAULT 1"
            )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS gmail_sync_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                completed_at TEXT NOT NULL,
                thread_count INTEGER NOT NULL
            )
            """
        )


def gmail_thread_url(account_email: str, thread_id: str) -> str:
    account = urllib.parse.quote(account_email, safe="")
    return f"https://mail.google.com/mail/?authuser={account}#all/{thread_id}"


def upsert_thread(path: Path, record: GmailThreadRecord, now: datetime) -> None:
    initialize_database(path)
    values = asdict(record) | {"updated_at": now.isoformat()}
    with database_connection(path) as connection:
        connection.execute(
            """
            INSERT INTO email_threads (
                thread_id, latest_message_id, account_email, subject, sender, received_at,
                snippet, gmail_url, importance, importance_score, reason, required_action,
                due_date, status, status_source, is_unread, updated_at
            ) VALUES (
                :thread_id, :latest_message_id, :account_email, :subject, :sender, :received_at,
                :snippet, :gmail_url, :importance, :importance_score, :reason, :required_action,
                :due_date, :status, :status_source, :is_unread, :updated_at
            )
            ON CONFLICT(thread_id) DO UPDATE SET
                latest_message_id=excluded.latest_message_id, subject=excluded.subject,
                sender=excluded.sender, received_at=excluded.received_at, snippet=excluded.snippet,
                gmail_url=excluded.gmail_url, importance=excluded.importance,
                importance_score=excluded.importance_score, reason=excluded.reason,
                required_action=excluded.required_action, due_date=excluded.due_date,
                is_unread=excluded.is_unread,
                status=CASE WHEN email_threads.status_source IN
                    ('marked_done', 'snoozed', 'dismissed')
                    AND email_threads.latest_message_id = excluded.latest_message_id
                    THEN email_threads.status
                    ELSE excluded.status END,
                status_source=CASE WHEN email_threads.status_source IN
                    ('marked_done', 'snoozed', 'dismissed')
                    AND email_threads.latest_message_id = excluded.latest_message_id
                    THEN email_threads.status_source
                    ELSE excluded.status_source END,
                updated_at=excluded.updated_at
            """,
            values,
        )


def list_reminders(path: Path, period: str, now: datetime) -> list[dict[str, Any]]:
    if period not in {"daily", "weekly"}:
        raise ValueError("period must be daily or weekly")
    initialize_database(path)
    cutoff = now - timedelta(days=1 if period == "daily" else 7)
    with database_connection(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT * FROM email_threads
            WHERE status IN ('open', 'awaiting_reply', 'snoozed')
              AND is_unread = 1
              AND importance IN ('high', 'medium')
              AND (remind_after IS NULL OR remind_after <= ?)
              AND (received_at >= ? OR due_date IS NOT NULL OR status = 'awaiting_reply')
            ORDER BY CASE importance WHEN 'high' THEN 0 ELSE 1 END,
              CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date, received_at DESC
            """,
            (now.isoformat(), cutoff.isoformat()),
        ).fetchall()
    reminders = [dict(row) for row in rows]
    for reminder in reminders:
        reminder["gmail_url"] = gmail_thread_url(
            reminder["account_email"], reminder["thread_id"]
        )
    return reminders


def get_gmail_sync_state(path: Path) -> dict[str, Any] | None:
    initialize_database(path)
    with database_connection(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT completed_at, thread_count FROM gmail_sync_state WHERE id = 1"
        ).fetchone()
    return dict(row) if row else None


def reconcile_unread_threads(path: Path, synchronized_thread_ids: set[str]) -> None:
    initialize_database(path)
    with database_connection(path) as connection:
        if synchronized_thread_ids:
            placeholders = ",".join("?" for _ in synchronized_thread_ids)
            connection.execute(
                f"UPDATE email_threads SET is_unread=0 WHERE thread_id NOT IN ({placeholders})",
                tuple(synchronized_thread_ids),
            )
        else:
            connection.execute("UPDATE email_threads SET is_unread=0")


def update_status(path: Path, thread_id: str, action: str, now: datetime) -> bool:
    initialize_database(path)
    mapping = {
        "done": ("done", "marked_done", None),
        "dismiss": ("dismissed", "dismissed", None),
        "snooze": ("snoozed", "snoozed", (now + timedelta(days=1)).isoformat()),
    }
    if action not in mapping:
        return False
    status, source, remind_after = mapping[action]
    with database_connection(path) as connection:
        cursor = connection.execute(
            """UPDATE email_threads SET status=?, status_source=?, remind_after=?, updated_at=?
            WHERE thread_id=?""",
            (status, source, remind_after, now.isoformat(), thread_id),
        )
    return cursor.rowcount == 1


def mark_gmail_thread_read(
    database: Path,
    client_secret: Path,
    token_path: Path,
    thread_id: str,
    now: datetime,
) -> bool:
    initialize_database(database)
    with database_connection(database) as connection:
        stored = connection.execute(
            "SELECT 1 FROM email_threads WHERE thread_id = ?", (thread_id,)
        ).fetchone()
    if stored is None:
        return False

    credentials = load_credentials(
        client_secret, token_path, interactive=False, require_modify=True
    )
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    service.users().threads().modify(
        userId="me",
        id=thread_id,
        body={"removeLabelIds": ["UNREAD"]},
    ).execute()
    with database_connection(database) as connection:
        connection.execute(
            "UPDATE email_threads SET is_unread=0, updated_at=? WHERE thread_id=?",
            (now.isoformat(), thread_id),
        )
    return True


def load_credentials(
    client_secret: Path,
    token_path: Path,
    interactive: bool,
    require_modify: bool = False,
) -> Credentials:
    credentials = None
    if token_path.exists():
        token = json.loads(token_path.read_text(encoding="utf-8"))
        token_scopes = set(token.get("scopes", []))
        read_scope = (
            GMAIL_MODIFY_SCOPE
            if GMAIL_MODIFY_SCOPE in token_scopes
            else GMAIL_READONLY_SCOPE
        )
        if (GMAIL_MODIFY_SCOPE if require_modify else read_scope) in token_scopes:
            credentials = Credentials.from_authorized_user_info(
                token, [GMAIL_MODIFY_SCOPE if require_modify else read_scope]
            )
    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())
    if not credentials or not credentials.valid:
        if not interactive:
            raise GmailAuthorizationRequired(
                "Gmail authorization required: run daily-reader-gmail auth"
            )
        flow = InstalledAppFlow.from_client_secrets_file(client_secret, [GMAIL_MODIFY_SCOPE])
        credentials = flow.run_local_server(
            host="127.0.0.1", port=0, open_browser=True, prompt="consent"
        )
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(credentials.to_json(), encoding="utf-8")
    os.chmod(token_path, 0o600)
    return credentials


def get_gmail_sync_status(path: Path) -> dict[str, Any] | None:
    initialize_database(path)
    with database_connection(path) as connection:
        connection.row_factory = sqlite3.Row
        row = connection.execute(
            "SELECT last_attempt_at, last_error, authorization_required, can_mark_read "
            "FROM gmail_sync_status WHERE id = 1"
        ).fetchone()
    return dict(row) if row else None


def record_gmail_sync_status(
    path: Path,
    attempted_at: datetime,
    error: str | None,
    authorization_required: bool,
    can_mark_read: bool,
) -> None:
    initialize_database(path)
    with database_connection(path) as connection:
        connection.execute(
            """
            INSERT INTO gmail_sync_status (
                id, last_attempt_at, last_error, authorization_required, can_mark_read
            ) VALUES (1, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                last_attempt_at=excluded.last_attempt_at,
                last_error=excluded.last_error,
                authorization_required=excluded.authorization_required,
                can_mark_read=excluded.can_mark_read
            """,
            (attempted_at.isoformat(), error, authorization_required, can_mark_read),
        )


def list_unread_threads(path: Path, now: datetime) -> list[dict[str, Any]]:
    initialize_database(path)
    with database_connection(path) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT * FROM email_threads
            WHERE status IN ('open', 'awaiting_reply', 'snoozed')
              AND is_unread = 1
              AND (remind_after IS NULL OR remind_after <= ?)
            ORDER BY CASE importance WHEN 'high' THEN 0 WHEN 'medium' THEN 1 ELSE 2 END,
              received_at DESC
            """,
            (now.isoformat(),),
        ).fetchall()
    items = [dict(row) for row in rows]
    for item in items:
        item["gmail_url"] = gmail_thread_url(item["account_email"], item["thread_id"])
    return items


def _headers(message: dict[str, Any]) -> dict[str, str]:
    return {
        item["name"].casefold(): item["value"]
        for item in message.get("payload", {}).get("headers", [])
        if "name" in item and "value" in item
    }


def _message_body(message: dict[str, Any], max_length: int = 1200) -> str:
    parts = [message.get("payload", {})]
    while parts:
        part = parts.pop(0)
        parts.extend(part.get("parts", []))
        if part.get("mimeType") not in {"text/plain", "text/html"}:
            continue
        encoded = part.get("body", {}).get("data")
        if encoded:
            return clean_message_body(
                base64.urlsafe_b64decode(encoded).decode(errors="replace"),
                part.get("mimeType") == "text/html",
                max_length,
            )
    return clean_message_text(message.get("snippet", ""), max_length)


def fetch_gmail_thread_content(
    database: Path,
    client_secret: Path,
    token_path: Path,
    thread_id: str,
) -> dict[str, Any] | None:
    initialize_database(database)
    with database_connection(database) as connection:
        connection.row_factory = sqlite3.Row
        stored = connection.execute(
            "SELECT subject, account_email FROM email_threads WHERE thread_id = ?",
            (thread_id,),
        ).fetchone()
    if stored is None:
        return None

    credentials = load_credentials(client_secret, token_path, interactive=False)
    service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
    thread = service.users().threads().get(
        userId="me", id=thread_id, format="full"
    ).execute()
    messages = sorted(
        thread.get("messages", []), key=lambda value: int(value["internalDate"])
    )
    content = []
    for message in messages:
        headers = _headers(message)
        received_at = datetime.fromtimestamp(
            int(message["internalDate"]) / 1000, tz=UTC
        ).isoformat()
        content.append(
            {
                "sender": headers.get("from", ""),
                "received_at": received_at,
                "body": _message_body(message, 8000),
            }
        )
    return {
        "thread_id": thread_id,
        "subject": stored["subject"],
        "account_email": stored["account_email"],
        "messages": content,
    }


def _list_thread_ids(service: Any, query: str) -> list[str]:
    thread_ids: list[str] = []
    page_token = None
    while True:
        request = service.users().threads().list(
            userId="me", q=query, maxResults=500, **(
                {"pageToken": page_token} if page_token else {}
            )
        )
        response = request.execute()
        thread_ids.extend(
            item["id"] for item in response.get("threads", []) if item.get("id")
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            return thread_ids


def sync_gmail(
    database: Path,
    client_secret: Path,
    token_path: Path,
    query: str = GMAIL_RECENT_QUERY,
    account_index: int = 0,
    interactive: bool = False,
) -> int:
    attempted_at = datetime.now(UTC)
    credentials = None
    try:
        credentials = load_credentials(client_secret, token_path, interactive)
        can_mark_read = GMAIL_MODIFY_SCOPE in set(credentials.scopes or ())
        service = build("gmail", "v1", credentials=credentials, cache_discovery=False)
        account_email = service.users().getProfile(userId="me").execute()["emailAddress"]
        unread_thread_ids = set(_list_thread_ids(service, GMAIL_UNREAD_QUERY))
        target_thread_ids = set(unread_thread_ids)
        if query != GMAIL_UNREAD_QUERY:
            target_thread_ids.update(_list_thread_ids(service, query))
        now = datetime.now(UTC)
        records: list[GmailThreadRecord] = []
        for thread_id in sorted(target_thread_ids):
            thread = service.users().threads().get(
                userId="me", id=thread_id, format="full"
            ).execute()
            messages = sorted(
                thread.get("messages", []), key=lambda value: int(value["internalDate"])
            )
            if not messages:
                continue
            latest = messages[-1]
            headers = _headers(latest)
            sender = headers.get("from", "")
            from_user = email.utils.parseaddr(sender)[1].casefold() == account_email.casefold()
            inbound = next(
                (
                    message
                    for message in reversed(messages)
                    if email.utils.parseaddr(_headers(message).get("from", ""))[1].casefold()
                    != account_email.casefold()
                ),
                latest,
            )
            assessment_headers = _headers(inbound)
            assessment = assess_email(
                assessment_headers.get("subject", headers.get("subject", "(件名なし)")),
                _message_body(inbound),
                assessment_headers,
                now,
            )
            received_at = datetime.fromtimestamp(int(latest["internalDate"]) / 1000, tz=UTC)
            records.append(
                GmailThreadRecord(
                    thread_id=thread["id"], latest_message_id=latest["id"],
                    account_email=account_email, subject=headers.get("subject", "(件名なし)"),
                    sender=sender, received_at=received_at.isoformat(),
                    snippet=clean_message_text(latest.get("snippet", ""), 300),
                    gmail_url=gmail_thread_url(account_email, thread["id"]),
                    importance=assessment.importance, importance_score=assessment.score,
                    reason=assessment.reason, required_action=assessment.required_action,
                    due_date=assessment.due_date,
                    status="awaiting_reply" if from_user else "open",
                    status_source="replied_by_user" if from_user else "classified",
                    is_unread=any("UNREAD" in message.get("labelIds", []) for message in messages),
                )
            )
        for record in records:
            upsert_thread(database, record, now)
        reconcile_unread_threads(database, unread_thread_ids)
        with database_connection(database) as connection:
            connection.execute(
                """
                INSERT INTO gmail_sync_state (id, completed_at, thread_count)
                VALUES (1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    completed_at=excluded.completed_at,
                    thread_count=excluded.thread_count
                """,
                (datetime.now(UTC).isoformat(), len(records)),
            )
        record_gmail_sync_status(database, attempted_at, None, False, can_mark_read)
        return len(records)
    except GmailAuthorizationRequired:
        record_gmail_sync_status(database, attempted_at, "authorization_required", True, False)
        raise
    except Exception:
        record_gmail_sync_status(
            database,
            attempted_at,
            "sync_failed",
            False,
            bool(credentials and GMAIL_MODIFY_SCOPE in set(credentials.scopes or ())),
        )
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Authorize or synchronize Gmail")
    parser.add_argument("command", choices=("auth", "sync"))
    parser.add_argument("--database", type=Path, default=Path("data/assistant.sqlite3"))
    parser.add_argument("--client-secret", type=Path, default=Path("secrets/gmail-client.json"))
    parser.add_argument("--token", type=Path, default=Path("secrets/gmail-token.json"))
    parser.add_argument("--query", default="newer_than:7d -in:spam -in:trash")
    parser.add_argument("--account-index", type=int, default=0)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    count = sync_gmail(
        args.database, args.client_secret, args.token, args.query,
        args.account_index, interactive=args.command == "auth"
    )
    print(f"Synchronized {count} Gmail threads")


if __name__ == "__main__":
    main()
