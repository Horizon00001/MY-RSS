"""Database connection and models for MY-RSS using SQLite."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

from .article_identity import normalize_article_link
from .config import settings


class Database:
    """SQLite database connection."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or str(settings.project_root / "myrss.db")
        self._ensure_db()

    def _ensure_db(self):
        """Ensure database file exists and has tables."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_tables()

    @contextmanager
    def get_connection(self) -> Generator:
        """Get a connection from the pool."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def get_cursor(self):
        """Get a cursor with automatic connection management."""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
            finally:
                cursor.close()

    def _init_tables(self):
        """Initialize database tables."""
        with self.get_cursor() as cursor:
            # Enable WAL mode for better concurrent read/write performance
            cursor.execute("PRAGMA journal_mode=WAL;")

            # Articles table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    link TEXT UNIQUE NOT NULL,
                    normalized_link TEXT UNIQUE,
                    summary TEXT,
                    content TEXT,
                    source TEXT,
                    source_name TEXT,
                    published_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tags TEXT,
                    ai_summary TEXT
                )
            """)

            # Add ai_summary column if it doesn't exist (for existing databases)
            try:
                cursor.execute("ALTER TABLE articles ADD COLUMN ai_summary TEXT")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE articles ADD COLUMN normalized_link TEXT")
            except Exception:
                pass

            # User interactions table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_interactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    article_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    weight REAL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (article_id) REFERENCES articles(id)
                )
            """)

            cursor.execute("""
                CREATE TABLE IF NOT EXISTS feed_status (
                    feed_url TEXT PRIMARY KEY,
                    etag TEXT,
                    last_modified TEXT,
                    last_status_code INTEGER,
                    last_success_at TIMESTAMP,
                    last_error_at TIMESTAMP,
                    last_error TEXT,
                    consecutive_failures INTEGER DEFAULT 0,
                    average_fetch_ms REAL
                )
            """)

            # Create indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_articles_published_at
                ON articles(published_at DESC)
            """)
            cursor.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS idx_articles_normalized_link
                ON articles(normalized_link)
                WHERE normalized_link IS NOT NULL AND normalized_link != ''
            """)
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_user_interactions_user_id
                ON user_interactions(user_id, created_at DESC)
            """)


# Global database instance
_db: Optional[Database] = None


def get_db() -> Database:
    """Get the database instance."""
    global _db
    if _db is None:
        _db = Database()
    return _db


def init_db():
    """Initialize database tables."""
    get_db()._init_tables()


def store_article(
    article_id: str,
    title: str,
    link: str,
    summary: str = "",
    content: str = "",
    source: str = "",
    source_name: str = "",
    published_at: Optional[datetime] = None,
    tags: list[str] = None,
    ai_summary: str = "",
    normalized_link: str = "",
) -> bool:
    """Store or update an article."""
    with get_db().get_cursor() as cursor:
        tags_json = json.dumps(tags or [])
        try:
            cursor.execute(
                """
                INSERT INTO articles (id, title, link, normalized_link, summary, content, source, source_name, published_at, tags, ai_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (article_id, title, link, normalized_link, summary, content, source, source_name, published_at, tags_json, ai_summary),
            )
            return True
        except sqlite3.IntegrityError:
            where_column = "normalized_link" if normalized_link else "link"
            where_value = normalized_link or link
            cursor.execute(
                f"""
                UPDATE articles SET
                    title = ?,
                    link = ?,
                    summary = ?,
                    content = ?,
                    source_name = ?,
                    published_at = ?,
                    tags = ?,
                    ai_summary = COALESCE(NULLIF(?, ''), ai_summary)
                WHERE {where_column} = ?
                """,
                (title, link, summary, content, source_name, published_at, tags_json, ai_summary, where_value),
            )
            return False


def batch_store_articles(articles: list[dict]) -> dict[str, int]:
    """Store or update multiple articles in one transaction."""
    if not articles:
        return {"inserted": 0, "updated": 0}

    inserted = 0
    updated = 0
    with get_db().get_cursor() as cursor:
        for article in articles:
            tags_json = json.dumps(article.get("tags") or [])
            article_id = article["article_id"]
            title = article.get("title", "")
            link = article.get("link", "")
            normalized_link = article.get("normalized_link", "")
            summary = article.get("summary", "")
            content = article.get("content", "")
            source = article.get("source", "")
            source_name = article.get("source_name", "")
            published_at = article.get("published_at")
            ai_summary = article.get("ai_summary", "")

            try:
                cursor.execute(
                    """
                    INSERT INTO articles (id, title, link, normalized_link, summary, content, source, source_name, published_at, tags, ai_summary)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (article_id, title, link, normalized_link, summary, content, source, source_name, published_at, tags_json, ai_summary),
                )
                inserted += 1
            except sqlite3.IntegrityError:
                where_column = "normalized_link" if normalized_link else "link"
                where_value = normalized_link or link
                cursor.execute(
                    f"""
                    UPDATE articles SET
                        title = ?,
                        link = ?,
                        summary = ?,
                        content = ?,
                    source = ?,
                    source_name = ?,
                    published_at = ?,
                    tags = ?,
                    ai_summary = COALESCE(NULLIF(?, ''), ai_summary)
                WHERE {where_column} = ?
                """,
                (title, link, summary, content, source, source_name, published_at, tags_json, ai_summary, where_value),
                )
                updated += 1
    return {"inserted": inserted, "updated": updated}


def get_article(article_id: str) -> Optional[dict]:
    """Get an article by ID."""
    with get_db().get_cursor() as cursor:
        cursor.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


def get_article_by_link(link: str) -> Optional[dict]:
    """Get an article by original or normalized link."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            "SELECT * FROM articles WHERE link = ? OR normalized_link = ?",
            (link, link),
        )
        row = cursor.fetchone()
        return dict(row) if row else None


