from __future__ import annotations

import calendar
import hashlib
import html
import json
import re
import tempfile
import tomllib
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import struct_time
from typing import Any

import feedparser

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "source",
}
TAG_PATTERN = re.compile(r"<[^>]+>")
SPACE_PATTERN = re.compile(r"\s+")
JAPANESE_DATE_PATTERN = re.compile(
    r"(?:(?P<year>20\d{2})年)?(?P<month>1[0-2]|0?[1-9])月(?P<day>3[01]|[12]\d|0?[1-9])日"
)


@dataclass(frozen=True)
class Feed:
    name: str
    url: str
    category: str
    kind: str = "feed"
    retention_days: int | None = None


@dataclass(frozen=True)
class Settings:
    retention_days: int = 30
    max_articles: int = 300
    request_timeout_seconds: int = 20
    summary_max_length: int = 280


@dataclass(frozen=True)
class Article:
    id: str
    title: str
    url: str
    source: str
    published_at: str
    summary: str
    category: str
    score: int
    image_url: str | None = None


def load_config(path: Path) -> tuple[Settings, list[Feed]]:
    with path.open("rb") as config_file:
        config = tomllib.load(config_file)

    settings = Settings(**config.get("settings", {}))
    feeds = [
        Feed(
            name=item["name"],
            url=item["url"],
            category=item["category"],
            kind=item.get("kind", "feed"),
            retention_days=item.get("retention_days"),
        )
        for item in config.get("feeds", [])
        if item.get("enabled", True)
    ]
    if not feeds:
        raise ValueError("At least one enabled feed is required")
    return settings, feeds


def load_keywords(path: Path) -> dict[str, int]:
    with path.open("rb") as config_file:
        config = tomllib.load(config_file)

    keywords: dict[str, int] = {}
    for group in ("positive", "negative"):
        for keyword, weight in config.get(group, {}).items():
            keywords[keyword.casefold()] = int(weight)
    return keywords


def canonicalize_url(url: str) -> str:
    parsed = urllib.parse.urlsplit(url.strip())
    query = [
        (key, value)
        for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_") and key.casefold() not in TRACKING_PARAMETERS
    ]
    return urllib.parse.urlunsplit(
        (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
            parsed.path,
            urllib.parse.urlencode(query),
            "",
        )
    )


def clean_text(value: str, max_length: int) -> str:
    text = html.unescape(TAG_PATTERN.sub(" ", value or ""))
    text = SPACE_PATTERN.sub(" ", text).strip()
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1].rstrip()}…"


def _published_at(entry: Any, fallback: datetime) -> datetime:
    parsed_time: struct_time | None = entry.get("published_parsed") or entry.get("updated_parsed")
    if parsed_time:
        return datetime.fromtimestamp(calendar.timegm(parsed_time), tz=UTC)
    return fallback


def _entry_image_url(entry: Any, base_url: str) -> str | None:
    candidates: list[str] = []
    for item in entry.get("media_content", []) or []:
        if item.get("url") and str(item.get("medium", "image")).casefold() == "image":
            candidates.append(str(item["url"]))
    for item in entry.get("media_thumbnail", []) or []:
        if item.get("url"):
            candidates.append(str(item["url"]))
    for item in entry.get("enclosures", []) or []:
        if item.get("href") and str(item.get("type", "")).casefold().startswith("image/"):
            candidates.append(str(item["href"]))
    raw_summary = str(entry.get("summary", entry.get("description", "")))
    image_match = re.search(r'<img[^>]+src=["\'](?P<url>[^"\']+)', raw_summary, re.I)
    if image_match:
        candidates.append(image_match.group("url"))
    for candidate in candidates:
        image_url = urllib.parse.urljoin(base_url, html.unescape(candidate.strip()))
        if urllib.parse.urlsplit(image_url).scheme in {"http", "https"}:
            return image_url
    return None


def calculate_score(title: str, summary: str, keywords: dict[str, int]) -> int:
    searchable = f"{title}\n{summary}".casefold()
    return sum(weight for keyword, weight in keywords.items() if keyword in searchable)


def is_recent_or_upcoming_store_opening(article: Article, now: datetime) -> bool:
    if article.category != "街の新店":
        return True
    searchable = f"{article.title} {article.summary}"
    dates: list[datetime] = []
    for match in JAPANESE_DATE_PATTERN.finditer(searchable):
        year = int(match.group("year") or now.year)
        try:
            dates.append(
                datetime(
                    year,
                    int(match.group("month")),
                    int(match.group("day")),
                    tzinfo=now.tzinfo or UTC,
                )
            )
        except ValueError:
            continue
    if not dates:
        return False
    cutoff = now - timedelta(days=60)
    return any(opening_date >= cutoff for opening_date in dates)


