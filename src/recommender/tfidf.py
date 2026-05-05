"""TF-IDF based content similarity for recommendations."""

from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np

from ..article_identity import compute_article_id

try:
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
except ImportError:
    TfidfVectorizer = None
    cosine_similarity = None

from .models import Article


class TFIDFRecommender:
    """Content-based recommender using TF-IDF vectors."""

    def __init__(
        self,
        max_features: int = 5000,
        min_df: int = 1,
        max_df: float = 0.95,
        n_neighbors: int = 5,
    ):
        if TfidfVectorizer is None:
            raise ImportError("sklearn is required: pip install scikit-learn")

        self.vectorizer = TfidfVectorizer(
            max_features=max_features,
            min_df=min_df,
            max_df=max_df,
            stop_words="english",
        )
        self.n_neighbors = n_neighbors
        self.article_matrix: Optional[np.ndarray] = None
        self.article_ids: list[str] = []
        self.article_dates: list[datetime] = []
        self._is_built = False

    def _compute_article_id(self, link: str) -> str:
        """Generate a deterministic ID from article link."""
        return compute_article_id(link)

    def _get_article_text(self, article: Article) -> str:
        """Combine title and content for vectorization."""
        return f"{article.title} {article.summary} {article.content}".strip()

    def build_index(self, articles: list[Article]):
        """Build TF-IDF index from articles."""
        if not articles:
            self._is_built = False
            return

        self.article_ids = []
        self.article_dates = []
        texts = []

        for article in articles:
            if not article.id:
                article.id = self._compute_article_id(article.link)
            self.article_ids.append(article.id)
            self.article_dates.append(article.date or datetime.now(timezone.utc))
            texts.append(self._get_article_text(article))

        # Handle edge case: single document
        if len(texts) == 1:
            self._is_built = True
            self.article_matrix = None
            return

        # For small corpora, use more lenient settings
        if len(texts) <= 5:
            # Use no min_df restriction for small corpora
            self.vectorizer = TfidfVectorizer(
                max_features=1000,
                min_df=1,
                max_df=1.0,
                stop_words="english",
                token_pattern=r"(?u)\b\w+\b",  # Single char tokens
            )
        else:
            self.vectorizer = TfidfVectorizer(
                max_features=min(5000, len(texts) * 100),
                min_df=1,
                max_df=0.95,
                stop_words="english",
            )

        try:
            self.article_matrix = self.vectorizer.fit_transform(texts)
        except ValueError:
            # Fallback: use raw count vectors
            from sklearn.feature_extraction.text import CountVectorizer

            self.vectorizer = CountVectorizer(max_features=100, min_df=1)
            self.article_matrix = self.vectorizer.fit_transform(texts)

        self._is_built = True

    def find_similar(
        self, article_id: str, top_k: int = 5, within_hours: Optional[int] = None
    ) -> list[tuple[str, float]]:
        """
        Find articles similar to the given article.

        Args:
            article_id: ID of the reference article
            top_k: Number of similar articles to return
            within_hours: If set, only consider articles published within this window

        Returns:
            List of (article_id, similarity_score) tuples
        """
        if not self._is_built or self.article_matrix is None:
            return []

        try:
            idx = self.article_ids.index(article_id)
        except ValueError:
            return []

        vector = self.article_matrix[idx]
        scores = cosine_similarity(vector, self.article_matrix)[0]

        # Get indices sorted by score (descending), excluding self
        sorted_indices = np.argsort(scores)[::-1]
        # Filter out self
        sorted_indices = [i for i in sorted_indices if i != idx]

        # Optionally filter by time
        if within_hours:
            ref_date = self.article_dates[idx]
            cutoff = ref_date - timedelta(hours=within_hours)
            sorted_indices = [
                i for i in sorted_indices if self.article_dates[i] > cutoff
            ]

        results = []
        for i in sorted_indices[:top_k]:
            results.append((self.article_ids[i], float(scores[i])))

        return results

    def score_articles_for_user(
        self, user_article_ids: list[str], top_k: int = 20
    ) -> list[tuple[str, float]]:
        """
        Score all articles based on similarity to articles user has interacted with.

        Returns articles most similar to user's interest profile.
        """
        if not self._is_built or not user_article_ids:
            return []

        # Aggregate vectors of user's articles
        user_indices = [
            i for i, aid in enumerate(self.article_ids) if aid in user_article_ids
        ]

        if not user_indices:
            return []

        # Average vector of user's articles
        user_profile = np.asarray(self.article_matrix[user_indices].mean(axis=0))

        # Compute similarity of all articles to user profile
        scores = cosine_similarity(user_profile, self.article_matrix)[0]

        # Exclude articles user has already interacted with
        for idx in user_indices:
            scores[idx] = -1

        # Sort and return top k
        sorted_indices = np.argsort(scores)[::-1]

        results = []
        for i in sorted_indices[:top_k]:
            if scores[i] > 0:
                results.append((self.article_ids[i], float(scores[i])))

        return results

    def dedupe_by_similarity(
        self, article_ids: list[str], threshold: float = 0.8
    ) -> list[str]:
        """
        Remove duplicate articles based on content similarity.

        Keeps the first article in each similar group.
        """
        if not self._is_built or not article_ids:
            return article_ids

        # Get indices for given articles
        id_to_idx = {aid: i for i, aid in enumerate(self.article_ids)}
        indices = [id_to_idx[aid] for aid in article_ids if aid in id_to_idx]

        if not indices:
            return article_ids

        # Compute similarity matrix for these articles
        subset_matrix = self.article_matrix[indices]
        sim_matrix = cosine_similarity(subset_matrix)

        n = len(indices)
        to_remove = set()

        for i in range(n):
            for j in range(i + 1, n):
                if sim_matrix[i, j] > threshold:
                    # Keep earlier (i), remove later (j)
                    to_remove.add(j)

        result = [
            article_ids[i]
            for i in range(len(article_ids))
            if i not in to_remove or article_ids[i] not in id_to_idx
        ]

        return result
