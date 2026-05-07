"""SQLite helpers for Spin Control."""

from __future__ import annotations

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator

DEFAULT_DB_PATH = os.environ.get("SPIN_CONTROL_DB", "spin_control.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS articles (
  id INTEGER PRIMARY KEY,
  url TEXT UNIQUE NOT NULL,
  source TEXT,
  category TEXT,
  title TEXT,
  raw_summary TEXT,
  published_at TIMESTAMP,
  fetched_at TIMESTAMP NOT NULL,
  summary TEXT,
  why_it_matters TEXT,
  scores_json TEXT,
  total_score INTEGER,
  status TEXT NOT NULL DEFAULT 'fetched'
);

CREATE INDEX IF NOT EXISTS idx_articles_status ON articles(status);
CREATE INDEX IF NOT EXISTS idx_articles_total_score ON articles(total_score);

CREATE TABLE IF NOT EXISTS drafts (
  id INTEGER PRIMARY KEY,
  article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
  variant TEXT NOT NULL,
  content TEXT NOT NULL,
  cta_url TEXT,
  created_at TIMESTAMP NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_drafts_article ON drafts(article_id);

CREATE TABLE IF NOT EXISTS digest_runs (
  id INTEGER PRIMARY KEY,
  sent_at TIMESTAMP NOT NULL,
  article_count INTEGER NOT NULL,
  notes TEXT
);
"""


@contextmanager
def connect(db_path: str = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(db_path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db(db_path: str = DEFAULT_DB_PATH) -> None:
    with connect(db_path) as conn:
        conn.executescript(SCHEMA)


def upsert_article(
    conn: sqlite3.Connection,
    *,
    url: str,
    source: str,
    category: str | None,
    title: str,
    raw_summary: str | None,
    published_at: datetime | None,
) -> int | None:
    """Insert article if URL is new. Returns the article id or None if duplicate."""
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO articles
            (url, source, category, title, raw_summary, published_at, fetched_at, status)
        VALUES (?, ?, ?, ?, ?, ?, ?, 'fetched')
        """,
        (url, source, category, title, raw_summary, published_at, datetime.utcnow()),
    )
    if cur.rowcount == 0:
        return None
    return cur.lastrowid


def fetch_unscored(conn: sqlite3.Connection, limit: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT * FROM articles
            WHERE status = 'fetched'
            ORDER BY fetched_at ASC
            LIMIT ?
            """,
            (limit,),
        )
    )


def save_score(
    conn: sqlite3.Connection,
    *,
    article_id: int,
    scores: dict,
    summary: str,
    why_it_matters: str,
    total_score: int,
) -> None:
    conn.execute(
        """
        UPDATE articles
        SET scores_json = ?, summary = ?, why_it_matters = ?, total_score = ?, status = 'scored'
        WHERE id = ?
        """,
        (json.dumps(scores), summary, why_it_matters, total_score, article_id),
    )


def fetch_qualifying_for_drafting(
    conn: sqlite3.Connection, threshold: int, limit: int
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT * FROM articles
            WHERE status = 'scored'
              AND total_score >= ?
            ORDER BY total_score DESC
            LIMIT ?
            """,
            (threshold, limit),
        )
    )


def save_drafts(
    conn: sqlite3.Connection,
    article_id: int,
    drafts: list[tuple[str, str, str | None]],
) -> None:
    """drafts is a list of (variant, content, cta_url) tuples."""
    now = datetime.utcnow()
    conn.executemany(
        """
        INSERT INTO drafts (article_id, variant, content, cta_url, created_at)
        VALUES (?, ?, ?, ?, ?)
        """,
        [(article_id, v, c, u, now) for v, c, u in drafts],
    )
    conn.execute(
        "UPDATE articles SET status = 'drafted' WHERE id = ?", (article_id,)
    )


def fetch_drafted_for_digest(
    conn: sqlite3.Connection, limit: int
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT * FROM articles
            WHERE status = 'drafted'
            ORDER BY total_score DESC
            LIMIT ?
            """,
            (limit,),
        )
    )


def fetch_drafts_for_article(
    conn: sqlite3.Connection, article_id: int
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT * FROM drafts WHERE article_id = ? ORDER BY id ASC",
            (article_id,),
        )
    )


def mark_sent(conn: sqlite3.Connection, article_ids: list[int]) -> None:
    if not article_ids:
        return
    placeholders = ",".join("?" * len(article_ids))
    conn.execute(
        f"UPDATE articles SET status = 'sent' WHERE id IN ({placeholders})",
        article_ids,
    )


def record_digest_run(
    conn: sqlite3.Connection, article_count: int, notes: str = ""
) -> None:
    conn.execute(
        "INSERT INTO digest_runs (sent_at, article_count, notes) VALUES (?, ?, ?)",
        (datetime.utcnow(), article_count, notes),
    )
