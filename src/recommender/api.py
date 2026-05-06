"""FastAPI routes for recommendation system."""

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any, Optional

from fastapi import APIRouter, HTTPException, Query

from ..article_identity import compute_article_id, normalize_article_link
from ..config import settings
from ..fetcher import Fetcher
from ..feed_parser import FeedParser
from ..models import RSSEntry
from .behavior_tracker import BehaviorTracker

if TYPE_CHECKING:
    from .hybrid_recommender import HybridRecommender
    from .models import Article

BEIJING_TZ = timezone(timedelta(hours=8))

router = APIRouter(prefix="/recommend", tags=["recommendations"])

# Global instances
feed_parser = FeedParser()
tracker = BehaviorTracker(settings.reading_history_file)
recommender: Optional["HybridRecommender"] = None


def _recommendation_unavailable(exc: ImportError) -> HTTPException:
    return HTTPException(
        status_code=503,
        detail="Recommendation system is unavailable because optional dependencies are missing",
    )


def _load_hybrid_recommender_class():
    try:
        from .hybrid_recommender import HybridRecommender
    except ImportError as exc:
        raise _recommendation_unavailable(exc) from exc

    return HybridRecommender


def _load_recommender_defaults() -> tuple[int, int]:
    try:
        from .hybrid_recommender import DEFAULT_RECOMMENDER_DAYS, DEFAULT_RECOMMENDER_LIMIT
    except ImportError as exc:
        raise _recommendation_unavailable(exc) from exc

    return DEFAULT_RECOMMENDER_DAYS, DEFAULT_RECOMMENDER_LIMIT


def get_recommender() -> "HybridRecommender":
    """Get or create recommender instance."""
    global recommender
    if recommender is None:
        HybridRecommender = _load_hybrid_recommender_class()
        try:
            recommender = HybridRecommender(
                tracker=tracker,
                alpha=0.6,
                curated_pool_size=100,
            )
        except ImportError as exc:
            raise _recommendation_unavailable(exc) from exc
    return recommender


def format_entry(entry: dict) -> RSSEntry:
    """Convert raw entry dict to RSSEntry model."""
    entry_date = feed_parser.get_entry_date(type("E", (), entry)())
    return RSSEntry(
        title=entry.get("title", ""),
        link=entry.get("link", ""),
        summary=entry.get("summary", ""),
        date=entry_date.strftime("%Y-%m-%d %H:%M:%S (北京时间)") if entry_date else None,
        content=entry.get("content", ""),
        ai_summary=entry.get("ai_summary", ""),
    )


def article_from_entry(entry: dict) -> "Article":
    """Convert raw entry to Article model."""
    from .models import Article

    entry_date = feed_parser.get_entry_date(type("E", (), entry)())
    link = entry.get("link", "")
    normalized_link = normalize_article_link(link)
    return Article(
        id=compute_article_id(normalized_link or link),
        title=entry.get("title", ""),
        link=link,
        summary=entry.get("summary", ""),
        content=entry.get("content", ""),
        source=entry.get("source", ""),
        source_name=entry.get("feed_title", ""),
        date=entry_date,
    )


@router.get("/", response_model=None, summary="获取推荐内容")
async def get_recommendations(
    user_id: str = Query(default="default", description="用户ID"),
    top_k: int = Query(default=20, ge=1, le=100, description="返回数量"),
    force_refresh: bool = Query(default=False, description="强制刷新"),
):
    """
    Get personalized recommendations for a user.

    - **user_id**: User identifier
    - **top_k**: Number of recommendations to return
    - **force_refresh**: Force rebuild of recommendation index
    """
    rec = get_recommender()

    # Refresh if needed (every 30 minutes)
    if force_refresh or rec.refresh_if_needed(interval_minutes=30):
        await refresh_recommender(rec)

    articles = rec.recommend(user_id=user_id, top_k=top_k)

    from .models import RecommendationResponse

    return RecommendationResponse(
        articles=articles,
        refreshed_at=rec.last_refresh or datetime.now(BEIJING_TZ),
    )


@router.post(
    "/articles/{article_id}/feedback",
    summary="记录用户反馈",
)
async def record_feedback(
    article_id: str,
    user_id: str = Query(default="default", description="用户ID"),
    action: str = Query(default="view", description="动作: view, bookmark, skip, not_interested"),
):
    """
    Record user interaction with an article.

    - **view**: User viewed the article
    - **bookmark**: User bookmarked the article
    - **skip**: User quickly skipped (negative signal)
    - **not_interested**: User explicitly not interested
    """
    valid_actions = ["view", "bookmark", "share", "skip", "not_interested"]
    if action not in valid_actions:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid action. Must be one of: {valid_actions}",
        )

    rec = get_recommender()
    rec.record_interaction(user_id, article_id, action)

    return {"status": "ok", "action": action, "article_id": article_id}


@router.post("/refresh", summary="强制刷新推荐索引")
async def refresh_index(
    user_id: str = Query(default="default", description="用户ID"),
):
    """Force refresh of the recommendation index."""
    rec = get_recommender()
    await refresh_recommender(rec)
    return {"status": "refreshed", "last_refresh": rec.last_refresh}


async def refresh_recommender(rec: "HybridRecommender"):
    """Refresh recommender with latest articles from DB."""
    recommender_days, recommender_limit = _load_recommender_defaults()

    # Load articles from database
    rec.load_articles_from_db(days=recommender_days, limit=recommender_limit)

    # If no articles in DB, fetch and store
    if not rec.articles:
        now = datetime.now(BEIJING_TZ)
        cutoff = now - timedelta(days=2)  # Last 2 days

        urls = list(settings.rss_feeds.values())

        async with Fetcher() as fetcher:
            async for entry in fetcher.fetch_all(urls):
                entry_date = feed_parser.get_entry_date(entry)
                if entry_date and entry_date > cutoff:
                    article = article_from_entry(entry)
                    rec.add_article(article)
                    store_article_to_db(article)

    # Rebuild index
    rec.build()


def store_article_to_db(article: Any):
    """Store article to database."""
    from ..database import store_article as db_store_article

    db_store_article(
        article_id=article.id,
        title=article.title,
        link=article.link,
        summary=article.summary,
        content=article.content,
        source=article.source,
        source_name=article.source_name,
        published_at=article.date,
        tags=article.tags,
        normalized_link=normalize_article_link(article.link),
    )


@router.get("/popular", summary="获取热门内容")
async def get_popular(
    top_k: int = Query(default=10, ge=1, le=50),
):
    """Get most popular articles (placeholder - based on recent articles)."""
    rec = get_recommender()
    if not rec._is_built:
        await refresh_recommender(rec)

    # Sort by date, return most recent
    articles = sorted(
        rec.articles.values(),
        key=lambda a: a.date or datetime.min.replace(tzinfo=BEIJING_TZ),
        reverse=True,
    )

    return {
        "articles": articles[:top_k],
        "refreshed_at": rec.last_refresh or datetime.now(BEIJING_TZ),
    }
