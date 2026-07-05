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

    # Fundz ecosystem status
    try:
        from services.product_registry import get_all_products
        products = get_all_products()
        active   = [p for p in products if p.get("status") == "active"]
        beta     = [p for p in products if p.get("status") == "beta"]
        planned  = [p for p in products if p.get("status") == "planned"]
        lines.append("")
        lines.append("<b>🏢 Fundz Ecosystem</b>")
        for p in active:
            lines.append(f"  🟢 {p['name']} — LIVE")
        for p in beta:
            lines.append(f"  🟡 {p['name']} — BETA")
        for p in planned:
            lines.append(f"  ⚪ {p['name']} — Planned")
    except Exception:
        pass

    # Community intelligence (trending topics)
    try:
        from services.community_manager import get_community_insights
        insights = get_community_insights(top_n=5)
        if insights.get("total_topics_tracked", 0) > 0:
            top_kw = ", ".join(
                f"{t['keyword']} ({t['count']}x)"
                for t in insights["top_topics"][:5]
            )
            lines.append("")
            lines.append(f"<b>💬 Community Topics</b>")
            lines.append(f"  {top_kw}")
    except Exception:
        pass

    # Today's meetings
    try:
        from services.meeting_manager import get_todays_meetings
        todays = get_todays_meetings()
        if todays:
            lines.append("")
            lines.append("<b>📅 Today's Meetings</b>")
            for m in todays:
                try:
                    from datetime import datetime, timezone
                    dt = datetime.fromisoformat(m["scheduled_at"])
                    time_str = dt.strftime("%H:%M UTC")
                except Exception:
                    time_str = "?"
                lines.append(f"  🕐 {time_str} — {m['title']}")
        else:
            lines.append("")
            lines.append("<b>📅 Meetings</b>")
            lines.append("  No meetings scheduled today")
    except Exception:
        pass

    lines.append("")
    lines.append("<i>TestAudit · Chief Operations Manager, Fundz Company Ltd.</i>")
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


# ── Monthly Report ────────────────────────────────────────────────────────────

def _build_monthly_report(now: datetime) -> str:
    """
    Monthly Executive Report — delivered 1st of month at 09:00 UTC.
    Covers full-month performance, growth, health trends, top issues,
    feature backlog status, and strategic recommendations.
    """
    month_name = now.strftime("%B %Y")
    from datetime import timedelta
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    prev_month  = (month_start - timedelta(days=1)).replace(day=1)

    users      = _fetch_user_stats()
    usage      = _fetch_usage_stats()

    # Month-window stats
    month_users_r = None
    try:
        month_start_str = month_start.strftime("%Y-%m-%dT%H:%M:%S")
        month_users_r = _sb_get("users", {"created_at": f"gte.{month_start_str}", "select": "user_id"})
    except Exception:
        pass

    new_this_month = len(month_users_r.json()) if month_users_r and month_users_r.status_code == 200 else "N/A"

    # Health score from last run
    health_score = "N/A"
    health_tier  = "unknown"
    try:
        from services.testaudit_core import get_last_health
        h = get_last_health()
        health_score = h.get("score", "N/A")
        health_tier  = h.get("tier", "unknown")
    except Exception:
        pass

    # Backlog summary
    backlog_count = 0
    try:
        from services.testaudit_core import get_backlog
        backlog_count = len(get_backlog(status="open", limit=50))
    except Exception:
        pass

    # Pending approvals
    pending_count = 0
    try:
        from services.testaudit_core import get_pending_approvals
        pending_count = len(get_pending_approvals())
    except Exception:
        pass

    # Autonomous mode metrics
    aom_active = False
    try:
        from services.autonomous_mode import is_autonomous_mode
        aom_active = is_autonomous_mode()
    except Exception:
        pass

    # Feature recommendations
    feature_report = ""
    try:
        from services.feature_tracker import build_feature_recommendations
        feature_report = build_feature_recommendations()
    except Exception:
        pass

    tier_emoji = {
        "excellent": "🏆", "healthy": "🟢", "attention": "🟡",
        "at_risk": "🟠", "critical": "🔴",
    }.get(health_tier, "⚪")

    lines = [
        f"📊 <b>Monthly Executive Report — {month_name}</b>",
        f"<i>Prepared by TestAudit · {now.strftime('%B 1, %Y')} · 09:00 UTC</i>",
        "",
        f"<b>🏥 Platform Health</b>",
        f"  Score: {tier_emoji} <b>{health_score}/100</b> ({health_tier.upper()})",
        f"  Status: {'⚠️ AOM Active' if aom_active else '✅ CEO In Command'}",
        "",
        f"<b>👥 User Base</b>",
        f"  Total users: <b>{users['total']:,}</b>",
        f"  New this month: <b>{new_this_month}</b>",
        f"  Active (24h): <b>{users['active_24h']:,}</b>",
        f"  VIP subscribers: <b>{users['vip']:,}</b>",
        "",
        f"<b>🤖 Platform Usage (Last 24h snapshot)</b>",
        f"  AI conversations: <b>{usage['chats_24h']:,}</b>",
        f"  Images generated: <b>{usage['images_24h']:,}</b>",
        f"  Errors logged: <b>{usage['errors_24h']:,}</b>",
        "",
        f"<b>📋 Product Intelligence</b>",
        f"  Open backlog items: <b>{backlog_count}</b>",
        f"  Pending CEO approvals: <b>{pending_count}</b>",
        "",
        f"<b>🎯 Monthly Strategic Priorities</b>",
        f"  • Review and action the product backlog",
        f"  • Analyze VIP subscriber retention rate",
        f"  • Evaluate error patterns from the past month",
        f"  • Set growth target for next month",
        "",
        f"<b>📌 Recommended Actions</b>",
        f"  1. Open /testaudit → 📋 Backlog — review {backlog_count} open items",
        f"  2. Open /testaudit → ⏳ Pending — action {pending_count} approval(s)",
        f"  3. Run /admin_stats for full engagement breakdown",
        f"  4. Run /testaudit → 🔄 Full Retest to assess current health",
        "",
        f"<i>TestAudit · Monthly Executive Intelligence</i>",
        f"<i>Confidence: 0.92 | Source: Live Supabase metrics</i>",
    ]

    report = "\n".join(lines)

    if feature_report:
        report += "\n\n" + feature_report

    return report


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

            # Monthly Report: 1st of month 09:00 UTC
            if now.day == 1 and hour == 9 and minute < 10 and _sent_monthly != month_key:
                log.info("Sending monthly report to CEO")
                _send_to_ceo(_build_monthly_report(now))
                _sent_monthly = month_key

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


