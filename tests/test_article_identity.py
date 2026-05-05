"""Tests for shared article identity helpers."""

from src.article_identity import compute_article_id, normalize_article_link


class TestNormalizeArticleLink:
    def test_removes_tracking_params_and_fragment(self):
        link = "https://example.com/news/123/?utm_source=rss&utm_campaign=test&keep=1#comments"

        assert normalize_article_link(link) == "https://example.com/news/123?keep=1"

    def test_normalizes_scheme_host_and_trailing_slash(self):
        link = "HTTP://EXAMPLE.com/news/123/"

        assert normalize_article_link(link) == "https://example.com/news/123"


class TestComputeArticleId:
    def test_uses_normalized_link(self):
        id1 = compute_article_id("https://example.com/news/123?utm_source=rss")
        id2 = compute_article_id("https://example.com/news/123")

        assert id1 == id2
