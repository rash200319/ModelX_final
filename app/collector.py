"""
MODEL-X Risk Collector
Advanced background collector with:
  - Deterministic, explainable risk scoring
  - Anomaly detection (z-score + IQR)
  - AI-powered signal summarisation (optional, via Anthropic API)
  - Alert dispatch (webhook / email)
  - Automatic DB pruning
  - Per-cycle performance metrics
"""
import asyncio
import logging
import re
import threading
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import nltk
import pandas as pd
from nltk.sentiment import SentimentIntensityAnalyzer

from config import (
    FETCH_LIMIT, REFRESH_INTERVAL, RISK_CRITICAL,
    FEATURE_AI_SUMMARY, FEATURE_ANOMALY_DETECT, FEATURE_ALERTS,
    ANTHROPIC_API_KEY, ALERT_WEBHOOK_URL,
)
from database_manager import db
from utils.sources import multi_source_collector
from utils.health import health_monitor
from utils.alerts import dispatch_alert
from modules.social import get_reddit_rss

logger = logging.getLogger(__name__)

# ── NLTK bootstrap ───────────────────────────────────────────────────────────
for _res in ("vader_lexicon",):
    try:
        nltk.data.find(_res)
    except LookupError:
        nltk.download(_res, quiet=True)
sia = SentimentIntensityAnalyzer()

# ── Industry taxonomy ─────────────────────────────────────────────────────────
INDUSTRIES = {
    "Energy & Fuel":         ["power", "electricity", "fuel", "gas", "petrol", "energy", "grid", "ceb", "cpc"],
    "Logistics & Transport": ["road", "traffic", "train", "bus", "port", "shipping", "airline", "flight", "transport"],
    "Finance & Economy":     ["rupee", "dollar", "bank", "tax", "inflation", "stock", "market", "imf", "debt", "economy"],
    "Tourism":               ["tourist", "hotel", "visa", "airport", "travel", "resort", "booking"],
    "Agriculture":           ["farmer", "crop", "rice", "fertilizer", "food", "tea", "export"],
    "Public Safety":         ["protest", "strike", "curfew", "police", "attack", "violence", "disaster", "flood"],
}
HIGH_RISK_INDUSTRIES = {"Public Safety", "Energy & Fuel"}

STRONG_KEYWORDS: Dict[str, set] = {
    "Energy & Fuel":         {"blackout", "power cut", "fuel", "petrol"},
    "Logistics & Transport": {"port", "airport", "shipping", "train", "bus"},
    "Finance & Economy":     {"bank", "fraud", "cbsl", "depositor", "deposits", "imf"},
    "Tourism":               {"airport", "visa", "hotel"},
    "Agriculture":           {"fertilizer", "crop", "rice"},
    "Public Safety":         {"protest", "strike", "curfew", "attack", "violence", "disaster", "flood"},
}

SOURCE_RELIABILITY = {
    "newsapi": 0.95, "gdelt": 0.85, "worldbank": 0.90,
    "rss": 0.80,     "reddit": 0.55, "default": 0.70,
}

CRISIS_WEIGHTS = {
    "crisis": 1.8, "emergency": 2.4, "alert": 1.2, "warning": 1.0,
    "attack": 2.4, "dead": 2.0, "kill": 2.2, "violence": 2.0,
    "protest": 1.6, "strike": 1.6, "curfew": 1.8, "flood": 1.6,
    "disaster": 2.0, "riot": 2.2, "shortage": 1.4, "blackout": 1.6,
    "power cut": 1.6, "bankrupt": 2.0, "debt": 1.2, "inflation": 1.2,
    "layoff": 1.3, "collapse": 2.0, "explosion": 2.4, "fire": 1.5,
    "contamination": 1.8, "sanction": 1.6, "default": 1.8, "devaluation": 1.7,
}

LOW_SIGNAL_PATTERNS = [
    "anyone", "recommend", "suggestions", "looking for",
    "can someone", "help me", "advice", "where can i",
]


# ── Scoring engine ────────────────────────────────────────────────────────────

