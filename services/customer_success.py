"""
FundzAiBot — Customer Success Manager (TestAudit role)

Monitors user engagement and identifies users who have become inactive.
Prepares thoughtful re-engagement suggestions for CEO review.

Policy:
  - Identifies users inactive for 3+ days since their last activity
  - Identifies users who started onboarding but never completed a full chat
  - Prepares re-engagement message drafts (does NOT auto-send to users)
  - Sends a summary to the CEO for awareness and optional action
  - Respects user experience — avoids excessive messaging
  - Runs daily at 14:00 UTC

CEO decides whether to act on suggestions via /testaudit → Customer Success.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, ADMIN_USER_ID,
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
    BOT_NAME,
)
from utils.logger import get_logger

log = get_logger(__name__)

_running: bool = False
_thread:  threading.Thread | None = None
_last_run_date: str = ""

# ── Supabase helpers ──────────────────────────────────────────────────────────

def _hdrs() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


def _sb_get(path: str, params: dict | None = None):
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return requests.get(
            f"{SUPABASE_URL}/rest/v1/{path}",
            headers=_hdrs(), params=params or {}, timeout=(5, 12),
        )
    except Exception:
        return None


# ── Inactive user detection ───────────────────────────────────────────────────

def find_inactive_users(inactive_days: int = 3, limit: int = 50) -> list[dict]:
    """
    Find users who have not been seen in the last `inactive_days` days.
    Excludes banned users. Returns a list of user dicts.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=inactive_days)).strftime("%Y-%m-%dT%H:%M:%S")
    r = _sb_get("users", {
        "last_seen":  f"lt.{cutoff}",
        "is_banned":  "eq.false",
        "select":     "user_id,first_name,last_name,last_seen,is_vip,vip_tier,created_at",
        "order":      "last_seen.asc",
        "limit":      str(limit),
    })
    if r and r.status_code == 200:
        return r.json()
    return []


def find_abandoned_onboarders(limit: int = 30) -> list[dict]:
    """
    Find users who registered but never sent a chat message.
    These are onboarding dropouts — high potential re-engagement targets.
    """
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S")
        # Get users created more than 1 day ago
        users_r = _sb_get("users", {
            "created_at": f"lt.{cutoff}",
            "is_banned":  "eq.false",
            "select":     "user_id,first_name,created_at",
            "limit":      "200",
        })
        if not users_r or users_r.status_code != 200:
            return []

        all_users = users_r.json()
        user_ids = [str(u["user_id"]) for u in all_users]

        # Find which have conversations
        convo_r = _sb_get("conversations", {
            "role":    "eq.user",
            "select":  "user_id",
            "limit":   "500",
        })
        active_ids: set[str] = set()
        if convo_r and convo_r.status_code == 200:
            active_ids = {str(row["user_id"]) for row in convo_r.json()}

        abandoned = [u for u in all_users if str(u["user_id"]) not in active_ids]
        return abandoned[:limit]
    except Exception as exc:
        log.debug("customer_success.find_abandoned_onboarders: %s", exc)
        return []


# ── Re-engagement suggestions ─────────────────────────────────────────────────

def _build_reengagement_suggestion(user: dict) -> str:
    """Generate a suggested message for a specific inactive user (for CEO review only)."""
    name = user.get("first_name") or "there"
    is_vip = user.get("is_vip", False)
    last_seen = user.get("last_seen", "")[:10] if user.get("last_seen") else "a while ago"

    if is_vip:
        return (
            f"Hey {name}! 👋 We noticed you haven't been around since {last_seen}. "
            f"As a VIP member, you still have premium AI access waiting for you. "
            f"A lot has improved — try /chat for a fresh conversation with our upgraded AI models!"
        )
    else:
        return (
            f"Hey {name}! 👋 It's been a while! "
            f"{BOT_NAME} has new AI models, image generation, and tools since you last visited. "
            f"Come back and try /chat — no cost, no commitment."
        )


# ── CEO Summary ───────────────────────────────────────────────────────────────

