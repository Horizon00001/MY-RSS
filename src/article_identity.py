"""Shared article link normalization and ID generation."""

import hashlib
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


TRACKING_QUERY_PREFIXES = ("utm_",)
TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "igshid",
    "mc_cid",
    "mc_eid",
}


def normalize_article_link(link: str) -> str:
    """Normalize article URLs so tracking variants deduplicate correctly."""
    if not link:
        return ""

    parsed = urlsplit(link.strip())
    scheme = "https" if parsed.scheme in {"http", "https"} else parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.rstrip("/") or parsed.path
    query = urlencode(
        [
            (key, value)
            for key, value in parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith(TRACKING_QUERY_PREFIXES)
            and key.lower() not in TRACKING_QUERY_KEYS
        ],
        doseq=True,
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def compute_article_id(link: str) -> str:
    """Generate a stable article ID from the normalized link."""
    stable_link = normalize_article_link(link) or (link or "").strip()
    return hashlib.md5(stable_link.encode()).hexdigest()[:12]
