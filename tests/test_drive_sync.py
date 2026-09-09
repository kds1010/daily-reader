import hashlib
import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest
from google.auth.exceptions import RefreshError
from googleapiclient.errors import HttpError

from daily_reader import drive_sync as sync


class FakeCredentials:
    def __init__(self, *, valid=True, expired=False, scopes=None, granted=None, refresh="refresh"):
        self.valid = valid
        self.expired = expired
        self.scopes = scopes if scopes is not None else [sync.DRIVE_SCOPE]
        self.granted_scopes = granted
        self.refresh_token = refresh
        self.refresh_error = None

    def refresh(self, request):
        if self.refresh_error:
            raise self.refresh_error
        self.valid = True
        self.expired = False

    def to_json(self):
        return json.dumps(
            {"token": "private-access", "refresh_token": self.refresh_token, "scopes": self.scopes}
        )


def token_file(tmp_path):
    path = tmp_path / "drive-token.json"
    path.write_text(FakeCredentials().to_json())
    return path


def test_auth_only_requests_drive_offline_without_url_output(tmp_path, monkeypatch, capsys):
    token = tmp_path / "drive-token.json"
    calls = {}

    def from_client(path, scopes):
        calls["scopes"] = scopes
        return SimpleNamespace(run_local_server=authorize)

    def authorize(**kwargs):
        calls.update(kwargs)
        assert logging.root.manager.disable == logging.CRITICAL
        return FakeCredentials()

    monkeypatch.setattr(sync.InstalledAppFlow, "from_client_secrets_file", from_client)
    prior_logging = logging.root.manager.disable
    sync.load_credentials(tmp_path / "gmail-client.json", token, interactive=True)
    assert calls["scopes"] == [sync.DRIVE_SCOPE]
    assert calls["authorization_prompt_message"] is None
    assert calls["host"] == "127.0.0.1"
    assert calls["port"] == 0
    assert calls["timeout_seconds"] == 180
    assert calls["open_browser"] and calls["access_type"] == "offline"
    assert calls["prompt"] == "consent"
    assert token.stat().st_mode & 0o777 == 0o600
    assert (tmp_path / "drive-token.json.lock").stat().st_mode & 0o777 == 0o600
    assert logging.root.manager.disable == prior_logging
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("failure", [RuntimeError("private-url?code=private"), KeyboardInterrupt()])
def test_auth_cancellation_preserves_existing_token(tmp_path, monkeypatch, failure, capsys):
    token = token_file(tmp_path)
    original = token.read_bytes()

    def authorize(**kwargs):
        raise failure

    monkeypatch.setattr(
        sync.InstalledAppFlow,
        "from_client_secrets_file",
        lambda *args: SimpleNamespace(run_local_server=authorize),
    )
    with pytest.raises(sync.DriveSyncError, match="auth_cancelled_or_failed"):
        sync.load_credentials(tmp_path / "client.json", token, interactive=True, force=True)
    assert token.read_bytes() == original
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize(
    "credentials",
    [
        FakeCredentials(granted=["unrelated"]),
        FakeCredentials(refresh=None),
    ],
)
def test_auth_missing_grant_or_refresh_preserves_token(tmp_path, monkeypatch, credentials):
    token = token_file(tmp_path)
    original = token.read_bytes()
    monkeypatch.setattr(
        sync.InstalledAppFlow,
        "from_client_secrets_file",
        lambda *args: SimpleNamespace(run_local_server=lambda **kwargs: credentials),
    )
    with pytest.raises(sync.DriveSyncError, match="auth_scope_or_offline_access_missing"):
        sync.load_credentials(tmp_path / "client.json", token, interactive=True, force=True)
    assert token.read_bytes() == original


def test_auth_concurrent_token_change_not_overwritten(tmp_path, monkeypatch):
    token = token_file(tmp_path)

    def authorize(**kwargs):
        token.write_text('{"concurrent":true}')
        return FakeCredentials()

    monkeypatch.setattr(
        sync.InstalledAppFlow,
        "from_client_secrets_file",
        lambda *args: SimpleNamespace(run_local_server=authorize),
    )
    with pytest.raises(sync.DriveSyncError, match="auth_changed_during_consent"):
        sync.load_credentials(tmp_path / "client.json", token, interactive=True, force=True)
    assert json.loads(token.read_text()) == {"concurrent": True}


