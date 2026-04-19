"""User behavior tracking for recommendations using database."""

from collections import defaultdict
from datetime import datetime, timezone
from typing import Optional

from ..database import record_interaction as db_record_interaction, get_db
from .models import UserInteraction


class BehaviorTracker:
    """Tracks user reading behavior and computes preferences."""

    ACTION_WEIGHTS = {
        "view": 1.0,
        "read_time": 2.0,
        "bookmark": 3.0,
        "share": 4.0,
        "skip": -1.0,
        "not_interested": -2.0,
    }

    def __init__(self, history_file: str = None):  # Kept for backwards compatibility
        pass

    def record(
        self, user_id: str, article_id: str, action: str, weight: Optional[float] = None
    ):
        """Record a user interaction to database."""
        if weight is None:
            weight = self.ACTION_WEIGHTS.get(action, 1.0)
        db_record_interaction(user_id, article_id, action, weight)

    def get_user_preferences(self, user_id: str) -> dict[str, float]:
        """Get tag weights for a user based on their interactions."""
        tag_weights: dict[str, float] = defaultdict(float)

        interactions = self.get_user_interactions(user_id, limit=500)
        for interaction in interactions:
            # Aggregate by source as a proxy for interests
            source = self._get_article_source(interaction.article_id)
            if source:
                tag_weights[source] += interaction.weight

        return dict(tag_weights)

    def _get_article_source(self, article_id: str) -> Optional[str]:
        """Get article source from database."""
        from ..database import get_article
        article = get_article(article_id)
        if article:
            return article.get("source", "")
        return None

    def get_user_interactions(
        self, user_id: str, limit: int = 100
    ) -> list[UserInteraction]:
        """Get recent interactions for a user from database."""
        from ..database import get_user_interactions as db_get_interactions

        rows = db_get_interactions(user_id, limit)
        return [
            UserInteraction(
                article_id=row["article_id"],
                action=row["action"],
                weight=row["weight"],
                timestamp=datetime.fromisoformat(row["created_at"]) if isinstance(row["created_at"], str) else row["created_at"],
            )
            for row in rows
        ]

    def get_all_user_ids(self) -> list[str]:
        """Get all user IDs with history from database."""
        # This requires a more complex query; for now return empty
        # In production, add a query to get distinct user_ids
        return []
