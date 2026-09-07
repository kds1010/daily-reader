from __future__ import annotations

import io
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from daily_reader import life_assistant as life
from daily_reader.conversations import (
    extract_recording_insights,
    list_insight_items,
    store_transcript,
)
from daily_reader.life_research import ResearchWorker, validate_result
from daily_reader.local_server import make_handler


@pytest.fixture
def database(tmp_path):
    return tmp_path / "conversations.sqlite3"


def task(**extra):
    return {"kind": "task", "title": "資料を確認する", **extra}


def event(**extra):
    return {
        "kind": "event",
        "title": "近所のイベント",
        "timezone": "Asia/Tokyo",
        "start_at": "2026-10-10T10:00:00+09:00",
        "end_at": "2026-10-10T11:00:00+09:00",
        "prepare_at": "2026-10-09T10:00:00+09:00",
        **extra,
    }


def result(**extra):
    return {
        "outcome": "answered",
        "conclusion": "公式情報を確認しました",
        "unresolved": "",
        "sources": [{"title": "公式", "url": "https://example.org/event"}],
        "options": [],
        "actions": [{"kind": "task", "title": "申し込む", "detail": "受付を確認"}],
        **extra,
    }


def test_manual_retry_and_concurrent_conversion_are_idempotent(database):
    life.snapshot(database)  # initialize before exercising concurrent writers
    payload = task(request_id="one")
    with ThreadPoolExecutor(4) as pool:
        entries = list(pool.map(lambda _: life.create_entry(database, payload), range(8)))
    assert len({e["id"] for e in entries}) == 1
    assert len(life.snapshot(database)["entries"]) == 1
    assert life.create_entry(database, task())["id"] != entries[0]["id"]


def test_revision_conflict_prevents_lost_updates(database):
    entry = life.create_entry(database, task())
    changed = life.update_entry(database, entry["id"], {"revision": 1, "title": "修正後"})
    assert changed["revision"] == 2
    with pytest.raises(ValueError, match="他の端末"):
        life.update_entry(database, entry["id"], {"revision": 1, "title": "古い端末"})
    assert life.get_entry(database, entry["id"])["title"] == "修正後"


@pytest.mark.parametrize(
    "extra",
    [
        {"start_at": "2026-10-10T10:00:00"},
        {"end_at": "2026-10-10T09:00:00+09:00"},
        {"deadline_at": "2026-10-11T09:00:00+09:00"},
        {"timezone": "invalid/zone"},
        {"start_at": ["invalid"]},
        {"source_url": {"url": "invalid"}},
    ],
)
def test_event_dates_and_urls_are_validated(database, extra):
    with pytest.raises(ValueError):
        life.create_entry(database, event(**extra))


def test_event_notification_changes_and_cancellation(database):
    entry = life.create_entry(database, event())
    notices = life.snapshot(database)["notifications"]
    assert notices[0]["at"] == "2026-10-09T01:00:00+00:00"
    assert notices[0]["entry_id"] == entry["id"]
    life.update_entry(database, entry["id"], {"revision": 1, "status": "cancelled"})
    assert life.snapshot(database)["notifications"] == []


def test_profile_requires_explicit_owner_and_expiry_limits_personalization(database):
    profile = {"kind": "profile", "title": "写真", "category": "interest"}
    with pytest.raises(ValueError):
        life.create_entry(database, profile)
    other = life.create_person(database, {"name": "同僚"})
    life.create_entry(database, {**profile, "person_id": other["id"]})
    life.create_entry(
        database,
        {
            **profile,
            "person_id": "self",
            "expires_at": "2020-01-01T00:00:00Z",
        },
    )
    articles = [{"id": "photo", "title": "写真の話題", "url": "https://example.org/photo"}]
    assert life.snapshot(database, articles)["news"][0]["reason"] == "最近の更新"
    own = life.create_entry(database, {**profile, "person_id": "self"})
    assert life.snapshot(database, articles)["news"][0]["reason"] == "関心: 写真"
    life.delete_profile(database, own["id"], 1)
    assert life.snapshot(database, articles)["news"][0]["reason"] == "最近の更新"
    with pytest.raises(KeyError):
        life.get_entry(database, own["id"])


