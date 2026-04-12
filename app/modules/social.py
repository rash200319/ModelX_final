"""
MODEL-X Social Module
Reddit RSS collection with relevance filtering and deduplication.
"""
import json
import logging
from datetime import datetime, timezone
from io import BytesIO

import feedparser
import pandas as pd
from dateutil import parser as date_parser

from utils.resilience import fetch_rss_with_retry

logger = logging.getLogger(__name__)

HIGH_PRIORITY = {
    "protest", "strike", "curfew", "riot", "violence", "attack", "flood", "disaster",
    "inflation", "tax", "interest rate", "debt", "imf", "economy", "recession",
    "fuel", "power cut", "electricity", "blackout", "port", "shipping", "airport",
    "supply chain", "import", "export", "policy", "regulation", "ministry",
    "bank", "rupee", "dollar", "forex", "unemployment", "layoff", "sanction",
    "collapse", "default", "devaluation", "contamination", "explosion",
}

BUSINESS_CTX = {
    "market", "business", "industry", "retail", "manufacturing", "tourism",
    "logistics", "transport", "agriculture", "tea", "garment", "construction",
    "company", "corporate", "investment", "cost", "price", "tariff", "budget",
}

LOW_SIGNAL = {
    "want to get", "which vehicle", "recommend me", "my first car",
    "best phone", "dating", "relationship", "movie suggestion", "meme", "joke",
    "for my family", "looking for",
}

HISTORICAL = {
    "history", "historical", "archive", "archival", "committee report",
    "thesis", "research", "study", "paper", "essay", "memoir",
    "massacre", "tragedy", "1974", "1980", "1981", "1983", "1990", "1996",
}

SUBREDDITS = [
    "srilanka", "Colombo", "kandy", "Galle", "Jaffna",
    "srilankans", "ceylon", "srilanka_memes",
]


def _is_relevant(title: str, summary: str) -> bool:
    text = f"{title} {summary}".lower()
    if any(p in text for p in LOW_SIGNAL) and not any(t in text for t in HIGH_PRIORITY):
        return False
    hist_hits = sum(1 for t in HISTORICAL if t in text)
    hp_hits   = sum(1 for t in HIGH_PRIORITY if t in text)
    if hist_hits >= 1 and hp_hits == 0:
        return False
    biz_hits = sum(1 for t in BUSINESS_CTX if t in text)
    return hp_hits >= 1 or biz_hits >= 2


def _parse_date(s: str) -> str:
    try:
        dt = date_parser.parse(s) if s else None
        if dt is None:
            return datetime.now(timezone.utc).isoformat()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return datetime.now(timezone.utc).isoformat()


def get_reddit_rss(limit: int = 50) -> pd.DataFrame:
    posts = []
    for sub in SUBREDDITS:
        url = f"https://www.reddit.com/r/{sub}/new/.rss?t={int(datetime.now().timestamp())}"
        try:
            resp = fetch_rss_with_retry(url)
            if resp.status_code != 200:
                logger.warning(f"r/{sub}: HTTP {resp.status_code}")
                continue
            feed = feedparser.parse(BytesIO(resp.content))
            kept = skipped = 0
            for entry in feed.entries[:limit]:
                title   = entry.get("title", "")
                summary = entry.get("summary", "")
                if not _is_relevant(title, summary):
                    skipped += 1
                    continue
                posts.append({
                    "source":    f"reddit_r/{sub}",
                    "title":     title,
                    "link":      entry.get("link", ""),
                    "published": _parse_date(entry.get("published", "")),
                    "raw_data":  json.dumps({"summary": summary[:500]}, default=str),
                })
                kept += 1
            logger.info(f"r/{sub}: kept={kept}, skipped={skipped}")
        except Exception as e:
            logger.error(f"r/{sub}: {e}")

    df = pd.DataFrame(posts)
    if not df.empty:
        df.drop_duplicates(subset=["link"], inplace=True)
        logger.info(f"Reddit total after dedup: {len(df)}")
    return df


# Placeholder to prevent ImportErrors
def fetch_twitter_data(*args, **kwargs):
    return []
