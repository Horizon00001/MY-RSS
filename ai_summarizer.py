"""AI summarizer for RSS entries - backward compatibility wrapper."""

from src.summarizer import Summarizer as _Summarizer, SUMMARIZE_PROMPT

# Re-export for backward compatibility
RSSSummarizer = _Summarizer
__all__ = ["RSSSummarizer", "SUMMARIZE_PROMPT"]