def test_conversation_evidence_survives_reanalysis_and_search_escapes_wildcards(
    database,
    tmp_path,
    monkeypatch,
):
    text = "写真が好きです。準備を100%終わらせてください".encode()
    recording = store_transcript(database, io.BytesIO(text), len(text), "20260907.txt")
    monkeypatch.setattr(
        "daily_reader.conversations.request_insights",
        lambda **kw: [
            {
                "kind": "interest",
                "title": "写真",
                "detail": "本人が明言",
                "assignee": "話者1",
                "due_date": None,
                "due_date_original": None,
                "certainty": "explicit",
                "evidence_utterance_ids": [kw["utterances"][0]["id"]],
            }
        ],
    )
    schema = tmp_path / "schema.json"
    schema.write_text("{}")
    extract_recording_insights(database, recording["id"], schema)
    item = next(i for i in list_insight_items(database) if i["kind"] == "interest")
    profile = life.create_entry(
        database,
        {
            "kind": "profile",
            "title": "写真",
            "person_id": "self",
            "category": "interest",
            "source_type": "conversation",
            "source_id": item["id"],
        },
    )
    assert profile["evidence"]["recording_id"] == recording["id"]
    assert "写真" in profile["evidence"]["quotes"][0]["quote"]
    with life.connect(database) as connection:
        connection.execute(
            "UPDATE utterances SET text='変更後' WHERE recording_id=?", (recording["id"],)
        )
    assert "写真" in life.get_entry(database, profile["id"])["evidence"]["quotes"][0]["quote"]
    assert life.search_conversations(database, "%") == []
    assert life.search_conversations(database, "変更後")[0]["recording_id"] == recording["id"]


def test_research_queue_capacity_claim_retry_and_cancel(database):
    worker = ResearchWorker(database, "unused", "unused")
    first = life.create_entry(database, {"kind": "research", "title": "調査"})
    assert worker.claim()["id"] == first["id"]
    assert worker.claim() is None
    running = life.get_entry(database, first["id"])
    assert running["status"] == "running"
    cancelled = life.update_entry(
        database,
        first["id"],
        {
            "revision": running["revision"],
            "status": "cancelled",
        },
    )
    for _ in range(10):
        life.create_entry(database, {"kind": "research", "title": "別の調査"})
    with pytest.raises(ValueError, match="10件"):
        life.create_entry(database, {"kind": "research", "title": "満杯"})
    with pytest.raises(ValueError, match="10件"):
        life.update_entry(
            database,
            first["id"],
            {
                "revision": cancelled["revision"],
                "status": "queued",
            },
        )


def test_interrupted_research_fails_visibly_and_can_retry(database, monkeypatch):
    worker = ResearchWorker(database, "unused", "unused")
    entry = life.create_entry(database, {"kind": "research", "title": "調査"})
    worker.claim()
    monkeypatch.setattr(worker, "run", lambda: None)
    worker.start()
    worker.thread.join(1)
    failed = life.get_entry(database, entry["id"])
    assert failed["status"] == "failed"
    assert "再起動" in failed["error"]
    retry = life.update_entry(
        database,
        entry["id"],
        {
            "revision": failed["revision"],
            "status": "queued",
        },
    )
    assert retry["status"] == "queued"
    assert "error" not in retry


@pytest.mark.parametrize(
    "raw",
    [
        result(sources=[]),
        result(sources=[{"title": "bad", "url": "file:///etc/passwd"}]),
        result(options=[{}] * 4),
        result(actions=[{"kind": "agent", "title": "execute"}]),
    ],
)
def test_research_rejects_unsourced_or_unbounded_results(raw):
    with pytest.raises(ValueError):
        validate_result(raw)


def test_research_action_keeps_sources_and_cannot_duplicate(database):
    entry = life.create_entry(database, {"kind": "research", "title": "調査"})
    with life.connect(database) as connection:
        data = {**entry, "result": result()}
        connection.execute(
            "UPDATE life_entries SET status='completed',data=? WHERE id=?",
            (json.dumps(data), entry["id"]),
        )
    payload = task(source_type="research", source_id=entry["id"], source_index=0)
    first = life.create_entry(database, payload)
    assert life.create_entry(database, payload)["id"] == first["id"]
    assert first["evidence"]["sources"][0]["url"] == "https://example.org/event"


def test_research_login_failure_does_not_stay_running(database, monkeypatch):
    worker = ResearchWorker(database, "unused", "unused")
    entry = life.create_entry(database, {"kind": "research", "title": "調査"})
    monkeypatch.setattr("daily_reader.life_research.codex_available", lambda _: False)
    worker.execute(worker.claim())
    failed = life.get_entry(database, entry["id"])
    assert failed["status"] == "failed"
    assert "ログイン" in failed["error"]


def test_http_life_routes_preserve_curated_news_filter(tmp_path, database):
    articles = tmp_path / "articles.json"
    articles.write_text(
        json.dumps(
            {
                "articles": [
                    {"id": "near", "title": "近所のイベント"},
                    {"id": "far", "title": "遠方のイベント"},
                ]
            }
        )
    )
    (tmp_path / "highlights.json").write_text(
        json.dumps(
            {
                "field_highlights": [
                    {"items": [{"article_id": "near"}]},
                ]
            }
        )
    )
    factory = make_handler(
        tmp_path / "site",
        articles,
        tmp_path / "read",
        tmp_path / "feedback",
        tmp_path / "assistant",
        tmp_path / "client",
        tmp_path / "token",
        conversations_db=database,
    )
    handler = factory.func.__new__(factory.func)
    responses = []
    handler._send_json = lambda status, payload: responses.append((status, payload))
    handler.path = "/api/life/entries"
    handler._read_json = lambda **_: task()
    handler.do_POST()
    assert responses[-1][0] == 200
    handler.path = "/api/life"
    handler.do_GET()
    assert [n["id"] for n in responses[-1][1]["news"]] == ["near"]
    handler.path = "/api/life/entries"
    handler._read_json = lambda **_: {"kind": [], "title": "invalid"}
    handler.do_POST()
    assert responses[-1][0] == 400


