"""Grounded recording recaps and bounded, read-only conversation list projections."""

from __future__ import annotations

import json
import math
import sqlite3

from daily_reader.conversation_insights import OVERVIEW_PROMPT_VERSION, ConversationInsightError

GPS_FIELDS = (
    "timestamp", "latitude", "longitude", "horizontal_accuracy", "is_approximate",
    "speed_mps", "speed_accuracy_mps", "is_simulated",
)


def grounded_points(raw: list[dict], utterances: dict[str, dict], chunk_index: int) -> list[dict]:
    points = []
    for item in raw:
        text = item.get("text")
        ids = item.get("evidence_utterance_ids")
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 500:
            raise ConversationInsightError("Codexの会話要点が不正です")
        if (
            not isinstance(ids, list) or not 1 <= len(ids) <= 8
            or not all(isinstance(value, str) and value in utterances for value in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ConversationInsightError("Codexの根拠発話が不正です")
        points.append({
            "text": text.strip(), "chunk_index": chunk_index,
            "evidence": [{
                "utterance_id": value,
                "quote": utterances[value].get("raw_text", utterances[value]["text"]),
                "corrected_quote": (
                    utterances[value]["text"] if utterances[value].get(
                        "raw_text", utterances[value]["text"]
                    ) != utterances[value]["text"] else None
                ),
                "correction_revision_id": utterances[value].get("correction_revision_id"),
                "correction_is_snapshot": bool(utterances[value].get("correction_revision_id")),
                "correction_uncertain": bool(utterances[value].get("correction_uncertain")),
                "speaker": utterances[value].get("speaker"),
                "start_seconds": utterances[value].get("start_seconds"),
                "end_seconds": utterances[value].get("end_seconds"),
            } for value in ids],
        })
    return points


def _metadata(recording: dict) -> dict:
    value = recording.get("transcription_metadata") or {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (ValueError, TypeError):
            return {}
    return value if isinstance(value, dict) else {}


def _summary(recording: dict, row: sqlite3.Row | None, *, full: bool) -> dict:
    metadata = _metadata(recording)
    warnings = metadata.get("warnings", [])
    result = {
        "status": {"extracting": "analyzing", "completed": "not_requested"}.get(
            recording["overview_status"], recording["overview_status"]
        ),
        "source": None, "text": None, "points": [], "chunk_count": 0,
        "scope": "unknown", "quality_warnings": [
            value[:300] for value in warnings[:8] if isinstance(value, str)
        ] if isinstance(warnings, list) else [],
        "generated_at": None, "chunks": [],
        "generation_status": recording["overview_status"],
        "generation_error": recording["overview_error"],
    }
    duration, speech = metadata.get("duration_seconds"), metadata.get("speech_seconds")
    if (
        recording["source_type"] == "audio" and isinstance(duration, (int, float))
        and isinstance(speech, (int, float)) and duration > 0 and 0 <= speech < duration * 0.1
    ):
        result["quality_warnings"].append(
            "音声に比べて文字起こしの対象区間が短いため、一部の内容だけを整理している可能性があります。"
        )
    if not row:
        return result
    if row["stale"] or row["extractor_version"] != OVERVIEW_PROMPT_VERSION:
        result["status"] = "stale"
        return result
    data = json.loads(row["data"])
    all_points = data["points"]
    points = all_points if full else all_points[:4]
    result.update({
        "status": "ready" if all_points else "empty", "source": "codex",
        "text": "\n".join(point["text"] for point in points) or None,
        "points": points if full else [{
            **point, "evidence": [
                {**evidence, "quote": evidence["quote"][:300]}
                for evidence in point["evidence"][:2]
            ],
        } for point in points],
        "chunk_count": len(data["chunks"]),
        "scope": "chunked" if len(data["chunks"]) > 1 else "full_recording",
        "generated_at": row["created_at"],
        "chunks": data["chunks"] if full else [],
        "point_count": len(all_points), "preview_truncated": len(points) < len(all_points),
    })
    if len(data["chunks"]) > 1:
        result["quality_warnings"].append("長い録音を区間ごとに整理しています。区間をまたぐ撤回や重複は未照合です。")
    return result


def attach_digests(connection: sqlite3.Connection, recordings: list[dict], *, full=False) -> None:
    """Batch only list-sized rows: no transcripts, device context, or utterance GPS reads."""
    for offset in range(0, len(recordings), 400):
        group = recordings[offset:offset + 400]
        ids = tuple(recording["id"] for recording in group)
        placeholders = ",".join("?" for _ in ids)
        summaries = {
            row["recording_id"]: row for row in connection.execute(
                f"SELECT * FROM conversation_overviews WHERE recording_id IN ({placeholders})", ids
            )
        }
        counts: dict[str, list[dict]] = {}
        for row in connection.execute(
            f"""SELECT recording_id,kind,status,COUNT(*) AS count FROM conversation_items
            WHERE recording_id IN ({placeholders})
            AND status IN ('awaiting_review','kept','approved')
            AND (visible_rule_item(source,status,title)
                OR (source='rule' AND updated_at<>created_at))
            GROUP BY recording_id,kind,status ORDER BY kind,status""", ids,
        ):
            entry = dict(row)
            counts.setdefault(entry.pop("recording_id"), []).append(entry)
        previews: dict[str, list[dict]] = {}
        for row in connection.execute(
            f"""WITH ranked AS (
                SELECT id,recording_id,kind,title,
                CASE WHEN source='rule' AND status='awaiting_review' THEN 'ambiguous'
                    ELSE certainty END AS certainty,status,
                ROW_NUMBER() OVER (PARTITION BY recording_id ORDER BY
                  CASE status WHEN 'awaiting_review' THEN 0 ELSE 1 END,created_at DESC,id) AS rank
                FROM conversation_items WHERE recording_id IN ({placeholders})
                AND status IN ('awaiting_review','kept','approved')
                AND (visible_rule_item(source,status,title)
                    OR (source='rule' AND updated_at<>created_at))
            ) SELECT ranked.*,(SELECT COUNT(*) FROM conversation_item_evidence e
                WHERE e.item_id=ranked.id) AS evidence_count
            FROM ranked WHERE rank<=3 ORDER BY recording_id,rank""", ids,
        ):
            entry = dict(row)
            entry.pop("rank")
            previews.setdefault(entry.pop("recording_id"), []).append(entry)
        locations = {}
        for row in connection.execute(
            f"""SELECT links.*,gps.timestamp,gps.latitude,gps.longitude,gps.horizontal_accuracy,
            gps.is_approximate,gps.speed_mps,gps.speed_accuracy_mps,gps.is_simulated
            FROM conversation_location_links links
            LEFT JOIN location_events gps ON gps.id=links.location_event_id
            WHERE links.recording_id IN ({placeholders}) AND links.utterance_id IS NULL""", ids,
        ):
            entry = dict(row)
            location = {key: entry.pop(key) for key in GPS_FIELDS}
            location["is_approximate"] = bool(location["is_approximate"])
            entry["location"] = location if entry["location_event_id"] else None
            locations[entry["recording_id"]] = entry
        for recording in group:
            recording_id = recording["id"]
            recording["recorded_at_verified"] = bool(recording["recorded_at_verified"])
            duration = _metadata(recording).get("duration_seconds")
            recording["duration_seconds"] = duration if (
                recording["source_type"] == "audio"
                and isinstance(duration, (int, float)) and not isinstance(duration, bool)
                and math.isfinite(duration) and duration > 0
            ) else None
            summary = _summary(recording, summaries.get(recording_id), full=False)
            recording["digest"] = {
                "summary": summary, "counts": counts.get(recording_id, []),
                "preview_items": previews.get(recording_id, []),
                "location_context": locations.get(recording_id),
            }
            if full:
                recording["overview"] = _summary(recording, summaries.get(recording_id), full=True)
