import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest

from daily_reader import connection_alerts as alerts
from daily_reader import drive_sync, local_server


def test_alert_episode_survives_unknown_observation_and_reopens_after_recovery(tmp_path):
    assert alerts.active_alerts(tmp_path) == []
    assert not (tmp_path / alerts.DATABASE_NAME).exists()
    alerts.report_soundcore(tmp_path, {"state": "unavailable"})
    assert alerts.active_alerts(tmp_path) == []
    alerts.report_soundcore(tmp_path, {
        "state": "authentication_required", "reason": "sign_in_required",
    })
    first = alerts.active_alerts(tmp_path)[0]
    alerts.report_soundcore(tmp_path, {
        "state": "authentication_required", "reason": "verification_required",
    })
    changed = alerts.active_alerts(tmp_path)[0]
    assert changed["id"] == first["id"] and changed["occurred_at"] == first["occurred_at"]
    assert changed["message"] != first["message"]
    alerts.report_soundcore(tmp_path, {"state": "unavailable"})
    assert alerts.active_alerts(tmp_path) == [changed]
    assert (tmp_path / alerts.DATABASE_NAME).stat().st_mode & 0o777 == 0o600
    alerts.report_soundcore(tmp_path, {"state": "connected"})
    assert alerts.active_alerts(tmp_path) == []
    alerts.report_soundcore(tmp_path, {
        "state": "authentication_required", "reason": "sign_in_required",
    })
    assert alerts.active_alerts(tmp_path)[0]["id"] != first["id"]


def test_concurrent_failure_observations_share_one_episode(tmp_path):
    def update(_):
        alerts.report(tmp_path, "soundcore", "authentication_required", "sign_in_required")
        return alerts.active_alerts(tmp_path)[0]["id"]

    with ThreadPoolExecutor(max_workers=8) as executor:
        assert len(set(executor.map(update, range(24)))) == 1
    alerts.report(tmp_path, "google_drive", "authentication_required", "permission_required")
    assert len(alerts.active_alerts(tmp_path)) == 2
    alerts.report(tmp_path, "soundcore", "connected")
    assert [item["provider"] for item in alerts.active_alerts(tmp_path)] == ["google_drive"]


@pytest.mark.parametrize("payload", [
    None, [], {}, {"state": []}, {"state": "private-data"},
    {"state": "authentication_required"}, {"state": "authentication_required", "reason": []},
    {"state": "authentication_required", "reason": "private-secret"},
    {"state": "connected", "reason": None}, {"state": "unavailable", "reason": "sign_in_required"},
    {"state": "connected", "url": "https://private.example"},
    {"state": "connected", "provider": "google_drive"},
    {"state": "connected", "message": "private-data"},
])
def test_soundcore_rejects_unstructured_or_sensitive_input_without_persisting(tmp_path, payload):
    with pytest.raises(ValueError) as caught:
        alerts.report_soundcore(tmp_path, payload)
    assert "private" not in str(caught.value)
    assert not (tmp_path / alerts.DATABASE_NAME).exists()


def test_alert_database_symlink_cannot_read_or_replace_another_file(tmp_path):
    original = tmp_path / "unrelated"
    original.write_text("unchanged")
    (tmp_path / alerts.DATABASE_NAME).symlink_to(original)
    with pytest.raises(OSError):
        alerts.report(tmp_path, "soundcore", "connected")
    with pytest.raises(OSError):
        alerts.active_alerts(tmp_path)
    assert original.read_text() == "unchanged"


@pytest.fixture
def handler(tmp_path, monkeypatch):
    factory = local_server.make_handler(
        tmp_path / "site", tmp_path / "articles", tmp_path / "read", tmp_path / "feedback",
        tmp_path / "assistant", tmp_path / "client", tmp_path / "token",
        conversations_db=tmp_path / "must-not-create-audio-db",
        connection_alert_data_dir=tmp_path,
    )
    result = factory.func.__new__(factory.func)
    responses = []
    result._send_json = lambda code, payload: responses.append((code, payload))
    result.headers = {"Host": "127.0.0.1:8787", "Content-Type": "application/json"}
    result.client_address = ("127.0.0.1", 1234)
    result.path = "/api/connection-health/soundcore"
    monkeypatch.setattr(local_server, "list_jobs", lambda database: [])
    monkeypatch.setattr(local_server, "start_analysis", lambda *args: pytest.fail("No audio work"))
    return result, responses


def set_body(request, payload):
    content = json.dumps(payload).encode()
    request.rfile = io.BytesIO(content)
    request.headers["Content-Length"] = str(len(content))


def test_http_report_list_background_envelope_and_recovery(handler, tmp_path):
    request, responses = handler
    set_body(request, {"state": "authentication_required", "reason": "verification_required"})
    request.do_POST()
    assert responses[-1] == (200, {"updated": True})
    request.path = "/api/connection-alerts"
    request.headers["Host"] = "sk-mins-mac-mini.tailc193b2.ts.net"
    request.do_GET()
    alert = responses[-1][1]["alerts"][0]
    assert set(alert) == {"id", "provider", "title", "message", "occurred_at"}
    request.path = "/api/agent-notifications"
    request.do_GET()
    assert responses[-1] == (200, {"jobs": [], "connection_alerts": [alert]})
    request.path = "/api/connection-health/soundcore"
    request.headers["Host"] = "localhost:8787"
    set_body(request, {"state": "connected"})
    request.do_POST()
    request.path = "/api/connection-alerts"
    request.do_GET()
    assert responses[-1] == (200, {"alerts": []})
    assert not (tmp_path / "must-not-create-audio-db").exists()


