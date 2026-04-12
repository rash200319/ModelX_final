"""
MODEL-X Health Monitor
Tracks per-source health, computes uptime, and surfaces dashboard metrics.
"""
import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SourceHealth:
    def __init__(self, name: str, stype: str = "unknown"):
        self.source_name = name
        self.source_type = stype
        self.last_fetch:   Optional[datetime] = None
        self.success_count = 0
        self.failure_count = 0
        self.last_error:   Optional[str] = None
        self.items_count   = 0
        self.total_items   = 0
        self.created_at    = datetime.now()

    def record_success(self, items: int = 0):
        self.last_fetch    = datetime.now()
        self.success_count += 1
        self.items_count   = max(0, items)
        self.total_items  += max(0, items)
        self.last_error    = None

    def record_failure(self, error: str):
        self.last_fetch    = datetime.now()
        self.failure_count += 1
        self.last_error    = error

    def is_healthy(self, timeout_hours: int = 2) -> bool:
        if self.last_fetch is None:
            return False
        fresh = datetime.now() - self.last_fetch < timedelta(hours=timeout_hours)
        total = self.success_count + self.failure_count
        if total == 0:
            return False
        ok_rate = self.failure_count / total
        return fresh and ok_rate < 0.5

    def get_stats(self) -> Dict[str, Any]:
        total = self.success_count + self.failure_count
        rate  = (self.success_count / total * 100) if total else 0
        since = str(datetime.now() - self.last_fetch).split(".")[0] if self.last_fetch else None
        status = ("no_data" if total == 0
                  else "healthy" if self.is_healthy()
                  else "unhealthy")
        return {
            "source_name":         self.source_name,
            "source_type":         self.source_type,
            "status":              status,
            "last_fetch":          self.last_fetch.isoformat() if self.last_fetch else "Never",
            "time_since_last_fetch": since,
            "success_count":       self.success_count,
            "failure_count":       self.failure_count,
            "total_attempts":      total,
            "success_rate":        f"{rate:.1f}%",
            "last_items_count":    self.items_count,
            "total_items_collected": self.total_items,
            "last_error":          self.last_error or "None",
            "uptime_days":         (datetime.now() - self.created_at).days,
        }


_TYPE_MAP = {
    "rss -": "RSS", "ada derana": "RSS", "daily mirror": "RSS",
    "lanka business online": "RSS", "news first": "RSS", "groundviews": "RSS",
    "sri lanka guardian": "RSS", "economy next": "RSS",
    "colombo telegraph": "RSS", "lanka news web": "RSS",
    "island.lk": "RSS", "gossip lanka": "RSS", "ceylon today": "RSS",
}