def test_event_updates_linked_task_dates_but_respects_manual_override(database):
    parent = life.create_entry(database, event())
    child = life.create_entry(
        database,
        task(
            source_type="event",
            source_id=parent["id"],
            source_role="prepare",
            due_at=parent["prepare_at"],
            remind_at=parent["prepare_at"],
        ),
    )
    moved = "2026-10-08T01:00:00+00:00"
    life.update_entry(database, parent["id"], {"revision": 1, "prepare_at": moved})
    updated = life.get_entry(database, child["id"])
    assert updated["due_at"] == updated["remind_at"] == moved
    assert json.loads(updated["history"][0]["data"])["due_at"] == parent["prepare_at"]
    manual = "2026-10-07T01:00:00+00:00"
    life.update_entry(database, child["id"], {"revision": 2, "due_at": manual})
    life.update_entry(
        database,
        parent["id"],
        {
            "revision": 2,
            "prepare_at": "2026-10-06T01:00:00+00:00",
        },
    )
    assert life.get_entry(database, child["id"])["due_at"] == manual
    life.update_entry(database, parent["id"], {"revision": 3, "status": "cancelled"})
    assert life.get_entry(database, child["id"])["status"] == "cancelled"
    assert life.snapshot(database)["notifications"] == []


def test_research_feedback_is_explicit_and_does_not_change_completion_time(database):
    entry = life.create_entry(database, {"kind": "research", "title": "調査"})
    with life.connect(database) as connection:
        data = {
            "title": "調査",
            "detail": "",
            "source_url": "",
            "constraints": "",
            "due_at": None,
            "completed_at": "2026-09-01T00:00:00Z",
            "result": result(),
        }
        connection.execute(
            "UPDATE life_entries SET status='completed',data=? WHERE id=?",
            (json.dumps(data), entry["id"]),
        )
    rated = life.update_entry(
        database,
        entry["id"],
        {
            "revision": 1,
            "feedback": {"useful": True, "saved_minutes": 15},
        },
    )
    assert rated["feedback"]["saved_minutes"] == 15
    assert rated["completed_at"] == "2026-09-01T00:00:00Z"
    with pytest.raises(ValueError):
        life.update_entry(
            database,
            entry["id"],
            {
                "revision": 2,
                "feedback": {"useful": True, "saved_minutes": -1},
            },
        )


def test_worker_executes_only_explicit_request_and_uses_readonly_web_mode(database, monkeypatch):
    import subprocess

    worker = ResearchWorker(database, "codex", "gpt-5.6-luna")
    life.create_entry(database, {"kind": "profile", "title": "非公開の好み", "person_id": "self"})
    entry = life.create_entry(database, {"kind": "research", "title": "公式情報を調べる"})
    captured = {}

    def popen(command, **kwargs):
        captured["command"] = command
        captured["input"] = json.load(kwargs["stdin"])
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps(result()))

        class Completed:
            returncode = 0

            def poll(self):
                return 0

        return Completed()

    monkeypatch.setattr("daily_reader.life_research.codex_available", lambda _: True)
    monkeypatch.setattr(subprocess, "Popen", popen)
    worker.execute(worker.claim())
    assert life.get_entry(database, entry["id"])["status"] == "completed"
    assert set(captured["input"]) == {
        "title",
        "detail",
        "constraints",
        "due_at",
        "source_url",
        "today",
    }
    assert "非公開" not in json.dumps(captured["input"], ensure_ascii=False)
    assert 'web_search="live"' in captured["command"]
    assert "features.shell_tool=false" in captured["command"]
    assert "read-only" in captured["command"]


def test_worker_terminates_cancelled_job_without_publishing_result(database, monkeypatch):
    import subprocess

    worker = ResearchWorker(database, "codex", "unused")
    entry = life.create_entry(database, {"kind": "research", "title": "調査"})
    job = worker.claim()
    life.update_entry(database, entry["id"], {"revision": 2, "status": "cancelled"})

    class Pending:
        returncode = None

        def poll(self):
            return None

    terminated = []
    monkeypatch.setattr("daily_reader.life_research.codex_available", lambda _: True)
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **kw: Pending())
    monkeypatch.setattr(worker, "_terminate", lambda: terminated.append(True))
    worker.execute(job)
    assert terminated
    assert life.get_entry(database, entry["id"])["status"] == "cancelled"
    assert "result" not in life.get_entry(database, entry["id"])
