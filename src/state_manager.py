"""State management for incremental updates."""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

BEIJING_TZ = timezone(timedelta(hours=8))


class StateManager:
    """Manages fetch state for incremental updates."""

    def __init__(self, state_file: Path):
        self.state_file = state_file
        self._state: dict = {}
        self._load()

    def _load(self) -> None:
        if self.state_file.exists():
            with open(self.state_file, "r", encoding="utf-8") as f:
                self._state = json.load(f)

    def _save(self) -> None:
        with open(self.state_file, "w", encoding="utf-8") as f:
            json.dump(self._state, f, ensure_ascii=False, indent=2)

    @property
    def last_fetch(self) -> datetime | None:
        """Get last fetch time as Beijing timezone datetime."""
        last_fetch_str = self._state.get("last_fetch")
        if not last_fetch_str:
            return None
        cleaned = last_fetch_str.replace(" (北京时间)", "")
        dt = datetime.strptime(cleaned, "%Y-%m-%d %H:%M:%S")
        return dt.replace(tzinfo=BEIJING_TZ)

    def update_last_fetch(self, dt: datetime | None = None) -> str:
        """Update last fetch time, returns formatted string."""
        if dt is None:
            dt = datetime.now(BEIJING_TZ)
        self._state["last_fetch"] = dt.strftime("%Y-%m-%d %H:%M:%S (北京时间)")
        self._save()
        return self._state["last_fetch"]

    def reset(self) -> None:
        """Clear state."""
        self._state = {}
        self._save()