def list_articles_missing_summary(limit: int = 20) -> list[dict]:
    """List stored articles that do not have an AI summary yet."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM articles
            WHERE ai_summary IS NULL OR ai_summary = ''
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (limit,),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_recent_articles(limit: int = 100, days: int = 7) -> list[dict]:
    """Get recent articles."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM articles
            WHERE published_at > datetime('now', '-' || ? || ' days')
            ORDER BY published_at DESC
            LIMIT ?
            """,
            (days, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def list_recent_articles(limit: int = 20, offset: int = 0, days: int = 30) -> list[dict]:
    """List recent articles from the local database without fetching feeds."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM articles
            WHERE published_at > datetime('now', '-' || ? || ' days')
            ORDER BY published_at DESC
            LIMIT ? OFFSET ?
            """,
            (days, limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_feed_status(feed_url: str) -> Optional[dict]:
    """Get cached HTTP and health status for a feed."""
    with get_db().get_cursor() as cursor:
        cursor.execute("SELECT * FROM feed_status WHERE feed_url = ?", (feed_url,))
        row = cursor.fetchone()
        return dict(row) if row else None


def record_feed_success(
    feed_url: str,
    status_code: int,
    etag: Optional[str] = None,
    last_modified: Optional[str] = None,
    fetch_ms: Optional[float] = None,
) -> None:
    """Record a successful feed fetch, including 304 cache hits."""
    previous = get_feed_status(feed_url) or {}
    previous_avg = previous.get("average_fetch_ms")
    if fetch_ms is not None and previous_avg is not None:
        average_fetch_ms = round((float(previous_avg) * 0.8) + (fetch_ms * 0.2), 2)
    else:
        average_fetch_ms = fetch_ms if fetch_ms is not None else previous_avg

    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO feed_status (
                feed_url, etag, last_modified, last_status_code, last_success_at,
                last_error_at, last_error, consecutive_failures, average_fetch_ms
            ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, NULL, NULL, 0, ?)
            ON CONFLICT(feed_url) DO UPDATE SET
                etag = COALESCE(excluded.etag, feed_status.etag),
                last_modified = COALESCE(excluded.last_modified, feed_status.last_modified),
                last_status_code = excluded.last_status_code,
                last_success_at = CURRENT_TIMESTAMP,
                last_error = NULL,
                consecutive_failures = 0,
                average_fetch_ms = COALESCE(excluded.average_fetch_ms, feed_status.average_fetch_ms)
            """,
            (feed_url, etag, last_modified, status_code, average_fetch_ms),
        )


