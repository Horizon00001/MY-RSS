"""Database connection and models for MY-RSS using SQLite."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

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
            # Articles table
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    link TEXT UNIQUE NOT NULL,
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

            # Create indexes
            cursor.execute("""
                CREATE INDEX IF NOT EXISTS idx_articles_published_at
                ON articles(published_at DESC)
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
) -> bool:
    """Store or update an article."""
    with get_db().get_cursor() as cursor:
        tags_json = json.dumps(tags or [])
        try:
            cursor.execute(
                """
                INSERT INTO articles (id, title, link, summary, content, source, source_name, published_at, tags, ai_summary)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (article_id, title, link, summary, content, source, source_name, published_at, tags_json, ai_summary),
            )
            return True
        except sqlite3.IntegrityError:
            cursor.execute(
                """
                UPDATE articles SET
                    title = ?,
                    summary = ?,
                    content = ?,
                    source_name = ?,
                    published_at = ?,
                    tags = ?,
                    ai_summary = ?
                WHERE link = ?
                """,
                (title, summary, content, source_name, published_at, tags_json, ai_summary, link),
            )
            return False


def get_article(article_id: str) -> Optional[dict]:
    """Get an article by ID."""
    with get_db().get_cursor() as cursor:
        cursor.execute("SELECT * FROM articles WHERE id = ?", (article_id,))
        row = cursor.fetchone()
        if row:
            return dict(row)
        return None


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
