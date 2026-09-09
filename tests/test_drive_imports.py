from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from daily_reader import conversations, drive_imports
from daily_reader.local_server import make_handler


@pytest.fixture(scope="module")
def audio_files(tmp_path_factory):
    directory = tmp_path_factory.mktemp("drive-audio")
    result = {}
    for extension in ("ogg", "mp3"):
        path = directory / ("tone." + extension)
        subprocess.run(
            [shutil.which("ffmpeg"), "-v", "error", "-f", "lavfi", "-i",
             "sine=frequency=440:duration=0.15", str(path)],
            check=True, capture_output=True, timeout=30,
        )
        result[extension] = path.read_bytes()
    return result


@pytest.fixture
def imported(tmp_path, monkeypatch, audio_files):
    database = tmp_path / "conversations.db"
    directory = tmp_path / "audio"
    data = audio_files["ogg"]
    metadata = {
        "file_id": "drive-file-fixture", "parent_folder_id": "drive-folder-fixture",
        "parent_folder_name": "2026-09-04 11:21:41", "file_name": "録音.ogg",
        "mime_type": "audio/ogg", "size": len(data),
        "md5_checksum": hashlib.md5(data).hexdigest(),
        "modified_time": "2026-09-09T00:00:00Z", "version": "1",
    }
    calls = []

    def analyze(db, recording_id, token, *, initial_only=False):
        calls.append(recording_id)
        with sqlite3.connect(db) as connection:
            connection.execute(
                "UPDATE recordings SET status='analyzing' WHERE id=?", (recording_id,),
            )
        return True

    monkeypatch.setattr(conversations, "MINIMUM_FREE_BYTES", 0)
    monkeypatch.setattr(conversations, "start_analysis", analyze)
    return database, directory, data, metadata, calls


def receive(imported, **changes):
    database, directory, data, metadata, _ = imported
    job = drive_imports.enqueue(database, {**metadata, **changes})
    return drive_imports.receive_audio(database, directory, job["id"], io.BytesIO(data), len(data))


def worker(imported):
    database, directory, *_ = imported
    return drive_imports.DriveImportWorker(database, directory, directory / "token")


def test_registration_is_private_durable_and_idempotent(imported):
    database, _, _, metadata, _ = imported
    job = drive_imports.enqueue(database, metadata)
    assert drive_imports.enqueue(database, metadata) == job
    assert job["status"] == "pending" and job["needs_audio"]
    assert drive_imports.get_import(database, job["id"]) == job
    assert drive_imports.list_imports(database)["items"] == [job]
    assert database.stat().st_mode & 0o777 == 0o600
    assert "parent_folder_name" not in job and "md5_checksum" not in job
    assert "録音" not in json.dumps(job, ensure_ascii=False)


def test_registration_concurrency_is_one_row(imported):
    database, _, _, metadata, _ = imported
    drive_imports.list_imports(database)
    with ThreadPoolExecutor(max_workers=5) as executor:
        jobs = list(executor.map(lambda _: drive_imports.enqueue(database, metadata), range(10)))
    assert len({job["id"] for job in jobs}) == 1


def test_capacity_and_stable_pagination(imported):
    database, _, _, metadata, _ = imported
    jobs = [drive_imports.enqueue(database, {**metadata, "file_id": f"file-{i}"})
            for i in range(10)]
    assert drive_imports.enqueue(database, {**metadata, "file_id": "file-0"}) == jobs[0]
    with pytest.raises(drive_imports.ImportQueueFull):
        drive_imports.enqueue(database, {**metadata, "file_id": "overflow"})
    one = drive_imports.list_imports(database, limit=4)
    two = drive_imports.list_imports(database, limit=4, offset=one["next_offset"])
    assert one["total"] == two["total"] == 10
    assert not ({job["id"] for job in one["items"]} & {job["id"] for job in two["items"]})


