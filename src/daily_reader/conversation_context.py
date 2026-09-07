"""Rebuildable links from conversation subjects to immutable GPS observations."""

from __future__ import annotations

import json
import math
import sqlite3
from datetime import UTC, datetime, timedelta

MATCH_VERSION = "nearest-gps-v2"
MAX_TIME_DELTA_SECONDS = 300
MAX_ACCURACY_METERS = 200


def _query(connection: sqlite3.Connection, sql: str, parameters: tuple = ()) -> sqlite3.Cursor:
    cursor = connection.cursor()
    cursor.row_factory = sqlite3.Row
    return cursor.execute(sql, parameters)


def read_contexts(connection: sqlite3.Connection, recording_id: str) -> list[dict[str, object]]:
    from daily_reader.device_context import context_at

    rows = _query(
        connection,
        """SELECT links.*, gps.timestamp, gps.latitude, gps.longitude,
        gps.horizontal_accuracy, gps.is_approximate,
        gps.speed_mps, gps.speed_accuracy_mps, gps.is_simulated
        FROM conversation_location_links AS links
        LEFT JOIN location_events AS gps ON gps.id=links.location_event_id
        WHERE links.recording_id=? ORDER BY links.target_timestamp, links.subject_id""",
        (recording_id,),
    ).fetchall()
    results = []
    for row in rows:
        item = dict(row)
        item["device_context"] = context_at(connection, item["target_timestamp"])
        keys = (
            "timestamp",
            "latitude",
            "longitude",
            "horizontal_accuracy",
            "is_approximate",
            "speed_mps",
            "speed_accuracy_mps",
            "is_simulated",
        )
        location = {key: item.pop(key) for key in keys}
        location["is_approximate"] = bool(location["is_approximate"])
        item["location"] = location if item["location_event_id"] else None
        results.append(item)
    return results


