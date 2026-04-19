"""Collaborative filtering for recommendations."""

import json
from pathlib import Path
from typing import Optional

import numpy as np

try:
    from sklearn.neighbors import NearestNeighbors
except ImportError:
    NearestNeighbors = None

from .behavior_tracker import BehaviorTracker
from .models import Article


class CollaborativeRecommender:
    """User-based collaborative filtering recommender."""

    def __init__(self, tracker: BehaviorTracker, n_neighbors: int = 10):
        if NearestNeighbors is None:
            raise ImportError("sklearn is required: pip install scikit-learn")

        self.tracker = tracker
        self.n_neighbors = n_neighbors
        self.user_item_matrix: Optional[np.ndarray] = None
        self.user_ids: list[str] = []
        self.article_ids: list[str] = []
        self.user_article_map: dict[str, dict[str, float]] = {}

    def _load_user_data(self):
        """Load user interaction data from tracker."""
        self.user_ids = self.tracker.get_all_user_ids()
        self.user_article_map = {}

        # Build article ID list from all users
        all_articles = set()
        for user_id in self.user_ids:
            interactions = self.tracker.get_user_interactions(user_id, limit=1000)
            article_weights: dict[str, float] = {}
            for interaction in interactions:
                article_weights[interaction.article_id] = interaction.weight
            self.user_article_map[user_id] = article_weights
            all_articles.update(article_weights.keys())

        self.article_ids = list(all_articles)
        return len(self.article_ids) > 0 and len(self.user_ids) > 1

    def build_model(self):
        """Build the collaborative filtering model."""
        if not self._load_user_data():
            return

        n_users = len(self.user_ids)
        n_articles = len(self.article_ids)

        if n_users < 2 or n_articles == 0:
            return

        # Create user-article matrix
        self.user_item_matrix = np.zeros((n_users, n_articles))

        article_to_idx = {aid: i for i, aid in enumerate(self.article_ids)}

        for user_idx, user_id in enumerate(self.user_ids):
            for article_id, weight in self.user_article_map[user_id].items():
                if article_id in article_to_idx:
                    self.user_item_matrix[user_idx, article_to_idx[article_id]] = weight

    def recommend_for_user(
        self, user_id: str, top_k: int = 20
    ) -> list[tuple[str, float]]:
        """
        Recommend articles for a user based on similar users' preferences.

        Returns list of (article_id, score) tuples.
        """
        if self.user_item_matrix is None:
            self.build_model()

        if self.user_item_matrix is None:
            return []

        try:
            user_idx = self.user_ids.index(user_id)
        except ValueError:
            # New user - return empty (will fall back to popularity)
            return []

        # Find similar users using k-NN
        model = NearestNeighbors(
            metric="cosine", algorithm="brute", n_neighbors=min(self.n_neighbors, len(self.user_ids))
        )
        model.fit(self.user_item_matrix)

        distances, indices = model.kneighbors(
            self.user_item_matrix[user_idx].reshape(1, -1)
        )

        # Aggregate scores from similar users
        article_scores: dict[str, float] = {}

        for neighbor_idx, distance in zip(indices[0], distances[0]):
            if distance == 0:
                continue
            similarity = 1 - distance
            neighbor_id = self.user_ids[neighbor_idx]

            for article_id, weight in self.user_article_map[neighbor_id].items():
                if article_id not in self.user_article_map.get(user_id, {}):
                    # User hasn't interacted with this article
                    article_scores[article_id] = (
                        article_scores.get(article_id, 0) + similarity * weight
                    )

        # Sort by score
        sorted_articles = sorted(
            article_scores.items(), key=lambda x: x[1], reverse=True
        )

        return sorted_articles[:top_k]

    def get_similar_users(self, user_id: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Find users most similar to the given user."""
        if self.user_item_matrix is None:
            self.build_model()

        if self.user_item_matrix is None:
            return []

        try:
            user_idx = self.user_ids.index(user_id)
        except ValueError:
            return []

        model = NearestNeighbors(
            metric="cosine", algorithm="brute", n_neighbors=min(top_k + 1, len(self.user_ids))
        )
        model.fit(self.user_item_matrix)

        distances, indices = model.kneighbors(
            self.user_item_matrix[user_idx].reshape(1, -1)
        )

        similar_users = []
        for neighbor_idx, distance in zip(indices[0], distances[0]):
            if neighbor_idx != user_idx:
                similar_users.append((self.user_ids[neighbor_idx], 1 - distance))

        return similar_users[:top_k]
