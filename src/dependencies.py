"""FastAPI dependency factories for the RSS API."""

from typing import Optional

from fastapi import FastAPI, Request

from .config import settings
from .fetcher import Fetcher
from .feed_parser import FeedParser
from .state_manager import StateManager
from .summarizer import Summarizer

_app: FastAPI | None = None


def set_app(app: FastAPI) -> None:
    global _app
    _app = app


def get_state_manager() -> StateManager:
    return StateManager(settings.state_file)


def get_feed_parser() -> FeedParser:
    return FeedParser()


def get_fetcher(request: Request = None) -> Fetcher:
    app = request.app if request is not None else _app
    if app is None:
        raise RuntimeError("FastAPI app has not been initialized")
    return app.state.fetcher


def get_summarizer() -> Optional[Summarizer]:
    try:
        return Summarizer()
    except ValueError:
        return None
