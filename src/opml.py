"""OPML import/export for RSS feed subscriptions."""

import configparser
import logging
from pathlib import Path
from typing import Optional
from xml.etree import ElementTree as ET

from .config import settings

logger = logging.getLogger(__name__)

OPML_TITLE = "MY-RSS Feed Export"


def parse_opml(file_content: str | bytes) -> list[dict]:
    """
    Parse OPML XML and extract RSS feed URLs.

    Recursively traverses all <outline> elements and collects
    those with an xmlUrl attribute (actual feed entries, not categories).

    Returns list of {"title": str, "url": str} dicts.
    """
    if isinstance(file_content, str):
        root = ET.fromstring(file_content)
    else:
        root = ET.fromstring(file_content.decode("utf-8", errors="replace"))

    feeds: list[dict] = []

    def _walk(element):
        for child in element:
            if child.tag == "outline":
                xml_url = child.get("xmlUrl") or child.get("xmlurl")
                if xml_url:
                    feeds.append({
                        "title": child.get("title") or child.get("text", ""),
                        "url": xml_url.strip(),
                    })
                # Recurse into nested outlines (categories)
                _walk(child)

    body = root.find("body")
    if body is not None:
        _walk(body)
    else:
        _walk(root)

    return feeds


def generate_opml(feeds: dict[str, str], title: str = OPML_TITLE) -> str:
    """
    Generate OPML XML from a feeds dict.

    Args:
        feeds: {key: url} dict (from config.ini [rss] section)
        title: OPML document title

    Returns formatted XML string.
    """
    opml = ET.Element("opml", version="1.0")

    head = ET.SubElement(opml, "head")
    ET.SubElement(head, "title").text = title

    body = ET.SubElement(opml, "body")

    for key, url in feeds.items():
        ET.SubElement(body, "outline", {
            "text": key,
            "title": key,
            "type": "rss",
            "xmlUrl": url,
        })

    ET.indent(opml, space="  ")
    xml_str = ET.tostring(opml, encoding="unicode")
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + xml_str


def import_feeds_to_config(
    feeds: list[dict],
    config_path: Optional[Path] = None,
) -> dict:
    """
    Import new feeds into config.ini, skipping duplicates.

    Returns {"added": int, "skipped": int, "total": int}.
    """
    if config_path is None:
        config_path = settings.project_root / "config.ini"

    if not config_path.exists():
        raise FileNotFoundError(f"config.ini not found at {config_path}")

    ini = configparser.ConfigParser()
    ini.read(config_path, encoding="utf-8")

    if "rss" not in ini:
        ini.add_section("rss")

    # Collect existing URLs for dedup
    existing_urls = {url.strip().rstrip("/").lower() for url in ini["rss"].values()}

    # Find next key index
    max_key = 0
    for key in ini["rss"]:
        if key.startswith("url"):
            try:
                max_key = max(max_key, int(key[3:]))
            except ValueError:
                pass

    added = 0
    skipped = 0

    for feed in feeds:
        url = feed["url"].strip()
        if not url:
            continue
        normalized = url.rstrip("/").lower()
        if normalized in existing_urls:
            skipped += 1
            continue

        max_key += 1
        ini.set("rss", f"url{max_key}", url)
        existing_urls.add(normalized)
        added += 1
        logger.info("Imported feed: %s (%s)", feed.get("title", ""), url)

    if added > 0:
        with open(config_path, "w", encoding="utf-8") as f:
            ini.write(f)
        # Reload settings
        settings._load_ini_config()
        logger.info("Imported %d feeds, skipped %d duplicates", added, skipped)

    return {"added": added, "skipped": skipped, "total": added + skipped}
