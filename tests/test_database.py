"""Tests for src/database.py."""

import sqlite3

import pytest

from src.database import CURRENT_SCHEMA_VERSION, Database, get_db, init_db


class TestWALMode:
    def test_wal_mode_enabled(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute("PRAGMA journal_mode;")
            result = cursor.fetchone()
            assert result[0].lower() == "wal"


class TestDatabaseMigrations:
    def test_new_database_sets_current_user_version(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))

        with db.get_cursor() as cursor:
            cursor.execute("PRAGMA user_version")
            assert cursor.fetchone()[0] == CURRENT_SCHEMA_VERSION

    def test_old_articles_schema_adds_summary_and_normalized_link(self, tmp_path):
        db_path = tmp_path / "legacy.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE articles (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    link TEXT UNIQUE NOT NULL,
                    summary TEXT,
                    content TEXT,
                    source TEXT,
                    source_name TEXT,
                    published_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    tags TEXT
                )
            """)
            conn.execute(
                "INSERT INTO articles (id, title, link, published_at) VALUES (?, ?, ?, datetime('now'))",
                ("a1", "Legacy", "https://example.com/a"),
            )
            conn.execute("PRAGMA user_version = 0")

        db = Database(db_path=str(db_path))

        with db.get_cursor() as cursor:
            cursor.execute("PRAGMA table_info(articles)")
            columns = {row[1] for row in cursor.fetchall()}
            cursor.execute("SELECT title, link FROM articles WHERE id = 'a1'")
            article = dict(cursor.fetchone())
            cursor.execute("PRAGMA user_version")
            user_version = cursor.fetchone()[0]

        assert {"ai_summary", "normalized_link"}.issubset(columns)
        assert article == {"title": "Legacy", "link": "https://example.com/a"}
        assert user_version == CURRENT_SCHEMA_VERSION

    def test_current_columns_with_old_user_version_are_idempotent(self, tmp_path):
        db_path = tmp_path / "current_columns_old_version.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE articles (
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
            conn.execute(
                """
                INSERT INTO articles (id, title, link, normalized_link, published_at, ai_summary)
                VALUES (?, ?, ?, ?, datetime('now'), ?)
                """,
                ("a1", "Current", "https://example.com/a?utm_source=rss", "https://example.com/a", "Existing summary"),
            )
            conn.execute("PRAGMA user_version = 0")

        db = Database(db_path=str(db_path))
        db._init_tables()

        with db.get_cursor() as cursor:
            cursor.execute("SELECT COUNT(*) FROM articles")
            row_count = cursor.fetchone()[0]
            cursor.execute("SELECT normalized_link, ai_summary FROM articles WHERE id = 'a1'")
            article = dict(cursor.fetchone())
            cursor.execute("PRAGMA user_version")
            user_version = cursor.fetchone()[0]

        assert row_count == 1
        assert article == {
            "normalized_link": "https://example.com/a",
            "ai_summary": "Existing summary",
        }
        assert user_version == CURRENT_SCHEMA_VERSION


class TestSearchArticles:
    def test_search_finds_by_title(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO articles (id, title, link, published_at) "
                "VALUES ('test1', 'Python Web Framework', 'https://x.com', datetime('now'))"
            )
        # Patch global db
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import search_articles
            results = search_articles("Python")
            assert len(results) >= 1
            assert results[0]["title"] == "Python Web Framework"
        finally:
            db_mod._db = old_db


class TestArticleLookup:
    def test_get_article_by_link(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO articles (id, title, link, normalized_link, published_at) "
                "VALUES ('a1', 'Title', 'https://example.com/a?utm_source=rss', 'https://example.com/a', datetime('now'))"
            )
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import get_article_by_link
            article = get_article_by_link("https://example.com/a")
            assert article["id"] == "a1"
        finally:
            db_mod._db = old_db

    def test_store_article_dedupes_by_normalized_link(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import get_article_by_link, store_article

            inserted = store_article(
                article_id="a1",
                title="Original",
                link="https://example.com/a?utm_source=rss",
                normalized_link="https://example.com/a",
            )
            updated = store_article(
                article_id="a2",
                title="Updated",
                link="https://example.com/a#comments",
                normalized_link="https://example.com/a",
            )

            article = get_article_by_link("https://example.com/a")
            assert inserted is True
            assert updated is False
            assert article["id"] == "a1"
            assert article["title"] == "Updated"
        finally:
            db_mod._db = old_db

    def test_search_no_match(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import search_articles
            results = search_articles("NonexistentKeyword12345")
            assert len(results) == 0
        finally:
            db_mod._db = old_db


class TestBatchStoreArticles:
    def test_batch_store_inserts_multiple_articles(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import batch_store_articles, get_article

            result = batch_store_articles([
                {
                    "article_id": "a1",
                    "title": "Title 1",
                    "link": "https://example.com/1",
                    "normalized_link": "https://example.com/1",
                },
                {
                    "article_id": "a2",
                    "title": "Title 2",
                    "link": "https://example.com/2",
                    "normalized_link": "https://example.com/2",
                },
            ])

            assert result == {"inserted": 2, "updated": 0}
            assert get_article("a1")["title"] == "Title 1"
            assert get_article("a2")["title"] == "Title 2"
        finally:
            db_mod._db = old_db

    def test_batch_store_updates_duplicate_normalized_link(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import batch_store_articles, get_article_by_link

            result = batch_store_articles([
                {
                    "article_id": "a1",
                    "title": "Original",
                    "link": "https://example.com/a?utm_source=rss",
                    "normalized_link": "https://example.com/a",
                },
                {
                    "article_id": "a2",
                    "title": "Updated",
                    "link": "https://example.com/a#comments",
                    "normalized_link": "https://example.com/a",
                },
            ])

            article = get_article_by_link("https://example.com/a")
            assert result == {"inserted": 1, "updated": 1}
            assert article["id"] == "a1"
            assert article["title"] == "Updated"
        finally:
            db_mod._db = old_db

    def test_batch_store_does_not_clear_existing_summary_on_duplicate(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import batch_store_articles, get_article_by_link

            batch_store_articles([
                {
                    "article_id": "a1",
                    "title": "Original",
                    "link": "https://example.com/a?utm_source=rss",
                    "normalized_link": "https://example.com/a",
                    "ai_summary": "Existing summary",
                },
            ])
            batch_store_articles([
                {
                    "article_id": "a2",
                    "title": "Updated",
                    "link": "https://example.com/a",
                    "normalized_link": "https://example.com/a",
                    "ai_summary": "",
                },
            ])

            article = get_article_by_link("https://example.com/a")
            assert article["title"] == "Updated"
            assert article["ai_summary"] == "Existing summary"
        finally:
            db_mod._db = old_db


class TestBatchUpdateSummaries:
    def test_batch_update(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO articles (id, title, link, published_at) "
                "VALUES ('a1', 'T1', 'https://1.com', datetime('now'))"
            )
            cursor.execute(
                "INSERT INTO articles (id, title, link, published_at) "
                "VALUES ('a2', 'T2', 'https://2.com', datetime('now'))"
            )
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import batch_update_summaries, get_article
            batch_update_summaries([
                {"id": "a1", "ai_summary": "Summary 1"},
                {"id": "a2", "ai_summary": "Summary 2"},
            ])
            a1 = get_article("a1")
            assert a1["ai_summary"] == "Summary 1"
            a2 = get_article("a2")
            assert a2["ai_summary"] == "Summary 2"
        finally:
            db_mod._db = old_db


class TestGetAllUserIds:
    def test_returns_distinct_ids(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO articles (id, title, link, published_at) "
                "VALUES ('a1', 'T', 'https://x.com', datetime('now'))"
            )
            cursor.execute(
                "INSERT INTO user_interactions (user_id, article_id, action) "
                "VALUES ('user_a', 'a1', 'view')"
            )
            cursor.execute(
                "INSERT INTO user_interactions (user_id, article_id, action) "
                "VALUES ('user_b', 'a1', 'view')"
            )
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import get_all_user_ids
            ids = get_all_user_ids()
            assert "user_a" in ids
            assert "user_b" in ids
            assert len(ids) == 2
        finally:
            db_mod._db = old_db


class TestArticleHasSummary:
    def test_true_when_summary_exists(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO articles (id, title, link, published_at, ai_summary) "
                "VALUES ('a1', 'T', 'https://x.com', datetime('now'), 'Good summary')"
            )
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import article_has_summary
            assert article_has_summary("a1") is True
        finally:
            db_mod._db = old_db

    def test_get_article_summary_returns_cached_value(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO articles (id, title, link, published_at, ai_summary) "
                "VALUES ('a1', 'T', 'https://x.com', datetime('now'), 'Cached summary')"
            )
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import get_article_summary
            assert get_article_summary("a1") == "Cached summary"
        finally:
            db_mod._db = old_db


    def test_batch_get_article_summaries_matches_id_link_and_normalized_link(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO articles (id, title, link, normalized_link, published_at, ai_summary) "
                "VALUES ('a1', 'T1', 'https://x.com/a?utm_source=rss', 'https://x.com/a', datetime('now'), 'By normalized')"
            )
            cursor.execute(
                "INSERT INTO articles (id, title, link, normalized_link, published_at, ai_summary) "
                "VALUES ('a2', 'T2', 'https://x.com/b', 'https://x.com/b', datetime('now'), 'By id')"
            )
            cursor.execute(
                "INSERT INTO articles (id, title, link, normalized_link, published_at, ai_summary) "
                "VALUES ('a3', 'T3', 'https://x.com/c', 'https://x.com/c', datetime('now'), '')"
            )
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import batch_get_article_summaries

            summaries = batch_get_article_summaries([
                {"id": "missing-id", "link": "https://x.com/a?utm_campaign=app"},
                {"id": "a2", "link": "https://not-the-stored-link.test"},
                {"id": "a3", "link": "https://x.com/c"},
                {"id": "none", "link": "https://x.com/none"},
            ])

            assert summaries == {
                "missing-id": "By normalized",
                "a2": "By id",
            }
        finally:
            db_mod._db = old_db

    def test_get_article_summary_by_link_uses_normalized_link(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute(
                "INSERT INTO articles (id, title, link, normalized_link, published_at, ai_summary) "
                "VALUES ('a1', 'T', 'https://x.com/a?utm_source=rss', 'https://x.com/a', datetime('now'), 'Cached summary')"
            )
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import get_article_summary_by_link
            assert get_article_summary_by_link("https://x.com/a?utm_campaign=app") == "Cached summary"
        finally:
            db_mod._db = old_db


class TestFeedStatus:
    def test_record_success_stores_cache_headers(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import get_feed_status, record_feed_success

            record_feed_success(
                "https://example.com/rss",
                status_code=200,
                etag='"abc"',
                last_modified="Wed, 01 May 2026 10:00:00 GMT",
                fetch_ms=123.4,
            )

            status = get_feed_status("https://example.com/rss")
            assert status["etag"] == '"abc"'
            assert status["last_modified"] == "Wed, 01 May 2026 10:00:00 GMT"
            assert status["last_status_code"] == 200
            assert status["consecutive_failures"] == 0
            assert status["average_fetch_ms"] == 123.4
            assert status["last_error_at"] is None
        finally:
            db_mod._db = old_db

    def test_record_error_increments_failures(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import get_feed_status, record_feed_error

            record_feed_error("https://example.com/rss", "timeout")
            record_feed_error("https://example.com/rss", "timeout again")

            status = get_feed_status("https://example.com/rss")
            assert status["last_error"] == "timeout again"
            assert status["consecutive_failures"] == 2
        finally:
            db_mod._db = old_db

    def test_batch_get_feed_statuses_returns_requested_statuses(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import batch_get_feed_statuses, record_feed_success

            record_feed_success("https://example.com/a", 200, etag='"a"')
            record_feed_success("https://example.com/b", 200, last_modified="Wed, 01 May 2026 10:00:00 GMT")

            statuses = batch_get_feed_statuses(["https://example.com/a", "https://example.com/missing", "https://example.com/b"])

            assert set(statuses) == {"https://example.com/a", "https://example.com/b"}
            assert statuses["https://example.com/a"]["etag"] == '"a"'
            assert statuses["https://example.com/b"]["last_modified"] == "Wed, 01 May 2026 10:00:00 GMT"
        finally:
            db_mod._db = old_db

    def test_batch_get_feed_statuses_empty_input_returns_empty_dict(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import batch_get_feed_statuses

            assert batch_get_feed_statuses([]) == {}
        finally:
            db_mod._db = old_db

    def test_batch_get_feed_statuses_handles_duplicate_urls(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import batch_get_feed_statuses, record_feed_success

            record_feed_success("https://example.com/rss", 200, etag='"abc"')

            statuses = batch_get_feed_statuses([
                "https://example.com/rss",
                "https://example.com/rss",
            ])

            assert list(statuses) == ["https://example.com/rss"]
            assert statuses["https://example.com/rss"]["etag"] == '"abc"'
        finally:
            db_mod._db = old_db

    def test_record_success_does_not_call_get_feed_status_before_write(self, tmp_path, monkeypatch):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import record_feed_success

            monkeypatch.setattr(db_mod, "get_feed_status", lambda url: pytest.fail("record_feed_success should not pre-read"))

            record_feed_success("https://example.com/rss", 200, fetch_ms=100.0)
            record_feed_success("https://example.com/rss", 200, fetch_ms=200.0)

            with db.get_cursor() as cursor:
                cursor.execute("SELECT average_fetch_ms, consecutive_failures FROM feed_status WHERE feed_url = ?", ("https://example.com/rss",))
                status = dict(cursor.fetchone())
            assert status["average_fetch_ms"] == 120.0
            assert status["consecutive_failures"] == 0
        finally:
            db_mod._db = old_db

    def test_record_error_does_not_call_get_feed_status_before_write(self, tmp_path, monkeypatch):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import record_feed_error

            monkeypatch.setattr(db_mod, "get_feed_status", lambda url: pytest.fail("record_feed_error should not pre-read"))

            record_feed_error("https://example.com/rss", "timeout", fetch_ms=100.0)
            record_feed_error("https://example.com/rss", "timeout again", fetch_ms=200.0)

            with db.get_cursor() as cursor:
                cursor.execute("SELECT average_fetch_ms, consecutive_failures, last_error FROM feed_status WHERE feed_url = ?", ("https://example.com/rss",))
                status = dict(cursor.fetchone())
            assert status["average_fetch_ms"] == 120.0
            assert status["consecutive_failures"] == 2
            assert status["last_error"] == "timeout again"
        finally:
            db_mod._db = old_db

    def test_record_success_clears_last_error_timestamp(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import get_feed_status, record_feed_error, record_feed_success

            record_feed_error("https://example.com/rss", "timeout", fetch_ms=100.0)
            record_feed_success("https://example.com/rss", 200, fetch_ms=120.0)

            status = get_feed_status("https://example.com/rss")
            assert status["last_error_at"] is None
            assert status["last_error"] is None
        finally:
            db_mod._db = old_db

    def test_false_when_no_summary(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        import src.database as db_mod
        old_db = db_mod._db
        db_mod._db = db
        try:
            from src.database import article_has_summary
            assert article_has_summary("nonexistent") is False
        finally:
            db_mod._db = old_db
