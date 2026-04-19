"""Test configuration and fixtures."""

import pytest
import sys
import os
from pathlib import Path
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["API_KEY"] = "test_api_key"
os.environ["API_URL"] = "https://api.deepseek.com/v1"


@pytest.fixture
def mock_feed_parser():
    """Mock FeedParser."""
    parser = MagicMock()
    parser.get_entry_date.return_value = None
    return parser


@pytest.fixture
def mock_fetcher():
    """Mock Fetcher with async generator."""
    async def empty_generator():
        return
        yield  # make it a generator

    fetcher = MagicMock()
    fetcher.fetch_all.return_value = empty_generator()
    return fetcher


@pytest.fixture
def mock_state_manager():
    """Mock StateManager."""
    manager = MagicMock()
    manager.last_fetch = None
    manager.update_last_fetch.return_value = "2026-04-19 10:00:00 (北京时间)"
    return manager


@pytest.fixture
def mock_summarizer():
    """Mock Summarizer."""
    summarizer = MagicMock()
    summarizer.summarize_batch = AsyncMock(return_value=[])
    return summarizer


@pytest.fixture
def sample_entry():
    """Sample RSS entry dict."""
    return {
        "title": "Test Article",
        "link": "https://example.com/article",
        "summary": "Test summary",
        "content": "Test content",
        "updated": "2026-04-19T10:00:00Z",
    }
