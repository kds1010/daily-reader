import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from google.oauth2.credentials import Credentials

from daily_reader import gmail_oauth as auth
from daily_reader.local_server import make_handler


@pytest.fixture
def manager(tmp_path, monkeypatch):
    client = tmp_path / "gmail-web-client.json"
    client.write_text(
        json.dumps(
            {
                "web": {
                    "client_id": "example.apps.googleusercontent.com",
                    "client_secret": "private-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": [auth.CALLBACK],
                }
            }
        )
    )
    client.chmod(0o600)
    monkeypatch.setattr(auth, "sync_gmail", lambda *args: None)
    return auth.GmailOAuth(client, tmp_path / "gmail-token.json", tmp_path / "mail.db")


def begin(manager):
    start = manager.start()
    ticket = parse_qs(urlsplit(start["browser_url"]).query)["ticket"][0]
    url, cookie = manager.open_browser(ticket)
    query = parse_qs(urlsplit(url).query)
    return start["session_id"], query, cookie


def credentials(**overrides):
    data = dict(
        token="access-private",
        refresh_token="refresh-private",
        token_uri="https://oauth2.googleapis.com/token",
        client_id="client",
        client_secret="secret",
        scopes=[auth.GMAIL_MODIFY_SCOPE],
        expiry=(datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None),
    )
    data.update(overrides)
    return Credentials(**data)


def install_exchange(manager, monkeypatch, value=None, action=None):
    def exchange(**kwargs):
        assert kwargs == {"code": "private-code", "timeout": 30}
        if action:
            action()

    flow = manager.session.flow
    monkeypatch.setattr(flow, "fetch_token", exchange)
    monkeypatch.setattr(type(flow), "credentials", property(lambda _: value or credentials()))


def callback(manager, query, cookie):
    return manager.callback(urlencode({"state": query["state"][0], "code": "private-code"}), cookie)