def rebuild_context(connection: sqlite3.Connection, recording_id: str) -> None:
    recording = _query(
        connection, "SELECT * FROM recordings WHERE id=?", (recording_id,)
    ).fetchone()
    if recording is None:
        raise KeyError(recording_id)
    start = None
    if recording["recorded_at_verified"] and recording["recorded_at"]:
        try:
            value = datetime.fromisoformat(recording["recorded_at"])
            if value.tzinfo is not None:
                start = value.astimezone(UTC)
        except ValueError:
            pass
    date_source = recording["recorded_at_source"]
    if start and date_source == "unknown":
        date_source = "legacy_verified"
    subjects = [(recording_id, None, start, "recording_start")]
    if recording["source_type"] == "audio":
        for row in _query(
            connection,
            "SELECT id,start_seconds FROM utterances WHERE recording_id=?",
            (recording_id,),
        ):
            offset = row["start_seconds"]
            target = None
            if start and math.isfinite(offset) and 0 <= offset <= 31 * 86400:
                target = start + timedelta(seconds=offset)
            subjects.append((row["id"], row["id"], target, "audio_offset_estimate"))
    connection.execute(
        "DELETE FROM conversation_location_links WHERE recording_id=?", (recording_id,)
    )
    connection.execute(
        """UPDATE recordings SET location_latitude=NULL, location_longitude=NULL,
        location_accuracy=NULL, location_timestamp=NULL, location_time_delta=NULL WHERE id=?""",
        (recording_id,),
    )
    now = datetime.now(UTC).isoformat()
    for subject_id, utterance_id, target, basis in subjects:
        candidate, delta = None, None
        state = "unknown_time"
        if target:
            bounds = (
                (target - timedelta(seconds=MAX_TIME_DELTA_SECONDS)).isoformat(),
                (target + timedelta(seconds=MAX_TIME_DELTA_SECONDS)).isoformat(),
            )
            candidate = _query(
                connection,
                """SELECT *, ABS((julianday(timestamp)-julianday(?))*86400) AS delta
                FROM location_events
                WHERE julianday(timestamp) BETWEEN julianday(?) AND julianday(?)
                AND horizontal_accuracy BETWEEN 0 AND ? AND is_approximate=0
                AND is_simulated=0 AND (speed_mps IS NULL OR speed_accuracy_mps IS NULL
                    OR horizontal_accuracy+(speed_mps+speed_accuracy_mps)
                    * ABS((julianday(timestamp)-julianday(?))*86400)<=300)
                ORDER BY delta, horizontal_accuracy, id LIMIT 1""",
                (target.isoformat(), *bounds, MAX_ACCURACY_METERS, target.isoformat()),
            ).fetchone()
            if candidate:
                state, delta = "matched_estimate", round(candidate["delta"], 3)
            else:
                nearby = connection.execute(
                    "SELECT 1 FROM location_events WHERE julianday(timestamp) "
                    "BETWEEN julianday(?) AND julianday(?) LIMIT 1",
                    bounds,
                ).fetchone()
                state = "low_accuracy" if nearby else "no_nearby_gps"
        connection.execute(
            """INSERT INTO conversation_location_links
            (recording_id,subject_id,utterance_id,location_event_id,target_timestamp,
             time_basis,date_source,state,time_delta_seconds,method_version,matched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (
                recording_id,
                subject_id,
                utterance_id,
                candidate["id"] if candidate else None,
                target.isoformat() if target else None,
                basis,
                date_source,
                state,
                delta,
                MATCH_VERSION,
                now,
            ),
        )
        if candidate and utterance_id is None:
            connection.execute(
                """UPDATE recordings SET location_latitude=?,location_longitude=?,
                location_accuracy=?,location_timestamp=?,location_time_delta=? WHERE id=?""",
                (
                    candidate["latitude"],
                    candidate["longitude"],
                    candidate["horizontal_accuracy"],
                    candidate["timestamp"],
                    delta,
                    recording_id,
                ),
            )


def rebuild_for_gps(connection: sqlite3.Connection, timestamps: list[str]) -> None:
    if not timestamps:
        return
    # Late/offline uploads may overlap any part of an existing recording.
    earliest = min(datetime.fromisoformat(value) for value in timestamps)
    latest = max(datetime.fromisoformat(value) for value in timestamps)
    rows = _query(
        connection,
        """SELECT recordings.id FROM recordings WHERE recorded_at_verified=1
        AND julianday(recorded_at) <= julianday(?)
        AND julianday(recorded_at) + COALESCE(
            (SELECT MAX(end_seconds) FROM utterances
             WHERE recording_id=recordings.id AND recordings.source_type='audio'),0)/86400
            >= julianday(?)""",
        (
            (latest + timedelta(seconds=MAX_TIME_DELTA_SECONDS)).isoformat(),
            (earliest - timedelta(seconds=MAX_TIME_DELTA_SECONDS)).isoformat(),
        ),
    ).fetchall()
    for row in rows:
        rebuild_context(connection, row["id"])


def snapshot_evidence_contexts(
    connection: sqlite3.Connection, recording_id: str, item_id: str | None = None
) -> None:
    """Freeze context when downstream work is approved, or before replacing utterances."""
    contexts = read_contexts(connection, recording_id)
    for context in contexts:
        if context["utterance_id"]:
            connection.execute(
                """UPDATE conversation_item_evidence SET context_snapshot=?
                WHERE utterance_id=? AND context_snapshot IS NULL
                AND (? IS NULL OR item_id=?)""",
                (
                    json.dumps(context, ensure_ascii=False),
                    context["utterance_id"],
                    item_id,
                    item_id,
                ),
            )
    # TXT has no real audio offsets; retain only its recording-level context.
    for context in contexts:
        if context["utterance_id"] is None:
            connection.execute(
                """UPDATE conversation_item_evidence SET context_snapshot=?
                WHERE context_snapshot IS NULL AND item_id IN
                (SELECT id FROM conversation_items WHERE recording_id=?)
                AND (? IS NULL OR item_id=?)""",
                (json.dumps(context, ensure_ascii=False), recording_id, item_id, item_id),
            )
