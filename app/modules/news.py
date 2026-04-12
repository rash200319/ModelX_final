"""
MODEL-X News Module
RSS + NewsAPI collection with retry, health tracking, and date normalisation.
"""
import json
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import feedparser
import requests
from dateutil import parser as date_parser

from utils.resilience import fetch_rss_with_retry
from utils.health import health_monitor
from config import NEWS_API_KEY

logger = logging.getLogger(__name__)

NEWS_SOURCES = [
    # ── Primary English feeds ─────────────────────────────────────────
    {"name": "Ada Derana",           "rss_url": "http://www.adaderana.lk/rss.php",                                      "active": True},
    {"name": "Daily Mirror",         "rss_url": "https://www.dailymirror.lk/RSS_Feeds/breaking-news",                   "active": True},
    {"name": "Lanka Business Online","rss_url": "https://www.lankabusinessonline.com/feed/",                             "active": True},
    {"name": "News First",           "rss_url": "https://www.newsfirst.lk/latest-news/feed/",                           "active": True},
    {"name": "Groundviews",          "rss_url": "https://groundviews.org/feed/",                                        "active": True},
    {"name": "Sri Lanka Guardian",   "rss_url": "https://slguardian.org/feed/",                                         "active": True},
    {"name": "Economy Next",         "rss_url": "https://economynext.com/feed/",                                        "active": True},
    {"name": "Colombo Telegraph",    "rss_url": "https://www.colombotelegraph.com/index.php/feed/",                      "active": True},
    {"name": "Lanka News Web",       "rss_url": "https://lankanewsweb.net/feed/",                                       "active": True},
    {"name": "Island.lk",            "rss_url": "http://island.lk/feed/",                                              "active": True},
    {"name": "Gossip Lanka",         "rss_url": "https://www.gossiplankanews.com/feeds/posts/default?alt=rss",          "active": True},
    {"name": "Ceylon Today",         "rss_url": "https://ceylontoday.lk/feed/",                                        "active": True},
    # ── Additional feeds (off by default — toggle to True to enable) ──
    {"name": "The Morning",          "rss_url": "https://www.themorning.lk/feed",                                       "active": False},
    {"name": "Daily FT",             "rss_url": "https://www.ft.lk/rss/front-page",                                    "active": False},
    {"name": "Sunday Times",         "rss_url": "https://www.sundaytimes.lk/rss/news.xml",                              "active": False},
    {"name": "Hiru News",            "rss_url": "https://www.hirunews.lk/rss/hirunews-lk-news-sinhala-fb.xml",         "active": False},
]


def _parse_date(s: str) -> str:
    if not s:
        return datetime.now(timezone.utc).isoformat()
    try:
        dt = date_parser.parse(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone(timedelta(hours=5, minutes=30)))
        return dt.isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def fetch_rss_feed(url: str, name: str, limit: int = 20) -> List[Dict[str, Any]]:
    try:
        joiner = "&" if "?" in url else "?"
        resp   = fetch_rss_with_retry(f"{url}{joiner}_={int(time.time())}")
        feed   = feedparser.parse(resp.content)
        items  = []
        for entry in feed.entries[:limit]:
            content = ""
            if hasattr(entry, "content") and entry.content:
                content = entry.content[0].value
            elif hasattr(entry, "summary"):
                content = entry.summary
            items.append({
                "source":    name,
                "title":     entry.get("title", ""),
                "content":   content,
                "url":       entry.get("link", ""),
                "published": _parse_date(entry.get("published", "")),
            })
        return items
    except Exception as e:
        logger.error(f"RSS {name} ({url}): {e}")
        return []


def fetch_news(limit_per_source: int = 20) -> List[Dict[str, Any]]:
    all_news: List[Dict[str, Any]] = []
    for src in [s for s in NEWS_SOURCES if s.get("active") and s.get("rss_url")]:
        try:
            items = fetch_rss_feed(src["rss_url"], src["name"], limit_per_source)
            all_news.extend(items)
            health_monitor.record_fetch(src["name"], True, len(items))
        except Exception as e:
            logger.error(f"Fetch error {src['name']}: {e}")
            health_monitor.record_fetch(src["name"], False, 0, str(e))
    all_news.sort(key=lambda x: x.get("published", ""), reverse=True)
    return all_news
