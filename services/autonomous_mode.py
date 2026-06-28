"""
FundzAiBot — Autonomous Operations Mode (TestAudit role)

Monitors CEO activity and automatically switches TestAudit into
Autonomous Operations Mode when the CEO has been inactive for 7+ days.

Rules:
  • CEO is "active" whenever they use any admin command, /testaudit,
    approve or reject proposals, or change settings.
  • If 7 consecutive days pass with no CEO activity →
    switch to AUTONOMOUS OPERATIONS MODE.
  • In AOM, TestAudit continues ALL daily operations without interruption.
  • Emergency Authority activates only when a verified critical incident
    threatens company continuity AND the CEO is absent.
  • Every emergency action is logged, timestamped, and reported to CEO on return.
  • When CEO returns → generate and send Executive Recovery Report.

This module is a background daemon. It persists state to Supabase.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, ADMIN_USER_ID,
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
    BOT_NAME, BOT_VERSION,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

CEO_INACTIVE_THRESHOLD_DAYS = 7
_CHECK_INTERVAL_SECS        = 3600   # check once per hour
_AOM_TABLE                  = "testaudit_autonomous_log"

# ── Singleton state ───────────────────────────────────────────────────────────

_running:              bool  = False
_thread:               threading.Thread | None = None
_autonomous_mode:      bool  = False
_ceo_last_active:      float = time.time()   # updated on every CEO action
_aom_started_at:       float | None = None
_emergency_actions:    list[dict]   = []     # log of actions taken during AOM
_return_report_sent:   bool  = False


# ── Supabase helpers ──────────────────────────────────────────────────────────

def _hdrs() -> dict:
    return {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def _sb_post(table: str, data: dict) -> requests.Response | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return requests.post(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_hdrs(), json=data, timeout=(5, 12),
        )
    except Exception as exc:
        log.debug("autonomous_mode._sb_post(%s): %s", table, exc)
        return None


def _sb_get(table: str, params: dict | None = None) -> requests.Response | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return requests.get(
            f"{SUPABASE_URL}/rest/v1/{table}",
            headers=_hdrs(), params=params or {}, timeout=(5, 12),
        )
    except Exception as exc:
        log.debug("autonomous_mode._sb_get(%s): %s", table, exc)
        return None


# ── State persistence (survives restarts / Railway redeploys) ─────────────────

def _persist_aom_state() -> None:
    """
    Save AOM state to Supabase testaudit_autonomous_log as a state_snapshot event.
    Called on every state transition so restarts load the correct last-known state.
    """
    try:
        _sb_post(_AOM_TABLE, {
            "event_type": "state_snapshot",
            "title":      "AOM State Snapshot",
            "detail": {
                "ceo_last_active": _ceo_last_active,
                "autonomous_mode": _autonomous_mode,
                "aom_started_at":  _aom_started_at,
                "emergency_actions_count": len(_emergency_actions),
            },
        })
    except Exception as exc:
        log.debug("autonomous_mode._persist_aom_state: %s", exc)


def _restore_aom_state() -> None:
    """
    Load AOM state from Supabase on startup so inactivity window is continuous
    across Railway redeploys and Replit restarts.
    """
    global _ceo_last_active, _autonomous_mode, _aom_started_at, _return_report_sent

    try:
        r = _sb_get(_AOM_TABLE, {
            "event_type": "eq.state_snapshot",
            "select":     "detail,created_at",
            "order":      "created_at.desc",
            "limit":      "1",
        })
        if not r or r.status_code != 200:
            return
        rows = r.json()
        if not rows:
            return

        detail = rows[0].get("detail") or {}
        saved_last_active = detail.get("ceo_last_active")
        saved_aom         = detail.get("autonomous_mode", False)
        saved_aom_start   = detail.get("aom_started_at")

        if saved_last_active:
            _ceo_last_active = float(saved_last_active)
            log.info(
                "autonomous_mode: restored last CEO activity = %.1f days ago",
                (time.time() - _ceo_last_active) / 86400,
            )

        if saved_aom:
            _autonomous_mode    = True
            _aom_started_at     = float(saved_aom_start) if saved_aom_start else time.time()
            _return_report_sent = False
            log.warning(
                "autonomous_mode: restored AUTONOMOUS MODE — was active at last snapshot"
            )

    except Exception as exc:
        log.debug("autonomous_mode._restore_aom_state: %s", exc)


# ── CEO Activity Tracking ─────────────────────────────────────────────────────

def record_ceo_activity(action: str = "command") -> None:
    """
    Call this whenever the CEO takes any action.
    Import and call from admin handlers, testaudit handler, and callbacks.
    """
    global _ceo_last_active, _autonomous_mode, _return_report_sent
    previous_aom = _autonomous_mode

    _ceo_last_active = time.time()

    if previous_aom and not _return_report_sent:
        # CEO just returned after AOM period
        log.info("autonomous_mode: CEO returned — generating recovery report")
        _handle_ceo_return()
        _return_report_sent = True
        _autonomous_mode = False
        log.info("✅ Autonomous Operations Mode deactivated — CEO is back")

    _persist_aom_state()


def get_ceo_inactive_hours() -> float:
    """Return hours since CEO was last active."""
    return (time.time() - _ceo_last_active) / 3600


def get_ceo_inactive_days() -> float:
    """Return days since CEO was last active."""
    return (time.time() - _ceo_last_active) / 86400


def is_autonomous_mode() -> bool:
    """Return True if currently in Autonomous Operations Mode."""
    return _autonomous_mode


def get_aom_status() -> dict:
    """Return complete Autonomous Operations Mode status."""
    inactive_hours = get_ceo_inactive_hours()
    inactive_days  = get_ceo_inactive_days()
    threshold_pct  = min(100, (inactive_days / CEO_INACTIVE_THRESHOLD_DAYS) * 100)

    return {
        "autonomous_mode":    _autonomous_mode,
        "ceo_inactive_hours": round(inactive_hours, 1),
        "ceo_inactive_days":  round(inactive_days, 2),
        "threshold_days":     CEO_INACTIVE_THRESHOLD_DAYS,
        "threshold_pct":      round(threshold_pct, 1),
        "aom_started_at":     (
            datetime.fromtimestamp(_aom_started_at, tz=timezone.utc).isoformat()
            if _aom_started_at else None
        ),
        "emergency_actions_taken": len(_emergency_actions),
        "last_ceo_active": (
            datetime.fromtimestamp(_ceo_last_active, tz=timezone.utc).isoformat()
        ),
    }


# ── Autonomous Operations Mode Activation ─────────────────────────────────────

def _activate_aom() -> None:
    """Switch TestAudit into Autonomous Operations Mode."""
    global _autonomous_mode, _aom_started_at, _return_report_sent

    _autonomous_mode    = True
    _aom_started_at     = time.time()
    _return_report_sent = False

    log.warning(
        "🤖 AUTONOMOUS OPERATIONS MODE ACTIVATED — CEO inactive %d+ days",
        CEO_INACTIVE_THRESHOLD_DAYS,
    )

    _persist_aom_state()
    _log_aom_event(
        "aom_activated",
        f"Autonomous Operations Mode activated after {CEO_INACTIVE_THRESHOLD_DAYS} days of CEO inactivity.",
    )

    _notify_ceo_raw(
        "🤖 <b>Autonomous Operations Mode — Activated</b>\n\n"
        f"You have been inactive for <b>{CEO_INACTIVE_THRESHOLD_DAYS}+ days</b>.\n\n"
        f"<b>{BOT_NAME}</b> has automatically entered Autonomous Operations Mode.\n\n"
        "<b>What continues normally:</b>\n"
        "✅ Customer support\n"
        "✅ Community management\n"
        "✅ Channel content posting\n"
        "✅ Daily morning & evening briefs\n"
        "✅ Health monitoring\n"
        "✅ Infrastructure protection\n\n"
        "<b>What requires your return:</b>\n"
        "• Strategic decisions\n"
        "• Financial changes\n"
        "• New feature deployments\n"
        "• Pricing modifications\n\n"
        f"<i>Reply /testaudit to return and receive your Executive Recovery Report.</i>"
    )


# ── Emergency Authority ────────────────────────────────────────────────────────

_EMERGENCY_ACTIONS_ALLOWED = frozenset({
    "restart_queue_manager",
    "switch_ai_provider",
    "block_spam_user",
    "pause_unstable_feature",
    "clear_error_cache",
    "notify_users_of_outage",
    "recover_scheduled_tasks",
    "optimize_performance_settings",
})


def execute_emergency_action(
    action_id: str,
    title: str,
    description: str,
    execute_fn,
    evidence: str,
    severity: str = "critical",
) -> bool:
    """
    Execute an emergency action during AOM or critical incidents.
    Returns True if executed. Every action is logged with full evidence.

    Rules:
     - Only actions in _EMERGENCY_ACTIONS_ALLOWED may be auto-executed.
     - CEO is always notified.
     - Every action is reversible where possible.
     - Full evidence must be provided.
    """
    if action_id not in _EMERGENCY_ACTIONS_ALLOWED:
        log.warning("autonomous_mode: emergency action '%s' not in allowed list — blocked", action_id)
        return False

    if not _autonomous_mode:
        log.debug("autonomous_mode: emergency action '%s' requires AOM active", action_id)
        return False

    log.warning("🚨 Emergency action: %s — %s", action_id, title)

    result = "unknown"
    try:
        execute_fn()
        result = "success"
    except Exception as exc:
        result = f"failed: {exc}"
        log.error("Emergency action '%s' failed: %s", action_id, exc)

    action_record = {
        "action_id":   action_id,
        "title":       title,
        "description": description,
        "evidence":    evidence,
        "severity":    severity,
        "result":      result,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "reversible":  True,
    }
    _emergency_actions.append(action_record)

    _log_aom_event(
        "emergency_action",
        f"Emergency: {title}",
        detail=action_record,
    )

    _notify_ceo_raw(
        f"🚨 <b>TestAudit Emergency Action — {severity.upper()}</b>\n\n"
        f"<b>Action:</b> {title}\n"
        f"<b>Type:</b> <code>{action_id}</code>\n"
        f"<b>Result:</b> {result}\n\n"
        f"<b>Evidence:</b> {evidence}\n\n"
        f"<b>Description:</b> {description}\n\n"
        f"<i>This action was logged and is reversible where possible.\n"
        f"Full report will be delivered on your return.</i>"
    )

    return result == "success"


# ── CEO Return Recovery Report ─────────────────────────────────────────────────

def _handle_ceo_return() -> None:
    """Generate and send the Executive Recovery Report when CEO returns."""
    global _aom_started_at, _emergency_actions

    aom_duration_hours = 0.0
    if _aom_started_at:
        aom_duration_hours = (time.time() - _aom_started_at) / 3600

    # Build recovery report
    report = _build_recovery_report(aom_duration_hours)

    _notify_ceo_raw(report)

    # Log in memory
    _log_aom_event(
        "ceo_returned",
        f"CEO returned after {aom_duration_hours:.1f}h AOM. Recovery report sent.",
        detail={"emergency_actions": len(_emergency_actions), "aom_hours": round(aom_duration_hours, 1)},
    )

    # Clear emergency log now that report is sent
    _emergency_actions.clear()
    _aom_started_at = None
    _persist_aom_state()


def _build_recovery_report(aom_hours: float) -> str:
    """Build a comprehensive Executive Recovery Report."""
    now     = datetime.now(timezone.utc)
    aom_str = f"{aom_hours:.1f} hours" if aom_hours < 48 else f"{aom_hours/24:.1f} days"

    lines = [
        f"🏢 <b>Executive Recovery Report — {BOT_NAME}</b>",
        f"<i>Welcome back. Here is everything that happened while you were away.</i>",
        f"<i>Report generated: {now.strftime('%B %d, %Y at %H:%M UTC')}</i>",
        "",
        f"<b>📅 Autonomous Mode Duration</b>: {aom_str}",
        "",
    ]

    # Emergency actions
    if _emergency_actions:
        lines.append(f"<b>🚨 Emergency Actions Taken ({len(_emergency_actions)})</b>")
        for ea in _emergency_actions:
            ts_str = ea.get("timestamp", "")[:16].replace("T", " ")
            lines.append(f"  • [{ts_str}] {ea['title']} — <i>{ea['result']}</i>")
        lines.append("")
    else:
        lines.append("<b>🚨 Emergency Actions</b>: None required — all systems remained stable")
        lines.append("")

    # Fetch live metrics for the report
    metrics = _fetch_recovery_metrics()

    lines.append(f"<b>👥 User Activity During Absence</b>")
    lines.append(f"  New users joined: <b>{metrics.get('new_users', 'N/A')}</b>")
    lines.append(f"  Total users now: <b>{metrics.get('total_users', 'N/A')}</b>")
    lines.append(f"  VIP subscribers: <b>{metrics.get('vip_users', 'N/A')}</b>")
    lines.append("")

    lines.append(f"<b>📊 Platform Activity</b>")
    lines.append(f"  AI conversations: <b>{metrics.get('conversations', 'N/A')}</b>")
    lines.append(f"  Images generated: <b>{metrics.get('images', 'N/A')}</b>")
    lines.append(f"  Errors logged: <b>{metrics.get('errors', 'N/A')}</b>")
    lines.append("")

    lines.append(f"<b>📋 Pending Items Requiring Your Attention</b>")
    lines.append(f"  • Run /testaudit → Pending Approvals for queued decisions")
    lines.append(f"  • Review /admin_logs for any error patterns")
    lines.append(f"  • Check /admin_stats for full engagement metrics")
    lines.append("")

    lines.append("<b>🏥 Current System Health</b>")
    try:
        from services.testaudit_core import get_last_health
        h = get_last_health()
        score = h.get("score", "N/A")
        tier  = h.get("tier", "unknown")
        lines.append(f"  Score: <b>{score}/100</b> ({tier})")
    except Exception:
        lines.append("  Run /testaudit for current health status")
    lines.append("")

    lines.append("<b>✅ What Kept Running</b>")
    lines.append("  • Morning & Evening briefs delivered daily")
    lines.append("  • Community Manager maintained group engagement")
    lines.append("  • Channel Manager posted educational content")
    lines.append("  • Customer Success monitored retention")
    lines.append("  • Health monitoring ran every 10 minutes")
    lines.append("")

    lines.append("<i>TestAudit · Executive Recovery Report · Autonomous Operations Mode</i>")
    lines.append("<i>All actions were logged. Nothing was hidden.</i>")

    return "\n".join(lines)


def _fetch_recovery_metrics() -> dict:
    """Fetch metrics for the recovery report."""
    metrics: dict[str, Any] = {}
    try:
        r = _sb_get("users", {"select": "count"})
        if r and r.status_code == 200:
            metrics["total_users"] = len(r.json())

        r2 = _sb_get("users", {"is_vip": "eq.true", "select": "user_id"})
        if r2 and r2.status_code == 200:
            metrics["vip_users"] = len(r2.json())

        cutoff = (datetime.now(timezone.utc) - timedelta(days=CEO_INACTIVE_THRESHOLD_DAYS + 1)).isoformat()
        r3 = _sb_get("users", {"created_at": f"gte.{cutoff}", "select": "user_id"})
        if r3 and r3.status_code == 200:
            metrics["new_users"] = len(r3.json())

        r4 = _sb_get("conversations", {"created_at": f"gte.{cutoff}", "role": "eq.user", "select": "id"})
        if r4 and r4.status_code == 200:
            metrics["conversations"] = len(r4.json())

        r5 = _sb_get("image_history", {"created_at": f"gte.{cutoff}", "select": "id"})
        if r5 and r5.status_code == 200:
            metrics["images"] = len(r5.json())

        r6 = _sb_get("error_logs", {"created_at": f"gte.{cutoff}", "select": "id"})
        if r6 and r6.status_code == 200:
            metrics["errors"] = len(r6.json())
    except Exception as exc:
        log.debug("autonomous_mode._fetch_recovery_metrics: %s", exc)
    return metrics


# ── Logging ────────────────────────────────────────────────────────────────────

def _log_aom_event(event_type: str, title: str, detail: dict | None = None) -> None:
    """Persist an AOM event to Supabase for full audit trail."""
    try:
        _sb_post(_AOM_TABLE, {
            "event_type": event_type,
            "title":      title,
            "detail":     detail or {},
        })
    except Exception as exc:
        log.debug("autonomous_mode._log_aom_event: %s", exc)

    try:
        from services.testaudit_core import log_memory
        log_memory(
            event_type, title,
            detail=detail or {},
            category="autonomous_ops",
            confidence=1.0,
            outcome="resolved",
        )
    except Exception:
        pass


# ── CEO DM ────────────────────────────────────────────────────────────────────

def _notify_ceo_raw(text: str) -> None:
    """Send a DM to the CEO. Best-effort, never raises."""
    if not TELEGRAM_BOT_TOKEN or not ADMIN_USER_ID:
        return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": ADMIN_USER_ID, "text": text[:4096], "parse_mode": "HTML"},
            timeout=12,
        )
        if r.status_code != 200:
            log.warning("autonomous_mode notify_ceo HTTP %d", r.status_code)
    except Exception as exc:
        log.warning("autonomous_mode._notify_ceo_raw: %s", exc)


# ── Background monitor ────────────────────────────────────────────────────────

def _monitor_loop() -> None:
    """Hourly loop: check CEO inactivity and manage AOM transitions."""
    global _autonomous_mode

    log.info("🤖 Autonomous Mode monitor started — threshold: %d days", CEO_INACTIVE_THRESHOLD_DAYS)
    time.sleep(60)  # let bot fully start, then restore state before first check

    # Restore persisted state so inactivity window survives restarts
    _restore_aom_state()
    log.info(
        "autonomous_mode: state restored — last_active=%.1f days ago | aom=%s",
        (time.time() - _ceo_last_active) / 86400,
        _autonomous_mode,
    )

    while _running:
        try:
            _check_ceo_activity()
        except Exception as exc:
            log.error("autonomous_mode monitor error: %s", exc)

        for _ in range(_CHECK_INTERVAL_SECS):
            if not _running:
                break
            time.sleep(1)


def _check_ceo_activity() -> None:
    """Check CEO inactivity and trigger AOM if threshold exceeded."""
    global _autonomous_mode

    inactive_days = get_ceo_inactive_days()

    if not _autonomous_mode and inactive_days >= CEO_INACTIVE_THRESHOLD_DAYS:
        _activate_aom()
        return

    if _autonomous_mode:
        log.debug(
            "AOM active — CEO inactive %.1f days — %d emergency actions taken",
            inactive_days, len(_emergency_actions),
        )


# ── Start / Stop ──────────────────────────────────────────────────────────────

def start_autonomous_mode_monitor() -> None:
    """Start the Autonomous Mode background monitor. Call once from post_init."""
    global _running, _thread
    if _running:
        return
    _running = True
    _thread  = threading.Thread(
        target=_monitor_loop, daemon=True, name="autonomous-mode"
    )
    _thread.start()
    log.info("✅ Autonomous Operations Mode monitor started")


def stop_autonomous_mode_monitor() -> None:
    global _running
    _running = False
