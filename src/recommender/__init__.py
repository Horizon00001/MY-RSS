"""Recommendation system module."""

from .behavior_tracker import BehaviorTracker
from .models import (
    Article,
    RecommendationRequest,
    RecommendationResponse,
    UserInteraction,
    UserPreferences,
)


def __getattr__(name: str):
    if name == "CollaborativeRecommender":
        from .collaborative import CollaborativeRecommender

        return CollaborativeRecommender
    if name == "HybridRecommender":
        from .hybrid_recommender import HybridRecommender

        return HybridRecommender
    if name == "RealtimeCollaborativeFilter":
        from .realtime import RealtimeCollaborativeFilter

        return RealtimeCollaborativeFilter
    if name == "TFIDFRecommender":
        from .tfidf import TFIDFRecommender

        return TFIDFRecommender
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

__all__ = [
    "Article",
    "BehaviorTracker",
    "CollaborativeRecommender",
    "HybridRecommender",
    "RealtimeCollaborativeFilter",
    "RecommendationRequest",
    "RecommendationResponse",
    "TFIDFRecommender",
    "UserInteraction",
    "UserPreferences",
]