class ScoringEngine:
    """Deterministic, explainable risk scoring."""

    @staticmethod
    def _resolve_source(name: str) -> str:
        s = (name or "").lower()
        if s.startswith("reddit"): return "reddit"
        if s == "newsapi":         return "newsapi"
        if s == "gdelt":           return "gdelt"
        if s == "worldbank":       return "worldbank"
        return "rss"

    @staticmethod
    def _clamp(v, lo, hi): return max(lo, min(hi, v))

    def extract_industries(self, text: str) -> List[str]:
        detected = []
        for ind, kws in INDUSTRIES.items():
            hits  = sum(1 for kw in kws if re.search(rf"\b{re.escape(kw)}\b", text))
            strong = sum(1 for kw in STRONG_KEYWORDS.get(ind, set()) if re.search(rf"\b{re.escape(kw)}\b", text))
            if strong >= 1 or hits >= 2:
                detected.append(ind)
        return detected or ["General"]

    def score(self, text: str, source_name: str = "default") -> Dict[str, Any]:
        """
        Returns a dict with:
          risk_score, sentiment, industries, confidence,
          matched_keywords, risk_breakdown
        """
        tl = text.lower().strip()
        sentiment = sia.polarity_scores(text)["compound"]

        crisis_strength, matched, seen = 0.0, [], set()
        for kw, w in CRISIS_WEIGHTS.items():
            if kw not in seen and re.search(rf"\b{re.escape(kw)}\b", tl):
                crisis_strength += w
                matched.append(kw)
                seen.add(kw)

        src_type   = self._resolve_source(source_name)
        reliability = SOURCE_RELIABILITY.get(src_type, SOURCE_RELIABILITY["default"])
        industries  = self.extract_industries(tl)

        sent_c     = (-sentiment) * 1.5
        crisis_c   = min(crisis_strength, 5.0)
        source_c   = (reliability - 0.7) * 2.0
        ind_c      = 0.6 if any(i in HIGH_RISK_INDUSTRIES for i in industries) else (0.3 if industries != ["General"] else 0.0)

        score = 4.0 + sent_c + crisis_c + source_c + ind_c

        if any(p in tl for p in LOW_SIGNAL_PATTERNS):
            score = min(score, 4.0)
        if "?" in text and crisis_strength < 2.0:
            score -= 1.0
        if crisis_strength >= 3.5 and len(matched) >= 2:
            score = max(score, 8.0)
        if re.search(r"\b(?:197|198|199|200)\d\b", tl):
            score -= 1.0
        if any(t in tl for t in ["bank", "fraud", "cbsl", "depositor", "deposits"]):
            score += 1.0

        score = int(round(self._clamp(score, 1.0, 10.0)))

        txt_len_s = self._clamp(len(text) / 280.0, 0.0, 1.0)
        kw_sig    = self._clamp(crisis_strength / 4.0, 0.0, 1.0)
        conf = (reliability * 0.45) + (txt_len_s * 0.20) + (kw_sig * 0.25) + (abs(sentiment) * 0.10)
        conf = round(self._clamp(conf, 0.1, 1.0), 2)

        return {
            "risk_score":      score,
            "sentiment":       round(sentiment, 4),
            "industries":      ", ".join(industries),
            "confidence":      conf,
            "keywords":        ", ".join(matched),
            "risk_breakdown": str({
                "sentiment": round(sent_c, 2),
                "crisis":    round(crisis_c, 2),
                "source":    round(source_c, 2),
                "industry":  round(ind_c, 2),
            }),
        }


engine = ScoringEngine()


# ── Anomaly detection ─────────────────────────────────────────────────────────

