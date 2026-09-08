from __future__ import annotations

import io
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from daily_reader import conversations, soundcore_imports
from daily_reader.local_server import make_handler
from daily_reader.soundcore_cloud import CloudAudio, SoundcoreCloudError

URL = "https://speaker-eu.eufylife.com/knowledge/sharelink/CODE1234"
STAMP = "2026-09-04T11:21:41+09:00"


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    database = tmp_path / "conversations.db"
    calls = {"download": 0, "analysis": []}
    monkeypatch.setattr(conversations, "MINIMUM_FREE_BYTES", 0)

    def download(url, directory):
        calls["download"] += 1
        assert url.startswith(URL)
        # A slow download must not prevent GPS, metadata or other queue writes.
        with sqlite3.connect(database, timeout=0.01) as connection:
            connection.execute("BEGIN IMMEDIATE")
        assert directory.stat().st_mode & 0o777 == 0o700
        path = directory / "original.ogg"
        path.write_bytes(b"OggS" + b"fixture audio" * 30)
        return CloudAudio(path, "1788488501.ogg", STAMP, {"title": "private fixture title"})

    def analyze(db, recording_id, token):
        calls["analysis"].append(recording_id)
        with sqlite3.connect(db) as connection:
            connection.execute(
                "UPDATE recordings SET status='analyzing' WHERE id=?", (recording_id,)
            )
        return True

    monkeypatch.setattr(soundcore_imports, "download_share", download)
    monkeypatch.setattr(conversations, "start_analysis", analyze)
    worker = soundcore_imports.SoundcoreImportWorker(
        database, tmp_path / "audio", tmp_path / "token"
    )
    return database, worker, calls


def test_enqueue_is_durable_private_and_idempotent(tmp_path):
    database = tmp_path / "db"
    one = soundcore_imports.enqueue(database, URL + "?language=ja")
    two = soundcore_imports.enqueue(database, URL)
    assert one == two
    assert soundcore_imports.list_imports(database) == [one]
    assert database.stat().st_mode & 0o777 == 0o600
    assert "CODE1234" not in json.dumps(one)
    assert "url" not in one and "url_hash" not in one
    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT url FROM soundcore_imports").fetchone()[0] == URL


def test_enqueue_concurrency_has_one_job(tmp_path):
    database = tmp_path / "db"
    soundcore_imports.list_imports(database)
    with ThreadPoolExecutor(max_workers=5) as executor:
        jobs = list(executor.map(lambda _: soundcore_imports.enqueue(database, URL), range(10)))
    assert len({job["id"] for job in jobs}) == 1


def test_queue_capacity_counts_active_and_still_returns_duplicate(tmp_path):
    database = tmp_path / "db"
    first = soundcore_imports.enqueue(database, URL)
    for index in range(9):
        soundcore_imports.enqueue(database, URL + str(index))
    assert soundcore_imports.enqueue(database, URL) == first
    with pytest.raises(soundcore_imports.ImportQueueFull):
        soundcore_imports.enqueue(database, URL + "extra")


def test_cloud_ogg_preserves_original_date_and_private_metadata(cloud):
    database, worker, calls = cloud
    job = soundcore_imports.enqueue(database, URL)
    assert worker.step()
    result = soundcore_imports.list_imports(database)[0]
    assert result["id"] == job["id"] and result["status"] == "completed"
    recording = conversations.get_recording(database, result["recording_id"])
    assert recording["source_type"] == "audio" and recording["status"] == "analyzing"
    assert recording["recorded_at"] == STAMP
    assert recording["recorded_at_source"] == "soundcore_cloud_timestamp"
    assert "cloud_metadata" not in recording
    assert calls["analysis"] == [recording["id"]]
    with sqlite3.connect(database) as connection:
        path, metadata = connection.execute(
            "SELECT audio_path,cloud_metadata FROM recordings"
        ).fetchone()
    assert Path(path).suffix == ".ogg" and Path(path).read_bytes().startswith(b"OggS")
    assert Path(path).stat().st_mode & 0o777 == 0o600
    assert json.loads(metadata)["title"] == "private fixture title"


def test_cloud_unknown_date_never_uses_filename_date(cloud, monkeypatch):
    database, worker, _ = cloud
    original = soundcore_imports.download_share

    def unknown(url, directory):
        audio = original(url, directory)
        return CloudAudio(audio.path, "2026-09-04 11:21:41.ogg", None, audio.metadata)

    monkeypatch.setattr(soundcore_imports, "download_share", unknown)
    soundcore_imports.enqueue(database, URL)
    worker.step()
    result = soundcore_imports.list_imports(database)[0]
    recording = conversations.get_recording(database, result["recording_id"])
    assert recording["recorded_at"] is None and recording["recorded_at_verified"] == 0
    assert recording["recorded_at_source"] == "unknown"