def record_feed_error(
    feed_url: str,
    error: str,
    status_code: Optional[int] = None,
    fetch_ms: Optional[float] = None,
) -> None:
    """Record a failed feed fetch."""
    previous = get_feed_status(feed_url) or {}
    failures = int(previous.get("consecutive_failures") or 0) + 1
    previous_avg = previous.get("average_fetch_ms")
    if fetch_ms is not None and previous_avg is not None:
        average_fetch_ms = round((float(previous_avg) * 0.8) + (fetch_ms * 0.2), 2)
    else:
        average_fetch_ms = fetch_ms if fetch_ms is not None else previous_avg

    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO feed_status (
                feed_url, last_status_code, last_error_at, last_error,
                consecutive_failures, average_fetch_ms
            ) VALUES (?, ?, CURRENT_TIMESTAMP, ?, ?, ?)
            ON CONFLICT(feed_url) DO UPDATE SET
                last_status_code = excluded.last_status_code,
                last_error_at = CURRENT_TIMESTAMP,
                last_error = excluded.last_error,
                consecutive_failures = excluded.consecutive_failures,
                average_fetch_ms = COALESCE(excluded.average_fetch_ms, feed_status.average_fetch_ms)
            """,
            (feed_url, status_code, error[:500], failures, average_fetch_ms),
        )


def list_feed_statuses() -> dict[str, dict]:
    """Return all recorded feed health and cache statuses keyed by URL."""
    with get_db().get_cursor() as cursor:
        cursor.execute("SELECT * FROM feed_status")
        return {row["feed_url"]: dict(row) for row in cursor.fetchall()}


def record_interaction(
    user_id: str,
    article_id: str,
    action: str,
    weight: float = 1.0,
):
    """Record a user interaction."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO user_interactions (user_id, article_id, action, weight)
            VALUES (?, ?, ?, ?)
            """,
            (user_id, article_id, action, weight),
        )


def get_user_interactions(user_id: str, limit: int = 100) -> list[dict]:
    """Get user's interaction history."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            SELECT * FROM user_interactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_user_interacted_article_ids(user_id: str, limit: int = 500) -> set[str]:
    """Get IDs of articles a user has interacted with."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT article_id FROM user_interactions
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        )
        return {row["article_id"] for row in cursor.fetchall()}


def get_all_user_ids() -> list[str]:
    """Get all distinct user IDs from interactions table."""
    with get_db().get_cursor() as cursor:
        cursor.execute("SELECT DISTINCT user_id FROM user_interactions")
        return [row["user_id"] for row in cursor.fetchall()]


def article_has_summary(article_id: str) -> bool:
    """Check if an article already has an AI summary."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            "SELECT ai_summary FROM articles WHERE id = ? AND ai_summary IS NOT NULL AND ai_summary != ''",
            (article_id,),
        )
        return cursor.fetchone() is not None


def get_article_summary(article_id: str) -> str:
    """Return cached AI summary for an article, or an empty string."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            "SELECT ai_summary FROM articles WHERE id = ? AND ai_summary IS NOT NULL AND ai_summary != ''",
            (article_id,),
        )
        row = cursor.fetchone()
        return row["ai_summary"] if row else ""


def get_article_summary_by_link(link: str) -> str:
    """Return cached AI summary for an article by original or normalized link."""
    normalized_link = normalize_article_link(link)
    if not normalized_link and not link:
        return ""

    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            SELECT ai_summary
            FROM articles
            WHERE ai_summary IS NOT NULL
              AND ai_summary != ''
              AND (
                normalized_link = ?
                OR link = ?
              )
            ORDER BY
              CASE
                WHEN normalized_link = ? THEN 0
                WHEN link = ? THEN 1
                ELSE 2
              END
            LIMIT 1
            """,
            (normalized_link, link, normalized_link, link),
        )
        row = cursor.fetchone()
        return row["ai_summary"] if row else ""


def update_article_summary(article_id: str, ai_summary: str) -> None:
    """Update AI summary for a single article."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            "UPDATE articles SET ai_summary = ? WHERE id = ?",
            (ai_summary, article_id),
        )


def batch_update_summaries(summaries: list[dict]) -> int:
    """Batch update AI summaries. Each dict must have 'id' and 'ai_summary'."""
    with get_db().get_cursor() as cursor:
        updated = 0
        for summary in summaries:
            ai_summary = summary.get("ai_summary", "")
            if not ai_summary:
                continue

            article_id = summary.get("id", "")
            link = summary.get("link", "")
            normalized_link = normalize_article_link(summary.get("normalized_link") or link)
            cursor.execute(
                """
                UPDATE articles
                SET ai_summary = ?
                WHERE (
                    (? != '' AND id = ?)
                    OR (? != '' AND normalized_link = ?)
                    OR (? != '' AND link = ?)
                )
                """,
                (ai_summary, article_id, article_id, normalized_link, normalized_link, link, link),
            )
            updated += cursor.rowcount
        return updated


def search_articles(keyword: str, limit: int = 50, offset: int = 0) -> list[dict]:
    """Search articles by keyword in title, summary, and content."""
    with get_db().get_cursor() as cursor:
        pattern = f"%{keyword}%"
        cursor.execute(
            """
            SELECT * FROM articles
            WHERE title LIKE ? OR summary LIKE ? OR content LIKE ? OR ai_summary LIKE ?
            ORDER BY published_at DESC
            LIMIT ? OFFSET ?
            """,
            (pattern, pattern, pattern, pattern, limit, offset),
        )
        return [dict(row) for row in cursor.fetchall()]


def get_feed_stats(days: int = 7) -> dict[str, dict]:
    """Get per-feed article count stats for health monitoring."""
    with get_db().get_cursor() as cursor:
        cursor.execute(
            """
            SELECT source_name, source, COUNT(*) as count,
                   MAX(published_at) as latest
            FROM articles
            WHERE published_at > datetime('now', '-' || ? || ' days')
            GROUP BY source
            ORDER BY count DESC
            """,
            (days,),
        )
        return {
            row["source"]: {
                "source_name": row["source_name"] or row["source"],
                "count": row["count"],
                "latest": row["latest"],
            }
            for row in cursor.fetchall()
        }
