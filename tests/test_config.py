"""Tests for src/config.py."""

from pathlib import Path

from src.config import Settings


def test_loads_sources_json_before_config_ini(tmp_path: Path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "sources.json").write_text(
        """{
  "sources": [
    {"id": "json-feed", "url": "https://example.com/json.xml", "enabled": true},
    {"id": "disabled-feed", "url": "https://example.com/disabled.xml", "enabled": false}
  ],
  "filter": {"days": 3},
  "headers": {"user_agent": "json-agent"}
}
""",
        encoding="utf-8",
    )
    (tmp_path / "config.ini").write_text(
        """[rss]
ini-feed = https://example.com/ini.xml
""",
        encoding="utf-8",
    )

    settings = Settings(project_root=tmp_path)

    assert settings.rss_feeds == {"json-feed": "https://example.com/json.xml"}
    assert settings.default_days == 3
    assert settings.user_agent == "json-agent"


def test_falls_back_to_config_ini(tmp_path: Path):
    (tmp_path / "config.ini").write_text(
        """[rss]
ini-feed = https://example.com/ini.xml

[filter]
days = 5

[headers]
user_agent = ini-agent
""",
        encoding="utf-8",
    )

    settings = Settings(project_root=tmp_path)

    assert settings.rss_feeds == {"ini-feed": "https://example.com/ini.xml"}
    assert settings.default_days == 5
    assert settings.user_agent == "ini-agent"