@pytest.mark.parametrize("change", [
    {"url": "https://invalid.example/private"}, {"token": "private"},
    {"file_id": "../file"}, {"parent_folder_name": "https://invalid.example"},
    {"file_name": "録音\u0000.ogg"}, {"mime_type": "text/html"}, {"file_name": "録音.mp3"},
    {"size": True}, {"size": 0}, {"size": 2 * 1024**3 + 1}, {"size": "40"},
    {"modified_time": "2026-09-04"}, {"md5_checksum": "bad"}, {"version": "1 OR 1=1"},
])
def test_rejects_invalid_metadata_and_credentials(imported, change):
    database, _, _, metadata, _ = imported
    with pytest.raises((ValueError, TypeError)):
        drive_imports.enqueue(database, {**metadata, **change})
    assert drive_imports.list_imports(database)["items"] == []


def test_new_audio_preserves_original_and_connects_analysis_date_and_gps(imported):
    database, directory, data, metadata, calls = imported
    saved = receive(imported)
    assert saved["status"] == "saved" and not saved["needs_audio"]
    assert calls == []
    assert worker(imported).step()
    result = drive_imports.get_import(database, saved["id"])
    assert result["status"] == "completed" and calls == [result["recording_id"]]
    recording = conversations.get_recording(database, result["recording_id"])
    assert recording["source_type"] == "audio"
    assert recording["recorded_at"] == "2026-09-04T11:21:41+09:00"
    assert recording["recorded_at_source"] == "soundcore_drive_folder_name"
    assert recording["filename"] == metadata["parent_folder_name"] + ".ogg"
    assert "cloud_metadata" not in recording
    with sqlite3.connect(database) as connection:
        path, private = connection.execute(
            "SELECT audio_path,cloud_metadata FROM recordings"
        ).fetchone()
    assert Path(path).read_bytes() == data
    assert Path(path).stat().st_mode & 0o777 == 0o600
    assert Path(path).parent.stat().st_mode & 0o777 == 0o700
    assert Path(path).parent.parent == directory
    assert json.loads(private)["file_name"] == "録音.ogg"
    with conversations._connect(database) as connection:
        conversations._replace_analysis_results(
            connection, recording["id"], [(600, 603, "確認します", None, "話者A")],
            datetime.now(UTC).isoformat(),
        )
    conversations.store_location_events(database, [{
        "id": "gps-fixture", "timestamp": "2026-09-04T11:31:41+09:00",
        "latitude": 35.0, "longitude": 139.0, "horizontal_accuracy": 10,
        "is_approximate": False,
    }])
    recording = conversations.get_recording(database, recording["id"])
    context = next(item for item in recording["location_contexts"] if item["utterance_id"])
    assert context["location_event_id"] and context["date_source"] == "soundcore_drive_folder_name"


@pytest.mark.parametrize("name", [
    "2026-09-04", "2026-02-30 11:21:41", "会議", "2026-9-4 11:21:41",
    "2026-09-04 11:21:41 renamed", "2026-09-04 11:21:41+09:00",
])
def test_only_exact_parent_folder_time_is_used(imported, name):
    database, *_ = imported
    saved = receive(imported, parent_folder_name=name, file_name="2026-09-04 11:21:41.ogg")
    recording = conversations.get_recording(database, saved["recording_id"])
    assert recording["recorded_at"] is None
    assert recording["recorded_at_verified"] == 0 and recording["recorded_at_source"] == "unknown"


def test_rename_with_same_checksum_preserves_original_metadata_date_and_candidates(imported):
    database, _, _, metadata, _ = imported
    saved = receive(imported)
    worker(imported).step()
    before = conversations.get_recording(database, saved["recording_id"])
    job = drive_imports.enqueue(database, {
        **metadata, "parent_folder_name": "renamed", "version": "3",
        "modified_time": "2026-09-10T00:00:00Z",
    })
    assert job["recording_id"] == saved["recording_id"] and not job["needs_audio"]
    assert conversations.get_recording(database, saved["recording_id"]) == before


