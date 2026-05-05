"""Tests for src/summarizer.py."""

import pytest
import asyncio

from src.summarizer import SUMMARIZE_PROMPT, Summarizer


class TestComputeArticleId:
    def test_deterministic_id(self):
        id1 = Summarizer._compute_article_id({"link": "https://example.com/a"})
        id2 = Summarizer._compute_article_id({"link": "https://example.com/a"})
        assert id1 == id2
        assert len(id1) == 12

    def test_normalized_link_produces_same_id(self):
        id1 = Summarizer._compute_article_id({"link": "https://example.com/a?utm_source=rss"})
        id2 = Summarizer._compute_article_id({"link": "https://example.com/a"})
        assert id1 == id2

    def test_different_links_different_ids(self):
        id1 = Summarizer._compute_article_id({"link": "https://a.com"})
        id2 = Summarizer._compute_article_id({"link": "https://b.com"})
        assert id1 != id2

    def test_missing_link_handled(self):
        result = Summarizer._compute_article_id({})
        assert len(result) == 12


class TestSummarize:
    def test_empty_text_returns_empty(self):
        summarizer = Summarizer(
            api_key="sk-test",
            api_url="https://api.test.com/v1",
        )
        result = summarizer.summarize("")
        assert result == ""

    def test_whitespace_only_returns_empty(self):
        summarizer = Summarizer(
            api_key="sk-test",
            api_url="https://api.test.com/v1",
        )
        result = summarizer.summarize("   ")
        assert result == ""

    def test_cached_summary_is_filled_without_api_call(self, monkeypatch):
        summarizer = Summarizer(
            api_key="sk-test",
            api_url="https://api.test.com/v1",
        )
        monkeypatch.setattr("src.summarizer.get_article_summary_by_link", lambda link: "Cached summary")
        monkeypatch.setattr("src.summarizer.get_article_summary", lambda article_id: "")
        monkeypatch.setattr(summarizer, "summarize", lambda text: pytest.fail("API should not be called"))

        entries = asyncio.run(summarizer.summarize_batch([{"link": "https://example.com/a?utm_source=rss"}]))

        assert entries[0]["ai_summary"] == "Cached summary"


class TestInit:
    def test_missing_api_key_raises(self):
        import os
        old_key = os.environ.pop("API_KEY", None)
        try:
            with pytest.raises(ValueError, match="API_KEY not configured"):
                Summarizer(api_key="your_api_key_here")
        finally:
            if old_key:
                os.environ["API_KEY"] = old_key

    def test_valid_api_key_succeeds(self):
        summarizer = Summarizer(
            api_key="sk-test",
            api_url="https://api.test.com/v1",
        )
        assert summarizer.api_key == "sk-test"


class TestPrompt:
    def test_prompt_formatting(self):
        prompt = SUMMARIZE_PROMPT.format(content="Test content")
        assert "Test content" in prompt
        assert len(prompt) < 500
