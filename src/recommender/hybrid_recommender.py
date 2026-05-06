"""Hybrid recommender combining content-based and collaborative filtering."""

from datetime import datetime, timedelta, timezone
from typing import Optional

from ..article_identity import compute_article_id
from .behavior_tracker import BehaviorTracker
from .collaborative import CollaborativeRecommender
from .models import Article
from .realtime import RealtimeCollaborativeFilter
from .tfidf import TFIDFRecommender


BEIJING_TZ = timezone(timedelta(hours=8))
DEFAULT_RECOMMENDER_DAYS = 7
DEFAULT_RECOMMENDER_LIMIT = 1000


def parse_db_datetime(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
    else:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=BEIJING_TZ)
    return parsed.astimezone(timezone.utc)

class HybridRecommender:
    """
    Hybrid recommender combining:
    - TF-IDF content similarity (content-based)
    - Collaborative filtering (user-based)

    Uses weighted combination with configurable alpha.
    """

    def __init__(
        self,
        tracker: Optional[BehaviorTracker] = None,
        alpha: float = 0.6,
        curated_pool_size: int = 100,
    ):
        """
        Args:
            alpha: Weight for content-based scores (0-1).
                  Higher = more content similarity, lower = more collaborative.
            curated_pool_size: Number of articles in curated pool.
        """
        self.tracker = tracker or BehaviorTracker()
        self.tfidf = TFIDFRecommender()
        self.collaborative = CollaborativeRecommender(self.tracker)
        self.realtime_cf = RealtimeCollaborativeFilter(self.tracker)
        self.alpha = alpha  # Weight for content-based
        self.curated_pool_size = curated_pool_size

        self.articles: dict[str, Article] = {}
        self.curated_articles: list[Article] = []
        self.last_refresh: Optional[datetime] = None
        self._is_built = False

    def _compute_article_id(self, link: str) -> str:
        """Generate a deterministic ID from article link."""
        return compute_article_id(link)

    def add_article(self, article: Article):
        """Add an article to the recommendation pool."""
        if not article.id:
            article.id = self._compute_article_id(article.link)
        self.articles[article.id] = article

    def add_curated_article(self, article: Article):
        """Add an article to the curated (premium) pool."""
        if not article.id:
            article.id = self._compute_article_id(article.link)
        self.articles[article.id] = article
        self.curated_articles.append(article)

    def load_articles_from_db(self, days: int = DEFAULT_RECOMMENDER_DAYS, limit: int = DEFAULT_RECOMMENDER_LIMIT):
        """Load articles from database."""
        from ..database import get_recent_articles

        rows = get_recent_articles(limit=limit, days=days)
        self.articles.clear()
        for row in rows:
            article = Article(
                id=row["id"],
                title=row["title"] or "",
                link=row["link"] or "",
                summary=row["summary"] or "",
                content=row["content"] or "",
                source=row["source"] or "",
                source_name=row["source_name"] or "",
                date=parse_db_datetime(row["published_at"]),
                tags=row.get("tags") or [],
            )
            self.articles[article.id] = article

    def build(self):
        """Build the recommendation index."""
        # Build TF-IDF index from all articles
        all_articles = list(self.articles.values())
        self.tfidf.build_index(all_articles)

        # Build collaborative filtering model
        self.collaborative.build_model()

        # Build realtime collaborative filter
        self.realtime_cf.build()

        self._is_built = True
        self.last_refresh = datetime.now(timezone.utc)

    def refresh_if_needed(self, interval_minutes: int = 30) -> bool:
        """Check if index needs refresh and rebuild if needed."""
        if not self._is_built:
            self.load_articles_from_db()
            self.build()
            return True

        if self.last_refresh is None:
            self.load_articles_from_db()
            self.build()
            return True

        elapsed = datetime.now(timezone.utc) - self.last_refresh
        if elapsed > timedelta(minutes=interval_minutes):
            self.load_articles_from_db()
            self.build()
            return True

        return False

    def recommend(
        self,
        user_id: str,
        top_k: int = 20,
        include_curated: bool = True,
        exclude_interacted: bool = True,
        realtime: bool = True,
    ) -> list[Article]:
        """
        Get recommendations for a user.

        Args:
            user_id: User ID
            top_k: Number of recommendations
            include_curated: Include curated pool articles
            exclude_interacted: Exclude articles user has already seen
            realtime: Use realtime collaborative filtering for immediate updates

        Returns:
            List of recommended articles
        """
        # Ensure model is built
        if not self._is_built:
            self.build()

        # Get user's interacted articles
        user_interactions = self.tracker.get_user_interactions(user_id, limit=500)
        interacted_ids = {i.article_id for i in user_interactions}

        # Get user's preferences (sources they like)
        user_prefs = self.tracker.get_user_preferences(user_id)

        # Score candidates from multiple sources
        candidates: dict[str, float] = {}

        # 1. Content-based scores (TF-IDF)
        if interacted_ids:
            content_scores = self.tfidf.score_articles_for_user(
                list(interacted_ids), top_k=top_k * 3
            )
            for article_id, score in content_scores:
                candidates[article_id] = candidates.get(article_id, 0) + score * self.alpha

        # 2. Collaborative filtering scores (batch model)
        collab_scores = self.collaborative.recommend_for_user(
            user_id, top_k=top_k * 3
        )
        for article_id, score in collab_scores:
            candidates[article_id] = (
                candidates.get(article_id, 0) + score * (1 - self.alpha)
            )

        # 3. Realtime collaborative filtering (immediate updates)
        if realtime and self._is_built:
            candidate_articles = list(self.articles.values())
            realtime_scores = self.realtime_cf.recommend_for_user(
                user_id, candidate_articles, top_k=top_k * 2
            )
            for article, score in realtime_scores:
                candidates[article.id] = (
                    candidates.get(article.id, 0) + score * 0.5
                )

        # 4. Curated pool boost (for exploration)
        if include_curated and self.curated_articles:
            for article in self.curated_articles[:self.curated_pool_size]:
                if article.id not in candidates:
                    # Boost based on user preferences
                    boost = 0.5
                    if article.source in user_prefs:
                        boost += user_prefs[article.source] * 0.1
                    candidates[article.id] = candidates.get(article.id, 0) + boost

        # 5. Popularity/recency boost (fallback for cold start)
        for article in list(self.articles.values())[:50]:
            if article.id not in candidates and article.date:
                days_old = (datetime.now(timezone.utc) - article.date).days
                if days_old < 1:  # Recent articles get a boost
                    candidates[article.id] = candidates.get(article.id, 0) + 0.3

        # Filter and sort candidates
        if exclude_interacted:
            candidates = {k: v for k, v in candidates.items() if k not in interacted_ids}

        sorted_ids = sorted(candidates.items(), key=lambda x: x[1], reverse=True)
        results = []
        for article_id, score in sorted_ids[:top_k]:
            if article_id in self.articles:
                results.append(self.articles[article_id])

        return results

    def record_interaction(
        self, user_id: str, article_id: str, action: str = "view"
    ):
        """Record a user interaction and update realtime model immediately."""
        self.tracker.record(user_id, article_id, action)
        # Immediately update realtime collaborative filter
        if self._is_built:
            self.realtime_cf.update_user(user_id)

    def handle_negative_feedback(
        self, user_id: str, article_id: str, article: Optional[Article] = None
    ):
        """
        Handle "not interested" feedback.
        Lowers weight for similar articles and same source.
        """
        # Record the negative interaction
        self.tracker.record(user_id, article_id, "not_interested")

        # Find similar articles to deprioritize
        if self._is_built and article:
            similar = self.tfidf.find_similar(article_id, top_k=10)
            for similar_id, _ in similar:
                self.tracker.record(user_id, similar_id, "skip")

            # Also deprioritize same source
            if article.source:
                source_prefs = self.tracker.get_user_preferences(user_id)
                if article.source in source_prefs:
                    # Reduce source weight
                    current = source_prefs[article.source]
                    # This would need a method to update weights directly
                    # For now just record the skip
                    pass
