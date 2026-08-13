from pathlib import Path

import pytest

from daily_reader.local_server import (
    append_feedback_event,
    append_read_event,
    build_parser,
    load_feedback_events,
    summarize_read_events,
)


def test_local_server_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["daily-reader-local"])

    args = build_parser().parse_args()

    assert args.host == "127.0.0.1"
    assert args.port == 8787
    assert args.site == Path("site")
    assert args.update_hours == "8,12,17,20"
    assert args.read_log == Path("data/read-events.jsonl")
    assert args.feedback_log == Path("data/feedback-events.jsonl")
    assert args.selection_history == Path("data/selection-history.jsonl")


def test_read_events_are_appended_and_summarized(tmp_path: Path) -> None:
    log_path = tmp_path / "read-events.jsonl"
    article = {
        "id": "article-1",
        "title": "Data Governance",
        "source": "Example",
        "category": "データマネジメント",
    }

    append_read_event(log_path, article, "field_highlight")
    append_read_event(log_path, article, "article_feed")

    summary = summarize_read_events(log_path)
    assert summary["total_reads"] == 2
    assert summary["unique_articles"] == 1
    assert summary["by_category"] == {"データマネジメント": 2}
    assert summary["by_surface"] == {"field_highlight": 1, "article_feed": 1}


def test_feedback_events_are_appended_and_loaded(tmp_path: Path) -> None:
    log_path = tmp_path / "feedback-events.jsonl"
    article = {
        "id": "article-1",
        "title": "Generic AI News",
        "source": "Example",
        "category": "テクノロジー",
    }

    append_feedback_event(log_path, article, "field_highlight")
    log_path.write_text(
        log_path.read_text(encoding="utf-8") + "invalid json\n",
        encoding="utf-8",
    )

    events = load_feedback_events(log_path)
    assert len(events) == 1
    assert events[0]["article_id"] == "article-1"
    assert events[0]["feedback"] == "not_interested"