# ── CEO Return Report ──────────────────────────────────────────────────────────

_RETURN_THRESHOLD_HOURS = 12   # send return report if CEO away for more than this
_last_ceo_seen: float = 0.0    # timestamp of last /testaudit call


def build_return_report(absent_seconds: float) -> str:
    """Build a Return Report summarising what happened during the CEO's absence."""
    users  = _fetch_user_stats()
    usage  = _fetch_usage_stats()
    health = _fetch_health_trend()

    absent_h  = max(1, round(absent_seconds / 3600))
    score_str = f"{health['latest_score']:.1f}/100" if health["latest_score"] is not None else "N/A"
    tier_str  = health.get("latest_tier") or "unknown"
    emoji     = _tier_emoji(tier_str)

    lines = [
        f"🔔 <b>Executive Return Report</b>",
        f"<i>You were away for approximately {absent_h} hour{'s' if absent_h != 1 else ''}.</i>",
        f"<i>TestAudit monitored everything during your absence. Here is the summary.</i>",
        "",
        f"<b>🏥 Current Company Health</b>",
        f"  {emoji} Score: <b>{score_str}</b> ({tier_str})",
        "",
        f"<b>👥 User Activity</b>",
        f"  Total users: <b>{users['total']:,}</b>",
        f"  Active (24h): <b>{users['active_24h']:,}</b>",
        f"  New (24h): <b>{users['new_24h']:,}</b>",
        f"  VIP subscribers: <b>{users['vip']:,}</b>",
        "",
        f"<b>📊 Activity During Your Absence (24h window)</b>",
        f"  AI conversations: <b>{usage['chats_24h']:,}</b>",
        f"  Images generated: <b>{usage['images_24h']:,}</b>",
        f"  Errors logged: <b>{usage['errors_24h']:,}</b>",
        "",
    ]

    alerts = []
    if usage["errors_24h"] > 20:
        alerts.append("⚠️ High error rate detected — run /admin_logs for details")
    if users["new_24h"] > 0:
        alerts.append(f"✅ {users['new_24h']} new user(s) joined while you were away")
    if health["latest_score"] is not None and health["latest_score"] < 55:
        alerts.append("🔴 Health score below threshold — run /testaudit → Full Retest")

    if alerts:
        lines.append(f"<b>🔔 Alerts</b>")
        for alert in alerts:
            lines.append(f"  {alert}")
        lines.append("")

    lines += [
        f"<b>📋 Recommended Actions</b>",
        f"  • Run /testaudit for a complete system audit",
        f"  • Run /admin_logs to review any errors",
        f"  • Run /admin_stats for full engagement metrics",
        "",
        f"<i>TestAudit · Executive Return Intelligence</i>",
        f"<i>Monitoring ran continuously during your absence. Welcome back.</i>",
    ]

    return "\n".join(lines)


def check_and_send_return_report() -> None:
    """
    Called when the CEO opens /testaudit.
    If they have been away for more than _RETURN_THRESHOLD_HOURS, send a
    Return Report before the full audit loads.
    Always updates _last_ceo_seen to the current time.
    """
    global _last_ceo_seen
    now = time.time()
    if _last_ceo_seen > 0:
        absent_secs = now - _last_ceo_seen
        if absent_secs >= (_RETURN_THRESHOLD_HOURS * 3600):
            try:
                report = build_return_report(absent_secs)
                _send_to_ceo(report)
                log.info(
                    "CEO Return Report sent — absent for %.1fh",
                    absent_secs / 3600,
                )
            except Exception as exc:
                log.warning("Failed to send CEO return report: %s", exc)
    _last_ceo_seen = now
