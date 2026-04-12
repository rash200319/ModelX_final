"""
MODEL-X Multi-Source Collector
Priority-ordered, fallback-resilient news aggregation:
  1. NewsAPI        (if configured)
  2. RSS Feeds      (always)
  3. GDELT          (throttled, no auth)
  4. World Bank     (macro enrichment)
  5. Historical     (30-day lookback via NewsAPI + GDELT)
"""
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List
from urllib.parse import urlparse

from utils.resilience import fetch_url
from config import (
    NEWS_API_KEY, WORLD_BANK_COUNTRY, WORLD_BANK_INDICATORS,
    HISTORICAL_LOOKBACK_DAYS, GDELT_DELAY_SECONDS,
)

logger = logging.getLogger(__name__)


class MultiSourceCollector:

    def __init__(self):
        self.newsapi_queries  = ["Sri Lanka", "Colombo", "Sri Lanka economy", "Sri Lanka protest"]
        self.gdelt_queries    = ['"Sri Lanka"', 'Colombo OR "Sri Lankan" economy']
        self.last_results     = {}
        self.fallback_flags   = {}

    # ── Deduplication ─────────────────────────────────────────────────────

    @staticmethod
    def _dedup(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        seen, out = set(), []
        for it in items:
            url   = (it.get("url") or "").strip().lower()
            title = (it.get("title") or "").strip().lower()
            pub   = (it.get("published") or "")[:10]
            key   = f"url:{urlparse(url).netloc}{urlparse(url).path}" if url else f"t:{title}|d:{pub}"
            if key not in seen:
                seen.add(key)
                out.append(it)
        return out

    # ── Public entry point ────────────────────────────────────────────────

    def collect_news_with_fallback(self, limit: int = 50) -> List[Dict[str, Any]]:
        all_news: List[Dict[str, Any]] = []

        # 1. NewsAPI
        if NEWS_API_KEY:
            try:
                items = self._fetch_newsapi(limit)
                all_news.extend(items)
                self.last_results["newsapi"] = {"count": len(items), "status": "ok"}
                logger.info(f"NewsAPI: {len(items)} items")
            except Exception as e:
                logger.warning(f"NewsAPI failed: {e}")
                self.fallback_flags["newsapi"] = True

        # 2. RSS
        try:
            items = self._fetch_rss(limit)
            all_news.extend(items)
            self.last_results["rss"] = {"count": len(items), "status": "ok"}
            logger.info(f"RSS: {len(items)} items")
        except Exception as e:
            logger.warning(f"RSS failed: {e}")
            self.fallback_flags["rss"] = True

        # 3. GDELT (throttled)
        try:
            items = self._fetch_gdelt(max(1, limit - len(all_news)))
            all_news.extend(items)
            self.last_results["gdelt"] = {"count": len(items), "status": "ok"}
            logger.info(f"GDELT: {len(items)} items")
        except Exception as e:
            logger.warning(f"GDELT failed: {e}")
            self.fallback_flags["gdelt"] = True

        # 4. Historical backfill (deduplicated against live items)
        try:
            hist = self._fetch_historical(HISTORICAL_LOOKBACK_DAYS, limit)
            all_news.extend(hist)
            self.last_results["historical"] = {"count": len(hist), "status": "enrichment"}
        except Exception as e:
            logger.warning(f"Historical backfill failed: {e}")

        # 5. World Bank macro
        try:
            wb = self._fetch_worldbank(min(10, max(1, limit - len(all_news))))
            all_news.extend(wb)
            self.last_results["worldbank"] = {"count": len(wb), "status": "enrichment"}
        except Exception as e:
            logger.warning(f"World Bank failed: {e}")

        deduped = self._dedup(all_news)
        logger.info(f"Total collected: {len(deduped)} unique items (before limit={limit})")
        return deduped[:limit]

    # ── Source handlers ───────────────────────────────────────────────────

    def _fetch_rss(self, limit: int) -> List[Dict[str, Any]]:
        from modules.news import fetch_news
        return fetch_news(limit_per_source=limit)

    def _fetch_newsapi(self, limit: int) -> List[Dict[str, Any]]:
        if not NEWS_API_KEY:
            return []
        all_articles: List[Dict[str, Any]] = []
        ps = max(1, min(limit // max(len(self.newsapi_queries), 1), 20))
        from_date = (datetime.now(timezone.utc) - timedelta(days=HISTORICAL_LOOKBACK_DAYS)).date().isoformat()
        to_date   = datetime.now(timezone.utc).date().isoformat()

        for q in self.newsapi_queries:
            try:
                resp = fetch_url(
                    "https://newsapi.org/v2/everything",
                    params={"q": q, "from": from_date, "to": to_date,
                            "sortBy": "publishedAt", "language": "en",
                            "pageSize": ps, "apiKey": NEWS_API_KEY},
                )
                for art in resp.json().get("articles", []):
                    all_articles.append({
                        "source":    "NewsAPI",
                        "title":     art.get("title", ""),
                        "content":   art.get("content", ""),
                        "url":       art.get("url", ""),
                        "published": art.get("publishedAt", datetime.now().isoformat()),
                    })
            except Exception as e:
                logger.debug(f"NewsAPI query '{q}': {e}")

        return self._dedup(all_articles)[:limit]

    def _fetch_gdelt(self, limit: int) -> List[Dict[str, Any]]:
        if limit <= 0:
            return []
        articles: List[Dict[str, Any]] = []
        end_dt   = datetime.now(timezone.utc)
        start_dt = end_dt - timedelta(days=HISTORICAL_LOOKBACK_DAYS)

        for q in self.gdelt_queries:
            time.sleep(GDELT_DELAY_SECONDS)  # Respect GDELT rate limits
            try:
                resp = fetch_url(
                    "https://api.gdeltproject.org/api/v2/doc/doc",
                    params={
                        "query":         q,
                        "mode":          "ArtList",
                        "format":        "json",
                        "sort":          "datedesc",
                        "startdatetime": start_dt.strftime("%Y%m%d%H%M%S"),
                        "enddatetime":   end_dt.strftime("%Y%m%d%H%M%S"),
                        "maxrecords":    min(limit, 100),
                    },
                )
                for art in resp.json().get("articles", [])[:limit]:
                    articles.append({
                        "source":    "GDELT",
                        "title":     art.get("title", ""),
                        "content":   art.get("snippet", "") or art.get("body", ""),
                        "url":       art.get("url", ""),
                        "published": art.get("seendate", datetime.now().isoformat()),
                    })
            except Exception as e:
                logger.debug(f"GDELT query '{q}': {e}")

        return self._dedup(articles)

    def _fetch_historical(self, days: int, limit: int) -> List[Dict[str, Any]]:
        items: List[Dict[str, Any]] = []
        if NEWS_API_KEY:
            items.extend(self._fetch_newsapi(max(1, limit // 2)))
        items.extend(self._fetch_gdelt(max(1, limit // 2)))
        return self._dedup(items)

    def _fetch_worldbank(self, limit: int) -> List[Dict[str, Any]]:
        _labels = {
            "FP.CPI.TOTL.ZG":  "Inflation (consumer prices, annual %)",
            "NY.GDP.MKTP.KD.ZG":"GDP growth (annual %)",
            "SL.UEM.TOTL.ZS":  "Unemployment (% labour force)",
            "GC.DOD.TOTL.GD.ZS":"Central government debt (% GDP)",
        }
        items: List[Dict[str, Any]] = []
        for ind in WORLD_BANK_INDICATORS:
            try:
                resp = fetch_url(
                    f"https://api.worldbank.org/v2/country/{WORLD_BANK_COUNTRY}/indicator/{ind}",
                    params={"format": "json", "per_page": 5},
                )
                data = resp.json()
                if not isinstance(data, list) or len(data) < 2:
                    continue
                point = next((r for r in data[1] if r.get("value") is not None), None)
                if not point:
                    continue
                label = _labels.get(ind, ind)
                value = point.get("value")
                year  = point.get("date")
                items.append({
                    "source":    "WorldBank",
                    "title":     f"World Bank: {label} = {value} ({year})",
                    "content":   f"{label}: {value} in {year} for {WORLD_BANK_COUNTRY}.",
                    "url":       f"https://data.worldbank.org/indicator/{ind}",
                    "published": datetime.now().isoformat(),
                })
                if len(items) >= limit:
                    break
            except Exception as e:
                logger.debug(f"WorldBank {ind}: {e}")
        return items

    def get_source_status(self) -> Dict[str, Any]:
        return {
            "timestamp":        datetime.now().isoformat(),
            "last_results":     self.last_results,
            "fallbacks":        self.fallback_flags,
            "newsapi_enabled":  bool(NEWS_API_KEY),
            "gdelt_ready":      True,
            "worldbank_ready":  True,
        }


multi_source_collector = MultiSourceCollector()
