"""
MODEL-X | National Risk Intelligence Platform
Advanced Streamlit dashboard — 5 tabs:
  1. Geospatial View
  2. Business Analytics  (correlation, category timeline)
  3. Live Risk Feed      (FTS search, AI summary, anomaly banners)
  4. Forecast            (7-day per-category with CI bands)
  5. Source Health       (rendered inline — no separate page needed)
"""
import re
import html
from collections import Counter
from datetime import datetime, timedelta

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import pydeck as pdk
import streamlit as st
from dotenv import load_dotenv
from streamlit_autorefresh import st_autorefresh

load_dotenv()

from config import (
    AUTO_REFRESH_DEFAULT, RISK_CRITICAL, RISK_HIGH, RISK_MEDIUM,
    SRI_LANKA_CITIES, APP_TITLE, APP_ICON, FEATURE_FORECAST,
    FEATURE_ANOMALY_DETECT, FEATURE_CORRELATION,
)
from database_manager import db

# ── Page config ───────────────────────────────────────────────────────────────
st.set_page_config(page_title=APP_TITLE, page_icon=APP_ICON, layout="wide",
                   initial_sidebar_state="expanded")

# ── Styling ───────────────────────────────────────────────────────────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Space+Mono:wght@400;700&family=DM+Sans:wght@300;400;600&display=swap');

html, body, [class*="css"] { font-family: 'DM Sans', sans-serif; }