def parse_feed(
    content: bytes,
    feed: Feed,
    keywords: dict[str, int],
    settings: Settings,
    fetched_at: datetime,
) -> list[Article]:
    parsed = feedparser.parse(content)
    articles: list[Article] = []
    for entry in parsed.entries:
        raw_url = entry.get("link", "")
        title = clean_text(entry.get("title", ""), 200)
        if not raw_url or not title:
            continue
        url = canonicalize_url(raw_url)
        summary = clean_text(
            entry.get("summary", entry.get("description", "")), settings.summary_max_length
        )
        published_at = _published_at(entry, fetched_at)
        articles.append(
            Article(
                id=hashlib.sha256(url.encode()).hexdigest()[:20],
                title=title,
                url=url,
                source=feed.name,
                published_at=published_at.isoformat(),
                summary=summary,
                category=feed.category,
                score=calculate_score(title, summary, keywords),
                image_url=_entry_image_url(entry, url),
            )
        )
    return articles


def parse_snowflake_release_notes(
    content: bytes,
    feed: Feed,
    keywords: dict[str, int],
) -> list[Article]:
    page = content.decode("utf-8", errors="replace")
    pattern = re.compile(
        r"release-notes/(?P<year>\d{4})/other/"
        r"(?P<date>\d{4}-\d{2}-\d{2})-(?P<slug>[a-z0-9-]+)"
    )
    articles: dict[str, Article] = {}
    for match in pattern.finditer(page):
        path = match.group(0)
        url = f"https://docs.snowflake.com/en/{path}"
        title = f"Snowflake: {match.group('slug').replace('-', ' ')}"
        published_at = datetime.fromisoformat(match.group("date")).replace(tzinfo=UTC)
        article = Article(
            id=hashlib.sha256(url.encode()).hexdigest()[:20],
            title=title,
            url=url,
            source=feed.name,
            published_at=published_at.isoformat(),
            summary="Snowflake公式リリースノート",
            category=feed.category,
            score=calculate_score(title, "", keywords),
        )
        articles[article.id] = article
    return list(articles.values())


def parse_woven_news(
    content: bytes,
    feed: Feed,
    keywords: dict[str, int],
) -> list[Article]:
    page = content.decode("utf-8", errors="replace").replace(r'\"', '"')
    pattern = re.compile(
        r'},"title":"(?P<title>[^"\\]+)","subTitle":"(?P<section>[^"\\]+)",'
        r'"filterCategory":"[^"\\]+","text":"[^"\\]*","date":"(?P<date>\d{4}\.\d{2}\.\d{2})",'
        r'"cta":\{"href":"(?P<path>/en/news/[^"\\]+)"\}'
    )
    articles: dict[str, Article] = {}
    for match in pattern.finditer(page):
        url = canonicalize_url(f"https://woven.toyota{match.group('path')}")
        title = html.unescape(match.group("title"))
        published_at = datetime.strptime(match.group("date"), "%Y.%m.%d").replace(tzinfo=UTC)
        article = Article(
            id=hashlib.sha256(url.encode()).hexdigest()[:20],
            title=title,
            url=url,
            source=feed.name,
            published_at=published_at.isoformat(),
            summary=f"Woven by Toyota公式 {match.group('section')}",
            category=feed.category,
            score=calculate_score(title, match.group("section"), keywords),
        )
        articles[article.id] = article
    return list(articles.values())


def _event_date(value: str) -> datetime | None:
    match = re.search(r"(?P<year>20\d{2})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日", value)
    if match is None:
        return None
    return datetime(
        int(match.group("year")),
        int(match.group("month")),
        int(match.group("day")),
        tzinfo=UTC,
    )


def parse_yokohama_child_events(
    content: bytes,
    feed: Feed,
    keywords: dict[str, int],
) -> list[Article]:
    page = content.decode("utf-8", errors="replace")
    item_pattern = re.compile(r"<li>(?P<body>.*?)</li>", re.DOTALL)
    link_pattern = re.compile(
        r'<a class="event" href="(?P<url>[^"]+)">(?P<title>.*?)</a>', re.DOTALL
    )
    articles = []
    for item_match in item_pattern.finditer(page):
        body = item_match.group("body")
        link_match = link_pattern.search(body)
        if link_match is None:
            continue
        date = _event_date(clean_text(body, 1000))
        if date is None:
            continue
        title = clean_text(link_match.group("title"), 200)
        description_match = re.search(
            r'<p class="name_sub">(?P<description>.*?)</p>', body, re.DOTALL
        )
        place_match = re.search(r'<p class="place">(?P<place>.*?)</p>', body, re.DOTALL)
        description = clean_text(
            description_match.group("description") if description_match else "", 180
        )
        place = clean_text(place_match.group("place") if place_match else "", 100)
        summary = "・".join(value for value in (description, place) if value)
        url = canonicalize_url(link_match.group("url"))
        articles.append(
            Article(
                id=hashlib.sha256(url.encode()).hexdigest()[:20],
                title=title,
                url=url,
                source=feed.name,
                published_at=date.isoformat(),
                summary=summary,
                category=feed.category,
                score=calculate_score(title, summary, keywords),
            )
        )
    return articles