def test_drive_title_slashes_are_preserved_without_using_them_as_local_paths(imported):
    database, directory, _, _, _ = imported
    saved = receive(imported, parent_folder_name="9/9 打ち合わせ\\メモ", file_name="音声/録音.ogg")
    recording = conversations.get_recording(database, saved["recording_id"])
    assert recording["filename"] == "9_9 打ち合わせ_メモ.ogg"
    assert recording["recorded_at"] is None
    with sqlite3.connect(database) as connection:
        path, metadata = connection.execute(
            "SELECT audio_path,cloud_metadata FROM recordings"
        ).fetchone()
    assert Path(path) == directory / saved["id"] / "original.ogg"
    assert json.loads(metadata)["parent_folder_name"] == "9/9 打ち合わせ\\メモ"
    assert json.loads(metadata)["file_name"] == "音声/録音.ogg"


@pytest.mark.parametrize("change", [{"md5_checksum": "a" * 32}, {"size": 500}])
def test_changed_drive_content_stops_without_overwriting(imported, change):
    database, _, _, metadata, _ = imported
    saved = receive(imported)
    before = conversations.get_recording(database, saved["recording_id"])
    with pytest.raises(drive_imports.ImportConflict):
        drive_imports.enqueue(database, {**metadata, **change})
    result = drive_imports.get_import(database, saved["id"])
    assert result["status"] == "conflict" and result["recording_id"] == saved["recording_id"]
    assert conversations.get_recording(database, saved["recording_id"]) == before
    assert not worker(imported).step()
    with pytest.raises(drive_imports.ImportConflict):
        drive_imports.retry(database, saved["id"])


def test_missing_checksum_requires_unchanged_version_and_modified_time(imported):
    database, _, _, metadata, _ = imported
    old = {**metadata, "md5_checksum": None}
    drive_imports.enqueue(database, old)
    with pytest.raises(drive_imports.ImportConflict):
        drive_imports.enqueue(database, {**old, "version": "2"})


def test_same_id_audio_retry_and_cross_source_sha_do_not_reanalyze(imported):
    database, directory, data, _, calls = imported
    saved = receive(imported)
    worker(imported).step()
    with conversations._connect(database) as connection:
        conversations._replace_analysis_results(
            connection, saved["recording_id"], [(0, 1, "資料を確認してください", None, "話者A")],
            datetime.now(UTC).isoformat(),
        )
        connection.execute("UPDATE recordings SET status='completed'")
    before = conversations.get_recording(database, saved["recording_id"])
    again = drive_imports.receive_audio(database, directory, saved["id"], io.BytesIO(), len(data))
    assert again["recording_id"] == saved["recording_id"]
    different = receive(imported, file_id="other-file", parent_folder_name="renamed")
    worker(imported).step()
    assert different["recording_id"] == saved["recording_id"]
    assert calls == [saved["recording_id"]]
    assert conversations.get_recording(database, saved["recording_id"]) == before
    assert len(list(directory.glob("*/original.ogg"))) == 1


def test_existing_manual_audio_deduplicates_without_analysis(imported, audio_files):
    database, directory, _, metadata, calls = imported
    data = audio_files["mp3"]
    manual = conversations.store_upload(database, directory, io.BytesIO(data), len(data), "old.mp3")
    job = drive_imports.enqueue(database, {
        **metadata, "file_name": "録音.mp3", "mime_type": "audio/mpeg", "size": len(data),
        "md5_checksum": hashlib.md5(data).hexdigest(),
    })
    drive_imports.receive_audio(database, directory, job["id"], io.BytesIO(data), len(data))
    worker(imported).step()
    assert drive_imports.get_import(database, job["id"])["recording_id"] == manual["id"]
    assert calls == []


def test_checksum_and_incomplete_transfer_never_create_recording(imported):
    database, directory, data, metadata, _ = imported
    job = drive_imports.enqueue(database, metadata)
    for body in (data[:-1], b"x" + data[1:], data[:-20]):
        with pytest.raises(ValueError):
            drive_imports.receive_audio(database, directory, job["id"], io.BytesIO(body), len(data))
        assert conversations.list_recordings(database) == []
        assert list(directory.iterdir()) == []
    failed = drive_imports.get_import(database, job["id"])
    assert failed["status"] == "failed" and failed["upload_attempts"] == 3
    assert drive_imports.retry(database, job["id"])["needs_audio"]
    assert receive(imported)["recording_id"]


