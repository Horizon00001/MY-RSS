"""Focused tests for recommender backend quality fixes."""

import subprocess
import sys
from datetime import datetime, timezone

import pytest

from src.article_identity import compute_article_id, normalize_article_link
from src.recommender.api import article_from_entry, get_recommender, store_article_to_db
from src.recommender.behavior_tracker import parse_interaction_timestamp
from src.recommender.hybrid_recommender import HybridRecommender, parse_db_datetime


def test_parse_db_datetime_returns_aware_utc_for_sqlite_string():
    parsed = parse_db_datetime("2026-05-01 10:00:00")

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)
    assert parsed.hour == 2


def test_load_articles_from_db_parses_published_at_strings(monkeypatch):
    monkeypatch.setattr(
        "src.database.get_recent_articles",
        lambda limit, days: [
            {
                "id": "a1",
                "title": "Title",
                "link": "https://example.com/a",
                "summary": "Summary",
                "content": "Content",
                "source": "feed",
                "source_name": "Feed",
                "published_at": "2026-05-01 10:00:00",
                "tags": [],
            }
        ],
    )

    recommender = HybridRecommender.__new__(HybridRecommender)
    recommender.articles = {}
    recommender.load_articles_from_db(days=7, limit=1000)

    assert recommender.articles["a1"].date.tzinfo is not None


def test_article_from_entry_uses_deterministic_normalized_link_id():
    article = article_from_entry({
        "title": "Title",
        "link": "https://example.com/a?utm_source=rss",
        "published": "Fri, 01 May 2026 10:00:00 GMT",
    })

    assert article.id == compute_article_id(normalize_article_link(article.link))


def test_store_article_to_db_passes_normalized_link(monkeypatch):
    calls = []
    article = article_from_entry({
        "title": "Title",
        "link": "https://example.com/a?utm_source=rss",
    })

    monkeypatch.setattr("src.database.store_article", lambda **kwargs: calls.append(kwargs))

    store_article_to_db(article)

    assert calls[0]["article_id"] == article.id
    assert calls[0]["normalized_link"] == "https://example.com/a"


def test_parse_interaction_timestamp_returns_aware_utc():
    parsed = parse_interaction_timestamp("2026-05-01 10:00:00")

    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timezone.utc.utcoffset(parsed)


def test_recommender_api_import_does_not_load_heavy_modules():
    script = """
import sys
import src.recommender.api
heavy = [name for name in sys.modules if name == 'sklearn' or name.startswith('sklearn.')]
assert heavy == [], heavy
"""

    subprocess.run([sys.executable, "-c", script], check=True)


def test_get_recommender_returns_503_when_optional_dependencies_missing(monkeypatch):
    import src.recommender.api as recommender_api

    def fail_load():
        raise recommender_api._recommendation_unavailable(ImportError("missing sklearn"))

    monkeypatch.setattr(recommender_api, "recommender", None)
    monkeypatch.setattr(recommender_api, "_load_hybrid_recommender_class", fail_load)

    with pytest.raises(Exception) as exc_info:
        get_recommender()

    assert getattr(exc_info.value, "status_code", None) == 503
