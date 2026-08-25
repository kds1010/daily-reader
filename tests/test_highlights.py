from datetime import datetime
from pathlib import Path

from daily_reader.core import Article
from daily_reader.highlights import (
    _candidate_articles,
    _candidate_rank,
    _diverse_articles,
    _feedback_examples,
    _has_fresh_alternative,
    _is_cli_productivity_article,
    _is_local_store_opening,
    _is_non_promotional_article,
    _is_parenting_walking_distance,
    _is_sakuragicho_area_article,
    _normalized_title,
    _sakuragicho_area_priority,
    _selection_streak,
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


def test_cli_productivity_excludes_transport_terminal_news() -> None:
    transport = Article(
        **{
            **_event("Automated container port operations").__dict__,
            "title": "仁川港初の完全自動化ターミナル、2028年稼働へ",
            "source": "CLI・ターミナルツール発見",
        }
    )
    codex_cli = Article(
        **{
            **transport.__dict__,
            "id": "codex-cli",
            "title": "Codex Security CLIが公開",
        }
    )

    assert not _is_cli_productivity_article(transport)
    assert _is_cli_productivity_article(codex_cli)


def test_non_promotional_article_excludes_sale_but_keeps_product_news() -> None:
    sale = Article(
        **{
            **_event("display").__dict__,
            "title": "4KモニターがAmazonセールで20%OFF",
        }
    )
    product_news = Article(
        **{
            **sale.__dict__,
            "id": "product-news",
            "title": "目の疲れを抑える新型4Kモニターを発表",
        }
    )

    assert not _is_non_promotional_article(sale)
    assert _is_non_promotional_article(product_news)


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


def test_candidate_rank_boosts_fresh_and_penalizes_previous() -> None:
    generated_at = datetime.fromisoformat("2026-08-13T00:00:00+00:00")
    fresh = Article(**{**_event("fresh").__dict__, "published_at": "2026-08-12T12:00:00+00:00"})
    old = Article(**{**_event("old").__dict__, "published_at": "2026-08-01T12:00:00+00:00"})

    assert _candidate_rank(fresh, set(), generated_at)[0] == fresh.score + 12
    assert _candidate_rank(old, {old.id}, generated_at)[0] == old.score - 10


def test_candidate_rank_decays_as_article_ages() -> None:
    generated_at = datetime.fromisoformat("2026-08-13T00:00:00+00:00")

    def article_at(article_id: str, published_at: str) -> Article:
        return Article(
            **{**_event(article_id).__dict__, "id": article_id, "published_at": published_at}
        )

    one_day = article_at("one-day", "2026-08-12T01:00:00+00:00")
    three_days = article_at("three-days", "2026-08-10T01:00:00+00:00")
    seven_days = article_at("seven-days", "2026-08-06T01:00:00+00:00")
    stale = article_at("stale", "2026-07-20T00:00:00+00:00")

    assert [_candidate_rank(article, set(), generated_at)[0] for article in (
        one_day,
        three_days,
        seven_days,
        stale,
    )] == [17, 12, 8, -1]


def test_candidate_rank_prefers_verified_primary_source() -> None:
    generated_at = datetime.fromisoformat("2026-08-13T00:00:00+00:00")
    base = {**_event("source-quality").__dict__, "published_at": "2026-08-12T12:00:00+00:00"}
    primary = Article(**{**base, "id": "primary", "source_priority": 8})
    undated = Article(
        **{**base, "id": "undated", "source_priority": 8, "published_at_verified": False}
    )

    assert _candidate_rank(primary, set(), generated_at)[0] == 25
    assert _candidate_rank(undated, set(), generated_at)[0] == 17


def test_candidates_reserve_fresh_data_ai_before_other_pools() -> None:
    generated_at = datetime.fromisoformat("2026-08-13T00:00:00+00:00")
    fresh_data = Article(
        id="fresh-data",
        title="Fresh data governance finding",
        url="https://example.com/fresh-data",
        source="Data source",
        published_at="2026-08-12T18:00:00+00:00",
        summary="metadata lineage",
        category="データマネジメント",
        score=3,
    )
    books = [
        Article(
            **{
                **_event(f"book-{index}").__dict__,
                "id": f"book-{index}",
                "category": "データ関連書籍",
                "published_at": f"2026-09-{index + 1:02d}T00:00:00+00:00",
            }
        )
        for index in range(8)
    ]

    candidates = _candidate_articles(
        [*books, fresh_data], limit=3, generated_at=generated_at
    )

    assert fresh_data in candidates


def test_diverse_articles_deduplicates_titles_and_limits_each_source() -> None:
    articles = [
        Article(
            **{
                **_event(f"article-{index}").__dict__,
                "id": f"article-{index}",
                "title": "Same story" if index < 2 else f"Story {index}",
                "source": "Repeated source" if index < 5 else "Another source",
            }
        )
        for index in range(6)
    ]

    selected = _diverse_articles(articles, 8)

    assert [article.id for article in selected] == [
        "article-0",
        "article-2",
        "article-3",
        "article-5",
    ]
    assert _normalized_title("ＡＩ Agent: 実践！") == _normalized_title("AI agent 実践")


def test_candidates_do_not_let_duplicate_desk_stories_fill_field_quota() -> None:
    generated_at = datetime.fromisoformat("2026-08-26T00:00:00+00:00")
    duplicates = [
        Article(
            **{
                **_event(f"desk-{index}").__dict__,
                "id": f"desk-{index}",
                "title": "The same desk tour",
                "source": "デスクツアー・作業環境",
                "category": "業務改善・QOL",
                "published_at": f"2026-08-{25 - index:02d}T00:00:00+00:00",
                "score": 20,
            }
        )
        for index in range(6)
    ]
    useful = Article(
        **{
            **_event("useful").__dict__,
            "id": "useful",
            "title": "A useful new display light",
            "source": "PC source",
            "category": "業務改善・QOL",
            "published_at": "2026-08-25T12:00:00+00:00",
        }
    )

    candidates = _candidate_articles([*duplicates, useful], generated_at=generated_at)

    assert useful in candidates
    assert len([article for article in candidates if article.title == "The same desk tour"]) == 1


def test_candidates_deduplicate_titles_across_balanced_and_newest_pools() -> None:
    generated_at = datetime.fromisoformat("2026-08-26T00:00:00+00:00")
    duplicates = [
        Article(
            **{
                **_event(f"data-{index}").__dict__,
                "id": f"data-{index}",
                "title": "Mirrored data platform announcement",
                "source": f"Source {index}",
                "category": "データ基盤",
                "published_at": f"2026-08-25T{index:02d}:00:00+00:00",
            }
        )
        for index in range(4)
    ]

    candidates = _candidate_articles(duplicates, generated_at=generated_at)

    assert len(candidates) == 1


def test_stale_article_has_fresh_alternative_only_in_time_sensitive_fields() -> None:
    generated_at = datetime.fromisoformat("2026-08-26T00:00:00+00:00")
    stale = Article(
        **{
            **_event("stale").__dict__,
            "id": "stale",
            "title": "Agent context engineering",
            "summary": "Context evaluation details",
            "category": "生成AI活用",
            "published_at": "2026-08-01T00:00:00+00:00",
        }
    )
    fresh = Article(
        **{
            **stale.__dict__,
            "id": "fresh",
            "published_at": "2026-08-25T00:00:00+00:00",
        }
    )

    assert _has_fresh_alternative(stale, "生成AI活用・テクニック", [stale, fresh], generated_at)
    unverified = Article(**{**fresh.__dict__, "id": "unverified", "published_at_verified": False})
    assert not _has_fresh_alternative(
        stale,
        "生成AI活用・テクニック",
        [stale, unverified],
        generated_at,
    )
    assert not _has_fresh_alternative(
        stale,
        "データマネジメント・エンジニアリング書籍",
        [stale, fresh],
        generated_at,
    )


def test_selection_streak_counts_only_consecutive_runs() -> None:
    runs = [
        {"データ・AI": ["article-1"]},
        {"データ・AI": ["article-1"]},
    ]

    assert _selection_streak("article-1", "データ・AI", runs) == 2
    assert _selection_streak("article-2", "データ・AI", runs) == 0
