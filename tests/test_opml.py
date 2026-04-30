"""Tests for src/opml.py."""

import configparser
import tempfile
from pathlib import Path

from src.opml import generate_opml, import_feeds_to_config, parse_opml


SAMPLE_OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="1.0">
  <head><title>Test Feeds</title></head>
  <body>
    <outline text="CategoryA" title="CategoryA">
      <outline text="Feed1" title="Feed1" type="rss" xmlUrl="https://a.com/rss"/>
      <outline text="Feed2" title="Feed2" type="rss" xmlUrl="https://b.com/rss"/>
    </outline>
    <outline text="Feed3" title="Feed3" type="rss" xmlUrl="https://c.com/rss"/>
  </body>
</opml>
"""


class TestParseOpml:
    def test_parses_all_feeds_with_xmlurl(self):
        feeds = parse_opml(SAMPLE_OPML)
        assert len(feeds) == 3

    def test_extracts_title_and_url(self):
        feeds = parse_opml(SAMPLE_OPML)
        urls = {f["url"] for f in feeds}
        assert "https://a.com/rss" in urls
        assert "https://b.com/rss" in urls
        assert "https://c.com/rss" in urls

    def test_uses_text_as_fallback_title(self):
        feeds = parse_opml(SAMPLE_OPML)
        titles = {f["title"] for f in feeds}
        assert "Feed1" in titles

    def test_handles_empty_opml(self):
        feeds = parse_opml("<opml><body></body></opml>")
        assert feeds == []

    def test_handles_opml_without_body(self):
        feeds = parse_opml("<opml><outline xmlUrl='https://x.com/rss'/></opml>")
        assert len(feeds) == 1

    def test_handles_bytes_input(self):
        feeds = parse_opml(SAMPLE_OPML.encode("utf-8"))
        assert len(feeds) == 3

    def test_skips_outlines_without_xmlurl(self):
        feeds = parse_opml("<opml><body><outline text='just a category'/></body></opml>")
        assert feeds == []

    def test_handles_lowercase_xmlurl(self):
        xml = '<opml><body><outline text="F" xmlurl="https://x.com/rss"/></body></opml>'
        feeds = parse_opml(xml)
        assert len(feeds) == 1


class TestGenerateOpml:
    def test_generates_valid_xml(self):
        feeds = {"url1": "https://x.com/rss", "url2": "https://y.com/rss"}
        result = generate_opml(feeds)
        assert '<?xml version="1.0"' in result
        assert '<opml version="1.0">' in result
        assert 'xmlUrl="https://x.com/rss"' in result
        assert 'xmlUrl="https://y.com/rss"' in result

    def test_includes_custom_title(self):
        result = generate_opml({"u": "https://x.com"}, title="My Feeds")
        assert "<title>My Feeds</title>" in result

    def test_roundtrip(self):
        feeds = {"url1": "https://a.com/rss", "url2": "https://b.com/rss"}
        xml = generate_opml(feeds)
        parsed = parse_opml(xml)
        assert len(parsed) == 2


class TestImportFeedsToConfig:
    def test_adds_new_feeds(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[rss]\nurl1 = https://old.com/rss\n")
            config_path = Path(f.name)

        try:
            new_feeds = [
                {"title": "New", "url": "https://new.com/rss"},
            ]
            result = import_feeds_to_config(new_feeds, config_path)
            assert result["added"] == 1
            assert result["skipped"] == 0

            ini = configparser.ConfigParser()
            ini.read(config_path)
            urls = list(ini["rss"].values())
            assert "https://new.com/rss" in urls
        finally:
            config_path.unlink(missing_ok=True)

    def test_skips_duplicates(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[rss]\nurl1 = https://old.com/rss\n")
            config_path = Path(f.name)

        try:
            feeds = [{"title": "Old", "url": "https://old.com/rss"}]
            result = import_feeds_to_config(feeds, config_path)
            assert result["added"] == 0
            assert result["skipped"] == 1
        finally:
            config_path.unlink(missing_ok=True)

    def test_handles_empty_opml(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".ini", delete=False) as f:
            f.write("[rss]\nurl1 = https://x.com/rss\n")
            config_path = Path(f.name)

        try:
            result = import_feeds_to_config([], config_path)
            assert result["added"] == 0
        finally:
            config_path.unlink(missing_ok=True)
