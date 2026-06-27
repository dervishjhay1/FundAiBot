"""
FundzAiBot — Decision Engine

Every autonomous action TestAudit considers must pass through this engine.

Decision rules:
  If ALL of the following:
    - confidence >= CONFIDENCE_THRESHOLD (0.85)
    - action_type is in OPERATIONAL_ACTIONS (safe, operational only)
    - business_risk is False
    - irreversible is False
  → Execute automatically

  Otherwise:
    → Queue for CEO approval via Telegram DM

Emergency actions:
  Only stabilization, logging, and reporting are ever auto-executed.
  Never: pricing, policy, database schema, deployment strategy.
"""

from __future__ import annotations

import requests

from config.settings import TELEGRAM_BOT_TOKEN, ADMIN_USER_ID
from utils.logger import get_logger

log = get_logger(__name__)

# ── Decision constants ────────────────────────────────────────────────────────

CONFIDENCE_THRESHOLD = 0.85

# Actions that may be auto-executed without CEO approval
OPERATIONAL_ACTIONS = frozenset({
    "send_community_message",    # post discussion starter to group
    "send_channel_post",         # post educational content to channel
    "log_memory",                # write to operational memory
    "calculate_health",          # run health check
    "send_morning_brief",        # send morning report to CEO
    "send_evening_brief",        # send evening report to CEO
    "send_weekly_report",        # send weekly report to CEO
    "re_engagement_suggestion",  # prepare (not send) re-engagement message
    "refresh_cache",             # in-memory cache refresh
    "update_backlog",            # add item to improvement backlog
    "risk_alert_dm",             # DM CEO with risk alert (informational only)
})

# Actions that ALWAYS require CEO approval
REQUIRES_CEO_APPROVAL = frozenset({
    "broadcast_to_all_users",
    "ban_user",
    "change_pricing",
    "modify_vip_plan",
    "change_policy",
    "database_migration",
    "deploy_change",
    "send_bulk_dm",
    "change_bot_settings",
})


def evaluate(
    action_type: str,
    title: str,
    description: str,
    payload: dict,
    confidence: float,
    business_risk: bool = False,
    irreversible: bool = False,
) -> dict:
    """
    Evaluate whether an action should be auto-executed or sent for CEO approval.

    Returns:
        {
          "decision": "auto" | "ceo_required",
          "reason": str,
          "action_type": str,
          "confidence": float,
        }
    """
    # Hard block: always-requires-CEO list
    if action_type in REQUIRES_CEO_APPROVAL:
        reason = f"Action '{action_type}' always requires CEO approval"
        log.info("Decision: CEO required — %s", reason)
        return {
            "decision":    "ceo_required",
            "reason":      reason,
            "action_type": action_type,
            "confidence":  confidence,
        }

    # Business risk or irreversible → CEO
    if business_risk:
        reason = "Action carries business risk — escalating to CEO"
        log.info("Decision: CEO required — %s", reason)
        return {
            "decision":    "ceo_required",
            "reason":      reason,
            "action_type": action_type,
            "confidence":  confidence,
        }

    if irreversible:
        reason = "Action is irreversible — escalating to CEO"
        log.info("Decision: CEO required — %s", reason)
        return {
            "decision":    "ceo_required",
            "reason":      reason,
            "action_type": action_type,
            "confidence":  confidence,
        }

    # Not in operational whitelist → CEO
    if action_type not in OPERATIONAL_ACTIONS:
        reason = f"Action '{action_type}' not in operational whitelist"
        log.info("Decision: CEO required — %s", reason)
        return {
            "decision":    "ceo_required",
            "reason":      reason,
            "action_type": action_type,
            "confidence":  confidence,
        }

    # Below confidence threshold → CEO
    if confidence < CONFIDENCE_THRESHOLD:
        reason = f"Confidence {confidence:.0%} below threshold {CONFIDENCE_THRESHOLD:.0%}"
        log.info("Decision: CEO required — %s", reason)
        return {
            "decision":    "ceo_required",
            "reason":      reason,
            "action_type": action_type,
            "confidence":  confidence,
        }

    # All checks passed → auto-execute
    log.debug("Decision: auto-execute — %s (confidence=%.0f%%)", action_type, confidence * 100)
    return {
        "decision":    "auto",
        "reason":      f"Confidence {confidence:.0%} >= threshold, operational action, no risk",
        "action_type": action_type,
        "confidence":  confidence,
    }


def notify_ceo(title: str, message: str, parse_mode: str = "HTML") -> bool:
    """
    Send a direct message to the CEO (ADMIN_USER_ID) via Telegram Bot API.
    Used for: risk alerts, approval requests, executive reports.
    Returns True on success.
    """
    if not TELEGRAM_BOT_TOKEN or not ADMIN_USER_ID:
        log.warning("notify_ceo: missing token or admin_user_id")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    ADMIN_USER_ID,
                "text":       message,
                "parse_mode": parse_mode,
            },
            timeout=10,
        )
        if r.status_code == 200:
            log.info("CEO notified: %s", title)
            return True
        log.warning("notify_ceo HTTP %d: %s", r.status_code, r.text[:80])
        return False
    except Exception as exc:
        log.warning("notify_ceo error: %s", exc)
        return False


def send_approval_request(
    title: str,
    description: str,
    action_type: str,
    confidence: float,
    risk_level: str = "medium",
) -> None:
    """Send a CEO approval request via DM."""
    from services.testaudit_core import queue_ceo_approval
    queue_ceo_approval(
        action_type=action_type,
        title=title,
        description=description,
        payload={},
        confidence=confidence,
        risk_level=risk_level,
    )
    msg = (
        f"🔔 <b>TestAudit — Approval Request</b>\n\n"
        f"<b>Action:</b> {title}\n"
        f"<b>Type:</b> <code>{action_type}</code>\n"
        f"<b>Risk:</b> {risk_level}\n"
        f"<b>Confidence:</b> {confidence:.0%}\n\n"
        f"{description}\n\n"
        f"<i>Use /testaudit → Pending Approvals to review.</i>"
    )
    notify_ceo(title, msg)