@pytest.mark.parametrize("peer,headers", [
    ("192.168.10.2", {}), ("100.64.0.1", {}),
    ("127.0.0.1", {"Host": "sk-mins-mac-mini.tailc193b2.ts.net"}),
    ("127.0.0.1", {"Host": "untrusted.example"}),
    ("127.0.0.1", {"Origin": "https://untrusted.example"}),
    ("127.0.0.1", {"Sec-Fetch-Site": "cross-site"}),
    ("127.0.0.1", {"X-Forwarded-For": "100.64.0.1"}),
    ("127.0.0.1", {"X-Forwarded-Proto": "https"}),
    ("127.0.0.1", {"X-Forwarded-Host": "127.0.0.1:8787"}),
    ("127.0.0.1", {"Forwarded": "for=100.64.0.1"}),
    ("127.0.0.1", {"Tailscale-User-Login": "anonymous"}),
    ("127.0.0.1", {"x-FORWARDED-for": ""}),
])
def test_soundcore_write_access_is_local_only_before_reading_body(handler, peer, headers):
    request, responses = handler
    request.client_address = (peer, 1234)
    request.headers.update(headers)
    request._read_json = lambda **kwargs: pytest.fail("Must reject before reading")
    request.do_POST()
    assert responses[-1][0] == 403


@pytest.mark.parametrize("headers,expected", [
    ({"Content-Type": "text/plain"}, 415), ({"Transfer-Encoding": "chunked"}, 400),
    ({"Content-Length": "2048"}, 400),
])
def test_soundcore_rejects_invalid_transfer(handler, headers, expected):
    request, responses = handler
    set_body(request, {"state": "connected"})
    request.headers.update(headers)
    request.do_POST()
    assert responses[-1][0] == expected


def test_health_read_failure_keeps_job_notifications_and_marks_alerts_unknown(handler, monkeypatch):
    request, responses = handler

    def unavailable(_):
        raise sqlite3.OperationalError("private-path must not be printed")

    monkeypatch.setattr(alerts, "active_alerts", unavailable)
    request.path = "/api/agent-notifications"
    request.do_GET()
    assert responses[-1] == (200, {"jobs": [], "connection_alerts": None})
    request.path = "/api/connection-alerts"
    request.do_GET()
    assert responses[-1][0] == 503 and "private" not in json.dumps(responses[-1])


def test_soundcore_storage_failure_and_logs_exclude_private_inputs(handler, monkeypatch):
    request, responses = handler

    def unavailable(*_):
        raise OSError("private-path")

    monkeypatch.setattr(alerts, "report_soundcore", unavailable)
    set_body(request, {"state": "connected"})
    request.do_POST()
    assert responses[-1][0] == 503 and "private" not in json.dumps(responses[-1])
    messages = []
    request.log_message = lambda fmt, *args: messages.append(fmt % args)
    request.path += "?secret=private-input"
    request.log_request(503)
    assert "private" not in " ".join(messages)


def run_worker_once(tmp_path, monkeypatch, outcome):
    worker = drive_sync.DriveSyncWorker(tmp_path)

    def once(*args, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(drive_sync, "sync_once", once)
    monkeypatch.setattr(worker._stop, "wait", lambda seconds: True)
    worker._run()


@pytest.mark.parametrize("code", sorted(drive_sync.AUTH_ALERT_REASONS))
def test_drive_worker_reports_actionable_failure_and_success_resolves(tmp_path, monkeypatch, code):
    run_worker_once(tmp_path, monkeypatch, drive_sync.DriveSyncError(code))
    first = alerts.active_alerts(tmp_path)[0]
    assert first["provider"] == "google_drive"
    run_worker_once(tmp_path, monkeypatch, drive_sync.DriveSyncError(code))
    assert alerts.active_alerts(tmp_path)[0]["id"] == first["id"]
    run_worker_once(tmp_path, monkeypatch, {"status": "ready", "failed": 0})
    assert alerts.active_alerts(tmp_path) == []


@pytest.mark.parametrize("outcome", [
    {"status": "not_configured"}, {"status": "attention_required", "failed": 1},
    {"status": "ready", "failed": 1},
    *[drive_sync.DriveSyncError(code) for code in (
        "busy", "drive_api_failed", "auth_refresh_failed", "source_folder_unavailable",
        "audio_download_failed", "stopped",
    )],
    OSError("private-network-details"),
])
def test_drive_worker_unknown_or_unconfigured_does_not_create_or_resolve_auth_alert(
    tmp_path, monkeypatch, outcome,
):
    run_worker_once(tmp_path, monkeypatch, outcome)
    assert alerts.active_alerts(tmp_path) == []
    alerts.report(tmp_path, "google_drive", "authentication_required", "sign_in_required")
    first = alerts.active_alerts(tmp_path)
    run_worker_once(tmp_path, monkeypatch, outcome)
    assert alerts.active_alerts(tmp_path) == first


def test_notification_storage_failure_cannot_break_drive_worker(tmp_path, monkeypatch):
    called = []

    def unavailable(*args):
        called.append(True)
        raise OSError("private-storage-path")

    monkeypatch.setattr(alerts, "report", unavailable)
    run_worker_once(tmp_path, monkeypatch, drive_sync.DriveSyncError("auth_required"))
    run_worker_once(tmp_path, monkeypatch, {"status": "ready"})
    assert called == [True, True]
