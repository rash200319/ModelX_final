"""
MODEL-X Forecast Module
Generates 7-day risk forecasts per category using:
  - Holt-Winters (double) exponential smoothing
  - Simple linear regression trend extrapolation
  - Confidence interval estimation via residual variance
"""
import logging
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config import FEATURE_FORECAST

logger = logging.getLogger(__name__)


def _holt_smooth(series: np.ndarray, alpha: float = 0.3, beta: float = 0.1) -> Tuple[np.ndarray, float, float]:
    """Double exponential smoothing. Returns smoothed series, final level, final trend."""
    n = len(series)
    s = np.empty(n)
    b = np.empty(n)
    s[0] = series[0]
    b[0] = series[1] - series[0] if n > 1 else 0.0
    for t in range(1, n):
        s[t] = alpha * series[t] + (1 - alpha) * (s[t - 1] + b[t - 1])
        b[t] = beta  * (s[t] - s[t - 1]) + (1 - beta) * b[t - 1]
    return s, s[-1], b[-1]


def _linear_trend(values: np.ndarray) -> Tuple[float, float]:
    """Returns (slope, intercept) via least squares."""
    x = np.arange(len(values), dtype=float)
    slope, intercept = np.polyfit(x, values, 1)
    return float(slope), float(intercept)


def forecast_category(scores: List[float], horizon: int = 7) -> pd.DataFrame:
    """
    Forecast `horizon` days ahead given a list of daily average scores.
    Returns DataFrame with columns: day, forecast, lower_ci, upper_ci.
    """
    if len(scores) < 3:
        return pd.DataFrame()

    arr = np.array(scores, dtype=float)

    # Smooth
    _, level, trend = _holt_smooth(arr)

    # Compute in-sample residuals for CI
    slope, intercept = _linear_trend(arr)
    fitted   = np.array([intercept + slope * i for i in range(len(arr))])
    residuals = arr - fitted
    sigma     = residuals.std()

    rows = []
    today = datetime.utcnow().date()
    for h in range(1, horizon + 1):
        point = level + trend * h
        # Blend Holt with linear trend
        linear_pt = intercept + slope * (len(arr) + h)
        point = 0.6 * point + 0.4 * linear_pt
        point = float(np.clip(point, 1.0, 10.0))
        ci    = 1.96 * sigma * np.sqrt(h)   # widen CI with horizon
        rows.append({
            "day":      str(today + timedelta(days=h)),
            "forecast": round(point, 2),
            "lower_ci": round(float(np.clip(point - ci, 1.0, 10.0)), 2),
            "upper_ci": round(float(np.clip(point + ci, 1.0, 10.0)), 2),
        })
    return pd.DataFrame(rows)


def generate_all_forecasts(horizon: int = 7) -> Dict[str, pd.DataFrame]:
    """
    Generate forecasts for every category present in the DB.
    Returns dict: {category: forecast_df}
    """
    if not FEATURE_FORECAST:
        return {}
    try:
        from database_manager import db
        timeline = db.get_category_timeline(days=21)
        if timeline.empty:
            return {}

        results = {}
        for cat, grp in timeline.groupby("category"):
            grp_sorted = grp.sort_values("day")
            scores     = grp_sorted["avg_score"].tolist()
            if len(scores) < 3:
                continue
            fc = forecast_category(scores, horizon)
            if not fc.empty:
                results[cat] = fc
        return results
    except Exception as e:
        logger.error(f"generate_all_forecasts: {e}")
        return {}


def get_overall_forecast(horizon: int = 7) -> pd.DataFrame:
    """
    Aggregate forecast across all categories into a single overall risk forecast.
    """
    if not FEATURE_FORECAST:
        return pd.DataFrame()
    try:
        from database_manager import db
        trend = db.get_daily_trend(days=21)
        if trend.empty or "avg_score" not in trend.columns:
            return pd.DataFrame()
        scores = trend["avg_score"].tolist()
        return forecast_category(scores, horizon)
    except Exception as e:
        logger.error(f"get_overall_forecast: {e}")
        return pd.DataFrame()
