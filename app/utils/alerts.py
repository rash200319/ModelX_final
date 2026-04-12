"""
MODEL-X Alert Dispatcher
Sends critical risk notifications to webhook endpoints (Slack / Teams / Discord)
and optionally via SMTP email.
"""
import json
import logging
import smtplib
from email.mime.text import MIMEText
from typing import Any, Dict

import requests

from config import (
    FEATURE_ALERTS, ALERT_WEBHOOK_URL,
    ALERT_EMAIL_ENABLED, ALERT_EMAIL_TO,
    ALERT_SMTP_HOST, ALERT_SMTP_PORT,
    ALERT_SMTP_USER, ALERT_SMTP_PASS,
    RISK_CRITICAL,
)

logger = logging.getLogger(__name__)


def _send_webhook(payload: Dict[str, Any]) -> bool:
    if not ALERT_WEBHOOK_URL:
        return False
    try:
        score    = payload.get("score", 0)
        signal   = payload.get("signal", "")
        source   = payload.get("source", "")
        category = payload.get("category", "")
        link     = payload.get("link", "#")

        body = {
            "text": f"🚨 *CRITICAL RISK {score}/10* — {source}\n>{signal}\nCategory: {category}\n<{link}|View source>"
        }
        resp = requests.post(ALERT_WEBHOOK_URL, json=body, timeout=8)
        resp.raise_for_status()
        logger.info(f"Webhook alert sent for risk {score}/10")
        return True
    except Exception as e:
        logger.warning(f"Webhook dispatch failed: {e}")
        return False


def _send_email(payload: Dict[str, Any]) -> bool:
    if not ALERT_EMAIL_ENABLED or not ALERT_EMAIL_TO:
        return False
    try:
        score   = payload.get("score", 0)
        signal  = payload.get("signal", "")
        source  = payload.get("source", "")
        link    = payload.get("link", "#")

        body = (
            f"MODEL-X Critical Alert\n\n"
            f"Risk Score: {score}/10\n"
            f"Source: {source}\n"
            f"Signal: {signal}\n"
            f"Link: {link}\n"
        )
        msg          = MIMEText(body)
        msg["Subject"] = f"[MODEL-X] Critical Risk Alert — {score}/10"
        msg["From"]    = ALERT_SMTP_USER
        msg["To"]      = ALERT_EMAIL_TO

        with smtplib.SMTP(ALERT_SMTP_HOST, ALERT_SMTP_PORT) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(ALERT_SMTP_USER, ALERT_SMTP_PASS)
            smtp.send_message(msg)
        logger.info(f"Email alert sent for risk {score}/10")
        return True
    except Exception as e:
        logger.warning(f"Email dispatch failed: {e}")
        return False


def dispatch_alert(payload: Dict[str, Any], risk_id: str = ""):
    """Send alert via all configured channels."""
    if not FEATURE_ALERTS:
        return
    from database_manager import db

    ok_webhook = _send_webhook(payload)
    ok_email   = _send_email(payload)

    db.log_alert(
        channel="webhook",
        risk_id=risk_id,
        payload=json.dumps(payload),
        success=ok_webhook,
    )
    if ALERT_EMAIL_ENABLED:
        db.log_alert(
            channel="email",
            risk_id=risk_id,
            payload=json.dumps(payload),
            success=ok_email,
        )
