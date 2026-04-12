"""
MODEL-X Configuration Module
Centralised, environment-driven settings with validation and feature flags.
"""
import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# ─── Paths ────────────────────────────────────────────────────────────────────
BACKEND_DIR   = Path(__file__).resolve().parent
PROJECT_ROOT  = BACKEND_DIR.parent
DEFAULT_DB    = BACKEND_DIR / "data" / "modelx.db"
LOG_DIR       = BACKEND_DIR / "logs"
EXPORT_DIR    = BACKEND_DIR / "exports"

_db_env = os.getenv("MODELX_DB_PATH", str(DEFAULT_DB))
_db_candidate = Path(_db_env)
if not _db_candidate.is_absolute():
    _db_candidate = (PROJECT_ROOT / _db_candidate).resolve()
DB_PATH       = str(_db_candidate)
LOG_DIR.mkdir(parents=True, exist_ok=True)
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Collector Behaviour ──────────────────────────────────────────────────────
REFRESH_INTERVAL       = int(os.getenv("MODELX_REFRESH_INTERVAL", "120"))
FETCH_LIMIT            = int(os.getenv("MODELX_FETCH_LIMIT", "30"))
AUTO_REFRESH_DEFAULT   = os.getenv("MODELX_AUTO_REFRESH_DEFAULT", "true").lower() == "true"
MAX_DB_RECORDS         = int(os.getenv("MODELX_MAX_DB_RECORDS", "50000"))
RETENTION_DAYS         = int(os.getenv("MODELX_RETENTION_DAYS", "90"))

# ─── API Keys ─────────────────────────────────────────────────────────────────
NEWS_API_KEY           = os.getenv("NEWS_API_KEY", "")
ANTHROPIC_API_KEY      = os.getenv("ANTHROPIC_API_KEY", "")

# ─── World Bank ───────────────────────────────────────────────────────────────
WORLD_BANK_COUNTRY     = os.getenv("WORLD_BANK_COUNTRY", "LKA")
WORLD_BANK_INDICATORS  = [
    i.strip() for i in os.getenv(
        "WORLD_BANK_INDICATORS",
        "FP.CPI.TOTL.ZG,NY.GDP.MKTP.KD.ZG,SL.UEM.TOTL.ZS,GC.DOD.TOTL.GD.ZS"
    ).split(",") if i.strip()
]
HISTORICAL_LOOKBACK_DAYS = int(os.getenv("HISTORICAL_LOOKBACK_DAYS", "30"))
GDELT_DELAY_SECONDS      = float(os.getenv("GDELT_REQUEST_DELAY_SECONDS", "2.5"))

# ─── Risk Thresholds ──────────────────────────────────────────────────────────
RISK_CRITICAL    = int(os.getenv("RISK_CRITICAL", "8"))
RISK_HIGH        = int(os.getenv("RISK_HIGH", "6"))
RISK_MEDIUM      = int(os.getenv("RISK_MEDIUM", "4"))

# ─── Alert System ─────────────────────────────────────────────────────────────
ALERT_EMAIL_ENABLED  = os.getenv("ALERT_EMAIL_ENABLED", "false").lower() == "true"
ALERT_EMAIL_TO       = os.getenv("ALERT_EMAIL_TO", "")
ALERT_SMTP_HOST      = os.getenv("ALERT_SMTP_HOST", "smtp.gmail.com")
ALERT_SMTP_PORT      = int(os.getenv("ALERT_SMTP_PORT", "587"))
ALERT_SMTP_USER      = os.getenv("ALERT_SMTP_USER", "")
ALERT_SMTP_PASS      = os.getenv("ALERT_SMTP_PASS", "")
ALERT_WEBHOOK_URL    = os.getenv("ALERT_WEBHOOK_URL", "")   # Slack / Teams / Discord

# ─── Feature Flags ────────────────────────────────────────────────────────────
FEATURE_AI_SUMMARY      = os.getenv("FEATURE_AI_SUMMARY", "false").lower() == "true"
FEATURE_ANOMALY_DETECT  = os.getenv("FEATURE_ANOMALY_DETECT", "true").lower() == "true"
FEATURE_FORECAST        = os.getenv("FEATURE_FORECAST", "true").lower() == "true"
FEATURE_ALERTS          = os.getenv("FEATURE_ALERTS", "false").lower() == "true"
FEATURE_CORRELATION     = os.getenv("FEATURE_CORRELATION", "true").lower() == "true"

# ─── Sri Lanka geography (used across modules) ───────────────────────────────
SRI_LANKA_CITIES = {
    "Colombo":      {"lat": 6.9271,  "lon": 79.8612, "province": "Western"},
    "Kandy":        {"lat": 7.2906,  "lon": 80.6337, "province": "Central"},
    "Galle":        {"lat": 6.0535,  "lon": 80.2210, "province": "Southern"},
    "Jaffna":       {"lat": 9.6615,  "lon": 80.0255, "province": "Northern"},
    "Trincomalee":  {"lat": 8.5874,  "lon": 81.2152, "province": "Eastern"},
    "Negombo":      {"lat": 7.2088,  "lon": 79.8358, "province": "Western"},
    "Matara":       {"lat": 5.9549,  "lon": 80.5550, "province": "Southern"},
    "Anuradhapura": {"lat": 8.3114,  "lon": 80.4037, "province": "North Central"},
    "Batticaloa":   {"lat": 7.7167,  "lon": 81.7000, "province": "Eastern"},
    "Ratnapura":    {"lat": 6.7056,  "lon": 80.3847, "province": "Sabaragamuwa"},
    "Kurunegala":   {"lat": 7.4863,  "lon": 80.3647, "province": "North Western"},
    "Ampara":       {"lat": 7.2985,  "lon": 81.6745, "province": "Eastern"},
}

# ─── Dashboard display ────────────────────────────────────────────────────────
APP_TITLE     = "MODEL-X | National Risk Intelligence Platform"
APP_ICON      = "🇱🇰"
DASHBOARD_TABS = ["Geospatial", "Analytics", "Live Feed", "Forecast", "Source Health"]
