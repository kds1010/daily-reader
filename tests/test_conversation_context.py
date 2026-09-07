from __future__ import annotations

import io
from pathlib import Path

import pytest

from daily_reader.conversations import (
    _connect,
    _insight_input,
    _replace_analysis_results,
    get_recording,
    mark_insight_item_approved,
    recover_interrupted_conversations,
    store_location_events,
    store_transcript,
    store_upload,
)


@pytest.fixture(autouse=True)
def allow_test_audio_storage(monkeypatch):
    monkeypatch.setattr("daily_reader.conversations.MINIMUM_FREE_BYTES", 0)


def gps(timestamp="2026-09-04T15:00:00Z", accuracy=20, approximate=False):
    return {
        "timestamp": timestamp,
        "latitude": 35.681236,
        "longitude": 139.767125,
        "horizontal_accuracy": accuracy,
        "is_approximate": approximate,
    }


def recording(tmp_path: Path, filename="2026-09-05 00:00:00.mp3"):
    item = store_upload(tmp_path / "db", tmp_path / "audio", io.BytesIO(b"ID3x"), 4, filename)
    return str(item["id"])


def analyze(tmp_path, recording_id):
    with _connect(tmp_path / "db") as connection:
        _replace_analysis_results(
            connection,
            recording_id,
            [
                (0, 3, "資料を確認してください", None, "話者1"),
                (1800, 1803, "次の資料も確認してください", None, "話者1"),
            ],
            "now",
        )


def contexts(tmp_path, recording_id):
    return get_recording(tmp_path / "db", recording_id)["location_contexts"]


def test_gps_first_uses_filename_date_across_utc_day(tmp_path):
    store_location_events(tmp_path / "db", [gps()])
    recording_id = recording(tmp_path)
    link = contexts(tmp_path, recording_id)[0]
    assert link["state"] == "matched_estimate"
    assert link["target_timestamp"] == "2026-09-04T15:00:00+00:00"
    assert link["date_source"] == "soundcore_filename_jst"
    assert link["location_event_id"]
    assert link["time_delta_seconds"] == 0
    assert link["method_version"] == "nearest-gps-v2"
    assert recording(tmp_path) == recording_id
    assert contexts(tmp_path, recording_id)[0]["location_event_id"] == link["location_event_id"]


def test_late_gps_matches_audio_offsets_without_reanalysis(tmp_path):
    recording_id = recording(tmp_path)
    analyze(tmp_path, recording_id)
    before = get_recording(tmp_path / "db", recording_id)
    store_location_events(tmp_path / "db", [gps("2026-09-04T15:30:30Z")])
    links = contexts(tmp_path, recording_id)
    assert len(links) == 3
    last = next(link for link in links if link["state"] == "matched_estimate")
    assert last["utterance_id"] == before["utterances"][1]["id"]
    assert last["time_basis"] == "audio_offset_estimate"
    assert last["time_delta_seconds"] == 30
    store_location_events(tmp_path / "db", [gps("2026-09-04T15:30:00Z")])
    store_location_events(tmp_path / "db", [gps("2026-09-04T15:30:00Z")])
    after = get_recording(tmp_path / "db", recording_id)
    assert after["utterances"] == before["utterances"]
    assert after["insight_status"] == before["insight_status"]
    assert (
        next(
            link
            for link in after["location_contexts"]
            if link["utterance_id"] == last["utterance_id"]
        )["time_delta_seconds"]
        == 0
    )
    with _connect(tmp_path / "db") as connection:
        assert connection.execute("SELECT count(*) FROM location_events").fetchone()[0] == 2
        _, inputs = _insight_input(connection, recording_id)
    assert all(
        set(value) == {"id", "start_seconds", "end_seconds", "text", "speaker"} for value in inputs
    )


@pytest.mark.parametrize(
    ("event", "state"),
    [
        (gps("2026-09-04T15:05:00Z", 200), "matched_estimate"),
        (gps("2026-09-04T15:05:01Z"), "no_nearby_gps"),
        (gps(accuracy=201), "low_accuracy"),
        (gps(approximate=True), "low_accuracy"),
    ],
)
def test_matching_quality_boundaries(tmp_path, event, state):
    store_location_events(tmp_path / "db", [event])
    recording_id = recording(tmp_path)
    assert contexts(tmp_path, recording_id)[0]["state"] == state


def test_unknown_date_never_uses_import_time(tmp_path):
    recording_id = recording(tmp_path, "2026-09-05.mp3")
    item = get_recording(tmp_path / "db", recording_id)
    store_location_events(tmp_path / "db", [gps(item["created_at"])])
    analyze(tmp_path, recording_id)
    assert {link["state"] for link in contexts(tmp_path, recording_id)} == {"unknown_time"}
    assert all(link["target_timestamp"] is None for link in contexts(tmp_path, recording_id))


def test_txt_links_only_recording_and_snapshots_that_basis(tmp_path):
    store_location_events(tmp_path / "db", [gps()])
    content = "資料を確認してください\n予定も確認してください".encode()
    item = store_transcript(
        tmp_path / "db", io.BytesIO(content), len(content), "2026-09-05 00:00:00_文字起こし.txt"
    )
    links = contexts(tmp_path, item["id"])
    assert len(links) == 1 and links[0]["utterance_id"] is None
    assert (
        item["insight_items"][0]["evidence"][0]["location_context"]["time_basis"]
        == "recording_start"
    )
    mark_insight_item_approved(tmp_path / "db", item["insight_items"][0]["id"], "planner", "task-1")
    updated = get_recording(tmp_path / "db", item["id"])
    approved = next(i for i in updated["insight_items"] if i["status"] == "approved")
    assert approved["evidence"][0]["location_context_is_snapshot"]


def test_approved_evidence_survives_better_gps_and_reanalysis(tmp_path):
    store_location_events(tmp_path / "db", [gps("2026-09-04T15:00:30Z")])
    recording_id = recording(tmp_path)
    analyze(tmp_path, recording_id)
    detail = get_recording(tmp_path / "db", recording_id)
    item = next(i for i in detail["insight_items"] if i["evidence"][0]["start_seconds"] == 0)
    mark_insight_item_approved(tmp_path / "db", item["id"], "planner", "task-1")
    store_location_events(tmp_path / "db", [gps()])
    assert contexts(tmp_path, recording_id)[0]["time_delta_seconds"] == 0
    analyze(tmp_path, recording_id)
    approved = next(
        i
        for i in get_recording(tmp_path / "db", recording_id)["insight_items"]
        if i["id"] == item["id"]
    )
    evidence = approved["evidence"][0]
    assert approved["approved_item_id"] == "task-1"
    assert evidence["utterance_id"] is None
    assert evidence["quote"] == "資料を確認してください"
    assert evidence["location_context_is_snapshot"]
    assert evidence["location_context"]["time_delta_seconds"] == 30
    assert evidence["location_context"]["utterance_id"] == item["evidence"][0]["utterance_id"]


def test_startup_backfills_links_and_clears_stale_legacy_coordinates(tmp_path):
    recording_id = recording(tmp_path)
    analyze(tmp_path, recording_id)
    with _connect(tmp_path / "db") as connection:
        connection.execute("DELETE FROM conversation_location_links")
        connection.execute(
            "UPDATE recordings SET location_latitude=35, recorded_at_source='unknown'"
        )
    recover_interrupted_conversations(tmp_path / "db")
    detail = get_recording(tmp_path / "db", recording_id)
    assert detail["location_latitude"] is None
    assert len(detail["location_contexts"]) == 3
    assert {link["date_source"] for link in detail["location_contexts"]} == {"legacy_verified"}
