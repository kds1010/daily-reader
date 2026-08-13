from __future__ import annotations

import argparse
import functools
import json
import logging
import threading
from collections import Counter
from datetime import UTC, datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from time import sleep

from daily_reader.core import collect, load_config, load_keywords, write_output
from daily_reader.highlights import generate_highlights

LOGGER = logging.getLogger(__name__)
READ_LOG_LOCK = threading.Lock()
FEEDBACK_LOG_LOCK = threading.Lock()
READ_SURFACES = {
    "field_highlight",
    "official_digest",
    "gadget_digest",
    "tech_pick",
    "article_feed",
}


def append_read_event(log_path: Path, article: dict[str, object], surface: str) -> None:
    event = {
        "read_at": datetime.now(UTC).isoformat(),
        "article_id": article["id"],
        "title": article["title"],
        "source": article["source"],
        "category": article["category"],
        "surface": surface,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with READ_LOG_LOCK, log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, ensure_ascii=False) + "\n")


def summarize_read_events(log_path: Path) -> dict[str, object]:
    events = []
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        lines = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return {
        "total_reads": len(events),
        "unique_articles": len({event.get("article_id") for event in events}),
        "by_category": dict(Counter(event.get("category") for event in events)),
        "by_source": dict(Counter(event.get("source") for event in events).most_common(20)),
        "by_surface": dict(Counter(event.get("surface") for event in events)),
        "recent": events[-20:][::-1],
    }


def append_feedback_event(
    log_path: Path, article: dict[str, object], surface: str
) -> None:
    event = {
        "feedback_at": datetime.now(UTC).isoformat(),
        "feedback": "not_interested",
        "article_id": article["id"],
        "title": article["title"],
        "source": article["source"],
        "category": article["category"],
        "surface": surface,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with FEEDBACK_LOG_LOCK, log_path.open("a", encoding="utf-8") as log_file:
        log_file.write(json.dumps(event, ensure_ascii=False) + "\n")


def load_feedback_events(log_path: Path) -> list[dict[str, object]]:
    try:
        lines = log_path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    events = []
    for line in lines:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("feedback") == "not_interested" and isinstance(
            event.get("article_id"), str
        ):
            events.append(event)
    return events


def make_handler(
    site: Path, articles_path: Path, read_log_path: Path, feedback_log_path: Path
):
    class DailyReaderHandler(SimpleHTTPRequestHandler):
        def _send_json(self, status: int, payload: object) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/api/analytics":
                self._send_json(200, summarize_read_events(read_log_path))
                return
            if self.path == "/api/feedback":
                events = load_feedback_events(feedback_log_path)
                self._send_json(
                    200,
                    {"hidden_article_ids": list(dict.fromkeys(
                        event["article_id"] for event in events
                    ))},
                )
                return
            super().do_GET()

        def do_POST(self) -> None:  # noqa: N802
            if self.path not in {"/api/read", "/api/feedback"}:
                self._send_json(404, {"error": "not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 2048:
                    raise ValueError("invalid content length")
                request = json.loads(self.rfile.read(length))
                article_id = request["article_id"]
                surface = request["surface"]
                if not isinstance(article_id, str) or surface not in READ_SURFACES:
                    raise ValueError("invalid event")
                articles = json.loads(articles_path.read_text(encoding="utf-8"))["articles"]
                article = next(item for item in articles if item["id"] == article_id)
                if self.path == "/api/read":
                    append_read_event(read_log_path, article, surface)
                else:
                    append_feedback_event(feedback_log_path, article, surface)
            except (
                FileNotFoundError,
                StopIteration,
                KeyError,
                TypeError,
                ValueError,
                json.JSONDecodeError,
            ):
                self._send_json(400, {"error": "invalid event"})
                return
            self._send_json(202, {"recorded": True})

    return functools.partial(DailyReaderHandler, directory=site)


def update_articles(
    feeds_path: Path,
    keywords_path: Path,
    output_path: Path,
    feedback_log_path: Path = Path("data/feedback-events.jsonl"),
    selection_history_path: Path = Path("data/selection-history.jsonl"),
) -> None:
    now = datetime.now(UTC)
    settings, feeds = load_config(feeds_path)
    keywords = load_keywords(keywords_path)
    articles, errors = collect(feeds, keywords, settings, now)
    if not articles and errors:
        LOGGER.error("All feeds failed; keeping the previous data file")
        return
    write_output(output_path, articles, errors, now)
    LOGGER.info("Updated %s with %d articles", output_path, len(articles))
    generate_highlights(
        articles,
        output_path.parent / "highlights.json",
        Path("config/highlight-schema.json"),
        now,
        feedback_log_path,
        selection_history_path,
    )


def run_scheduler(
    feeds_path: Path,
    keywords_path: Path,
    output_path: Path,
    update_hours: set[int],
    feedback_log_path: Path,
    selection_history_path: Path,
) -> None:
    last_run: tuple[str, int] | None = None
    while True:
        local_now = datetime.now().astimezone()
        run_key = (local_now.date().isoformat(), local_now.hour)
        if local_now.hour in update_hours and local_now.minute < 5 and run_key != last_run:
            update_articles(
                feeds_path,
                keywords_path,
                output_path,
                feedback_log_path,
                selection_history_path,
            )
            last_run = run_key
        sleep(60)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Serve Daily Reader on localhost")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--site", type=Path, default=Path("site"))
    parser.add_argument("--feeds", type=Path, default=Path("config/feeds.toml"))
    parser.add_argument("--keywords", type=Path, default=Path("config/keywords.toml"))
    parser.add_argument("--update-hours", default="8,12,17,20")
    parser.add_argument("--read-log", type=Path, default=Path("data/read-events.jsonl"))
    parser.add_argument(
        "--feedback-log", type=Path, default=Path("data/feedback-events.jsonl")
    )
    parser.add_argument(
        "--selection-history",
        type=Path,
        default=Path("data/selection-history.jsonl"),
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    output_path = args.site / "data" / "articles.json"
    update_hours = {int(hour) for hour in args.update_hours.split(",")}
    if not update_hours <= set(range(24)):
        raise SystemExit("--update-hours must contain hours from 0 to 23")

    update_articles(
        args.feeds,
        args.keywords,
        output_path,
        args.feedback_log,
        args.selection_history,
    )
    scheduler = threading.Thread(
        target=run_scheduler,
        args=(
            args.feeds,
            args.keywords,
            output_path,
            update_hours,
            args.feedback_log,
            args.selection_history,
        ),
        daemon=True,
    )
    scheduler.start()

    handler = make_handler(args.site, output_path, args.read_log, args.feedback_log)
    server = ThreadingHTTPServer((args.host, args.port), handler)
    LOGGER.info("Serving %s at http://%s:%d", args.site, args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Stopping")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