def test_signature_only_fake_audio_is_rejected(imported):
    database, directory, _, metadata, _ = imported
    data = b"OggS" + b"not real audio" * 10
    job = drive_imports.enqueue(database, {
        **metadata, "size": len(data), "md5_checksum": hashlib.md5(data).hexdigest(),
    })
    with pytest.raises(ValueError, match="audio contents"):
        drive_imports.receive_audio(database, directory, job["id"], io.BytesIO(data), len(data))
    assert conversations.list_recordings(database) == []


def test_audio_validation_and_transfer_do_not_hold_database_lock(imported, monkeypatch):
    database, _, _, _, _ = imported
    validate = drive_imports._validate_audio

    def validating(path, extension):
        with sqlite3.connect(database, timeout=0.01) as connection:
            connection.execute("BEGIN IMMEDIATE")
        validate(path, extension)

    monkeypatch.setattr(drive_imports, "_validate_audio", validating)
    assert receive(imported)["recording_id"]


def test_atomic_checkpoint_failure_cleans_audio_and_recording(imported, monkeypatch):
    database, directory, _, _, _ = imported

    def fail(*args):
        raise RuntimeError("private fixture failure")

    monkeypatch.setattr(conversations, "rebuild_context", fail)
    with pytest.raises(RuntimeError):
        receive(imported)
    assert conversations.list_recordings(database) == []
    assert list(directory.iterdir()) == []
    job = drive_imports.list_imports(database)["items"][0]
    assert job["recording_id"] is None and job["status"] == "pending"
    assert "private" not in job["error"]


def test_capacity_reserve_is_enforced_before_reading(imported, monkeypatch):
    database, directory, data, metadata, _ = imported
    job = drive_imports.enqueue(database, metadata)
    monkeypatch.setattr(conversations, "MINIMUM_FREE_BYTES", 5 * 1024**3)
    free = type("Space", (), {"free": 5 * 1024**3})()
    monkeypatch.setattr(conversations.shutil, "disk_usage", lambda _: free)
    source = io.BytesIO(data)
    with pytest.raises(OSError):
        drive_imports.receive_audio(database, directory, job["id"], source, len(data))
    assert source.tell() == 0 and conversations.list_recordings(database) == []


def test_upload_lock_rejects_concurrent_receivers(imported):
    database, directory, data, metadata, _ = imported
    job = drive_imports.enqueue(database, metadata)
    with drive_imports._operation_lock(database, "upload"), pytest.raises(
        drive_imports.ImportConflict
    ):
        drive_imports.receive_audio(database, directory, job["id"], io.BytesIO(data), len(data))
    assert drive_imports.get_import(database, job["id"])["upload_attempts"] == 0


def test_restart_recovers_interrupted_transfer_and_removes_only_uncommitted_copy(imported):
    database, directory, data, metadata, _ = imported
    job = drive_imports.enqueue(database, metadata)
    target = directory / job["id"]
    target.mkdir(parents=True)
    (target / "original.tmp").write_bytes(data[:10])
    unrelated = directory / "unrelated"
    unrelated.mkdir()
    (unrelated / "original.ogg").write_bytes(data)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE drive_imports SET status='receiving',upload_attempts=1")
    drive_imports.recover(database, directory)
    assert not target.exists() and (unrelated / "original.ogg").exists()
    assert drive_imports.get_import(database, job["id"])["needs_audio"]
    assert receive(imported)["recording_id"]


def test_analysis_start_failures_are_bounded_and_keep_saved_audio(imported, monkeypatch):
    database, directory, data, _, calls = imported
    saved = receive(imported)
    analyze = conversations.start_analysis

    def fail(*args):
        raise OSError("https://private.example/token")

    monkeypatch.setattr(conversations, "start_analysis", fail)
    process = worker(imported)
    now = datetime.now(UTC)
    assert process.step(now)
    assert not process.step(now + timedelta(seconds=20))
    assert process.step(now + timedelta(minutes=2))
    assert process.step(now + timedelta(minutes=5))
    failed = drive_imports.get_import(database, saved["id"])
    assert failed["status"] == "failed" and failed["attempts"] == 3
    assert "private" not in failed["error"]
    assert list(directory.glob("*/original.ogg"))[0].read_bytes() == data
    monkeypatch.setattr(conversations, "start_analysis", analyze)
    assert not drive_imports.retry(database, saved["id"])["needs_audio"]
    assert process.step() and calls == [saved["recording_id"]]


