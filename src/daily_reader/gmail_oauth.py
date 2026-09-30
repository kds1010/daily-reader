"""Tailnet-only browser consent, with short-lived in-memory sessions.

Never log OAuth URLs, query strings, library exceptions or credentials.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs

from google_auth_oauthlib.flow import Flow

from daily_reader.email_assistant import (
    GMAIL_MODIFY_SCOPE,
    _granted_scopes,
    _read_token,
    _save_credentials,
    _token_lock,
    sync_gmail,
)

ORIGIN = "https://sk-mins-mac-mini.tailc193b2.ts.net"
PREFIX = "/api/gmail-auth/"
CALLBACK = ORIGIN + PREFIX + "callback"
COOKIE = "__Host-daymeld-gmail"
TTL = 600
ACTIVE = {"pending", "exchanging", "syncing"}
MESSAGES = {
    "pending": "Googleでログインと同意を完了し、Daymeldに戻ってください。",
    "exchanging": "認証結果を確認しています。",
    "syncing": "認証情報を保存しました。メールを同期しています。",
    "connected": "Gmailに接続し、メールの同期が完了しました。",
    "sync_failed": ("認証情報は保存済みですが、メール同期に失敗しました。"
                    "次の定期同期で再試行します。"),
    "failed": "認証を完了できませんでした。既存の認証情報は保持しました。再接続してください。",
    "cancelled": "認証をキャンセルしました。既存の認証情報は保持しました。",
    "expired": "認証の待機期限が切れました。もう一度開始してください。",
}


class OAuthError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass(repr=False)
class Session:
    identifier: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    ticket: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    state: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    browser: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    deadline: float = field(default_factory=lambda: time.monotonic() + TTL)
    phase: str = "pending"
    flow: Flow | None = None
    original: str | None = None

    def public(self) -> dict:
        return {"status": self.phase, "message": MESSAGES[self.phase]}

    def clear(self) -> None:
        self.flow = None
        self.original = None
        self.ticket = ""
        self.browser = ""
        self.state = ""


def _matches(expected: str, actual: str) -> bool:
    return bool(expected and actual) and hmac.compare_digest(expected.encode(), actual.encode())


class GmailOAuth:
    def __init__(self, client: Path, token: Path, database: Path):
        self.client, self.token, self.database = client, token, database
        self.lock = threading.Lock()
        self.session: Session | None = None

    def _config(self) -> dict:
        try:
            info = self.client.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.getuid()
                or info.st_mode & 0o077
                or info.st_size > 32_768
            ):
                raise ValueError("unsafe client file")
            config = json.loads(self.client.read_text())
            web = config["web"]
            if (
                web["auth_uri"] != "https://accounts.google.com/o/oauth2/auth"
                and web["auth_uri"] != "https://accounts.google.com/o/oauth2/v2/auth"
            ):
                raise ValueError("invalid authorization endpoint")
            if (
                web["token_uri"] != "https://oauth2.googleapis.com/token"
                or not isinstance(web["redirect_uris"], list)
                or CALLBACK not in web["redirect_uris"]
                or not isinstance(web["client_id"], str)
                or not web["client_id"]
                or not isinstance(web["client_secret"], str)
                or not web["client_secret"]
            ):
                raise ValueError("invalid client")
            return {"web": web}
        except (OSError, ValueError, KeyError, TypeError):
            raise OAuthError(
                "Mac miniにWebアプリ用のGmail認証設定が必要です。設定手順を確認してください。",
                503,
            ) from None

    def configuration(self) -> dict:
        try:
            self._config()
        except OAuthError as error:
            return {"configured": False, "message": str(error)}
        return {"configured": True, "message": "iPhone・MacからGoogleの認証を開始できます。"}

    def _expire(self) -> None:
        session = self.session
        if session and session.phase == "pending" and time.monotonic() >= session.deadline:
            session.phase = "expired"
            session.clear()

    def _session(self, identifier: str) -> Session:
        self._expire()
        if not self.session or not _matches(self.session.identifier, identifier):
            raise OAuthError("認証要求が見つかりません。もう一度開始してください。", 404)
        return self.session

    def start(self) -> dict:
        with self.lock:
            self._expire()
            if self.session and self.session.phase in ACTIVE:
                raise OAuthError(
                    "認証が進行中です。元の画面で完了・キャンセルするか、10分後に再試行してください。",
                    409,
                )
            config = self._config()
            session = Session()
            session.flow = Flow.from_client_config(
                config,
                scopes=[GMAIL_MODIFY_SCOPE],
                state=session.state,
                redirect_uri=CALLBACK,
                autogenerate_code_verifier=True,
            )
            with _token_lock(self.token):
                session.original = _read_token(self.token)
            self.session = session
            return {
                **session.public(),
                "session_id": session.identifier,
                "browser_url": ORIGIN + PREFIX + "open?ticket=" + session.ticket,
            }

    def status(self, identifier: str) -> dict:
        with self.lock:
            try:
                return self._session(identifier).public()
            except OAuthError:
                return {"status": "expired", "message": MESSAGES["expired"]}

    def cancel(self, identifier: str) -> dict:
        with self.lock:
            try:
                session = self._session(identifier)
            except OAuthError:
                return {"status": "expired", "message": MESSAGES["expired"]}
            if session.phase in {"exchanging", "syncing"}:
                raise OAuthError("認証結果を処理中です。完了までお待ちください。", 409)
            if session.phase == "pending":
                session.phase = "cancelled"
                session.clear()
            return session.public()

    def open_browser(self, ticket: str) -> tuple[str, str]:
        with self.lock:
            self._expire()
            session = self.session
            if not session or session.phase != "pending" or not _matches(session.ticket, ticket):
                raise OAuthError(
                    "認証リンクが無効または使用済みです。アプリからやり直してください。"
                )
            url, _ = session.flow.authorization_url(access_type="offline", prompt="consent")
            session.ticket = ""
            return url, session.browser

    def callback(self, query: str, browser: str) -> dict:
        values = parse_qs(query, keep_blank_values=True, max_num_fields=20)
        with self.lock:
            self._expire()
            session = self.session
            if (
                not session
                or session.phase != "pending"
                or len(values.get("state", [])) != 1
                or not _matches(session.state, values["state"][0])
                or not _matches(session.browser, browser)
                or session.ticket
            ):
                raise OAuthError("認証要求を確認できません。アプリからやり直してください。")
            if values.get("error") == ["access_denied"] and "code" not in values:
                session.phase = "cancelled"
                session.clear()
                return session.public()
            if "error" in values or len(values.get("code", [])) != 1 or not values["code"][0]:
                session.phase = "failed"
                session.clear()
                return session.public()
            session.phase = "exchanging"
        try:
            # State/browser were checked above. Pass only the code, never attacker URL fields.
            session.flow.fetch_token(code=values["code"][0], timeout=30)
            credentials = session.flow.credentials
            if (
                not credentials.valid
                or not credentials.refresh_token
                or GMAIL_MODIFY_SCOPE not in _granted_scopes(credentials)
            ):
                raise ValueError("incomplete grant")
            with _token_lock(self.token):
                if _read_token(self.token) != session.original:
                    raise ValueError("credentials changed during consent")
                _save_credentials(self.token, credentials)
        except Exception:  # noqa: BLE001 - do not expose provider response or tokens
            with self.lock:
                session.phase = "failed"
                session.clear()
                return session.public()
        with self.lock:
            session.phase = "syncing"
            session.clear()
            result = session.public()
        threading.Thread(target=self._sync, args=(session,), daemon=True).start()
        return result

    def _sync(self, session: Session) -> None:
        try:
            sync_gmail(self.database, self.client, self.token)
            phase = "connected"
        except Exception:  # noqa: BLE001 - success of consent is distinct from sync
            phase = "sync_failed"
        with self.lock:
            session.phase = phase
