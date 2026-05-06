"""Compatibility helpers for legacy src.api patch targets."""

import sys
from typing import Any


def api_attr(name: str, fallback: Any) -> Any:
    api_module = sys.modules.get("src.api")
    if api_module is not None and hasattr(api_module, name):
        return getattr(api_module, name)
    return fallback