def test_restart_after_analysis_acceptance_resumes_only_own_interrupted_recording(imported):
    database, directory, _, _, calls = imported
    saved = receive(imported)
    worker(imported).step()
    conversations.recover_interrupted_conversations(database)
    drive_imports.recover(database, directory)
    assert drive_imports.get_import(database, saved["id"])["status"] == "saved"
    worker(imported).step()
    assert calls == [saved["recording_id"], saved["recording_id"]]


def test_crash_after_start_does_not_start_completed_audio_twice(imported):
    database, _, _, _, calls = imported
    saved = receive(imported)
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE recordings SET status='completed'")
    assert worker(imported).step()
    assert drive_imports.get_import(database, saved["id"])["status"] == "completed"
    assert calls == []


def test_manual_analysis_completion_between_worker_read_and_claim_is_not_reanalyzed(
    imported, monkeypatch,
):
    database, _, _, _, _ = imported
    saved = receive(imported)
    monkeypatch.undo()  # Use the real transactional start_analysis implementation.
    real_get = conversations.get_recording

    def stale_status(db, recording_id):
        result = real_get(db, recording_id)
        with sqlite3.connect(db) as connection:
            connection.execute(
                "UPDATE recordings SET status='completed' WHERE id=?", (recording_id,)
            )
        return result

    def unexpected_thread(*args, **kwargs):
        raise AssertionError("completed audio must not start a new analysis thread")

    monkeypatch.setattr(conversations, "get_recording", stale_status)
    monkeypatch.setattr(conversations.threading, "Thread", unexpected_thread)
    assert worker(imported).step()
    assert drive_imports.get_import(database, saved["id"])["status"] == "completed"
    assert real_get(database, saved["recording_id"])["status"] == "completed"


@pytest.fixture
def handler(imported, tmp_path):
    database, directory, *_ = imported
    factory = make_handler(
        tmp_path / "site", tmp_path / "articles", tmp_path / "reads", tmp_path / "feedback",
        tmp_path / "assistant", tmp_path / "client", tmp_path / "token",
        conversations_db=database, conversation_audio_dir=directory,
    )
    result = factory.func.__new__(factory.func)
    responses = []
    result._send_json = lambda status, payload: responses.append((status, payload))
    result.headers = {"Host": "127.0.0.1:8787", "Content-Type": "application/json"}
    result.path = "/api/conversations/drive-imports"
    return result, responses


def body(handler, value):
    data = json.dumps(value).encode()
    handler.headers["Content-Length"] = str(len(data))
    handler.headers["Content-Type"] = "application/json"
    handler.rfile = io.BytesIO(data)


def test_http_register_stream_list_get_and_retry(handler, imported):
    request, responses = handler
    _, _, data, metadata, _ = imported
    body(request, metadata)
    request.do_POST()
    status, job = responses[-1]
    assert status == 202 and job["needs_audio"]
    base = request.path
    request.path = base + "/" + job["id"] + "/audio"
    request.headers.update({"Content-Type": "audio/ogg", "Content-Length": str(len(data))})
    request.rfile = io.BytesIO(data)
    request.do_POST()
    assert responses[-1][0] == 202 and responses[-1][1]["status"] == "saved"
    request.do_POST()
    assert responses[-1][0] == 200
    assert request.close_connection
    request.path = base + "/" + job["id"]
    request.do_GET()
    assert responses[-1][0] == 200 and responses[-1][1]["recording_id"]
    request.path = base
    request.do_GET()
    assert responses[-1][0] == 200 and responses[-1][1]["total"] == 1
    request.path = base + "/" + job["id"] + "/retry"
    body(request, {})
    request.do_POST()
    assert responses[-1][0] == 202


