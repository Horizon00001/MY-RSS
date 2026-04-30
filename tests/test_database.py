"""Tests for src/database.py."""

import pytest

from src.database import Database, get_db, init_db


class TestWALMode:
    def test_wal_mode_enabled(self, tmp_path):
        db = Database(db_path=str(tmp_path / "test.db"))
        with db.get_cursor() as cursor:
            cursor.execute("PRAGMA journal_mode;")
            result = cursor.fetchone()
            assert result[0].lower() == "wal"


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
