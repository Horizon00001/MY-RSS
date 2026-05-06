"""Compatibility checks for the public src.api import surface."""

import importlib
import sys


def test_src_api_import_compatibility():
    from src.api import (
        WSConnectionManager,
        app,
        get_feed_parser,
        get_fetcher,
        get_state_manager,
        get_summarizer,
        normalize_article_link,
        refresh_rss_entries_once,
        summarize_missing_articles,
    )

    import src.api as api

    assert api.app is app
    assert api.get_feed_parser is get_feed_parser
    assert api.get_fetcher is get_fetcher
    assert api.get_state_manager is get_state_manager
    assert api.get_summarizer is get_summarizer
    assert api.normalize_article_link is normalize_article_link
    assert api.WSConnectionManager is WSConnectionManager
    assert api.refresh_rss_entries_once is refresh_rss_entries_once
    assert api.summarize_missing_articles is summarize_missing_articles


def test_src_api_import_does_not_load_heavy_recommender_modules():
    import subprocess
    import sys

    script = """
import sys
import src.api
heavy = [name for name in sys.modules if name == 'sklearn' or name.startswith('sklearn.')]
assert heavy == [], heavy
"""

    subprocess.run([sys.executable, "-c", script], check=True)


def test_src_api_import_does_not_load_heavy_recommender_dependencies():
    modules_to_clear = [
        "src.api",
        "src.app_setup",
        "src.recommender",
        "src.recommender.api",
        "src.recommender.hybrid_recommender",
        "src.recommender.tfidf",
        "src.recommender.collaborative",
        "src.recommender.realtime",
        "numpy",
        "sklearn",
    ]
    previous_modules = {name: sys.modules.get(name) for name in modules_to_clear}
    for name in modules_to_clear:
        sys.modules.pop(name, None)

    try:
        importlib.import_module("src.api")

        assert "src.recommender.api" in sys.modules
        assert "src.recommender.hybrid_recommender" not in sys.modules
        assert "src.recommender.tfidf" not in sys.modules
        assert "src.recommender.collaborative" not in sys.modules
        assert "src.recommender.realtime" not in sys.modules
        assert "numpy" not in sys.modules
        assert "sklearn" not in sys.modules
    finally:
        for name in modules_to_clear:
            sys.modules.pop(name, None)
        for name, module in previous_modules.items():
            if module is not None:
                sys.modules[name] = module


def test_recommender_api_import_does_not_load_heavy_recommender_dependencies():
    modules_to_clear = [
        "src.recommender",
        "src.recommender.api",
        "src.recommender.hybrid_recommender",
        "src.recommender.tfidf",
        "src.recommender.collaborative",
        "src.recommender.realtime",
        "numpy",
        "sklearn",
    ]
    previous_modules = {name: sys.modules.get(name) for name in modules_to_clear}
    for name in modules_to_clear:
        sys.modules.pop(name, None)

    try:
        importlib.import_module("src.recommender.api")

        assert "src.recommender.hybrid_recommender" not in sys.modules
        assert "src.recommender.tfidf" not in sys.modules
        assert "src.recommender.collaborative" not in sys.modules
        assert "src.recommender.realtime" not in sys.modules
        assert "numpy" not in sys.modules
        assert "sklearn" not in sys.modules
    finally:
        for name in modules_to_clear:
            sys.modules.pop(name, None)
        for name, module in previous_modules.items():
            if module is not None:
                sys.modules[name] = module


def test_recommend_route_returns_503_when_optional_dependencies_missing(monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from src.recommender import api as recommender_api

    def raise_missing_dependency():
        raise recommender_api._recommendation_unavailable(ImportError("missing sklearn"))

    recommender_api.recommender = None
    monkeypatch.setattr(
        recommender_api,
        "_load_hybrid_recommender_class",
        raise_missing_dependency,
    )

    app = FastAPI()
    app.include_router(recommender_api.router)

    with TestClient(app) as client:
        response = client.get("/recommend/")

    assert response.status_code == 503
    assert response.json()["detail"] == (
        "Recommendation system is unavailable because optional dependencies are missing"
    )