def test_damaged_token_can_be_replaced_only_after_successful_consent(tmp_path, monkeypatch):
    token = tmp_path / "drive-token.json"
    token.write_text("broken JSON")
    monkeypatch.setattr(
        sync.InstalledAppFlow,
        "from_client_secrets_file",
        lambda *args: SimpleNamespace(run_local_server=lambda **kwargs: FakeCredentials()),
    )
    sync.load_credentials(tmp_path / "client.json", token, interactive=True)
    assert json.loads(token.read_text())["scopes"] == [sync.DRIVE_SCOPE]


def test_saved_gmail_scope_is_never_upgraded_by_requested_scopes(tmp_path, monkeypatch):
    token = tmp_path / "drive-token.json"
    token.write_text('{"scopes":["gmail-only"],"refresh_token":"private"}')
    monkeypatch.setattr(
        sync.Credentials,
        "from_authorized_user_info",
        lambda *args: pytest.fail("Must inspect persisted scopes first"),
    )
    with pytest.raises(sync.DriveSyncError, match="auth_required"):
        sync.load_credentials(tmp_path / "client.json", token)


def test_refresh_invalid_grant_keeps_token_and_requires_auth(tmp_path, monkeypatch):
    token = token_file(tmp_path)
    original = token.read_bytes()
    credentials = FakeCredentials(expired=True, valid=False)
    credentials.refresh_error = RefreshError("private detail", {"error": "invalid_grant"})
    monkeypatch.setattr(sync.Credentials, "from_authorized_user_info", lambda value: credentials)
    with pytest.raises(sync.DriveSyncError, match="auth_required"):
        sync.load_credentials(tmp_path / "client.json", token)
    assert token.read_bytes() == original


def test_successful_refresh_is_atomic_private_and_scope_checked(tmp_path, monkeypatch):
    token = token_file(tmp_path)
    credentials = FakeCredentials(expired=True, valid=False)
    monkeypatch.setattr(sync.Credentials, "from_authorized_user_info", lambda value: credentials)
    assert sync.load_credentials(tmp_path / "client.json", token) is credentials
    assert token.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".drive-*")) == []
    original = token.read_bytes()
    credentials.expired = True
    credentials.valid = False
    credentials.granted_scopes = ["unrelated"]
    with pytest.raises(sync.DriveSyncError, match="auth_required"):
        sync.load_credentials(tmp_path / "client.json", token)
    assert token.read_bytes() == original


@pytest.mark.parametrize("name", ["gmail-token.json", "gmail-client.json"])
def test_auth_refuses_overwriting_gmail_files(tmp_path, name):
    client = tmp_path / "gmail-client.json"
    with pytest.raises(sync.DriveSyncError, match="separate_drive_token_required"):
        sync.load_credentials(client, tmp_path / name)
    assert list(tmp_path.iterdir()) == []


def test_status_is_read_only_and_excludes_sensitive_metadata(tmp_path):
    data = tmp_path / "missing"
    token = tmp_path / "drive-token.json"
    assert sync.status(data, token)["status"] == "not_configured"
    assert not data.exists() and not token.exists()
    sync._atomic_json(data / "drive-sync.json", {"folder_id": "private-folder", "status": "ready"})
    token.write_text(FakeCredentials().to_json())
    rendered = json.dumps(sync.status(data, token))
    assert "private" not in rendered.replace('"token_private"', "")
    assert "authorized" in rendered


class Request:
    def __init__(self, value):
        self.value = value

    def execute(self, **kwargs):
        return self.value() if callable(self.value) else self.value


