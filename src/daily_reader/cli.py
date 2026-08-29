from __future__ import annotations

import argparse
from datetime import UTC, datetime
from pathlib import Path

from daily_reader.core import collect, load_config, load_keywords, write_output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect feeds and build the Daymeld data file"
    )
    parser.add_argument("--feeds", type=Path, default=Path("config/feeds.toml"))
    parser.add_argument("--keywords", type=Path, default=Path("config/keywords.toml"))
    parser.add_argument("--output", type=Path, default=Path("site/data/articles.json"))
    return parser


def main() -> None:
    args = build_parser().parse_args()
    now = datetime.now(UTC)
    settings, feeds = load_config(args.feeds)
    keywords = load_keywords(args.keywords)
    articles, errors = collect(feeds, keywords, settings, now)
    if not articles and errors:
        details = "; ".join(f"{error['source']}: {error['message']}" for error in errors)
        raise SystemExit(f"All feeds failed: {details}")
    write_output(args.output, articles, errors, now)
    print(f"Generated {args.output} with {len(articles)} articles ({len(errors)} feed errors)")


if __name__ == "__main__":
    main()