def test_real_authorization_url_has_pkce_offline_consent_and_fixed_callback(manager):
    identifier, query, _ = begin(manager)
    assert query["redirect_uri"] == [auth.CALLBACK]
    assert query["scope"] == [auth.GMAIL_MODIFY_SCOPE]
    assert query["access_type"] == ["offline"] and query["prompt"] == ["consent"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43
    import base64

    expected = (
        base64.urlsafe_b64encode(
            hashlib.sha256(manager.session.flow.code_verifier.encode()).digest()
        )
        .decode()
        .rstrip("=")
    )
    assert query["code_challenge"] == [expected]
    assert set(manager.status(identifier)) == {"status", "message"}
    with pytest.raises(auth.OAuthError):
        manager.start()
    with pytest.raises(auth.OAuthError):
        manager.open_browser("")


@pytest.mark.parametrize("bad", ["state", "cookie", "duplicate", "no-cookie"])
def test_browser_and_state_binding_reject_invalid_callbacks_without_consuming(manager, bad):
    _, query, cookie = begin(manager)
    state = query["state"][0]
    pairs = [("state", "wrong" if bad == "state" else state), ("code", "private-code")]
    if bad == "duplicate":
        pairs.append(("state", state))
    with pytest.raises(auth.OAuthError):
        manager.callback(
            urlencode(pairs), "wrong" if bad == "cookie" else "" if bad == "no-cookie" else cookie
        )
    assert manager.session.phase == "pending"
    assert not manager.token.exists()


def test_success_atomically_saves_private_token_syncs_and_rejects_replay(manager, monkeypatch):
    manager.token.write_text("old-token")
    identifier, query, cookie = begin(manager)
    synced = threading.Event()
    monkeypatch.setattr(auth, "sync_gmail", lambda *args: synced.set())
    install_exchange(manager, monkeypatch)
    assert callback(manager, query, cookie)["status"] == "syncing"
    assert synced.wait(2)
    assert manager.status(identifier)["status"] == "connected"
    assert json.loads(manager.token.read_text())["refresh_token"] == "refresh-private"
    assert manager.token.stat().st_mode & 0o777 == 0o600
    assert manager.session.flow is None and manager.session.original is None
    with pytest.raises(auth.OAuthError):
        callback(manager, query, cookie)


@pytest.mark.parametrize(
    "failure", ["no-refresh", "scope", "expired", "exchange", "save", "conflict"]
)
def test_failed_grant_preserves_existing_credentials(manager, monkeypatch, failure):
    manager.token.write_text("old-token")
    _, query, cookie = begin(manager)
    value = credentials()
    if failure == "no-refresh":
        value = credentials(refresh_token=None)
    if failure == "scope":
        value = credentials(granted_scopes=[])
    if failure == "expired":
        value = credentials(expiry=datetime(2000, 1, 1))

    def fail(*args):
        raise RuntimeError("private-provider-response")

    if failure == "save":
        monkeypatch.setattr(auth, "_save_credentials", fail)
    action = fail if failure == "exchange" else None
    if failure == "conflict":
        def action():
            manager.token.write_text("newer-token")
    install_exchange(manager, monkeypatch, value, action)
    result = callback(manager, query, cookie)
    assert result["status"] == "failed"
    assert "private" not in json.dumps(result)
    assert manager.token.read_text() == ("newer-token" if failure == "conflict" else "old-token")


def test_expiry_cancel_unknown_session_and_server_restart(manager):
    identifier, query, cookie = begin(manager)
    assert manager.cancel("wrong")["status"] == "expired"
    assert manager.session.phase == "pending"
    assert manager.cancel(identifier)["status"] == "cancelled"
    with pytest.raises(auth.OAuthError):
        callback(manager, query, cookie)
    second, _, _ = begin(manager)
    manager.session.deadline = 0
    assert manager.status(second)["status"] == "expired"
    assert manager.session.flow is None
    assert manager.status(identifier)["status"] == "expired"
    manager.session = None
    assert manager.status(second)["status"] == "expired"


def test_google_denial_keeps_token(manager):
    manager.token.write_text("old")
    identifier, query, cookie = begin(manager)
    result = manager.callback(
        urlencode({"state": query["state"][0], "error": "access_denied"}), cookie
    )
    assert result["status"] == "cancelled"
    assert manager.status(identifier)["status"] == "cancelled"
    assert manager.token.read_text() == "old"


def test_only_one_callback_can_exchange(manager, monkeypatch):
    identifier, query, cookie = begin(manager)

    def check_inflight():
        assert manager.status(identifier)["status"] == "exchanging"
        for attempt in (
            lambda: callback(manager, query, cookie),
            manager.start,
            lambda: manager.cancel(identifier),
        ):
            with pytest.raises(auth.OAuthError):
                attempt()

    install_exchange(manager, monkeypatch, action=check_inflight)
    assert callback(manager, query, cookie)["status"] == "syncing"


def test_sync_failure_is_distinct_from_consent_failure(manager, monkeypatch):
    identifier, query, cookie = begin(manager)
    install_exchange(manager, monkeypatch)
    monkeypatch.setattr(threading.Thread, "start", lambda self: self.run())

    def fail(*args):
        raise RuntimeError("private")

    monkeypatch.setattr(auth, "sync_gmail", fail)
    callback(manager, query, cookie)
    assert manager.status(identifier)["status"] == "sync_failed"
    assert manager.token.exists()


@pytest.mark.parametrize(
    "failure", ["missing", "permissions", "desktop", "redirect", "endpoint", "symlink"]
)
def test_config_validation(manager, failure):
    if failure == "missing":
        manager.client.unlink()
    elif failure == "permissions":
        manager.client.chmod(0o644)
    elif failure == "symlink":
        original = manager.client.with_suffix(".original")
        manager.client.rename(original)
        manager.client.symlink_to(original)
    else:
        config = json.loads(manager.client.read_text())
        if failure == "desktop":
            config = {"installed": config["web"]}
        elif failure == "redirect":
            config["web"]["redirect_uris"] = ["https://example.com"]
        else:
            config["web"]["token_uri"] = "https://example.com/steal"
        manager.client.write_text(json.dumps(config))
    assert manager.configuration()["configured"] is False
    with pytest.raises(auth.OAuthError):
        manager.start()
    assert not manager.token.exists()


@pytest.fixture
def http_api(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(auth, "GmailOAuth", lambda *args: manager)
    factory = make_handler(
        *(
            tmp_path / name
            for name in [
                "site",
                "articles",
                "reads",
                "feedback",
                "mail.db",
                "client.json",
                "token.json",
            ]
        )
    )
    logs = []
    monkeypatch.setattr(
        factory.func, "log_message", lambda self, fmt, *args: logs.append(fmt % args)
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), factory)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    def request(path, body=None, headers=None):
        connection = HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        values = {"Host": "sk-mins-mac-mini.tailc193b2.ts.net"}
        if body is not None:
            values["Content-Type"] = "application/json"
        values.update(headers or {})
        connection.request(
            "POST" if body is not None else "GET",
            path,
            body=json.dumps(body) if body is not None else None,
            headers=values,
        )
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    yield request, logs
    server.shutdown()
    server.server_close()
    thread.join()


def test_http_browser_handoff_cookie_callback_and_no_secret_logs(manager, http_api, monkeypatch):
    request, logs = http_api
    status, _, data = request(auth.PREFIX + "start", {})
    assert status == 200
    start = json.loads(data)
    path = urlsplit(start["browser_url"])
    status, headers, _ = request(path.path + "?" + path.query)
    assert status == 303 and headers["Referrer-Policy"] == "no-referrer"
    assert all(flag in headers["Set-Cookie"] for flag in ["Secure", "HttpOnly", "SameSite=Lax"])
    query = parse_qs(urlsplit(headers["Location"]).query)
    install_exchange(manager, monkeypatch)
    status, headers, body = request(
        auth.PREFIX
        + "callback?"
        + urlencode(
            {
                "state": query["state"][0],
                "code": "private-code",
            }
        ),
        headers={"Cookie": headers["Set-Cookie"], "Sec-Fetch-Site": "cross-site"},
    )
    assert status == 200
    assert headers["Cache-Control"] == "no-store"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]
    assert b"private-code" not in body
    assert not any(
        secret in " ".join(logs)
        for secret in ["private-code", query["state"][0], start["session_id"], "ticket="]
    )


@pytest.mark.parametrize(
    "headers",
    [
        {"Host": "evil.example"},
        {"Host": "sk-mins-mac-mini.tailc193b2.ts.net:8443"},
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
    ],
)
def test_http_start_rejects_cross_origin_and_funnel(http_api, headers):
    request, _ = http_api
    assert request(auth.PREFIX + "start", {}, headers)[0] == 403


def test_http_rejects_form_posts_and_malformed_bodies_and_callback(http_api):
    request, _ = http_api
    assert request(auth.PREFIX + "start", {}, {"Content-Type": "text/plain"})[0] == 415
    assert request(auth.PREFIX + "status", {"session_id": ["bad"]})[0] == 400
    assert request(auth.PREFIX + "callback?code=private")[0] == 400
    status, _, body = request(auth.PREFIX + "config")
    assert status == 200 and json.loads(body)["configured"] is True