class FakeDrive:
    def __init__(self):
        self.metadata = {
            "root": {
                "id": "root",
                "name": "SoundCore",
                "mimeType": sync.FOLDER_MIME,
                "parents": [],
            },
        }
        self.pages = {"root": [[]]}
        self.blobs = {}
        self.listed = []
        self.downloaded = []
        self.after_download = None

    def files(self):
        return self

    def get(self, *, fileId, **kwargs):
        return Request(lambda: dict(self.metadata[fileId]))

    def list(self, *, q, pageToken=None, **kwargs):
        folder = q.split("'")[1]
        self.listed.append((folder, pageToken))
        index = int(pageToken or 0)
        pages = self.pages[folder]
        result = {"files": [dict(self.metadata[value]) for value in pages[index]]}
        if index + 1 < len(pages):
            result["nextPageToken"] = str(index + 1)
        return Request(result)

    def get_media(self, *, fileId, **kwargs):
        self.downloaded.append(fileId)
        return (self, fileId)

    def add_folder(self, folder="recording", parent="root"):
        self.metadata[folder] = {
            "id": folder,
            "name": "2026-09-09 17:21:48",
            "mimeType": sync.FOLDER_MIME,
            "parents": [parent],
        }
        self.pages[folder] = [[]]
        self.pages[parent][0].append(folder)

    def add_audio(self, file_id="audio", parent="root", blob=b"OggS\x00example-audio"):
        self.blobs[file_id] = blob
        self.metadata[file_id] = {
            "id": file_id,
            "name": "録音.ogg",
            "mimeType": "audio/ogg",
            "size": str(len(blob)),
            "modifiedTime": "2026-09-09T08:23:00Z",
            "version": "1",
            "parents": [parent],
            "md5Checksum": hashlib.md5(blob, usedforsecurity=False).hexdigest(),
            "capabilities": {"canDownload": True},
        }
        self.pages[parent][0].append(file_id)

    def close(self):
        pass


class FakeDownloader:
    def __init__(self, stream, request, **kwargs):
        self.stream = stream
        self.drive, self.file_id = request

    def next_chunk(self, **kwargs):
        self.stream.write(self.drive.blobs[self.file_id])
        if self.drive.after_download:
            self.drive.after_download()
        return None, True


class FakeImports:
    def __init__(self):
        self.uploads = []
        self.registered = []
        self.job = {"id": "job123", "status": "pending", "needs_audio": True}
        self.error = None

    def find(self, file_id, check):
        return self.job if self.job["status"] != "pending" else None

    def request(self, method, path, *, metadata=None, audio=None, **kwargs):
        if self.error:
            raise sync.DriveSyncError(self.error)
        if audio:
            assert audio.stat().st_mode & 0o777 == 0o600
            self.uploads.append(audio.read_bytes())
            return {"id": "job123", "status": "saved", "needs_audio": False}
        self.registered.append(metadata)
        return self.job


@pytest.fixture
def environment(tmp_path, monkeypatch):
    drive, api = FakeDrive(), FakeImports()
    monkeypatch.setattr(sync, "load_credentials", lambda *args, **kwargs: FakeCredentials())
    monkeypatch.setattr(sync, "_drive_service", lambda credentials: drive)
    monkeypatch.setattr(sync, "LocalImports", lambda port: api)
    monkeypatch.setattr(sync, "MediaIoBaseDownload", FakeDownloader)
    monkeypatch.setattr(sync.shutil, "disk_usage", lambda path: SimpleNamespace(free=100 * 1024**3))
    return drive, api, tmp_path


def run(environment, **kwargs):
    _, _, directory = environment
    return sync.sync_once(
        directory / "data",
        directory / "client.json",
        directory / "token.json",
        folder_id="root",
        **kwargs,
    )


