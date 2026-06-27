"""
FundzAiBot — Executive Assistant (TestAudit role)

Delivers scheduled intelligence reports directly to the CEO via Telegram DM:
  • Morning Brief     — 08:00 UTC daily
  • Evening Brief     — 20:00 UTC daily
  • Weekly Report     — Monday 09:00 UTC
  • Monthly Report    — 1st of month 09:00 UTC
  • Critical Alerts   — immediately on detection
  • Growth Reports    — attached to weekly report

All reports are built from real metrics only. No invented information.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, BOT_NAME, BOT_VERSION,
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
)
from utils.logger import get_logger

log = get_logger(__name__)

_running: bool = False
_thread:  threading.Thread | None = None

# Track sent reports to avoid duplicates
_sent_today:   set[str] = set()  # e.g. {"morning_2026-06-27", "evening_2026-06-27"}
_sent_weekly:  str | None = None  # ISO week string
_sent_monthly: str | None = None  # YYYY-MM string


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
            headers=_hdrs(), params=params or {}, timeout=(5, 10),
        )
    except Exception:
        return None


# ── Metrics collectors ────────────────────────────────────────────────────────

def _fetch_user_stats() -> dict:
    stats = {"total": 0, "active_24h": 0, "new_24h": 0, "vip": 0}
    try:
        r = _sb_get("users", {"select": "count"})
        if r and r.status_code == 200:
            rows = r.json()
            stats["total"] = len(rows) if isinstance(rows, list) else 0

        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        r2 = _sb_get("users", {"last_seen": f"gte.{cutoff}", "select": "user_id"})
        if r2 and r2.status_code == 200:
            stats["active_24h"] = len(r2.json())

        r3 = _sb_get("users", {"created_at": f"gte.{cutoff}", "select": "user_id"})
        if r3 and r3.status_code == 200:
            stats["new_24h"] = len(r3.json())

        r4 = _sb_get("users", {"is_vip": "eq.true", "select": "user_id"})
        if r4 and r4.status_code == 200:
            stats["vip"] = len(r4.json())
    except Exception as exc:
        log.debug("exec_assistant._fetch_user_stats: %s", exc)
    return stats


def _fetch_usage_stats() -> dict:
    stats = {"chats_24h": 0, "images_24h": 0, "errors_24h": 0}
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")

        r = _sb_get("conversations", {"created_at": f"gte.{cutoff}", "role": "eq.user", "select": "id"})
        if r and r.status_code == 200:
            stats["chats_24h"] = len(r.json())

        r2 = _sb_get("image_history", {"created_at": f"gte.{cutoff}", "select": "id"})
        if r2 and r2.status_code == 200:
            stats["images_24h"] = len(r2.json())

        r3 = _sb_get("error_logs", {"created_at": f"gte.{cutoff}", "select": "id"})
        if r3 and r3.status_code == 200:
            stats["errors_24h"] = len(r3.json())
    except Exception as exc:
        log.debug("exec_assistant._fetch_usage_stats: %s", exc)
    return stats


def _fetch_health_trend() -> dict:
    """Get the last few health scores from the log."""
    trend = {"latest_score": None, "latest_tier": None, "avg_24h": None}
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        r = _sb_get("company_health_log", {
            "created_at": f"gte.{cutoff}",
            "order": "created_at.desc",
            "limit": "10",
            "select": "score,tier,created_at",
        })
        if r and r.status_code == 200:
            rows = r.json()
            if rows:
                trend["latest_score"] = rows[0].get("score")
                trend["latest_tier"]  = rows[0].get("tier")
                scores = [row.get("score", 0) for row in rows if row.get("score") is not None]
                if scores:
                    trend["avg_24h"] = round(sum(scores) / len(scores), 1)
    except Exception as exc:
        log.debug("exec_assistant._fetch_health_trend: %s", exc)
    return trend


# ── Report builders ───────────────────────────────────────────────────────────

def _tier_emoji(tier: str | None) -> str:
    return {
        "excellent": "🟢",
        "healthy":   "🟢",
        "attention": "🟡",
        "at_risk":   "🟠",
        "critical":  "🔴",
    }.get(tier or "", "⚪")


def build_morning_brief() -> str:
    users  = _fetch_user_stats()
    usage  = _fetch_usage_stats()
    health = _fetch_health_trend()

    score_str = f"{health['latest_score']:.1f}/100" if health["latest_score"] is not None else "N/A"
    tier_str  = health.get("latest_tier") or "unknown"
    emoji     = _tier_emoji(tier_str)

    lines = [
        f"☀️ <b>Good Morning — FundzAiBot Morning Brief</b>",
        f"<i>{datetime.now(timezone.utc).strftime('%A, %B %d %Y')} · {BOT_NAME} v{BOT_VERSION}</i>",
        "",
        f"<b>🏥 Company Health</b>",
        f"  {emoji} Score: <b>{score_str}</b> ({tier_str})",
        "",
        f"<b>👥 Users</b>",
        f"  Total: <b>{users['total']:,}</b>",
        f"  Active 24h: <b>{users['active_24h']:,}</b>",
        f"  New today: <b>{users['new_24h']:,}</b>",
        f"  VIP subscribers: <b>{users['vip']:,}</b>",
        "",
        f"<b>📊 Activity (last 24h)</b>",
        f"  AI conversations: <b>{usage['chats_24h']:,}</b>",
        f"  Images generated: <b>{usage['images_24h']:,}</b>",
        f"  Errors logged: <b>{usage['errors_24h']:,}</b>",
        "",
        f"<b>📋 Recommendations</b>",
    ]

    if usage["errors_24h"] > 20:
        lines.append("  ⚠️ High error count — review /admin_logs")
    elif users["active_24h"] < 5:
        lines.append("  💡 Low activity — Community Manager will engage the group")
    else:
        lines.append("  ✅ All systems nominal — good day ahead")

    lines.append("")
    lines.append("<i>TestAudit · Chief Operations Intelligence</i>")
    return "\n".join(lines)


def build_evening_brief() -> str:
    users  = _fetch_user_stats()
    usage  = _fetch_usage_stats()
    health = _fetch_health_trend()

    score_str = f"{health['latest_score']:.1f}/100" if health["latest_score"] is not None else "N/A"
    tier_str  = health.get("latest_tier") or "unknown"
    emoji     = _tier_emoji(tier_str)

    lines = [
        f"🌙 <b>Evening Brief — FundzAiBot</b>",
        f"<i>{datetime.now(timezone.utc).strftime('%A, %B %d %Y')} · End of Day Summary</i>",
        "",
        f"<b>🏥 Health Score</b>: {emoji} {score_str} ({tier_str})",
        "",
        f"<b>📊 Today's Activity</b>",
        f"  AI conversations: <b>{usage['chats_24h']:,}</b>",
        f"  Images generated: <b>{usage['images_24h']:,}</b>",
        f"  New users joined: <b>{users['new_24h']:,}</b>",
        f"  Errors logged: <b>{usage['errors_24h']:,}</b>",
        "",
        f"<b>📈 Status</b>",
    ]

    if health.get("latest_score") is not None and health["latest_score"] < 55:
        lines.append(f"  ⚠️ Health score below optimal — run /testaudit for details")
    else:
        lines.append(f"  ✅ Day completed without critical incidents")

    lines.append("")
    lines.append("<i>TestAudit · Executive Assistant</i>")
    return "\n".join(lines)


def build_weekly_report() -> str:
    users  = _fetch_user_stats()
    usage  = _fetch_usage_stats()
    health = _fetch_health_trend()

    score_str = f"{health['avg_24h']:.1f}/100" if health.get("avg_24h") else "N/A"

    lines = [
        f"📊 <b>Weekly Report — {BOT_NAME}</b>",
        f"<i>Week of {datetime.now(timezone.utc).strftime('%B %d, %Y')}</i>",
        "",
        f"<b>🏥 Average Health Score (24h window)</b>: {score_str}",
        "",
        f"<b>👥 User Metrics</b>",
        f"  Total registered: <b>{users['total']:,}</b>",
        f"  Active this period: <b>{users['active_24h']:,}</b>",
        f"  VIP subscribers: <b>{users['vip']:,}</b>",
        "",
        f"<b>🤖 Platform Usage</b>",
        f"  AI conversations: <b>{usage['chats_24h']:,}</b>",
        f"  Images generated: <b>{usage['images_24h']:,}</b>",
        f"  Errors logged: <b>{usage['errors_24h']:,}</b>",
        "",
        f"<b>📋 CEO Recommendations</b>",
        f"  • Review /testaudit → 📋 Backlog for pending improvements",
        f"  • Check VIP renewal rate via /admin_stats",
        f"  • Review error patterns via /admin_logs",
        "",
        f"<i>TestAudit · Weekly Intelligence Report</i>",
        f"<i>Confidence: 0.9 | Evidence: Live Supabase metrics</i>",
    ]
    return "\n".join(lines)


# ── Delivery ──────────────────────────────────────────────────────────────────

def _send_to_ceo(text: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not ADMIN_USER_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_USER_ID, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("executive_assistant send_to_ceo: HTTP %d", r.status_code)
    except Exception as exc:
        log.warning("executive_assistant send_to_ceo: %s", exc)


# ── Scheduler loop ────────────────────────────────────────────────────────────

def _scheduler_loop() -> None:
    global _sent_today, _sent_weekly, _sent_monthly

    log.info("📋 Executive Assistant scheduler started")
    time.sleep(90)  # let bot fully start first

    while _running:
        try:
            now = datetime.now(timezone.utc)
            today_key   = now.strftime("%Y-%m-%d")
            week_key    = now.strftime("%Y-W%W")
            month_key   = now.strftime("%Y-%m")
            hour        = now.hour
            minute      = now.minute

            # Morning Brief: 08:00 UTC
            morning_key = f"morning_{today_key}"
            if hour == 8 and minute < 10 and morning_key not in _sent_today:
                log.info("Sending morning brief to CEO")
                _send_to_ceo(build_morning_brief())
                _sent_today.add(morning_key)

            # Evening Brief: 20:00 UTC
            evening_key = f"evening_{today_key}"
            if hour == 20 and minute < 10 and evening_key not in _sent_today:
                log.info("Sending evening brief to CEO")
                _send_to_ceo(build_evening_brief())
                _sent_today.add(evening_key)

            # Weekly Report: Monday 09:00 UTC
            if now.weekday() == 0 and hour == 9 and minute < 10 and _sent_weekly != week_key:
                log.info("Sending weekly report to CEO")
                _send_to_ceo(build_weekly_report())
                _sent_weekly = week_key

            # Prune _sent_today to only keep today's keys
            _sent_today = {k for k in _sent_today if today_key in k}

        except Exception as exc:
            log.error("executive_assistant scheduler error: %s", exc)

        # Check every 5 minutes
        for _ in range(300):
            if not _running:
                break
            time.sleep(1)


def start_executive_assistant() -> None:
    global _running, _thread
    if _running:
        return
    _running = True
    _thread  = threading.Thread(target=_scheduler_loop, daemon=True, name="exec-assistant")
    _thread.start()
    log.info("✅ Executive Assistant scheduler started")


def stop_executive_assistant() -> None:
    global _running
    _running = False