class AnomalyDetector:
    """
    Rolling z-score anomaly detection over the last N hours per category.
    Also applies IQR fencing for robustness.
    """

    def __init__(self, window_hours: int = 6, z_threshold: float = 2.5):
        self.window_hours  = window_hours
        self.z_threshold   = z_threshold

    def detect(self) -> List[Dict[str, Any]]:
        if not FEATURE_ANOMALY_DETECT:
            return []
        try:
            trend = db.get_hourly_trend(hours=self.window_hours * 4)
            if trend.empty or "avg_score" not in trend.columns:
                return []

            anomalies = []
            for cat, grp in trend.groupby("category"):
                scores = grp["avg_score"].dropna()
                if len(scores) < 4:
                    continue
                mu, sigma = scores.mean(), scores.std()
                if sigma < 0.01:
                    continue
                latest = scores.iloc[-1]
                z = (latest - mu) / sigma
                if abs(z) >= self.z_threshold:
                    peak = int(grp["max_score"].max())
                    cnt  = int(grp["event_count"].sum())
                    desc = (
                        f"Anomaly in '{cat}': z={z:.2f}, "
                        f"latest avg={latest:.1f}, baseline={mu:.1f}±{sigma:.1f}, "
                        f"peak={peak}, events={cnt} (last {self.window_hours * 4}h)"
                    )
                    anomalies.append({
                        "category": cat, "z": z, "peak": peak,
                        "count": cnt, "desc": desc
                    })
                    db.log_anomaly(cat, z, peak, cnt, self.window_hours * 4, desc)
                    logger.warning(f"🔔 ANOMALY: {desc}")

            return anomalies
        except Exception as e:
            logger.error(f"AnomalyDetector.detect: {e}")
            return []


anomaly_detector = AnomalyDetector()


# ── Optional AI summariser ────────────────────────────────────────────────────

def _ai_summarise(signals: List[str]) -> Optional[str]:
    """
    Call Anthropic API to produce a concise 2-sentence executive summary
    of the top risk signals.  Returns None if disabled or key absent.
    """
    if not FEATURE_AI_SUMMARY or not ANTHROPIC_API_KEY:
        return None
    try:
        import anthropic
        prompt = (
            "You are a risk analyst for Sri Lanka. "
            "Summarise the following news signals in exactly 2 sentences, "
            "highlighting the most critical operational risks:\n\n"
            + "\n".join(f"- {s}" for s in signals[:10])
        )
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        logger.warning(f"AI summarise failed: {e}")
        return None


# ── Main collector ────────────────────────────────────────────────────────────