class HealthMonitor:
    def __init__(self):
        self.sources: Dict[str, SourceHealth] = {}
        self.logs:    List[Dict[str, Any]]    = []
        self._max_logs = 2000

    def _infer_type(self, name: str, stype: str) -> str:
        if stype not in ("unknown", ""):
            return stype
        s = (name or "").lower()
        if any(s.startswith(k) or s == k for k in _TYPE_MAP):
            return "RSS"
        if "reddit" in s:
            return "Social"
        if s in {"newsapi", "gdelt", "worldbank", "newsapi_multi-source"}:
            return "API"
        return "unknown"

    def register_source(self, name: str, stype: str = "unknown") -> SourceHealth:
        t = self._infer_type(name, stype)
        if name not in self.sources:
            self.sources[name] = SourceHealth(name, t)
        elif self.sources[name].source_type == "unknown" and t != "unknown":
            self.sources[name].source_type = t
        return self.sources[name]

    def record_fetch(self, name: str, success: bool, items: int = 0, error: str = None):
        self.register_source(name)
        if success:
            self.sources[name].record_success(items)
        else:
            self.sources[name].record_failure(error or "Unknown error")
        self.logs.append({
            "timestamp": datetime.now().isoformat(),
            "source":    name,
            "success":   success,
            "items":     items,
            "error":     error,
        })
        if len(self.logs) > self._max_logs:
            self.logs = self.logs[-self._max_logs:]

    def get_source_health(self, name: str = None):
        if name:
            return self.sources[name].get_stats() if name in self.sources else None
        return {n: h.get_stats() for n, h in self.sources.items()}

    def get_overall_health(self) -> Dict[str, Any]:
        if not self.sources:
            return {
                "overall_status": "no_data", "message": "No sources registered",
                "timestamp": datetime.now().isoformat(),
                "healthy_sources": 0, "active_sources": 0, "total_sources": 0,
                "health_percentage": "0.0%", "overall_success_rate": "0.0%",
                "total_attempts": 0, "best_performing": "N/A", "worst_performing": "N/A",
                "uptime": {"successful_collections": 0, "failed_collections": 0},
            }
        active = [s for s in self.sources.values() if s.success_count + s.failure_count > 0]
        healthy = sum(1 for s in active if s.is_healthy())
        total_s  = sum(s.success_count for s in self.sources.values())
        total_f  = sum(s.failure_count for s in self.sources.values())
        total_a  = total_s + total_f
        rate = (total_s / total_a * 100) if total_a else 0
        best  = max(active, key=lambda s: s.success_count - s.failure_count, default=None)
        worst = min(active, key=lambda s: s.success_count - s.failure_count, default=None)
        status = ("healthy" if active and healthy / len(active) >= 0.7
                  else "degraded" if active else "no_data")
        pct = f"{(healthy / len(active) * 100):.1f}%" if active else "0.0%"
        return {
            "timestamp":            datetime.now().isoformat(),
            "overall_status":       status,
            "healthy_sources":      healthy,
            "active_sources":       len(active),
            "total_sources":        len(self.sources),
            "health_percentage":    pct,
            "overall_success_rate": f"{rate:.1f}%",
            "total_attempts":       total_a,
            "best_performing":      best.source_name  if best  else "N/A",
            "worst_performing":     worst.source_name if worst else "N/A",
            "uptime": {"successful_collections": total_s, "failed_collections": total_f},
        }

    def get_recent_logs(self, n: int = 100):
        return self.logs[-n:]

    def get_dashboard_data(self) -> Dict[str, Any]:
        return {
            "overall_health":  self.get_overall_health(),
            "sources_health":  self.get_source_health(),
            "recent_logs":     self.get_recent_logs(100),
            "timestamp":       datetime.now().isoformat(),
        }

    def export_health_report(self, filepath: str = None) -> str:
        report = {
            "generated_at":    datetime.now().isoformat(),
            "overall_health":  self.get_overall_health(),
            "detailed_sources": self.get_source_health(),
            "recent_logs":     self.get_recent_logs(200),
        }
        out = json.dumps(report, indent=2)
        if filepath:
            try:
                with open(filepath, "w") as f:
                    f.write(out)
            except Exception as e:
                logger.error(f"Health report export failed: {e}")
        return out

    def get_type_summary(self) -> Dict[str, Dict[str, Any]]:
        """Aggregate health by source type."""
        by_type: Dict[str, list] = defaultdict(list)
        for s in self.sources.values():
            by_type[s.source_type].append(s)
        result = {}
        for t, sources in by_type.items():
            ok = sum(s.success_count for s in sources)
            fail = sum(s.failure_count for s in sources)
            result[t] = {
                "source_count": len(sources),
                "total_success": ok,
                "total_failure": fail,
                "success_rate": f"{ok/(ok+fail)*100:.1f}%" if (ok+fail) else "0.0%",
            }
        return result


# ── Singleton & pre-registration ─────────────────────────────────────────────
health_monitor = HealthMonitor()

_PRE_REGISTER = [
    ("Ada Derana", "RSS"), ("Daily Mirror", "RSS"),
    ("Lanka Business Online", "RSS"), ("News First", "RSS"),
    ("Groundviews", "RSS"), ("Sri Lanka Guardian", "RSS"),
    ("Economy Next", "RSS"), ("Colombo Telegraph", "RSS"),
    ("Lanka News Web", "RSS"), ("Island.lk", "RSS"),
    ("Gossip Lanka", "RSS"), ("Ceylon Today", "RSS"),
    ("NewsAPI", "API"), ("GDELT", "API"),
    ("WorldBank", "API"), ("NewsAPI_Multi-Source", "API"),
    ("Reddit - Mixed Subreddits", "Social"),
]
for _name, _type in _PRE_REGISTER:
    health_monitor.register_source(_name, _type)