@pytest.mark.parametrize("headers", [
    {"Host": "malicious.example"}, {"Origin": "https://malicious.example"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_http_origin_guards_apply_to_all_routes_before_body(handler, headers):
    request, responses = handler
    request.headers.update(headers)
    base = request.path
    for path in (base, base + "/fake", base + "/fake/audio", base + "/fake/retry"):
        request.path = path
        request.do_POST()
        assert responses[-1][0] == 403
        request.do_GET()
        assert responses[-1][0] == 403


def test_http_invalid_transfer_routes_and_private_errors(handler, imported):
    request, responses = handler
    _, _, data, metadata, _ = imported
    body(request, {**metadata, "token": "private-token"})
    request.do_POST()
    assert responses[-1][0] == 400 and "private" not in json.dumps(responses)
    body(request, metadata)
    request.do_POST()
    job = responses[-1][1]
    base = request.path
    request.path = base + "/" + job["id"] + "/audio"
    request.headers["Content-Type"] = "audio/ogg"
    del request.headers["Content-Length"]
    request.do_POST()
    assert responses[-1][0] == 411
    request.headers["Content-Length"] = str(len(data))
    request.headers["Transfer-Encoding"] = "chunked"
    request.do_POST()
    assert responses[-1][0] == 400
    del request.headers["Transfer-Encoding"]
    request.path = base + "/fake/invalid"
    request.do_GET()
    assert responses[-1][0] == 404
    request.do_POST()
    assert responses[-1][0] == 404
    logs = []
    request.log_message = lambda *args: logs.append(args)
    request.path = base + "?token=private-token"
    request.log_request(400)
    assert "private" not in json.dumps(logs)


def test_http_distinguishes_content_conflict_queue_full_and_busy(handler, imported):
    request, responses = handler
    database, _, data, metadata, _ = imported
    body(request, metadata)
    request.do_POST()
    job = responses[-1][1]
    base = request.path
    request.path += "/" + job["id"] + "/audio"
    request.headers.update({"Content-Type": "audio/ogg", "Content-Length": str(len(data))})
    with drive_imports._operation_lock(database, "upload"):
        request.do_POST()
    assert responses[-1][0] == 409 and responses[-1][1]["code"] == "busy"
    request.path = base
    for index in range(9):
        body(request, {**metadata, "file_id": f"other-{index}"})
        request.do_POST()
        assert responses[-1][0] == 202
    body(request, {**metadata, "file_id": "overflow"})
    request.do_POST()
    assert responses[-1][0] == 409 and responses[-1][1]["code"] == "queue_full"
    body(request, {**metadata, "md5_checksum": "a" * 32})
    request.do_POST()
    assert responses[-1][0] == 409 and responses[-1][1]["code"] == "source_conflict"


def test_real_http_stream_is_saved_and_responses_are_not_cached(imported, tmp_path):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer

    database, directory, data, metadata, _ = imported
    factory = make_handler(
        tmp_path / "site", tmp_path / "articles", tmp_path / "reads", tmp_path / "feedback",
        tmp_path / "assistant", tmp_path / "client", tmp_path / "token",
        conversations_db=database, conversation_audio_dir=directory,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), factory)
    serving = threading.Thread(target=server.serve_forever, daemon=True)
    serving.start()
    connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=10)
    try:
        base = "/api/conversations/drive-imports"
        connection.request("POST", base, json.dumps(metadata), {"Content-Type": "application/json"})
        response = connection.getresponse()
        assert response.status == 202 and response.getheader("Cache-Control") == "no-store"
        job = json.loads(response.read())
        connection.request("POST", base + "/" + job["id"] + "/audio", io.BytesIO(data), {
            "Content-Length": str(len(data)), "Content-Type": "audio/ogg",
        })
        response = connection.getresponse()
        assert response.status == 202
        saved = json.loads(response.read())
        assert saved["recording_id"] and saved["status"] == "saved"
        assert list(directory.glob("*/original.ogg"))[0].read_bytes() == data
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        serving.join(timeout=5)