def test_empty_page_cursor_and_nested_folder_resume(environment):
    drive, api, directory = environment
    drive.add_folder()
    drive.add_audio(parent="recording")
    drive.pages["root"] = [[], ["recording"]]
    result = run(environment, max_pages=1)
    assert result["pages"] == 1 and result["pending_folders"] == 1
    assert drive.listed == [("root", None)]
    result = run(environment, max_pages=1)
    assert result["pending_folders"] == 1 and result["uploaded"] == 0
    result = run(environment, max_pages=1)
    assert result["uploaded"] == 1
    assert drive.listed == [("root", None), ("root", "1"), ("recording", None)]
    metadata = api.registered[0]
    assert metadata["parent_folder_name"] == "2026-09-09 17:21:48"
    assert metadata["modified_time"] == "2026-09-09T08:23:00+00:00"
    assert metadata["file_name"] == "録音.ogg"
    assert "recorded_at" not in metadata  # The backend validates the folder timestamp.
    assert list((directory / "data" / "drive-sync-staging").iterdir()) == []
    for name in ("drive-sync.json", "drive-sync.sqlite3", "drive-sync.lock"):
        assert (directory / "data" / name).stat().st_mode & 0o777 == 0o600


def test_ten_file_limit_resumes_remaining_files_without_relisting(environment):
    drive, api, _ = environment
    for index in range(12):
        drive.add_audio(f"audio{index}")
    first = run(environment)
    assert first["uploaded"] == 10 and first["pending_files"] == 2
    second = run(environment)
    assert second["uploaded"] == 2 and second["pending_files"] == 0
    assert len(api.uploads) == 12
    assert len(drive.listed) == 1


def test_expired_page_cursor_restarts_only_current_folder(environment):
    drive, _, _ = environment
    drive.add_audio()
    drive.pages["root"] = [[], ["audio"]]
    run(environment, max_pages=1)
    original = drive.list

    def expired(**kwargs):
        if kwargs.get("pageToken") == "1":
            raise HttpError(SimpleNamespace(status=410, reason="Gone"), b"{}")
        return original(**kwargs)

    drive.list = expired
    result = run(environment, max_pages=1)
    assert result["pending_folders"] == 1
    drive.list = original
    assert run(environment)["uploaded"] == 1
    assert drive.listed == [("root", None), ("root", None), ("root", "1")]


def test_page_limit_is_bounded_and_remaining_cursor_is_saved(environment):
    drive, _, _ = environment
    drive.pages["root"] = [[] for _ in range(51)]
    assert run(environment, max_pages=100)["pages"] == 50
    assert run(environment)["pages"] == 1
    assert len(drive.listed) == 51


def test_server_already_registered_skips_download(environment):
    drive, api, _ = environment
    drive.add_audio()
    api.job = {"id": "job123", "status": "saved", "needs_audio": False}
    result = run(environment)
    assert result["already_registered"] == 1
    assert not drive.downloaded and not api.uploads


@pytest.mark.parametrize("error", ["daymeld_busy", "daymeld_unavailable"])
def test_temporary_server_errors_preserve_pending_candidate(environment, error):
    drive, api, directory = environment
    drive.add_audio()
    api.error = error
    with pytest.raises(sync.DriveSyncError, match=error):
        run(environment)
    with sync._queue(directory / "data") as connection:
        row = connection.execute("SELECT * FROM files").fetchone()
        assert row["status"] == "pending" and row["attempts"] == 0
    api.error = None
    assert run(environment)["uploaded"] == 1
    assert len(drive.listed) == 1


@pytest.mark.parametrize("changed", ["checksum", "short", "long", "during_download"])
def test_download_integrity_failure_never_uploads(environment, changed):
    drive, api, directory = environment
    drive.add_audio()
    if changed == "checksum":
        drive.metadata["audio"]["md5Checksum"] = "0" * 32
    elif changed == "short":
        drive.blobs["audio"] = b"OggS"
    elif changed == "long":
        drive.blobs["audio"] += b"more"
    else:
        drive.after_download = lambda: drive.metadata["audio"].update(version="2")
    assert run(environment)["failed"] == 1
    assert not api.uploads
    assert not api.registered
    assert list((directory / "data" / "drive-sync-staging").iterdir()) == []


def test_disk_reserve_includes_temporary_and_server_copies(environment, monkeypatch):
    drive, api, _ = environment
    drive.add_audio()
    size = len(drive.blobs["audio"])
    monkeypatch.setattr(
        sync.shutil,
        "disk_usage",
        lambda path: SimpleNamespace(free=sync.MIN_FREE_BYTES + 2 * size - 1),
    )
    with pytest.raises(sync.DriveSyncError, match="insufficient_disk_space"):
        run(environment)
    assert not drive.downloaded and not api.uploads