def test_same_audio_different_links_keeps_analysis_candidates_and_metadata(cloud):
    database, worker, calls = cloud
    soundcore_imports.enqueue(database, URL)
    worker.step()
    first = soundcore_imports.list_imports(database)[0]
    with conversations._connect(database) as connection:
        conversations._replace_analysis_results(
            connection, first["recording_id"], [(0, 3, "資料を確認してください", None, "話者A")],
            datetime.now(UTC).isoformat(),
        )
        connection.execute("UPDATE recordings SET status='completed'")
    before = conversations.get_recording(database, first["recording_id"])
    soundcore_imports.enqueue(database, URL + "DIFFERENT")
    worker.step()
    after = conversations.get_recording(database, first["recording_id"])
    assert before == after
    assert len(calls["analysis"]) == 1
    assert len({job["recording_id"] for job in soundcore_imports.list_imports(database)}) == 1
    assert len(list(worker.audio_directory.glob("*/original.ogg"))) == 1


def test_cloud_recording_and_utterance_link_to_gps(cloud):
    database, worker, _ = cloud
    soundcore_imports.enqueue(database, URL)
    worker.step()
    recording_id = soundcore_imports.list_imports(database)[0]["recording_id"]
    with conversations._connect(database) as connection:
        conversations._replace_analysis_results(
            connection, recording_id, [(600, 603, "確認します", None, "話者A")],
            datetime.now(UTC).isoformat(),
        )
    # GPS arrives later; the source remains audio so the utterance span is considered.
    conversations.store_location_events(database, [{
        "id": "gps-fixture", "timestamp": "2026-09-04T11:31:41+09:00",
        "latitude": 35.0, "longitude": 139.0, "horizontal_accuracy": 10,
        "is_approximate": False,
    }])
    recording = conversations.get_recording(database, recording_id)
    context = next(item for item in recording["location_contexts"] if item["utterance_id"])
    assert context["location_event_id"] is not None
    assert datetime.fromisoformat(context["location"]["timestamp"]) == datetime.fromisoformat(
        "2026-09-04T11:31:41+09:00"
    )
    assert context["date_source"] == "soundcore_cloud_timestamp"


def test_normal_upload_still_rejects_ogg(tmp_path):
    with pytest.raises(ValueError, match="only MP3"):
        conversations.store_upload(tmp_path / "db", tmp_path / "audio", io.BytesIO(b"OggS"),
                                   4, "recording.ogg")


def test_storage_failure_rolls_back_recording_and_job_together(cloud, monkeypatch):
    database, worker, _ = cloud
    soundcore_imports.enqueue(database, URL)
    rebuild = conversations.rebuild_context

    def fail(*args):
        raise RuntimeError("failed before recording transaction committed")

    monkeypatch.setattr(conversations, "rebuild_context", fail)
    now = datetime.now(UTC)
    worker.step(now)
    job = soundcore_imports.list_imports(database)[0]
    assert job["status"] == "queued" and job["recording_id"] is None
    assert conversations.list_recordings(database) == []
    assert list(worker.audio_directory.iterdir()) == []
    monkeypatch.setattr(conversations, "rebuild_context", rebuild)
    worker.step(now + timedelta(minutes=2))
    assert soundcore_imports.list_imports(database)[0]["status"] == "completed"


def test_failed_analysis_start_uses_saved_recording_on_retry(cloud, monkeypatch):
    database, worker, calls = cloud
    soundcore_imports.enqueue(database, URL)
    analyze = conversations.start_analysis

    def fail(*args):
        raise RuntimeError("secret URL must never be returned")

    monkeypatch.setattr(conversations, "start_analysis", fail)
    now = datetime.now(UTC)
    worker.step(now)
    saved = soundcore_imports.list_imports(database)[0]
    assert saved["status"] == "saved" and saved["recording_id"]
    assert "secret" not in saved["error"]
    monkeypatch.setattr(conversations, "start_analysis", analyze)
    worker.step(now + timedelta(minutes=2))
    assert calls["download"] == 1
    assert soundcore_imports.list_imports(database)[0]["status"] == "completed"


