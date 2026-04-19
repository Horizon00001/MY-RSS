import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock
from rss_api import RSSExtractorAPI, BEIJING_TZ


class TestRSSExtractorAPI:
    @pytest.fixture
    def extractor(self, tmp_path):
        config = tmp_path / "config.ini"
        config.write_text("""[rss]
url1 = https://example.com/feed1
url2 = https://example.com/feed2

[headers]
user_agent = TestAgent/1.0

[filter]
days = 7
enabled = true
""")
        ext = RSSExtractorAPI()
        ext.config_path = config
        ext.config = MagicMock()
        ext.config.get.side_effect = lambda section, key: {
            ('headers', 'user_agent'): 'TestAgent/1.0',
            ('filter', 'days'): '7',
        }.get((section, key))
        return ext

    def test_load_rss_feeds(self, extractor):
        with patch.object(extractor, 'config') as mock_config:
            mock_config.items.return_value = [
                ('url1', 'https://example.com/feed1'),
                ('url2', 'https://example.com/feed2'),
            ]
            urls = extractor.load_rss_feeds()
            assert len(urls) == 2
            assert 'https://example.com/feed1' in urls
            assert 'https://example.com/feed2' in urls

    def test_get_entry_date_with_updated(self):
        entry = MagicMock()
        entry.updated = '2026-04-19T10:00:00Z'
        result = RSSExtractorAPI.get_entry_date(None, entry)
        assert result.tzinfo is not None

    def test_get_entry_date_with_published(self):
        entry = MagicMock()
        entry.published = '2026-04-19T10:00:00Z'
        result = RSSExtractorAPI.get_entry_date(None, entry)
        assert result.tzinfo is not None

    def test_get_entry_date_missing_all_fields(self):
        entry = MagicMock(spec=[])
        result = RSSExtractorAPI.get_entry_date(None, entry)
        assert result is None

    def test_filter_by_date_within_range(self, extractor):
        now = datetime.now(BEIJING_TZ)
        entry = MagicMock()
        entry.updated = (now - timedelta(hours=1)).isoformat()
        with patch.object(extractor, 'get_entry_date', return_value=now - timedelta(hours=1)):
            result = extractor.filter_by_date([entry], days=7)
            assert len(result) == 1

    def test_filter_by_date_out_of_range(self, extractor):
        entry = MagicMock()
        with patch.object(extractor, 'get_entry_date', return_value=None):
            result = extractor.filter_by_date([entry], days=7)
            assert len(result) == 0

    def test_format_entry(self, extractor):
        now = datetime.now(BEIJING_TZ)
        entry = MagicMock()
        entry.get.side_effect = lambda k: {
            'title': 'Test Title',
            'link': 'https://example.com/article',
            'summary': 'Test summary',
            'content': 'Test content',
        }.get(k)

        with patch.object(extractor, 'get_entry_date', return_value=now):
            result = extractor.format_entry(entry)
            assert result['title'] == 'Test Title'
            assert result['link'] == 'https://example.com/article'
            assert result['summary'] == 'Test summary'
            assert result['content'] == 'Test content'
            assert 'ai_summary' not in result