def test_moved_folder_is_not_enumerated_outside_root(environment):
    drive, api, _ = environment
    drive.add_folder()
    drive.add_audio(parent="recording")
    drive.add_folder("still_inside")
    drive.add_audio("other_audio", parent="still_inside")
    run(environment, max_pages=1)
    drive.metadata["recording"]["parents"] = ["outside"]
    result = run(environment)
    assert result["skipped_folders"] == 1 and result["uploaded"] == 1
    assert drive.listed == [("root", None), ("still_inside", None)]
    assert len(api.uploads) == 1


def test_only_owned_crash_downloads_are_cleaned(environment):
    _, _, directory = environment
    staging = directory / "data" / "drive-sync-staging"
    stale = staging / "audio-old"
    stale.mkdir(parents=True)
    (stale / ".owner").write_text("daymeld-drive-sync-v1")
    (stale / "original.audio").write_bytes(b"OggS partial")
    unrelated = staging / "audio-other"
    unrelated.mkdir()
    (unrelated / "keep").write_text("keep")
    outside = directory / "outside"
    outside.mkdir()
    (outside / "keep").write_text("keep")
    (staging / "audio-link").symlink_to(outside)
    run(environment)
    assert not stale.exists()
    assert (unrelated / "keep").exists()
    assert (outside / "keep").exists()
    assert (staging / "audio-link").is_symlink()


def test_file_input_rejection_does_not_stop_other_recordings(environment):
    drive, api, _ = environment
    drive.add_audio("bad")
    drive.add_audio("good")
    original = api.request

    def request(method, path, *, metadata=None, **kwargs):
        if metadata and metadata["file_id"] == "bad":
            raise sync.DriveSyncError("daymeld_input_rejected")
        return original(method, path, metadata=metadata, **kwargs)

    api.request = request
    result = run(environment)
    assert result["failed_files"] == 1 and result["uploaded"] == 1
    assert len(api.uploads) == 1


def test_name_limit_is_validated_before_download_and_other_files_continue(environment):
    drive, api, _ = environment
    drive.add_audio("long_name")
    drive.add_audio("good")
    drive.metadata["long_name"]["name"] = "a" * 241
    result = run(environment)
    assert result["failed_files"] == 1 and result["uploaded"] == 1
    assert drive.downloaded == ["good"]
    assert len(api.uploads) == 1


def test_server_state_pagination_uses_every_page():
    api = sync.LocalImports()
    calls = []

    def request(method, path):
        calls.append(path)
        if len(calls) == 1:
            return {"items": [], "next_offset": 100}
        return {"items": [{"id": "job", "file_id": "target"}], "next_offset": None}

    api.request = request
    assert api.find("target", lambda: None)["id"] == "job"
    assert len(calls) == 2 and "offset=100" in calls[1]


def test_slow_recording_is_deferred_before_run_deadline_exits(environment, monkeypatch):
    drive, api, directory = environment
    drive.add_audio("slow")
    drive.add_audio("later")
    original = sync._download

    def download(service, metadata, path, check):
        if metadata["file_id"] == "slow":
            raise sync.DriveSyncError("run_time_limit")
        return original(service, metadata, path, check)

    monkeypatch.setattr(sync, "_download", download)
    with pytest.raises(sync.DriveSyncError, match="run_time_limit"):
        run(environment)
    with sync._queue(directory / "data") as connection:
        row = connection.execute("SELECT * FROM files WHERE id='slow'").fetchone()
        assert row["status"] == "pending" and row["attempts"] == 1
        assert row["retry_at"] > sync.time.time()
    assert run(environment)["uploaded"] == 1
    assert len(api.uploads) == 1