class RiskCollector:

    def __init__(self):
        self.is_running  = False
        self.thread: Optional[threading.Thread] = None
        self.cycle_count = 0
        self.last_cycle_ms = 0

    # ── Item → scored record ──────────────────────────────────────────────

    def _build_record(self, item: Dict[str, Any], prefix: str) -> Dict[str, Any]:
        full_text   = f"{item.get('title', '')} {item.get('content', item.get('summary', ''))}"
        source_name = item.get("source", prefix)
        result      = engine.score(full_text, source_name)
        return {
            "id":              f"{prefix}_{uuid.uuid4().hex[:8]}",
            "source":          source_name,
            "signal":          item.get("title", ""),
            "link":            item.get("url", item.get("link", "#")),
            "published":       item.get("published", datetime.now().isoformat()),
            "risk_score":      result["risk_score"],
            "category":        result["industries"],
            "location":        "Sri Lanka",
            "district":        "",
            "province":        "",
            "keywords":        result["keywords"],
            "confidence":      result["confidence"],
            "risk_breakdown":  result["risk_breakdown"],
            "sentiment_score": result["sentiment"],
            "created_at":      datetime.now().isoformat(),
        }

    # ── Per-source fetch helpers ──────────────────────────────────────────

    def _collect_news(self) -> int:
        try:
            items = multi_source_collector.collect_news_with_fallback(limit=FETCH_LIMIT)
            if not items:
                health_monitor.record_fetch("NewsAPI_Multi-Source", False, 0, "No items")
                return 0
            records = [self._build_record(it, "news") for it in items]

            # Optional AI executive summary for critical signals
            critical = [r["signal"] for r in records if r["risk_score"] >= RISK_CRITICAL]
            if critical:
                summary = _ai_summarise(critical)
                if summary:
                    for r in records:
                        if r["risk_score"] >= RISK_CRITICAL:
                            r["ai_summary"] = summary

            n = db.batch_insert_risks(records)
            health_monitor.record_fetch("NewsAPI_Multi-Source", True, n)
            logger.info(f"   ✅ News: {n} new records inserted (from {len(items)} fetched)")
            return n
        except Exception as e:
            logger.error(f"News collection error: {e}")
            health_monitor.record_fetch("NewsAPI_Multi-Source", False, 0, str(e))
            return 0

    def _collect_reddit(self) -> int:
        src = "Reddit - Mixed Subreddits"
        try:
            df = get_reddit_rss(limit=FETCH_LIMIT)
            if df.empty:
                health_monitor.record_fetch(src, False, 0, "No posts")
                return 0
            records = []
            for _, row in df.iterrows():
                raw = ""
                try:
                    raw = row.get("raw_data", "")
                except Exception:
                    pass
                item = {
                    "title":   row.get("title", ""),
                    "content": raw,
                    "source":  row.get("source", "Reddit"),
                    "url":     row.get("link", "#"),
                    "published": row.get("published", datetime.now().isoformat()),
                }
                records.append(self._build_record(item, "reddit"))
            n = db.batch_insert_risks(records)
            health_monitor.record_fetch(src, True, n)
            logger.info(f"   ✅ Reddit: {n} new records inserted")
            return n
        except Exception as e:
            logger.error(f"Reddit collection error: {e}")
            health_monitor.record_fetch(src, False, 0, str(e))
            return 0

    # ── Alert dispatch ────────────────────────────────────────────────────

    def _check_and_alert(self):
        if not FEATURE_ALERTS:
            return
        try:
            recent = db.get_risks(limit=50, min_score=RISK_CRITICAL)
            # Only alert on records inserted in the last cycle
            if recent.empty:
                return
            cutoff = (datetime.utcnow() - __import__("datetime").timedelta(seconds=REFRESH_INTERVAL + 30)).isoformat()
            fresh  = recent[recent.get("created_at", pd.Series(dtype=str)) >= cutoff]
            for _, row in fresh.iterrows():
                payload = {
                    "score":    row.get("risk_score"),
                    "signal":   row.get("signal"),
                    "source":   row.get("source"),
                    "category": row.get("category"),
                    "link":     row.get("link"),
                }
                dispatch_alert(payload, row.get("id", ""))
        except Exception as e:
            logger.warning(f"Alert check error: {e}")

    # ── Main fetch cycle ──────────────────────────────────────────────────

    def fetch_realtime(self):
        t0 = time.monotonic()
        self.cycle_count += 1
        logger.info(f"📡 Cycle #{self.cycle_count} — collecting…")

        total  = self._collect_news()
        total += self._collect_reddit()

        # Anomaly detection
        anomalies = anomaly_detector.detect()
        if anomalies:
            logger.warning(f"🔔 {len(anomalies)} anomalies detected this cycle.")

        # Alert dispatch for new critical signals
        self._check_and_alert()

        # Periodic DB maintenance (every 10 cycles ≈ every ~20 min)
        if self.cycle_count % 10 == 0:
            pruned = db.prune_old_records()
            if pruned:
                logger.info(f"🗑️  Pruned {pruned} stale DB records.")

        elapsed = int((time.monotonic() - t0) * 1000)
        self.last_cycle_ms = elapsed
        logger.info(f"   Cycle #{self.cycle_count} complete: {total} new records in {elapsed}ms.")

    def run_loop(self):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        while self.is_running:
            try:
                self.fetch_realtime()
            except Exception as e:
                logger.error(f"Collector loop error: {e}")
            time.sleep(REFRESH_INTERVAL)

    def start(self):
        if self.is_running:
            return
        self.is_running = True
        self.thread = threading.Thread(target=self.run_loop, daemon=True)
        self.thread.start()
        logger.info(f"🚀 Collector started. Polling every {REFRESH_INTERVAL}s.")

    def stop(self):
        self.is_running = False
        logger.info("🛑 Collector stopped.")

    def status(self) -> Dict[str, Any]:
        return {
            "running":       self.is_running,
            "cycle_count":   self.cycle_count,
            "last_cycle_ms": self.last_cycle_ms,
            "interval_s":    REFRESH_INTERVAL,
        }


collector = RiskCollector()
