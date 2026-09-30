import fcntl
import json
import subprocess
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from daily_reader import highlights
from daily_reader.core import Article


@pytest.fixture
def generation(monkeypatch, tmp_path):
    now = datetime.fromisoformat("2026-09-12T08:00:00+09:00")
    article = Article(
        id="a1", title="A new data governance capability", source="Example",
        url="https://example.test/a1", published_at=now.isoformat(),
        summary="A source-grounded finding", category="データ基盤", score=5,
    )
    output = tmp_path / "highlights.json"
    history = tmp_path / "history.jsonl"
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        Path(command[command.index("--output-last-message") + 1]).write_text(json.dumps({
            "headline": "Headline", "overview": "Overview",
            "field_highlights": [{"field": "データ・AI", "items": [{"article_id": "a1"}]}],
            "official_digest": [], "gadget_digest": [], "tech_picks": [],
        }))

    monkeypatch.setattr(highlights.shutil, "which", lambda _: "/fake/codex")
    monkeypatch.setattr(highlights, "run_codex", run)
    monkeypatch.setattr(highlights, "_highlight_images", lambda *_: {})

    def generate(*, articles=None, at=now, feedback=None):
        return highlights.generate_highlights(
            [article] if articles is None else articles, output, tmp_path / "schema.json",
            at, feedback, history,
        )

    return generate, calls, article, now, output, history


def test_own_selection_does_not_regenerate_after_restart(generation):
    generate, calls, _, now, output, history = generation
    assert generate()
    saved = output.read_bytes()
    first_history = history.read_bytes()
    # All admission data comes from disk, including after a process restart.
    for offset in (1, 2, 4, 9, 14):
        assert not generate(at=now + timedelta(hours=offset))
    assert len(calls) == 1
    assert output.read_bytes() == saved
    assert history.read_bytes() == first_history


def test_unchanged_news_rotates_after_twenty_four_hours(generation):
    generate, calls, _, now, _, history = generation
    assert generate()
    assert generate(at=now + timedelta(days=1))
    assert not generate(at=now + timedelta(days=1, seconds=1))
    assert len(calls) == 2
    assert len(history.read_text().splitlines()) == 2


def test_source_edit_invalidates_cache_without_waiting(generation):
    generate, calls, article, now, _, _ = generation
    assert generate()
    assert generate(articles=[replace(article, summary="A corrected finding")],
                    at=now + timedelta(minutes=1))
    assert len(calls) == 2


def test_feedback_invalidates_cache_without_waiting(generation, tmp_path):
    generate, calls, _, now, _, _ = generation
    assert generate()
    feedback = tmp_path / "feedback.jsonl"
    feedback.write_text(json.dumps({
        "feedback": "not_interested", "article_id": "another-story", "title": "Unhelpful",
    }) + "\n")
    assert generate(at=now + timedelta(minutes=1), feedback=feedback)
    assert len(calls) == 2


def test_source_hash_ignores_order_images_and_unverified_fetch_time(generation):
    _, _, article, now, _, _ = generation
    other = replace(article, id="a2", published_at_verified=False)
    later = replace(other, published_at=(now + timedelta(hours=1)).isoformat(),
                    image_url="https://example.test/image.png")
    assert highlights._input_hash([article, other]) == highlights._input_hash([later, article])


def test_candidate_ties_do_not_regenerate_when_fetch_order_changes(generation):
    generate, calls, article, now, _, _ = generation
    articles = [replace(article, id=key) for key in ("left", "right")]
    before = highlights._candidate_articles(articles, generated_at=now)
    after = highlights._candidate_articles(list(reversed(articles)), generated_at=now)
    assert [item.id for item in before] == [item.id for item in after]
    assert generate(articles=articles)
    assert not generate(articles=list(reversed(articles)))
    assert len(calls) == 1


def test_edit_to_previous_rotated_candidate_invalidates_cache(generation, monkeypatch):
    generate, calls, article, now, output, history = generation
    extra = replace(article, id="rotated", title="Another relevant story")
    history.write_text(json.dumps({"fields": {"データ・AI": ["a1"]}}) + "\n")

    def select(articles, *, previous_ids=None, **_kwargs):
        return list(articles) if previous_ids else [articles[0]]

    # The rotating pool can contain relevant evidence outside the default 105.
    monkeypatch.setattr(highlights, "_candidate_articles", select)
    assert generate(articles=[article, extra])
    assert "rotated" in json.loads(output.read_text())["candidate_ids"]
    assert not generate(articles=[article, extra], at=now + timedelta(minutes=1))
    assert generate(articles=[article, replace(extra, summary="Correction to selected evidence")],
                    at=now + timedelta(minutes=2))
    assert len(calls) == 2


def test_concurrent_generation_is_skipped(generation):
    generate, calls, _, _, output, _ = generation
    with output.with_suffix(".lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert not generate()
    assert calls == []
    assert generate()


def test_empty_candidates_never_invoke_model(generation):
    generate, calls, _, _, _, _ = generation
    assert not generate(articles=[])
    assert calls == []


def test_failure_keeps_last_result_and_does_not_mark_new_input_current(generation, monkeypatch):
    generate, calls, article, _, output, history = generation
    assert generate()
    saved, saved_history = output.read_bytes(), history.read_bytes()
    changed = replace(article, summary="New evidence")
    success = highlights.run_codex

    def fail(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "fake-codex")

    monkeypatch.setattr(highlights, "run_codex", fail)
    assert not generate(articles=[changed])
    assert output.read_bytes() == saved
    assert history.read_bytes() == saved_history
    monkeypatch.setattr(highlights, "run_codex", success)
    assert generate(articles=[changed])
    assert len(calls) == 2