def test_moved_file_is_not_downloaded(environment):
    drive, api, directory = environment
    drive.add_audio()
    run(environment, max_files=0)
    # Put the listing in the persisted queue, then move the file before a later run.
    connection = sync._queue(directory / "data")
    folder = connection.execute("SELECT * FROM folders").fetchone()
    sync._list_page(drive, connection, folder)
    connection.close()
    drive.metadata["audio"]["parents"] = ["outside"]
    assert run(environment)["failed_files"] == 1
    assert not drive.downloaded and not api.uploads


def test_busy_lock_and_different_root_do_not_change_configuration(environment):
    _, _, directory = environment
    run(environment)
    data = directory / "data"
    with sync._lock(data / "drive-sync.lock"), pytest.raises(sync.DriveSyncError, match="busy"):
        run(environment)
    with pytest.raises(sync.DriveSyncError, match="folder_already_configured"):
        sync.sync_once(data, directory / "client", directory / "token", folder_id="elsewhere")
    assert json.loads((data / "drive-sync.json").read_text())["folder_id"] == "root"


@pytest.mark.parametrize(
    "arguments",
    [
        ["sync", "--force"],
        ["auth", "--retry-failed"],
        ["status", "--folder-id", "root"],
    ],
)
def test_command_specific_flags_are_rejected(monkeypatch, capsys, arguments):
    monkeypatch.setattr("sys.argv", ["drive_sync", *arguments])
    with pytest.raises(SystemExit) as error:
        sync.main()
    assert error.value.code == 1
    assert json.loads(capsys.readouterr().out)["status"] == "invalid_command_options"


def test_worker_no_configuration_or_auth_never_opens_browser(tmp_path, monkeypatch):
    monkeypatch.setattr(
        sync.InstalledAppFlow,
        "from_client_secrets_file",
        lambda *args: pytest.fail("Worker must never authorize interactively"),
    )
    worker = sync.DriveSyncWorker(tmp_path / "data", token_path=tmp_path / "token.json")
    called = threading.Event()
    original = sync.sync_once

    def once(*args, **kwargs):
        try:
            return original(*args, **kwargs)
        finally:
            called.set()

    monkeypatch.setattr(sync, "sync_once", once)
    worker.start()
    assert called.wait(timeout=2)
    worker.stop()
    assert not worker._thread.is_alive()
    assert not (tmp_path / "data" / "drive-sync.json").exists()
    sync._atomic_json(tmp_path / "data" / "drive-sync.json", {"folder_id": "root"})
    called.clear()
    worker.start()
    assert called.wait(timeout=2)
    worker.stop()
    assert sync.status(tmp_path / "data", tmp_path / "token.json")["status"] == "auth_required"


def test_local_api_streams_exact_bytes_and_classifies_conflicts(tmp_path):
    received = []
    responses = [
        (202, {"id": "job", "status": "saved"}),
        (409, {"code": "queue_full", "error": "private"}),
        (409, {"code": "busy"}),
        (409, {"code": "source_conflict"}),
        (409, {"error": "unknown"}),
        (400, {"error": "private"}),
        (411, {"error": "private"}),
        (413, {"error": "private"}),
        (415, {"error": "private"}),
        (500, {"error": "private"}),
    ]

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(
                (
                    self.headers.get("Content-Type"),
                    self.rfile.read(int(self.headers["Content-Length"])),
                )
            )
            code, value = responses.pop(0)
            body = json.dumps(value).encode()
            self.send_response(code)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    audio = tmp_path / "example.ogg"
    audio.write_bytes(b"OggS" + os.urandom(sync.CHUNK_BYTES + 1))
    try:
        api = sync.LocalImports(server.server_port)
        api.request(
            "POST", "/api/conversations/drive-imports/job/audio", audio=audio, mime="audio/ogg"
        )
        assert received[0] == ("audio/ogg", audio.read_bytes())
        for expected in (
            "daymeld_busy",
            "daymeld_busy",
            "source_conflict",
            "daymeld_busy",
            "daymeld_input_rejected",
            "daymeld_input_rejected",
            "daymeld_input_rejected",
            "daymeld_input_rejected",
            "daymeld_unavailable",
        ):
            with pytest.raises(sync.DriveSyncError, match=expected):
                api.request("POST", "/api/conversations/drive-imports", metadata={})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
