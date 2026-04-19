"""Real-time collaborative filtering with incremental updates."""

from datetime import datetime, timezone
from typing import Optional

import numpy as np

try:
    from sklearn.neighbors import NearestNeighbors
except ImportError:
    NearestNeighbors = None

from .behavior_tracker import BehaviorTracker
from .models import Article


class RealtimeCollaborativeFilter:
    """
    Real-time collaborative filtering that updates recommendations
    immediately when user interactions are recorded.

    Instead of rebuilding the entire model, we incrementally update
    user vectors and trigger targeted re-ranking.
    """

    def __init__(
        self,
        tracker: BehaviorTracker,
        n_neighbors: int = 10,
        score_decay: float = 0.9,
    ):
        """
        Args:
            tracker: Behavior tracker instance
            n_neighbors: Number of similar users to consider
            score_decay: Decay factor for older interactions
        """
        if NearestNeighbors is None:
            raise ImportError("sklearn is required")

        self.tracker = tracker
        self.n_neighbors = n_neighbors
        self.score_decay = score_decay

        self.user_vectors: dict[str, np.ndarray] = {}
        self.article_ids: list[str] = []
        self.article_scores: dict[str, float] = {}  # Popularity scores
        self.last_update: Optional[datetime] = None
        self._is_built = False

    def build(self):
        """Build user vectors from interaction history."""
        self.user_vectors.clear()
        self.article_scores.clear()

        user_ids = self.tracker.get_all_user_ids()

        # Collect all article IDs
        all_articles: set[str] = set()
        for user_id in user_ids:
            interactions = self.tracker.get_user_interactions(user_id, limit=1000)
            for i in interactions:
                all_articles.add(i.article_id)

        self.article_ids = list(all_articles)
        if not self.article_ids:
            return

        article_to_idx = {aid: i for i, aid in enumerate(self.article_ids)}
        n_articles = len(self.article_ids)

        # Build user vectors
        for user_id in user_ids:
            interactions = self.tracker.get_user_interactions(user_id, limit=1000)
            vector = np.zeros(n_articles)

            for interaction in interactions:
                if interaction.article_id in article_to_idx:
                    # Apply time decay
                    ts = interaction.timestamp
                    if ts.tzinfo is None:
                        ts = ts.replace(tzinfo=timezone.utc)
                    days_old = (
                        datetime.now(timezone.utc) - ts
                    ).days
                    decay = self.score_decay ** days_old
                    vector[article_to_idx[interaction.article_id]] = (
                        interaction.weight * decay
                    )

            self.user_vectors[user_id] = vector

        # Compute article popularity scores
        for user_vector in self.user_vectors.values():
            for idx, score in enumerate(user_vector):
                if score > 0:
                    aid = self.article_ids[idx]
                    self.article_scores[aid] = (
                        self.article_scores.get(aid, 0) + score
                    )

        self._is_built = True
        self.last_update = datetime.now(timezone.utc)

    def update_user(self, user_id: str):
        """
        Update a single user's vector after new interaction.
        More efficient than full rebuild.
        """
        if not self._is_built or user_id not in self.user_vectors:
            self.build()
            return

        # Get user's latest interactions
        interactions = self.tracker.get_user_interactions(user_id, limit=100)
        article_to_idx = {aid: i for i, aid in enumerate(self.article_ids)}

        vector = np.zeros(len(self.article_ids))
        for interaction in interactions:
            if interaction.article_id in article_to_idx:
                ts = interaction.timestamp
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                days_old = (
                    datetime.now(timezone.utc) - ts
                ).days
                decay = self.score_decay ** days_old
                vector[article_to_idx[interaction.article_id]] = (
                    interaction.weight * decay
                )

        self.user_vectors[user_id] = vector

        # Update article popularity
        self.article_scores[interaction.article_id] = (
            self.article_scores.get(interaction.article_id, 0)
            + interaction.weight
        )

        self.last_update = datetime.now(timezone.utc)

    def recommend_for_user(
        self,
        user_id: str,
        candidate_articles: list[Article],
        top_k: int = 20,
    ) -> list[tuple[Article, float]]:
        """
        Get real-time recommendations for a user.

        Args:
            user_id: User ID
            candidate_articles: List of candidate articles to rank
            top_k: Number of recommendations

        Returns:
            List of (article, score) tuples
        """
        if not self._is_built:
            self.build()

        # Ensure user exists
        if user_id not in self.user_vectors:
            self.user_vectors[user_id] = np.zeros(len(self.article_ids))

        user_vector = self.user_vectors[user_id]

        # Build article index for candidates
        article_to_global_idx = {
            aid: i for i, aid in enumerate(self.article_ids)
        }

        scored = []
        for article in candidate_articles:
            aid = article.id

            # Get popularity score (normalized)
            popularity = self.article_scores.get(aid, 0)
            max_popularity = max(self.article_scores.values()) if self.article_scores else 1
            popularity_score = popularity / max_popularity if max_popularity > 0 else 0

            # Compute similarity score with user
            if aid in article_to_global_idx:
                idx = article_to_global_idx[aid]
                interaction_score = float(user_vector[idx]) if len(user_vector) > idx else 0
            else:
                interaction_score = 0

            # Combined score
            combined_score = 0.6 * interaction_score + 0.4 * popularity_score

            # Boost for recency
            if article.date:
                days_old = (datetime.now(timezone.utc) - article.date).days
                if days_old < 1:
                    combined_score += 0.3
                elif days_old < 3:
                    combined_score += 0.1

            scored.append((article, combined_score))

        # Sort by score
        scored.sort(key=lambda x: x[1], reverse=True)

        return scored[:top_k]

    def get_similar_users(self, user_id: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Find users most similar to the given user."""
        if not self._is_built or user_id not in self.user_vectors:
            return []

        if len(self.user_vectors) < 2:
            return []

        user_vector = self.user_vectors[user_id]
        other_users = [
            (uid, vec)
            for uid, vec in self.user_vectors.items()
            if uid != user_id
        ]

        if not other_users:
            return []

        # Compute cosine similarity
        similarities = []
        for other_id, other_vector in other_users:
            norm1 = np.linalg.norm(user_vector)
            norm2 = np.linalg.norm(other_vector)

            if norm1 > 0 and norm2 > 0:
                similarity = np.dot(user_vector, other_vector) / (norm1 * norm2)
                similarities.append((other_id, float(similarity)))

        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:top_k]
