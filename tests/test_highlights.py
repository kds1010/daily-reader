from pathlib import Path

from daily_reader.core import Article
from daily_reader.highlights import (
    _feedback_examples,
    _is_cli_productivity_article,
    _is_local_store_opening,
    _is_parenting_walking_distance,
    _is_sakuragicho_area_article,
    _sakuragicho_area_priority,
)


def _event(summary: str) -> Article:
    return Article(
        id=summary,
        title="親子イベント",
        url="https://example.com/event",
        source="横浜市イベント",
        published_at="2026-08-20T00:00:00+00:00",
        summary=summary,
        category="子育て",
        score=5,
    )


def test_sakuragicho_area_priority_prefers_nearby_locations() -> None:
    assert _sakuragicho_area_priority(_event("会場: みなとみらい")) == 0
    assert _sakuragicho_area_priority(_event("会場: 中区竹之丸")) == 1
    assert _sakuragicho_area_priority(_event("会場: 戸塚区")) == 2
    assert not _is_sakuragicho_area_article(_event("会場: 戸塚区"))
    assert _is_parenting_walking_distance(_event("会場: 野毛地区センター"))
    assert not _is_parenting_walking_distance(_event("会場: 竹之丸地区センター (中区)"))


def test_local_store_opening_requires_walkable_area_and_opening_news() -> None:
    assert _is_local_store_opening(_event("野毛にカフェが新規オープン"))
    assert not _is_local_store_opening(_event("野毛のカフェが閉店"))
    assert not _is_local_store_opening(_event("関内にカフェが新規オープン"))


def test_cli_productivity_excludes_nightly_releases() -> None:
    article = _event("Yazi update")
    article = Article(**{**article.__dict__, "title": "Nightly Build", "source": "Yazi Releases"})
    assert not _is_cli_productivity_article(article)


def test_feedback_examples_ignore_invalid_and_keep_recent_entries(tmp_path: Path) -> None:
    feedback_path = tmp_path / "feedback.jsonl"
    feedback_path.write_text(
        '{"feedback":"not_interested","article_id":"a1","title":"Noisy",'
        '"source":"Example","category":"AI"}\ninvalid\n',
        encoding="utf-8",
    )

    assert _feedback_examples(feedback_path) == [
        {
            "article_id": "a1",
            "title": "Noisy",
            "source": "Example",
            "category": "AI",
        }
    ]
    assert _feedback_examples(Path("missing.jsonl")) == []