def test_download_failures_back_off_and_stop_after_three(cloud, monkeypatch):
    database, worker, _ = cloud
    job = soundcore_imports.enqueue(database, URL)

    def fail(*args):
        raise OSError("https://private.example/secret-token")

    monkeypatch.setattr(soundcore_imports, "download_share", fail)
    now = datetime.now(UTC)
    assert worker.step(now)
    assert not worker.step(now + timedelta(seconds=10))
    assert worker.step(now + timedelta(minutes=2))
    assert worker.step(now + timedelta(minutes=5))
    result = soundcore_imports.list_imports(database)[0]
    assert result["status"] == "failed" and result["attempts"] == 3
    assert "secret" not in json.dumps(result)
    assert not worker.step(now + timedelta(hours=1))
    retried = soundcore_imports.retry(database, job["id"])
    assert retried["status"] == "queued" and retried["attempts"] == 0


def test_expired_link_fails_without_automatic_retry(cloud, monkeypatch):
    database, worker, _ = cloud
    soundcore_imports.enqueue(database, URL)

    def fail(*args):
        raise SoundcoreCloudError("expired")

    monkeypatch.setattr(soundcore_imports, "download_share", fail)
    worker.step()
    result = soundcore_imports.list_imports(database)[0]
    assert result["status"] == "failed" and "期限切れ" in result["error"]


def test_restart_recovers_download_and_preserves_attempt_limit(cloud):
    database, _, _ = cloud
    one = soundcore_imports.enqueue(database, URL)
    two = soundcore_imports.enqueue(database, URL + "TWO")
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE soundcore_imports SET status='downloading',attempts=1")
        connection.execute("UPDATE soundcore_imports SET attempts=3 WHERE id=?", (two["id"],))
    soundcore_imports.recover(database)
    jobs = {job["id"]: job for job in soundcore_imports.list_imports(database)}
    assert jobs[one["id"]]["status"] == "queued"
    assert jobs[two["id"]]["status"] == "failed"


def test_shutdown_during_download_keeps_job_for_restart(cloud, monkeypatch):
    database, worker, calls = cloud
    original = soundcore_imports.download_share

    def stopping(url, directory):
        audio = original(url, directory)
        worker.stop()
        return audio

    monkeypatch.setattr(soundcore_imports, "download_share", stopping)
    soundcore_imports.enqueue(database, URL)
    worker.step()
    result = soundcore_imports.list_imports(database)[0]
    assert result["status"] == "downloading" and result["recording_id"] is None
    assert calls["analysis"] == []
    soundcore_imports.recover(database)
    assert soundcore_imports.list_imports(database)[0]["status"] == "queued"


@pytest.fixture
def handler(tmp_path):
    factory = make_handler(
        tmp_path / "site", tmp_path / "articles", tmp_path / "reads", tmp_path / "feedback",
        tmp_path / "assistant", tmp_path / "client", tmp_path / "token",
        conversations_db=tmp_path / "conversations",
    )
    handler = factory.func.__new__(factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.headers = {"Host": "127.0.0.1:8787", "Content-Type": "application/json"}
    handler.path = "/api/conversations/soundcore-imports"
    return handler, responses


def body(handler, value):
    content = json.dumps(value).encode()
    handler.headers["Content-Length"] = str(len(content))
    handler.rfile = io.BytesIO(content)


def test_api_enqueue_list_retry_and_private_responses(handler):
    handler, responses = handler
    body(handler, {"url": URL})
    handler.do_POST()
    status, job = responses[-1]
    assert status == 202 and job["status"] == "queued"
    handler.do_GET()
    assert responses[-1] == (200, {"items": [job]})
    handler.path += "/" + job["id"] + "/retry"
    body(handler, {})
    handler.do_POST()
    assert responses[-1] == (202, job)
    assert "CODE1234" not in json.dumps(responses)


@pytest.mark.parametrize("headers", [
    {"Host": "malicious.example"},
    {"Origin": "https://malicious.example"},
    {"Sec-Fetch-Site": "cross-site"},
])
def test_api_rejects_untrusted_origin_before_reading_body(handler, headers):
    handler, responses = handler
    handler.headers.update(headers)
    handler.do_POST()
    assert responses[-1][0] == 403
    handler.do_GET()
    assert responses[-1][0] == 403


def test_api_invalid_url_and_logs_do_not_echo_input(handler):
    handler, responses = handler
    body(handler, {"url": "https://evil.example/secret"})
    handler.do_POST()
    assert responses[-1][0] == 400 and "secret" not in json.dumps(responses)
    logs = []
    handler.log_message = lambda *args: logs.append(args)
    handler.path += "?url=https://evil.example/secret"
    handler.log_request(400)
    assert "secret" not in json.dumps(logs)
