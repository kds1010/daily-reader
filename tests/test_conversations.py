from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import pytest

from daily_reader.conversations import (
    get_recording,
    mark_proposal_approved,
    store_upload,
    update_speaker,
)


def test_upload_keeps_original_and_deduplicates(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    audio = tmp_path / "audio"
    content = b"ID3" + b"recording" * 100

    first = store_upload(database, audio, io.BytesIO(content), len(content), "meeting.mp3")
    second = store_upload(database, audio, io.BytesIO(content), len(content), "copy.mp3")

    assert first["id"] == second["id"]
    assert (audio / str(first["id"]) / "original.mp3").read_bytes() == content
    assert len(list(audio.glob("*/original.mp3"))) == 1


def test_upload_rejects_non_mp3(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="only MP3"):
        store_upload(tmp_path / "db", tmp_path / "audio", io.BytesIO(b"x"), 1, "x.wav")


def test_recording_returns_speakers_utterances_topics_and_tasks(tmp_path: Path) -> None:
    database = tmp_path / "conversations.sqlite3"
    item = store_upload(database, tmp_path / "audio", io.BytesIO(b"ID3x"), 4, "x.mp3")
    recording_id = str(item["id"])
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO speakers(id,recording_id,label) VALUES('s1',?,'話者1')", (recording_id,)
        )
        connection.execute(
            """INSERT INTO utterances VALUES
            ('u1',?,'s1',0,2,'資料を確認して','-0.1','会話','依頼・タスク')""",
            (recording_id,),
        )
        connection.execute(
            "INSERT INTO conversation_topics VALUES('t1',?,'仕事','会話','資料の確認')",
            (recording_id,),
        )
        connection.execute(
            """INSERT INTO task_proposals
            (id,recording_id,utterance_id,title,created_at)
            VALUES('p1',?,'u1','資料を確認','now')""",
            (recording_id,),
        )

    update_speaker(database, "s1", "田中さん")
    detail = get_recording(database, recording_id)
    assert detail["speakers"][0]["display_name"] == "田中さん"
    assert detail["utterances"][0]["speaker"] == "田中さん"
    assert detail["topics"][0]["name"] == "仕事"
    assert detail["task_proposals"][0]["status"] == "awaiting_review"

    mark_proposal_approved(database, "p1", "planner", "task-1")
    assert (
        get_recording(database, recording_id)["task_proposals"][0]["approved_item_id"] == "task-1"
    )
    with pytest.raises(ValueError, match="not awaiting review"):
        mark_proposal_approved(database, "p1", "agent", "job-1")
