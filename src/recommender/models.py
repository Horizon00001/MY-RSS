"""Data models for recommendation system."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Article(BaseModel):
    """Article model with ID for recommendation."""

    id: str = Field(description="Unique article identifier (hash of link)")
    title: str = ""
    link: str = ""
    summary: str = ""
    content: str = ""
    source: str = ""  # feed URL
    source_name: str = ""  # feed title
    date: Optional[datetime] = None
    tags: list[str] = Field(default_factory=list)
    embedding: Optional[list[float]] = None  # TF-IDF vector representation


class UserInteraction(BaseModel):
    """Single user interaction with an article."""

    article_id: str
    action: str = Field(description="view, bookmark, share, skip")
    weight: float = 1.0
    timestamp: datetime = Field(default_factory=datetime.now)


class UserPreferences(BaseModel):
    """User preferences computed from interactions."""

    user_id: str
    tag_weights: dict[str, float] = Field(default_factory=dict)
    interest_sources: dict[str, float] = Field(default_factory=dict)


class ReadingHistory(BaseModel):
    """Complete reading history for a user."""

    user_id: str
    interactions: list[UserInteraction] = Field(default_factory=list)

    def add_interaction(self, article_id: str, action: str, weight: float = 1.0):
        """Add an interaction."""
        self.interactions.append(
            UserInteraction(article_id=article_id, action=action, weight=weight)
        )


class RecommendationRequest(BaseModel):
    """Request for recommendations."""

    user_id: str
    top_k: int = Field(default=20, ge=1, le=100)
    include_exploration: bool = True  # Include content from curated pool


class RecommendationResponse(BaseModel):
    """Response with recommended articles."""

    articles: list[Article]
    refreshed_at: datetime
