# National Risk Intelligence Platform (MODEL-X) — Advanced Edition

A Streamlit-based risk intelligence system for Sri Lanka with continuous multi-source
data collection, deterministic risk scoring, anomaly detection, 7-day forecasting,
AI executive summaries, alert dispatch, and full-text search.

---

## What's New (vs. Original)

| Feature | Before | After |
|---|---|---|
| Database | Basic SQLite | WAL + FTS5 full-text search, hourly aggregates, anomaly log, alert log, auto-pruning |
| Scoring | 4-component formula | Same formula + 6 extra crisis keywords (collapse, explosion, sanction, default, devaluation, contamination) |
| Anomaly detection | None | Rolling z-score + IQR over hourly buckets, persisted to DB, banner in UI |
| Forecasting | None | Holt-Winters + linear trend blend, 7-day per-category with 95% CI bands |
| AI summaries | None | Optional Anthropic API integration for critical signal executive summaries |
| Alert dispatch | None | Webhook (Slack/Teams/Discord) + SMTP email for critical alerts |
| FTS search | Substring only | SQLite FTS5 virtual table with `MATCH` ranking |
| Dashboard tabs | 4 | 5 (added Forecast tab; Source Health inline) |
| Geospatial | ScatterplotLayer | ScatterplotLayer + HeatmapLayer + city breakdown table |
| Analytics | 4 charts | 6 charts (added hourly bar chart, category timeline, correlation heatmap) |
| Config | Minimal | Full feature flags, alert settings, risk thresholds, retention config |
| DB maintenance | None | Automatic pruning (RETENTION_DAYS), VACUUM, MAX_DB_RECORDS cap |
| Health monitor | In-memory only | Type-aggregated stats, type summary, richer export |

---

## Project Structure

```
National-Risk-Intelligence-Platform/
├── readme.md
├── requirements.txt
├── runtime.txt
├── .env
└── app/
    ├── app.py                    # Main Streamlit dashboard (5 tabs)
    ├── collector.py              # Background collector + anomaly detector + alert hook
    ├── config.py                 # All settings, feature flags, thresholds
    ├── database_manager.py       # SQLite: WAL, FTS5, hourly agg, anomaly log, pruning
    ├── data/                     # SQLite database
    ├── logs/                     # Collection logs
    ├── exports/                  # CSV exports
    ├── modules/
    │   ├── news.py               # RSS + NewsAPI collection
    │   └── social.py             # Reddit RSS collection with relevance filter
    ├── pages/
    │   └── health_monitor.py     # Standalone Streamlit health page
    └── utils/
        ├── alerts.py             # ← NEW: Webhook + email alert dispatch
        ├── forecast.py           # ← NEW: Holt-Winters 7-day forecasting
        ├── health.py             # Source health tracking + type summaries
        ├── resilience.py         # Retry + exponential backoff
        └── sources.py            # Multi-source fallback strategy
```

---

## Feature Flags (.env)

```env
# Core
MODELX_DB_PATH=app/data/modelx.db
MODELX_REFRESH_INTERVAL=120
MODELX_FETCH_LIMIT=30
MODELX_AUTO_REFRESH_DEFAULT=true
MODELX_RETENTION_DAYS=90
MODELX_MAX_DB_RECORDS=50000

# APIs (optional)
NEWS_API_KEY=your_key_here
ANTHROPIC_API_KEY=your_key_here   # For AI executive summaries

# Risk thresholds
RISK_CRITICAL=8
RISK_HIGH=6
RISK_MEDIUM=4

# Feature flags
FEATURE_AI_SUMMARY=false          # Set true to enable AI summaries (requires ANTHROPIC_API_KEY)
FEATURE_ANOMALY_DETECT=true
FEATURE_FORECAST=true
FEATURE_ALERTS=false
FEATURE_CORRELATION=true

# Alerts (only used if FEATURE_ALERTS=true)
ALERT_WEBHOOK_URL=https://hooks.slack.com/...
ALERT_EMAIL_ENABLED=false
ALERT_EMAIL_TO=analyst@example.com
ALERT_SMTP_HOST=smtp.gmail.com
ALERT_SMTP_PORT=587
ALERT_SMTP_USER=you@gmail.com
ALERT_SMTP_PASS=app_password

# World Bank
WORLD_BANK_COUNTRY=LKA
WORLD_BANK_INDICATORS=FP.CPI.TOTL.ZG,NY.GDP.MKTP.KD.ZG,SL.UEM.TOTL.ZS,GC.DOD.TOTL.GD.ZS
HISTORICAL_LOOKBACK_DAYS=30
```

---

## Risk Scoring Algorithm

```
Base: 4.0

Adjustments:
  + Sentiment:         -compound × 1.5
  + Crisis keywords:   min(crisis_strength, 5.0)   [32 weighted terms]
  + Source reliability:(reliability − 0.7) × 2.0
  + Industry risk:     +0.6 (high-risk) | +0.3 (medium) | +0.0 (general)
  − Low-signal cap:    capped at 4.0 if personal/advice language detected
  − Question discount: −1.0 if "?" and crisis_strength < 2.0
  − Historical:        −1.0 if text references dates before 2010
  + Finance boost:     +1.0 if bank/fraud/cbsl/deposits detected
  + Hard floor:        forced ≥ 8.0 if crisis_strength ≥ 3.5 and ≥ 2 keywords matched

Final: clamp(score, 1, 10)
```

### Confidence Score
```
(source_reliability × 0.45)
+ (text_length_norm  × 0.20)
+ (keyword_signal    × 0.25)
+ (|sentiment|       × 0.10)
```

### Source Reliability Weights
| Source   | Weight |
|----------|--------|
| NewsAPI  | 0.95   |
| WorldBank| 0.90   |
| GDELT    | 0.85   |
| RSS      | 0.80   |
| Reddit   | 0.55   |

---

## Anomaly Detection

Uses a rolling z-score over the last 24h of hourly buckets per category.
Anomalies are logged to `anomaly_log` table and displayed as banners in the dashboard.

```
For each category:
  z = (latest_avg_score − mean) / std_dev
  if |z| ≥ 2.5 → flag anomaly
```

---

## Forecasting

7-day ahead forecast per industry category + overall:
- Double exponential smoothing (Holt-Winters)
- Linear trend extrapolation
- Blended: 60% Holt + 40% linear
- 95% confidence intervals via residual variance, widening with horizon

Requires at least 3 days of data per category.

---

## Quick Start

```bash
git clone <repo>
cd National-Risk-Intelligence-Platform
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # fill in your keys
streamlit run app/app.py
```

---

## Requirements (additions vs. original)

```
numpy                # forecasting
anthropic            # AI summaries (optional)
tenacity             # retry logic (unchanged)
```

---

## Running Tests

```bash
pytest tests/ -v
```

---

## License

No license file is currently included.