def parse_yokohama_tourism_events(
    content: bytes,
    feed: Feed,
    keywords: dict[str, int],
) -> list[Article]:
    page = content.decode("utf-8", errors="replace")
    pattern = re.compile(
        r'<a class="event-list-box" href="(?P<path>[^"]+)">(?P<body>.*?)</a>',
        re.DOTALL,
    )
    articles = []
    for match in pattern.finditer(page):
        body = match.group("body")
        title_match = re.search(
            r'<h3 class="event-list-box-title">(?P<title>.*?)</h3>', body, re.DOTALL
        )
        date_match = re.search(r"開催日程</span>(?P<date>.*?)</li>", body, re.DOTALL)
        if title_match is None or date_match is None:
            continue
        date = _event_date(clean_text(date_match.group("date"), 200))
        if date is None:
            continue
        title = clean_text(title_match.group("title"), 200)
        tags = "、".join(re.findall(r"<span>(.*?)</span>", body))
        details = clean_text(body, 400)
        url = canonicalize_url(
            urllib.parse.urljoin("https://www.welcome.city.yokohama.jp", match.group("path"))
        )
        articles.append(
            Article(
                id=hashlib.sha256(url.encode()).hexdigest()[:20],
                title=title,
                url=url,
                source=feed.name,
                published_at=date.isoformat(),
                summary=clean_text(f"{tags}・{details}", 280),
                category=feed.category,
                score=calculate_score(title, f"{tags} {details}", keywords),
            )
        )
    return articles


def parse_source(
    content: bytes,
    feed: Feed,
    keywords: dict[str, int],
    settings: Settings,
    fetched_at: datetime,
) -> list[Article]:
    if feed.kind == "snowflake_release_notes":
        return parse_snowflake_release_notes(content, feed, keywords)
    if feed.kind == "woven_news":
        return parse_woven_news(content, feed, keywords)
    if feed.kind == "yokohama_child_events":
        return parse_yokohama_child_events(content, feed, keywords)
    if feed.kind == "yokohama_tourism_events":
        return parse_yokohama_tourism_events(content, feed, keywords)
    if feed.kind != "feed":
        raise ValueError(f"Unsupported feed kind: {feed.kind}")
    return parse_feed(content, feed, keywords, settings, fetched_at)


def fetch_feed(feed: Feed, timeout: int, fetched_at: datetime | None = None) -> bytes:
    url = feed.url
    if fetched_at is not None:
        url = url.format(date=fetched_at.date().isoformat())
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "daily-reader/0.1 (+https://github.com/kds1010/daily-reader)"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def collect(
    feeds: list[Feed],
    keywords: dict[str, int],
    settings: Settings,
    now: datetime,
) -> tuple[list[Article], list[dict[str, str]]]:
    articles: dict[str, Article] = {}
    errors: list[dict[str, str]] = []
    for feed in feeds:
        try:
            content = fetch_feed(feed, settings.request_timeout_seconds, now)
            parsed_articles = parse_source(content, feed, keywords, settings, now)
            retention_days = feed.retention_days or settings.retention_days
            cutoff = now - timedelta(days=retention_days)
            for article in parsed_articles:
                published_at = datetime.fromisoformat(article.published_at)
                if published_at >= cutoff and is_recent_or_upcoming_store_opening(article, now):
                    existing = articles.get(article.id)
                    if existing is None or article.published_at > existing.published_at:
                        articles[article.id] = article
        except (OSError, ValueError, urllib.error.URLError) as error:
            errors.append({"source": feed.name, "message": str(error)})

    ranked = sorted(
        articles.values(),
        key=lambda article: (article.score, article.published_at),
        reverse=True,
    )
    return ranked[: settings.max_articles], errors


def write_output(
    output_path: Path,
    articles: list[Article],
    errors: list[dict[str, str]],
    generated_at: datetime,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "generated_at": generated_at.isoformat(),
        "article_count": len(articles),
        "errors": errors,
        "articles": [asdict(article) for article in articles],
    }
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=output_path.parent, delete=False
    ) as temporary_file:
        json.dump(payload, temporary_file, ensure_ascii=False, indent=2)
        temporary_file.write("\n")
        temporary_path = Path(temporary_file.name)
    temporary_path.replace(output_path)
