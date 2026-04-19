import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient
import sys
import os
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

os.environ["API_KEY"] = "test_api_key"
os.environ["API_URL"] = "https://api.deepseek.com/v1"

from rss_api import app, RSSExtractorAPI


class TestRSSAPI:
    @pytest.fixture
    def client(self):
        return TestClient(app)

    @pytest.fixture
    def mock_extractor(self):
        with patch('rss_api.extractor') as mock:
            mock.load_rss_feeds.return_value = ['https://example.com/feed1']
            mock.config.get.return_value = 'TestAgent/1.0'
            yield mock

    @pytest.fixture
    def mock_summarizer(self):
        with patch('rss_api.RSSSummarizer') as mock:
            instance = MagicMock()
            instance.summarize_entries_async = AsyncMock(return_value=[])
            mock.return_value = instance
            yield mock

    def test_root(self, client):
        response = client.get("/")
        assert response.status_code == 200
        assert "message" in response.json()

    def test_get_feeds(self, client, mock_extractor):
        response = client.get("/rss/feeds")
        assert response.status_code == 200
        assert "feeds" in response.json()

    def test_get_entries_without_summarize(self, client, mock_extractor, mock_summarizer):
        mock_entry = MagicMock()
        mock_entry.get.side_effect = lambda k: {
            'title': 'Test',
            'link': 'https://example.com',
            'summary': 'Summary',
            'content': 'Content',
        }.get(k)

        with patch.object(mock_extractor, 'fetch_rss_entries', return_value=[mock_entry]):
            with patch.object(mock_extractor, 'filter_by_date', return_value=[mock_entry]):
                with patch.object(mock_extractor, 'format_entry', return_value={
                    'title': 'Test',
                    'link': 'https://example.com',
                    'summary': 'Summary',
                    'date': '2026-04-19 10:00:00 (北京时间)',
                    'content': 'Content',
                    'ai_summary': None,
                }):
                    response = client.get("/rss/entries?summarize=false")
                    assert response.status_code == 200
                    data = response.json()
                    assert "entries" in data
                    assert "total" in data

    def test_get_entries_with_summarize(self, client, mock_extractor, mock_summarizer):
        mock_entry = MagicMock()
        mock_entry.get.side_effect = lambda k: {
            'title': 'Test',
            'link': 'https://example.com',
            'summary': 'Summary',
            'content': 'Content',
        }.get(k)

        formatted_entry = {
            'title': 'Test',
            'link': 'https://example.com',
            'summary': 'Summary',
            'date': '2026-04-19 10:00:00 (北京时间)',
            'content': 'Content',
        }

        async def mock_summarize(formatted):
            formatted[0]['ai_summary'] = 'AI摘要'
            return formatted

        with patch.object(mock_extractor, 'load_rss_feeds', return_value=['https://example.com/feed1']):
            with patch('rss_api._fetch_one_url', return_value=[mock_entry]):
                with patch.object(mock_extractor, 'filter_by_date', return_value=[mock_entry]):
                    with patch.object(mock_extractor, 'format_entry', return_value=formatted_entry):
                        with patch('rss_api.RSSSummarizer') as MockSummarizer:
                            instance = MagicMock()
                            instance.summarize_entries_async = AsyncMock(side_effect=mock_summarize)
                            MockSummarizer.return_value = instance

                            response = client.get("/rss/entries?summarize=true")
                            assert response.status_code == 200

    def test_get_entries_with_limit(self, client, mock_extractor, mock_summarizer):
        mock_entry = MagicMock()
        mock_entry.get.side_effect = lambda k: {
            'title': 'Test',
            'link': 'https://example.com',
            'summary': 'Summary',
            'content': 'Content',
        }.get(k)

        formatted = [{
            'title': 'Test',
            'link': 'https://example.com',
            'summary': 'Summary',
            'date': '2026-04-19 10:00:00 (北京时间)',
            'content': 'Content',
        }]

        with patch.object(mock_extractor, 'load_rss_feeds', return_value=['https://example.com/feed1']):
            with patch('rss_api._fetch_one_url', return_value=[mock_entry]):
                with patch.object(mock_extractor, 'filter_by_date', return_value=[mock_entry]):
                    with patch.object(mock_extractor, 'format_entry', return_value=formatted[0]):
                        response = client.get("/rss/entries?limit=1&summarize=false")
                        assert response.status_code == 200
                        data = response.json()
                        assert data['total'] == 1

    def test_get_entries_error_handling(self, client, mock_extractor):
        with patch.object(mock_extractor, 'load_rss_feeds', side_effect=Exception("Test error")):
            response = client.get("/rss/entries")
            assert response.status_code == 500