def build_customer_success_report() -> str:
    inactive  = find_inactive_users(inactive_days=3, limit=10)
    abandoned = find_abandoned_onboarders(limit=10)

    lines = [
        f"👤 <b>Customer Success Report — {BOT_NAME}</b>",
        f"<i>{datetime.now(timezone.utc).strftime('%B %d, %Y')}</i>",
        "",
    ]

    lines.append(f"<b>📉 Inactive Users (3+ days)</b>: {len(inactive)} found")
    if inactive:
        for u in inactive[:5]:
            name = u.get("first_name") or f"user_{u['user_id']}"
            last = u.get("last_seen", "")[:10] if u.get("last_seen") else "unknown"
            vip  = "⭐ VIP" if u.get("is_vip") else "Free"
            lines.append(f"  • {name} (ID: {u['user_id']}) — last seen {last} [{vip}]")
        if len(inactive) > 5:
            lines.append(f"  ...and {len(inactive) - 5} more")

    lines.append("")
    lines.append(f"<b>🚪 Onboarding Dropouts</b>: {len(abandoned)} found")
    if abandoned:
        for u in abandoned[:5]:
            name = u.get("first_name") or f"user_{u['user_id']}"
            joined = u.get("created_at", "")[:10] if u.get("created_at") else "unknown"
            lines.append(f"  • {name} (ID: {u['user_id']}) — joined {joined}, never chatted")
        if len(abandoned) > 5:
            lines.append(f"  ...and {len(abandoned) - 5} more")

    lines.append("")
    lines.append("<b>📋 CEO Action Options</b>")
    lines.append("  • Use /admin_dm <user_id> <message> to reach any user")
    lines.append("  • Use /broadcast to reach all users at once")
    lines.append("  • Review /admin_stats for broader engagement trends")
    lines.append("")
    lines.append("<i>Note: TestAudit never auto-messages users. CEO decides all outreach.</i>")
    lines.append("<i>TestAudit · Customer Success Intelligence</i>")

    return "\n".join(lines)


def _run_daily_check() -> None:
    """Run the daily customer success analysis and report to CEO."""
    log.info("Customer Success: running daily check")

    from services.testaudit_core import log_memory, add_backlog_item
    from services.decision_engine import evaluate, notify_ceo

    inactive  = find_inactive_users(inactive_days=3, limit=5)
    abandoned = find_abandoned_onboarders(limit=5)

    if inactive or abandoned:
        report = build_customer_success_report()

        # Decision: this is a risk_alert_dm — sending info to CEO only
        decision = evaluate(
            action_type="risk_alert_dm",
            title="Customer Success daily report",
            description="Summary of inactive and abandoned users for CEO awareness",
            payload={"inactive_count": len(inactive), "abandoned_count": len(abandoned)},
            confidence=0.9,
            business_risk=False,
            irreversible=False,
        )

        if decision["decision"] == "auto":
            notify_ceo("Customer Success Report", report)

        log_memory(
            "action_taken",
            f"Customer Success report: {len(inactive)} inactive, {len(abandoned)} abandoned",
            detail={"inactive": len(inactive), "abandoned": len(abandoned)},
            category="customer",
            confidence=0.9,
            outcome="resolved",
        )

        # Add to backlog if engagement is very low
        if len(inactive) > 20:
            add_backlog_item(
                title="High user churn — engagement strategy needed",
                description=f"{len(inactive)} users inactive 3+ days. Consider re-engagement campaign.",
                category="ux",
                priority="high",
                impact="high",
                difficulty="medium",
                source="testaudit",
                confidence=0.88,
            )
    else:
        log.info("Customer Success: no inactive users found — engagement healthy")


# ── Scheduler ─────────────────────────────────────────────────────────────────

def _scheduler_loop() -> None:
    global _last_run_date
    log.info("👤 Customer Success Manager started")
    time.sleep(180)

    while _running:
        try:
            now = datetime.now(timezone.utc)
            today = now.strftime("%Y-%m-%d")
            # Run at 14:00 UTC daily
            if now.hour == 14 and now.minute < 15 and _last_run_date != today:
                _run_daily_check()
                _last_run_date = today
        except Exception as exc:
            log.error("customer_success scheduler error: %s", exc)

        for _ in range(600):  # check every 10 min
            if not _running:
                break
            time.sleep(1)


def start_customer_success() -> None:
    global _running, _thread
    if _running:
        return
    _running = True
    _thread  = threading.Thread(target=_scheduler_loop, daemon=True, name="customer-success")
    _thread.start()
    log.info("✅ Customer Success Manager started")


def stop_customer_success() -> None:
    global _running
    _running = False
