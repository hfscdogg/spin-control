"""Article ingestion: RSS feeds and optional NewsAPI."""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape

import feedparser
import requests
from bs4 import BeautifulSoup

import db
from config_loader import env, load_config

log = logging.getLogger("spin_control.ingest")


@dataclass
class FetchedArticle:
    url: str
    source: str
    category: str | None
    title: str
    raw_summary: str | None
    published_at: datetime | None


def _strip_html(value: str | None) -> str | None:
    if not value:
        return None
    text = BeautifulSoup(unescape(value), "html.parser").get_text(" ", strip=True)
    return text[:4000] if text else None


def _coerce_published(entry) -> datetime | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    if not parsed:
        return None
    try:
        return datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def fetch_rss(config: dict) -> list[FetchedArticle]:
    cutoff = datetime.now(timezone.utc) - timedelta(
        hours=config["ingestion"]["max_age_hours"]
    )
    per_source_limit = config["ingestion"]["per_source_limit"]
    out: list[FetchedArticle] = []

    for source in config["rss_sources"]:
        name, url, category = source["name"], source["url"], source.get("category")
        try:
            parsed = feedparser.parse(url)
        except Exception as exc:
            log.warning("RSS fetch failed for %s: %s", name, exc)
            continue

        if parsed.bozo and not parsed.entries:
            log.warning("RSS feed parse error for %s: %s", name, parsed.bozo_exception)
            continue

        kept = 0
        for entry in parsed.entries:
            if kept >= per_source_limit:
                break
            link = entry.get("link")
            title = entry.get("title")
            if not link or not title:
                continue
            published = _coerce_published(entry)
            if published and published < cutoff:
                continue
            summary = _strip_html(entry.get("summary") or entry.get("description"))
            out.append(
                FetchedArticle(
                    url=link.strip(),
                    source=name,
                    category=category,
                    title=title.strip(),
                    raw_summary=summary,
                    published_at=published,
                )
            )
            kept += 1
        log.info("RSS %s: %d kept", name, kept)
    return out


def fetch_newsapi(config: dict) -> list[FetchedArticle]:
    api_key = env("NEWSAPI_KEY")
    if not api_key:
        log.info("NEWSAPI_KEY not set, skipping NewsAPI ingestion")
        return []
    if not config.get("newsapi", {}).get("enabled", False):
        return []

    queries = config["newsapi"]["queries"]
    language = config["newsapi"].get("language", "en")
    from_dt = datetime.now(timezone.utc) - timedelta(
        hours=config["newsapi"].get("from_hours", 36)
    )
    from_iso = from_dt.strftime("%Y-%m-%dT%H:%M:%S")

    out: list[FetchedArticle] = []
    for query in queries:
        try:
            resp = requests.get(
                "https://newsapi.org/v2/everything",
                params={
                    "q": query,
                    "language": language,
                    "from": from_iso,
                    "sortBy": "publishedAt",
                    "pageSize": 20,
                },
                headers={"X-Api-Key": api_key},
                timeout=20,
            )
        except requests.RequestException as exc:
            log.warning("NewsAPI query %r failed: %s", query, exc)
            continue
        if resp.status_code != 200:
            log.warning(
                "NewsAPI query %r returned %s: %s", query, resp.status_code, resp.text[:200]
            )
            continue
        for art in resp.json().get("articles", []):
            url = art.get("url")
            title = art.get("title")
            if not url or not title:
                continue
            published_at = None
            if art.get("publishedAt"):
                try:
                    published_at = datetime.fromisoformat(
                        art["publishedAt"].replace("Z", "+00:00")
                    )
                except ValueError:
                    published_at = None
            description = art.get("description") or art.get("content") or ""
            out.append(
                FetchedArticle(
                    url=url.strip(),
                    source=f"NewsAPI: {art.get('source', {}).get('name', 'unknown')}",
                    category=f"newsapi:{query}",
                    title=title.strip(),
                    raw_summary=_strip_html(description),
                    published_at=published_at,
                )
            )
        log.info("NewsAPI %r returned %d articles", query, len(out))
    return out


def ingest() -> int:
    """Fetch all sources, dedupe, persist new articles. Returns count of new rows."""
    config = load_config()
    db.init_db()

    articles = fetch_rss(config) + fetch_newsapi(config)
    if not articles:
        log.info("No articles fetched")
        return 0

    inserted = 0
    with db.connect() as conn:
        for art in articles:
            new_id = db.upsert_article(
                conn,
                url=art.url,
                source=art.source,
                category=art.category,
                title=art.title,
                raw_summary=art.raw_summary,
                published_at=art.published_at,
            )
            if new_id is not None:
                inserted += 1
    log.info("Ingestion complete: %d new articles persisted", inserted)
    return inserted


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    ingest()
