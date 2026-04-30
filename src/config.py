"""Configuration management - unified settings from .ini and .env."""

from pathlib import Path
from typing import Any, Optional

import configparser
from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from both .env and config.ini."""

    # API settings
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # RSS settings
    rss_feeds: dict[str, str] = Field(default_factory=dict)
    user_agent: str = "Mozilla/5.0 (compatible; MY-RSS Bot/1.0)"
    default_days: int = 7

    # AI summarizer settings
    api_key: str = ""
    api_url: str = "https://zenmux.ai/api/anthropic"
    model: str = "deepseek/deepseek-v4-pro-free"
    max_concurrent: int = 20

    # Paths
    project_root: Path = Field(default_factory=lambda: Path(__file__).parent.parent)
    state_file: Optional[Path] = None
    reading_history_file: Optional[Path] = None

    # Semaphore limit for concurrent fetches
    semaphore_limit: int = 20

    # Polling interval for background watcher (seconds)
    polling_interval_seconds: int = 300

    # Logging
    log_level: str = "INFO"

    # Database settings
    db_host: Optional[str] = None
    db_port: Optional[int] = None
    db_name: Optional[str] = None
    db_user: Optional[str] = None
    db_password: Optional[str] = None

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        extra = "ignore"

    def __init__(self, **kwargs: Any):
        super().__init__(**kwargs)
        if self.state_file is None:
            self.state_file = self.project_root / "fetch_state.json"
        if self.reading_history_file is None:
            self.reading_history_file = self.project_root / "reading_history.json"
        self._load_ini_config()

    def _load_ini_config(self) -> None:
        ini_path = self.project_root / "config.ini"
        if not ini_path.exists():
            return

        ini_config = configparser.ConfigParser()
        ini_config.read(ini_path, encoding="utf-8")

        # Load RSS feeds from [rss] section
        if "rss" in ini_config:
            self.rss_feeds = dict(ini_config["rss"])

        # Load filter settings
        if "filter" in ini_config and "days" in ini_config["filter"]:
            self.default_days = int(ini_config["filter"]["days"])

        # Load headers
        if "headers" in ini_config and "user_agent" in ini_config["headers"]:
            self.user_agent = ini_config["headers"]["user_agent"]


def load_settings() -> Settings:
    """Load settings with .env file."""
    env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path)
    return Settings()


settings = load_settings()