/* Risk cards */
.risk-card {
    border-left: 4px solid #444;
    border-radius: 6px;
    padding: 14px 16px;
    margin-bottom: 10px;
    background: linear-gradient(135deg, #16213e 0%, #0f3460 100%);
    box-shadow: 0 2px 12px rgba(0,0,0,0.4);
    transition: transform 0.15s ease;
}
.risk-card:hover { transform: translateX(3px); }
.risk-critical { border-left-color: #ff4d4d; background: linear-gradient(135deg,#2e0d0d,#1a0000); }
.risk-high     { border-left-color: #ff9a00; background: linear-gradient(135deg,#2e1d00,#1a1000); }
.risk-low      { border-left-color: #00e676; background: linear-gradient(135deg,#0d2e14,#001a0a); }

/* Score badge */
.score-badge {
    font-family: 'Space Mono', monospace;
    font-size: 13px;
    font-weight: 700;
    padding: 3px 10px;
    border-radius: 12px;
    background: rgba(255,255,255,0.08);
}

/* Anomaly banner */
.anomaly-banner {
    background: linear-gradient(90deg,#7b0000,#1a0000);
    border: 1px solid #ff4d4d;
    border-radius: 8px;
    padding: 12px 18px;
    margin-bottom: 14px;
    font-size: 13px;
    color: #ffcccc;
}

/* Pulse indicator */
.pulse { display:inline-block; width:9px; height:9px; border-radius:50%;
         background:#00e676; box-shadow:0 0 0 0 rgba(0,230,118,.7);
         animation: pulseAnim 2s infinite; }
@keyframes pulseAnim {
    0%   { box-shadow: 0 0 0 0   rgba(0,230,118,.7); }
    70%  { box-shadow: 0 0 0 10px rgba(0,230,118,0); }
    100% { box-shadow: 0 0 0 0   rgba(0,230,118,0); }
}

/* Metric card */
.kpi-card {
    background: #0f3460;
    border-radius: 10px;
    padding: 16px 20px;
    text-align: center;
    border: 1px solid #1a4a7a;
}
.kpi-value { font-size: 2rem; font-weight: 700; font-family: 'Space Mono', monospace; }
.kpi-label { font-size: 0.75rem; color: #8ab4d4; text-transform: uppercase; letter-spacing: .08em; }
</style>
""", unsafe_allow_html=True)

# ── Trend keyword sets ────────────────────────────────────────────────────────
TREND_FOCUS_TERMS = {
    "power","electricity","fuel","gas","petrol","energy","grid","ceb","cpc",
    "road","traffic","train","bus","port","shipping","airline","flight","transport",
    "rupee","dollar","bank","tax","inflation","stock","market","imf","debt","economy",
    "tourist","hotel","visa","airport","travel","resort","booking",
    "farmer","crop","rice","fertilizer","food","tea","export",
    "protest","strike","curfew","police","attack","violence","disaster","flood",
    "crisis","emergency","warning","alert","shortage","blackout","shutdown",
    "layoff","recession","unemployment","bankrupt","supply","import","policy",
    "regulation","ministry","investment","budget","tariff","collapse","sanction",
}
TREND_STOPWORDS = {
    "the","and","for","with","from","that","this","news","sri","lanka",
    "breaking","update","daily","after","before","about","over","under",
    "ceylon","colombo","wants","need","make","going","said","says","will",
    "could","would","should","new","old","today","yesterday","latest",
    "report","reported","reports","people","story","thread","really",
    "thing","things","one","two","three","also","there","their","them",
    "been","has","have","had","into","onto","our","your","you","they",
}

# ── Session state init ────────────────────────────────────────────────────────
def _init():
    defaults = {
        "collector_started": False,
        "collector_error":   "",
        "collector_auto_start": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

_init()

# ── Collector helpers ─────────────────────────────────────────────────────────
def start_collector():
    try:
        from collector import collector
        if not collector.is_running:
            collector.start()
        st.session_state.collector_started = True
        st.session_state.collector_error   = ""
        return True
    except Exception as e:
        st.session_state.collector_error = str(e)
        return False

def stop_collector():
    try:
        from collector import collector
        collector.stop()
        st.session_state.collector_started = False
        return True
    except Exception as e:
        st.session_state.collector_error = str(e)
        return False

if st.session_state.collector_auto_start and not st.session_state.collector_started:
    start_collector()

# ── Data helpers ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=15)
def get_data(limit=1500, min_score=None, sources=None, fts=None):
    try:
        return db.get_risks(limit=limit, min_score=min_score,
                            sources=sources, fts_query=fts or None), db.get_risk_stats()
    except Exception:
        return pd.DataFrame(), {}

@st.cache_data(ttl=30)
def get_hourly(hours=48):
    return db.get_hourly_trend(hours=hours)

@st.cache_data(ttl=30)
def get_daily(days=30):
    return db.get_daily_trend(days=days)

@st.cache_data(ttl=30)
def get_cat_timeline(days=14):
    return db.get_category_timeline(days=days)

@st.cache_data(ttl=60)
def get_anomalies():
    return db.get_recent_anomalies(hours=48)

@st.cache_data(ttl=120)
def get_forecasts():
    from utils.forecast import generate_all_forecasts, get_overall_forecast
    return generate_all_forecasts(), get_overall_forecast()

def extract_map_data(df: pd.DataFrame) -> pd.DataFrame:
    pts = []
    if df.empty or "signal" not in df.columns:
        return pd.DataFrame()
    for _, row in df.iterrows():
        text  = str(row.get("signal", "")).lower()
        score = int(row.get("risk_score", 1) or 1)
        color = ([255, 77, 77, 210] if score >= RISK_CRITICAL
                 else [255, 154, 0, 190] if score >= RISK_HIGH
                 else [0,  230, 118, 160])
        for city, info in SRI_LANKA_CITIES.items():
            if city.lower() in text:
                pts.append({
                    "lat": info["lat"], "lon": info["lon"],
                    "City": city, "Province": info["province"],
                    "Source": row.get("source", ""),
                    "risk_score": score,
                    "signal": str(row.get("signal", "")),
                    "radius": max(12000, score * 4000),
                    "color": color,
                })
    return pd.DataFrame(pts)

def trending_keywords(df: pd.DataFrame, n: int = 12):
    if df.empty or "signal" not in df.columns:
        return []
    raw = re.findall(r"[A-Za-z][A-Za-z']+",
                     " ".join(df["signal"].fillna("").str.lower()))
    tokens = [t.capitalize() for t in raw
              if len(t) >= 4 and t not in TREND_STOPWORDS and t in TREND_FOCUS_TERMS]
    return Counter(tokens).most_common(n)

def score_color(score):
    if score >= RISK_CRITICAL: return "#ff4d4d"
    if score >= RISK_HIGH:     return "#ff9a00"
    return "#00e676"

def card_class(score):
    if score >= RISK_CRITICAL: return "risk-critical"
    if score >= RISK_HIGH:     return "risk-high"
    return "risk-low"

def clean_feed_text(value) -> str:
    text = "" if value is None else str(value)
    text = re.split(r"(?i)(?:<div\s+style=|&lt;div\s+style=)", text, maxsplit=1)[0]
    # Decode escaped HTML first so both raw and encoded tags can be removed.
    text = html.unescape(html.unescape(text))
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("```", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text

# ── Sidebar ───────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown(f"## {APP_ICON} MODEL-X")

    # Collector controls
    st.markdown("### Collector")
    st.session_state.collector_auto_start = st.checkbox(
        "Auto-start per session", value=st.session_state.collector_auto_start)

    status_label = "🟢 LIVE" if st.session_state.collector_started else "🟠 STANDBY"
    st.markdown(f"**Status:** {status_label}")

    c1, c2 = st.columns(2)
    with c1:
        if st.button("Start", use_container_width=True):
            if start_collector(): st.rerun()
    with c2:
        if st.button("Stop", use_container_width=True):
            if stop_collector(): st.rerun()

    if st.session_state.collector_error:
        st.error(st.session_state.collector_error)

    st.divider()

    # Filters
    st.markdown("### Filters")
    fts_query      = st.text_input("🔍 Full-text search", placeholder="flood, bankrupt, curfew…")
    selected_industry = st.selectbox("Sector", [
        "All", "Energy & Fuel", "Logistics & Transport", "Finance & Economy",
        "Tourism", "Agriculture", "Public Safety"])
    stats_snap  = db.get_risk_stats()
    src_options = sorted({s["source"] for s in stats_snap.get("sources", []) if s.get("source")})
    sel_sources = st.multiselect("Sources", options=src_options)
    min_risk    = st.slider("Min Risk Score", 1, 10, 1)

    st.divider()

    # Settings
    st.markdown("### Settings")
    refresh_rate = st.slider("Auto-refresh (s)", 10, 300, 30)
    auto_refresh = st.checkbox("Auto-Refresh", value=AUTO_REFRESH_DEFAULT)
    show_limit   = st.slider("Feed items shown", 10, 100, 30)

    st.divider()

    # Demo crisis inject
    st.markdown("### Demo")
    demo_city = st.selectbox("Crisis city", list(SRI_LANKA_CITIES.keys()))
    if st.button("⚡ Inject Crisis", use_container_width=True):
        _demo_map = {
            "Colombo":      ("MAJOR POWER FAILURE: Island-wide blackout in Colombo. Emergency protocols activated.",
                             "Energy & Fuel, Public Safety", "blackout,emergency", 10, -0.92, 1.0),
            "Kandy":        ("Severe transport disruption in Kandy due to emergency road closures.",
                             "Logistics & Transport, Public Safety", "transport,closure", 8, -0.71, 0.95),
            "Galle":        ("Flood warning issued for Galle coastal belt. Heavy rainfall expected.",
                             "Public Safety, Agriculture", "flood,warning", 9, -0.80, 0.97),
            "Jaffna":       ("Public order disruption in Jaffna after emergency service delays.",
                             "Public Safety", "public order,emergency", 8, -0.75, 0.96),
            "Trincomalee":  ("Port operations suspended in Trincomalee following industrial action.",
                             "Logistics & Transport", "port,strike", 7, -0.65, 0.90),
        }
        sig, cat, kw, sc, sent, conf = _demo_map.get(
            demo_city, _demo_map["Colombo"])
        db.batch_insert_risks([{
            "source": "National Alert System (DEMO)", "signal": sig,
            "risk_score": sc, "category": cat, "location": demo_city,
            "link": "#", "published": datetime.now().isoformat(),
            "created_at": datetime.now().isoformat(),
            "sentiment_score": sent, "confidence": conf, "keywords": kw,
        }])
        st.toast(f"Crisis scenario injected for {demo_city}.")
        st.rerun()

    st.divider()

    # CSV export
    st.markdown("### Export")
    _exp_df, _ = get_data(limit=5000, min_score=min_risk if min_risk > 1 else None)
    if not _exp_df.empty:
        st.download_button(
            "📥 Download CSV", data=_exp_df.to_csv(index=False),
            file_name=f"modelx_{datetime.now():%Y%m%d_%H%M}.csv",
            mime="text/csv", use_container_width=True)

# ── Load data ─────────────────────────────────────────────────────────────────
df, stats = get_data(
    limit=1500,
    min_score=min_risk if min_risk > 1 else None,
    sources=sel_sources or None,
    fts=fts_query or None,
)

# Sector filter
if selected_industry != "All" and not df.empty:
    df = df[df["category"].str.contains(selected_industry, case=False, na=False)]

total      = len(df)
high_risk  = len(df[df["risk_score"] >= RISK_CRITICAL]) if not df.empty else 0
avg_score  = round(df["risk_score"].mean(), 2) if not df.empty else 0
sources_n  = len(stats.get("sources", []))
recent_24h = stats.get("recent_24h", 0)

# ── Top KPIs ──────────────────────────────────────────────────────────────────
st.markdown(f"# {APP_ICON} MODEL-X: National Risk Intelligence")
k1, k2, k3, k4, k5 = st.columns(5)
for col, val, label, color in [
    (k1, total,      "Total Signals",    "#4fc3f7"),
    (k2, high_risk,  "Critical Alerts",  "#ff4d4d"),
    (k3, avg_score,  "Avg Risk Score",   "#ff9a00"),
    (k4, sources_n,  "Active Sources",   "#69f0ae"),
    (k5, recent_24h, "Last 24h",         "#ce93d8"),
]:
    col.markdown(f"""
    <div class="kpi-card">
        <div class="kpi-value" style="color:{color}">{val}</div>
        <div class="kpi-label">{label}</div>
    </div>""", unsafe_allow_html=True)

st.markdown("<br>", unsafe_allow_html=True)

# Anomaly banners at top
anomalies_df = get_anomalies()
if not anomalies_df.empty:
    for _, arow in anomalies_df.head(3).iterrows():
        st.markdown(f"""
        <div class="anomaly-banner">
            🔔 <strong>ANOMALY DETECTED</strong> — {arow.get('category','?')} &nbsp;|&nbsp;
            z={arow.get('score_z',0):.2f} &nbsp;|&nbsp;
            Peak: {arow.get('peak_score','?')}/10 &nbsp;|&nbsp;
            {arow.get('description','')[:120]}
        </div>""", unsafe_allow_html=True)

# ── 5 Tabs ────────────────────────────────────────────────────────────────────
tab_geo, tab_analytics, tab_feed, tab_forecast, tab_health = st.tabs(
    ["🗺 Geospatial", "📊 Analytics", "📡 Live Feed", "🔭 Forecast", "🩺 Source Health"])

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 1: Geospatial
# ═══════════════════════════════════════════════════════════════════════════════
with tab_geo:
    map_df = extract_map_data(df)
    if not map_df.empty:
        scatter = pdk.Layer(
            "ScatterplotLayer", data=map_df,
            get_position="[lon, lat]", get_radius="radius",
            get_fill_color="color", pickable=True, opacity=0.55,
        )
        heatmap = pdk.Layer(
            "HeatmapLayer", data=map_df,
            get_position="[lon, lat]", get_weight="risk_score",
            opacity=0.25,
        )
        view = pdk.ViewState(latitude=7.87, longitude=80.77, zoom=7, pitch=25)
        tooltip = {
            "html": "<b>{City}</b> ({Province})<br/><i>{Source}</i><br/>Risk: <b>{risk_score}/10</b><br/>{signal}",
            "style": {"color": "white", "backgroundColor": "#0d0d1a", "fontSize": "12px",
                      "borderRadius": "6px", "padding": "8px"},
        }
        st.pydeck_chart(pdk.Deck(
            layers=[heatmap, scatter],
            initial_view_state=view, tooltip=tooltip,
            map_style="mapbox://styles/mapbox/dark-v10",
        ))

        with st.expander("Location breakdown"):
            city_risk = map_df.groupby("City").agg(
                avg_risk=("risk_score", "mean"),
                max_risk=("risk_score", "max"),
                count=("risk_score", "count"),
            ).reset_index().sort_values("avg_risk", ascending=False)
            st.dataframe(city_risk, use_container_width=True, hide_index=True)
    else:
        st.info("No location-tagged risks found in current filter. Try lowering Min Risk Score.")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 2: Analytics
# ═══════════════════════════════════════════════════════════════════════════════
with tab_analytics:
    if df.empty:
        st.info("No data matching current filters.")
    else:
        # ── Row 1: Hourly trend + Category timeline ─────────────────────────
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Hourly Risk Volume")
            hourly = get_hourly(48)
            if not hourly.empty:
                fig = px.bar(hourly, x="bucket", y="event_count",
                             color="category",
                             title="Events per hour (last 48h)",
                             color_discrete_sequence=px.colors.qualitative.Vivid)
                fig.update_layout(xaxis_title="Hour", yaxis_title="Events",
                                  legend_title="Category",
                                  plot_bgcolor="rgba(0,0,0,0)",
                                  paper_bgcolor="rgba(0,0,0,0)",
                                  font_color="white")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("Not enough hourly data yet.")

        with c2:
            st.subheader("Category Risk Timeline")
            cat_tl = get_cat_timeline(14)
            if not cat_tl.empty:
                fig = px.line(cat_tl, x="day", y="avg_score", color="category",
                              title="Avg risk score per category (14 days)",
                              markers=True,
                              color_discrete_sequence=px.colors.qualitative.Bold)
                fig.update_layout(plot_bgcolor="rgba(0,0,0,0)",
                                  paper_bgcolor="rgba(0,0,0,0)",
                                  font_color="white",
                                  yaxis=dict(range=[0, 10.5]))
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.caption("Not enough multi-day data yet.")

        st.divider()

        # ── Row 2: Keyword bar + Risk histogram ─────────────────────────────
        c3, c4 = st.columns(2)
        with c3:
            st.subheader("Trending Risk Keywords")
            kws = trending_keywords(df, 12)
            if kws:
                kdf = pd.DataFrame(kws, columns=["Keyword", "Count"])
                fig = px.bar(kdf, x="Count", y="Keyword", orientation="h",
                             color="Count", color_continuous_scale="Inferno")
                fig.update_layout(yaxis={"categoryorder": "total ascending"},
                                  showlegend=False,
                                  plot_bgcolor="rgba(0,0,0,0)",
                                  paper_bgcolor="rgba(0,0,0,0)",
                                  font_color="white")
                st.plotly_chart(fig, use_container_width=True)
            else:
                st.info("No risk keywords found.")

        with c4:
            st.subheader("Risk Score Distribution")
            fig = px.histogram(df, x="risk_score", nbins=10,
                               color_discrete_sequence=["#ff4d4d"],
                               title="Score histogram")
            fig.update_layout(plot_bgcolor="rgba(0,0,0,0)",
                              paper_bgcolor="rgba(0,0,0,0)",
                              font_color="white")
            st.plotly_chart(fig, use_container_width=True)

        st.divider()

        # ── Row 3: Industry pie + Correlation heatmap ───────────────────────
        c5, c6 = st.columns(2)
        with c5:
            st.subheader("Industry Impact")
            if "category" in df.columns:
                cats = df["category"].str.split(",").explode().str.strip()
                cdf  = cats.value_counts().reset_index()
                cdf.columns = ["Industry", "Count"]
                fig = px.pie(cdf, values="Count", names="Industry", hole=0.45,
                             color_discrete_sequence=px.colors.qualitative.Pastel)
                fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="white")
                st.plotly_chart(fig, use_container_width=True)

        with c6:
            if FEATURE_CORRELATION:
                st.subheader("Source × Score Correlation")
                num_cols = ["risk_score", "sentiment_score", "confidence"]
                available = [c for c in num_cols if c in df.columns and df[c].notna().any()]
                if len(available) >= 2:
                    corr = df[available].corr()
                    fig  = px.imshow(corr, text_auto=True, color_continuous_scale="RdBu_r",
                                     title="Feature correlation matrix",
                                     zmin=-1, zmax=1)
                    fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", font_color="white")
                    st.plotly_chart(fig, use_container_width=True)
                else:
                    st.info("Insufficient numeric columns for correlation.")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: Live Feed
# ═══════════════════════════════════════════════════════════════════════════════
import html
from datetime import datetime
import textwrap  # Added for clean string formatting

import html
from datetime import datetime

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 3: Live Feed
# ═══════════════════════════════════════════════════════════════════════════════
with tab_feed:
    if high_risk > 0:
        st.error(f"🚨 **{high_risk} critical signal(s)** detected (score ≥ {RISK_CRITICAL}). Review below.")

    if df.empty:
        st.info("No signals match current filters.")
    else:
        feed_df = df.head(show_limit)
        for _, row in feed_df.iterrows():
            score     = int(row.get("risk_score", 0) or 0)
            sentiment = float(row.get("sentiment_score", 0) or 0)
            conf      = float(row.get("confidence", 0) or 0)
            clazz     = card_class(score)
            color     = score_color(score)
            source_text = clean_feed_text(row.get("source", ""))
            signal_text = clean_feed_text(row.get("signal", ""))
            category_text = clean_feed_text(row.get("category", ""))

            try:
                pub = datetime.fromisoformat(str(row.get("published", "")))
                diff = datetime.now() - pub
                time_str = f"{diff.seconds // 3600}h ago" if diff.days == 0 else pub.strftime("%d %b %Y")
            except Exception:
                time_str = str(row.get("published", ""))

            # Flattened AI Summary string to avoid parser-breaking newlines
            ai_sum = clean_feed_text(row.get("ai_summary", "") or "")
            ai_html = f"<div style='font-size:11px;color:#a0cfee;margin-top:6px;'>🤖 {ai_sum}</div>" if ai_sum else ""

            # Flattened Keywords string to avoid parser-breaking newlines
            keywords  = row.get("keywords", "") or ""
            kw_badges = " ".join(
                f"<span style='background:#1a2a3a;padding:1px 6px;border-radius:10px;font-size:10px;'>{clean_feed_text(kw.strip())}</span>"
                for kw in keywords.split(",") if clean_feed_text(kw.strip())
            )

            safe_source = html.escape(source_text)
            safe_signal = html.escape(signal_text)
            safe_category = html.escape(category_text)
            safe_time = html.escape(time_str)

            link = str(row.get("link", "#") or "#").strip()
            if not (link.startswith("http://") or link.startswith("https://")):
                link = "#"

            # Formatted exactly like your working code
            st.markdown(f"""<div class="risk-card {clazz}">
<div style="display: flex; justify-content: space-between; align-items: center;">
<span style="font-size: 13px; font-weight: 600; color: #cde;">{safe_source}</span>
<span class="score-badge" style="color: {color}">⚡ {score}/10</span>
</div>
<div style="color: #dde; margin: 7px 0 5px; font-size: 14px; line-height: 1.45;">
{safe_signal}
</div>
{ai_html}
<div style="font-size: 12px; color: #888; margin-top: 12px; display: flex; justify-content: space-between; flex-wrap: wrap; gap: 4px;">
<span style="background: #0d2030; padding: 2px 8px; border-radius: 8px;">{safe_category}</span>
<span>Sent: {sentiment:+.2f}</span>
<span>Conf: {conf:.0%}</span>
<div>
<span style="margin-right: 10px;">{safe_time}</span>
<a href="{link}" target="_blank" style="color: #4fc3f7; text-decoration: none;">View ↗</a>
</div>
</div>
<div style="margin-top: 6px;">{kw_badges}</div>
</div>
            """, unsafe_allow_html=True)
# ═══════════════════════════════════════════════════════════════════════════════
# TAB 4: Forecast
# ═══════════════════════════════════════════════════════════════════════════════
with tab_forecast:
    if not FEATURE_FORECAST:
        st.info("Forecast feature is disabled. Set `FEATURE_FORECAST=true` in `.env` to enable.")
    else:
        st.subheader("7-Day Risk Forecast")
        st.caption("Double exponential smoothing + linear trend blend. Shaded region = 95% CI.")

        cat_forecasts, overall_fc = get_forecasts()

        # Overall forecast
        if not overall_fc.empty:
            fig = go.Figure()
            fig.add_trace(go.Scatter(
                x=overall_fc["day"], y=overall_fc["upper_ci"],
                line=dict(width=0), showlegend=False, name="Upper CI"))
            fig.add_trace(go.Scatter(
                x=overall_fc["day"], y=overall_fc["lower_ci"],
                fill="tonexty", fillcolor="rgba(79,195,247,0.15)",
                line=dict(width=0), showlegend=True, name="95% CI"))
            fig.add_trace(go.Scatter(
                x=overall_fc["day"], y=overall_fc["forecast"],
                line=dict(color="#4fc3f7", width=2.5),
                mode="lines+markers", name="Overall Forecast"))
            fig.update_layout(
                title="Overall National Risk — 7-day outlook",
                xaxis_title="Date", yaxis_title="Risk Score (1-10)",
                yaxis=dict(range=[0, 10.5]),
                plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                font_color="white", hovermode="x unified")
            st.plotly_chart(fig, use_container_width=True)

        st.divider()

        # Per-category forecasts
        if cat_forecasts:
            cols = st.columns(min(len(cat_forecasts), 3))
            for i, (cat, fc_df) in enumerate(cat_forecasts.items()):
                with cols[i % 3]:
                    fig = go.Figure()
                    fig.add_trace(go.Scatter(
                        x=fc_df["day"], y=fc_df["upper_ci"],
                        line=dict(width=0), showlegend=False))
                    fig.add_trace(go.Scatter(
                        x=fc_df["day"], y=fc_df["lower_ci"],
                        fill="tonexty", fillcolor="rgba(255,154,0,0.12)",
                        line=dict(width=0), showlegend=False))
                    fig.add_trace(go.Scatter(
                        x=fc_df["day"], y=fc_df["forecast"],
                        line=dict(color="#ff9a00", width=2),
                        mode="lines+markers"))
                    fig.update_layout(
                        title=f"{cat}",
                        height=250, margin=dict(t=40, b=20, l=10, r=10),
                        yaxis=dict(range=[0, 10.5]),
                        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                        font_color="white", showlegend=False)
                    st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("Not enough historical data to forecast yet. Collect data for 3+ days per category.")

# ═══════════════════════════════════════════════════════════════════════════════
# TAB 5: Source Health
# ═══════════════════════════════════════════════════════════════════════════════
with tab_health:
    from utils.health import health_monitor

    dash = health_monitor.get_dashboard_data()
    overall = dash["overall_health"]

    st.subheader("System Health Overview")
    h1, h2, h3, h4 = st.columns(4)
    h1.metric("System Status",    overall.get("overall_status","N/A").upper())
    h2.metric("Health",           overall.get("health_percentage","N/A"),
              f"{overall.get('healthy_sources',0)}/{overall.get('active_sources',0)} sources")
    h3.metric("Success Rate",     overall.get("overall_success_rate","N/A"))
    h4.metric("Total Attempts",   overall.get("total_attempts",0))

    st.divider()

    # Source table
    src_health = dash["sources_health"]
    if src_health:
        rows = []
        for name, s in src_health.items():
            rows.append({
                "Source":      name,
                "Type":        s["source_type"],
                "Status":      s["status"].upper(),
                "Last Fetch":  s.get("time_since_last_fetch") or "Never",
                "Success":     s["success_count"],
                "Failures":    s["failure_count"],
                "Success %":   s["success_rate"],
                "Items (last)":s["last_items_count"],
                "Items (total)":s.get("total_items_collected", 0),
                "Last Error":  (s["last_error"] or "")[:60] if s["last_error"] != "None" else "✓",
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    # Charts
    if src_health:
        c1, c2 = st.columns(2)
        with c1:
            rates = [{"Source": n, "Rate": float(s["success_rate"].rstrip("%"))}
                     for n, s in src_health.items()]
            fig = px.bar(pd.DataFrame(rates).sort_values("Rate"),
                         x="Rate", y="Source", orientation="h",
                         color="Rate", color_continuous_scale=["red","yellow","green"],
                         range_color=[0, 100], title="Success Rate by Source")
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                              font_color="white")
            st.plotly_chart(fig, use_container_width=True)
        with c2:
            att = [{"Source": n, "Successful": s["success_count"], "Failed": s["failure_count"]}
                   for n, s in src_health.items()]
            fig = px.bar(pd.DataFrame(att), x="Source", y=["Successful", "Failed"],
                         barmode="stack", title="Collection Attempts",
                         color_discrete_map={"Successful": "#69f0ae", "Failed": "#ff5252"})
            fig.update_layout(paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                              font_color="white")
            st.plotly_chart(fig, use_container_width=True)

    # Logs
    logs = dash.get("recent_logs", [])
    if logs:
        with st.expander("Recent collection logs"):
            log_rows = [{"Time": l["timestamp"], "Source": l["source"],
                         "Status": "✓" if l["success"] else "✗",
                         "Items": l["items"],
                         "Error": l.get("error","") or ""}
                        for l in reversed(logs[-80:])]
            st.dataframe(pd.DataFrame(log_rows), use_container_width=True, hide_index=True)

    # Export
    st.divider()
    report = health_monitor.export_health_report()
    st.download_button("Download Health Report (JSON)", data=report,
                       file_name=f"health_{datetime.now():%Y%m%d_%H%M}.json",
                       mime="application/json")

# ── Auto refresh ──────────────────────────────────────────────────────────────
if auto_refresh:
    st_autorefresh(interval=refresh_rate * 1000, key="modelx_refresh")
