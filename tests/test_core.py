from datetime import UTC, datetime
from pathlib import Path

from daily_reader.core import (
    Article,
    Feed,
    Settings,
    calculate_score,
    canonicalize_url,
    clean_text,
    is_recent_or_upcoming_store_opening,
    load_keywords,
    parse_feed,
    parse_snowflake_release_notes,
    parse_woven_news,
    parse_yokohama_child_events,
    parse_yokohama_tourism_events,
    write_output,
)

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT_ROOT = Path(__file__).parent.parent


def test_project_keyword_config_is_valid() -> None:
    keywords = load_keywords(PROJECT_ROOT / "config" / "keywords.toml")

    assert keywords["生成ai"] == 4
    assert keywords["広告"] == -4


def test_canonicalize_url_removes_tracking_parameters_and_fragment() -> None:
    url = "HTTPS://Example.COM/story?utm_source=rss&id=7&fbclid=x#section"

    assert canonicalize_url(url) == "https://example.com/story?id=7"


def test_clean_text_strips_markup_and_truncates() -> None:
    assert clean_text("<p>Hello&nbsp; world</p>", 20) == "Hello world"
    assert clean_text("abcdefghij", 6) == "abcde…"


def test_calculate_score_counts_each_matching_keyword_once() -> None:
    keywords = {"python": 4, "ai": 3, "広告": -4}

    assert calculate_score("Python Python", "AIのニュース", keywords) == 7


def test_parse_feed_normalizes_and_scores_articles() -> None:
    content = (FIXTURES / "sample.xml").read_bytes()
    feed = Feed("Example", "https://example.com/feed", "開発")
    settings = Settings()
    now = datetime(2026, 8, 12, 2, tzinfo=UTC)

    articles = parse_feed(
        content,
        feed,
        {"python": 4, "ai": 4, "github": 3, "広告": -4, "キャンペーン": -3},
        settings,
        now,
    )

    assert len(articles) == 2
    assert articles[0].url == "https://example.com/article?id=42"
    assert articles[0].summary == "GitHubで公開された新機能です。"
    assert articles[0].score == 11
    assert articles[0].image_url == "https://images.example.com/python-ai.jpg"
    assert articles[1].score == -7


def test_parse_snowflake_release_notes_extracts_unique_updates() -> None:
    content = b"""
      release-notes/2026/other/2026-08-10-iceberg-rest-catalog-ga
      release-notes/2026/other/2026-08-10-iceberg-rest-catalog-ga
    """
    feed = Feed(
        "Snowflake Release Notes",
        "https://docs.snowflake.com/en/release-notes/all-release-notes",
        "データ基盤",
        "snowflake_release_notes",
    )

    articles = parse_snowflake_release_notes(content, feed, {"iceberg": 8})

    assert len(articles) == 1
    assert articles[0].published_at == "2026-08-10T00:00:00+00:00"
    assert articles[0].score == 8
    assert articles[0].url.endswith("2026-08-10-iceberg-rest-catalog-ga")


def test_parse_woven_news_extracts_cards() -> None:
    content = (
        rb'\"title\":\"AI Data Platform\",\"description\":\"image\"},'
        rb'\"title\":\"Data Fabric for Mobility\",'
        rb'\"subTitle\":\"Tech Insights\",'
        rb'\"filterCategory\":\"Tech Insights\",'
        rb'\"text\":\"Data Fabric for Mobility\",\"date\":\"2026.04.22\",'
        rb'\"cta\":{\"href\":\"/en/news/data-fabric\"}'
    )
    feed = Feed(
        "Woven by Toyota",
        "https://woven.toyota/en/news/",
        "ML・データ事例",
        "woven_news",
        730,
    )

    articles = parse_woven_news(content, feed, {"data fabric": 8, "mobility": 5})

    assert len(articles) == 1
    assert articles[0].title == "Data Fabric for Mobility"
    assert articles[0].published_at == "2026-04-22T00:00:00+00:00"
    assert articles[0].score == 13


def test_parse_yokohama_child_events_extracts_date_and_place() -> None:
    content = '''<li><p class="text"><a class="event" href="https://example.com/e/1">\
夏まつり</a></p><p class="name_sub">親子で遊べます</p><p class="date">\
開催日: 2026年8月15日(土)</p><p class="place">場所: 市役所</p></li>'''.encode()
    feed = Feed("横浜市こどもイベント", "https://example.com", "子育て")

    articles = parse_yokohama_child_events(content, feed, {"夏まつり": 9, "親子": 6})

    assert len(articles) == 1
    assert articles[0].published_at == "2026-08-15T00:00:00+00:00"
    assert articles[0].score == 15
    assert "市役所" in articles[0].summary


def test_parse_yokohama_tourism_events_extracts_event_card() -> None:
    content = '''<a class="event-list-box" href="/eventinfo/ev_detail.php?bid=1">\
<span>こども向け</span><h3 class="event-list-box-title">花火大会</h3>\
<li><span class="event-disc-category">開催日程</span>2026年8月20日(木)</li></a>'''.encode()
    feed = Feed("横浜市公式観光イベント", "https://example.com", "横浜イベント")

    articles = parse_yokohama_tourism_events(content, feed, {"花火": 6, "こども向け": 6})

    assert len(articles) == 1
    assert articles[0].published_at == "2026-08-20T00:00:00+00:00"
    assert articles[0].score == 12


def test_store_opening_rejects_stale_date_even_when_feed_date_is_recent() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    article = Article(
        id="store",
        title="すみれ 横浜店が2月13日に野毛町で復活オープン",
        url="https://example.com/store",
        source="桜木町・野毛の新店",
        published_at=now.isoformat(),
        summary="",
        category="街の新店",
        score=1,
    )

    assert not is_recent_or_upcoming_store_opening(article, now)


def test_store_opening_keeps_recent_or_upcoming_date() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    article = Article(
        id="store",
        title="野毛の新店が9月1日にオープン",
        url="https://example.com/store",
        source="桜木町・野毛の新店",
        published_at=now.isoformat(),
        summary="",
        category="街の新店",
        score=1,
    )

    assert is_recent_or_upcoming_store_opening(article, now)


def test_store_opening_rejects_article_without_confirmable_opening_date() -> None:
    now = datetime(2026, 8, 12, tzinfo=UTC)
    article = Article(
        id="store",
        title="野毛に新しいカフェがオープン",
        url="https://example.com/store",
        source="桜木町・野毛の新店",
        published_at=now.isoformat(),
        summary="",
        category="街の新店",
        score=1,
    )

    assert not is_recent_or_upcoming_store_opening(article, now)


def test_write_output_writes_utf8_json(tmp_path: Path) -> None:
    output = tmp_path / "data" / "articles.json"
    now = datetime(2026, 8, 12, 2, tzinfo=UTC)

    write_output(output, [], [], now)

    text = output.read_text(encoding="utf-8")
    assert '"generated_at": "2026-08-12T02:00:00+00:00"' in text
    assert '"article_count": 0' in text
