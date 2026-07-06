"""
FundzAiBot — TestAudit Continuous Intelligence Core

TestAudit is NOT a chatbot command. It is a permanent background operational
intelligence layer that runs continuously, observes, learns, reports, and assists.

This module provides:
  • Continuous health monitoring (every 10 minutes)
  • Company Health Score calculation (0–100, transparent and explainable)
  • Long-term operational memory (persisted to Supabase)
  • Risk prediction and early warning
  • Product improvement backlog management
  • Decision engine integration

Architecture: runs as a daemon thread started in post_init().
All DB writes are synchronous (requests-based, like the rest of the codebase).
"""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone, timedelta
from typing import Any

import requests

from config.settings import (
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
    TELEGRAM_BOT_TOKEN, ADMIN_USER_ID,
    OPENROUTER_API_KEY, GEMINI_API_KEY, HUGGINGFACE_API_KEY,
    BOT_NAME, BOT_VERSION,
    TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID,
)
from utils.logger import get_logger

# ── Fundz Company Constitution ────────────────────────────────────────────────
# TestAudit derives its operational authority from the Constitution.
# KPI thresholds and compliance checks reference constitutional standards.
try:
    from services.constitution import (
        get_version as _constitution_version,
        get_mandate as _constitution_mandate,
        check_compliance as _constitution_check,
        TESTAUDIT_MANDATE as _MANDATE,
    )
    _CONSTITUTION_LOADED = True
except Exception:
    _CONSTITUTION_LOADED = False
    _MANDATE = {"kpis": {"health_score_target": 90.0, "error_rate_threshold": 20}}

log = get_logger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

_CHECK_INTERVAL   = 600   # 10 minutes between health checks
_MEMORY_MAX_ROWS  = 1000  # prune memory table beyond this
_HEALTH_TIERS = [
    (90, "excellent"),
    (75, "healthy"),
    (55, "attention"),
    (35, "at_risk"),
    (0,  "critical"),
]

# Singleton state
_last_score: float = 0.0
_last_tier:  str   = "unknown"
_last_check: float = 0.0
_running:    bool  = False
_thread:     threading.Thread | None = None

# Alert throttling — only notify CEO once per 2 hours per risk type to prevent spam
_last_alert_ts: dict[str, float] = {}
_ALERT_COOLDOWN_SECS = 7200   # 2 hours

# ── Supabase helpers ──────────────────────────────────────────────────────────

_DB_TIMEOUT = (5, 12)


def _hdrs() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _sb_get(path: str, params: dict | None = None) -> requests.Response | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return requests.get(
            f"{SUPABASE_URL}/rest/v1/{path}",
            headers=_hdrs(),
            params=params or {},
            timeout=_DB_TIMEOUT,
        )
    except Exception as exc:
        log.debug("testaudit_core._sb_get(%s): %s", path, exc)
        return None


def _sb_post(path: str, data: dict | list) -> requests.Response | None:
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return None
    try:
        return requests.post(
            f"{SUPABASE_URL}/rest/v1/{path}",
            headers=_hdrs(),
            json=data,
            timeout=_DB_TIMEOUT,
        )
    except Exception as exc:
        log.debug("testaudit_core._sb_post(%s): %s", path, exc)
        return None


# ── Memory ────────────────────────────────────────────────────────────────────

def log_memory(
    event_type: str,
    title: str,
    detail: dict | None = None,
    category: str = "operations",
    confidence: float = 1.0,
    outcome: str | None = None,
) -> None:
    """Write an operational memory entry to Supabase (non-blocking best-effort)."""
    try:
        _sb_post("testaudit_memory", {
            "event_type": event_type,
            "category":   category,
            "title":      title,
            "detail":     detail or {},
            "confidence": confidence,
            "outcome":    outcome,
        })
    except Exception as exc:
        log.debug("log_memory: %s", exc)


def get_recent_memory(limit: int = 20, category: str | None = None) -> list[dict]:
    """Fetch recent memory entries."""
    params: dict = {"order": "created_at.desc", "limit": str(limit)}
    if category:
        params["category"] = f"eq.{category}"
    r = _sb_get("testaudit_memory", params)
    if r and r.status_code == 200:
        return r.json()
    return []


def get_trend_summary() -> dict:
    """Return a summary of the last 24 hours of events for briefing."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).isoformat()
    r = _sb_get("testaudit_memory", {
        "created_at": f"gte.{cutoff}",
        "order": "created_at.desc",
        "limit": "200",
    })
    if not r or r.status_code != 200:
        return {}
    rows = r.json()
    by_type: dict[str, int] = {}
    by_cat:  dict[str, int] = {}
    outcomes = {"resolved": 0, "escalated": 0, "pending": 0}
    for row in rows:
        t = row.get("event_type", "unknown")
        c = row.get("category", "general")
        o = row.get("outcome", "")
        by_type[t] = by_type.get(t, 0) + 1
        by_cat[c]  = by_cat.get(c, 0) + 1
        if o in outcomes:
            outcomes[o] += 1
    return {
        "total_events": len(rows),
        "by_type": by_type,
        "by_category": by_cat,
        "outcomes": outcomes,
    }


# ── Health Score ──────────────────────────────────────────────────────────────

def _score_bot_core() -> tuple[float, list[str]]:
    """Check bot token validity. Returns (score 0-20, issues)."""
    if not TELEGRAM_BOT_TOKEN:
        return 0.0, ["Bot token missing"]
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe",
            timeout=8,
        )
        if r.status_code == 200:
            return 20.0, []
        return 5.0, [f"Telegram API HTTP {r.status_code}"]
    except Exception as exc:
        return 5.0, [f"Telegram unreachable: {exc}"]


def _score_ai_providers() -> tuple[float, list[str]]:
    """Check AI provider availability. Returns (score 0-20, issues)."""
    available = 0
    issues = []
    providers = [
        ("OpenRouter", OPENROUTER_API_KEY, "https://openrouter.ai/api/v1/models"),
        ("Gemini",     GEMINI_API_KEY,     f"https://generativelanguage.googleapis.com/v1beta/models?key={GEMINI_API_KEY}"),
    ]
    for name, key, url in providers:
        if not key:
            issues.append(f"{name}: no key")
            continue
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {key}"} if name == "OpenRouter" else {}, timeout=8)
            if r.status_code in (200, 400):
                available += 1
            else:
                issues.append(f"{name}: HTTP {r.status_code}")
        except Exception:
            issues.append(f"{name}: unreachable")
    if not HUGGINGFACE_API_KEY:
        issues.append("HuggingFace: no key")
    score = (available / max(len(providers), 1)) * 20.0
    return score, issues


def _score_database() -> tuple[float, list[str]]:
    """Check database connectivity. Returns (score 0-20, issues)."""
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return 0.0, ["Supabase not configured"]
    issues = []
    reachable = 0
    tables = ["users", "user_credits", "error_logs"]
    for table in tables:
        r = _sb_get(f"{table}?limit=1")
        if r and r.status_code == 200:
            reachable += 1
        else:
            status = r.status_code if r else "timeout"
            issues.append(f"Table '{table}': {status}")
    score = (reachable / len(tables)) * 20.0
    return score, issues


def _score_active_users() -> tuple[float, list[str]]:
    """Check recent user activity. Returns (score 0-20, issues)."""
    issues = []
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%S")
        r = _sb_get("users", {"last_seen": f"gte.{cutoff}", "select": "count"})
        if r and r.status_code == 200:
            rows = r.json()
            count = len(rows) if isinstance(rows, list) else 0
            if count >= 10:
                return 20.0, []
            elif count >= 3:
                return 12.0, [f"Only {count} active users in last 24h"]
            else:
                return 5.0, [f"Very low activity: {count} users in 24h"]
    except Exception as exc:
        issues.append(f"User activity check failed: {exc}")
    return 10.0, issues


def _score_error_rate() -> tuple[float, list[str]]:
    """Check recent error frequency. Returns (score 0-20, issues)."""
    issues = []
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")
        r = _sb_get("error_logs", {"created_at": f"gte.{cutoff}", "select": "id"})
        if r and r.status_code == 200:
            count = len(r.json())
            if count == 0:
                return 20.0, []
            elif count < 5:
                return 15.0, [f"{count} errors in last hour"]
            elif count < 20:
                return 8.0,  [f"{count} errors in last hour — elevated"]
            else:
                return 2.0,  [f"{count} errors in last hour — critical"]
    except Exception as exc:
        issues.append(f"Error rate check failed: {exc}")
    return 10.0, issues


def calculate_health_score() -> dict:
    """
    Calculate the Company Health Score (0–100).
    Transparent, explainable, using real measurable data.
    Returns full breakdown with score, tier, and issues.
    """
    dims: dict[str, tuple[float, list[str]]] = {}
    dims["bot_core"]      = _score_bot_core()
    dims["ai_providers"]  = _score_ai_providers()
    dims["database"]      = _score_database()
    dims["active_users"]  = _score_active_users()
    dims["error_rate"]    = _score_error_rate()

    total = sum(s for s, _ in dims.values())
    score = min(100.0, max(0.0, total))

    tier = "critical"
    for threshold, label in _HEALTH_TIERS:
        if score >= threshold:
            tier = label
            break

    all_issues = []
    breakdown  = {}
    for dim, (s, issues) in dims.items():
        breakdown[dim] = {"score": round(s, 1), "max": 20}
        all_issues.extend(issues)

    return {
        "score":     round(score, 1),
        "tier":      tier,
        "breakdown": breakdown,
        "issues":    all_issues,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def _persist_health(result: dict) -> None:
    """Write health score to Supabase history log."""
    try:
        _sb_post("company_health_log", {
            "score":           result["score"],
            "tier":            result["tier"],
            "breakdown":       result["breakdown"],
            "error_count":     len(result.get("issues", [])),
        })
    except Exception as exc:
        log.debug("_persist_health: %s", exc)


# ── Risk Prediction ───────────────────────────────────────────────────────────

def predict_risks(health: dict) -> list[dict]:
    """
    Predict operational risks based on current health state and trends.
    Returns list of risk items with type, description, severity, and recommendation.
    """
    risks = []
    breakdown = health.get("breakdown", {})
    issues    = health.get("issues", [])
    score     = health.get("score", 100)

    # AI provider failure risk
    ai_score = breakdown.get("ai_providers", {}).get("score", 20)
    if ai_score < 10:
        risks.append({
            "type": "ai_provider_outage",
            "severity": "critical",
            "description": "All or most AI providers are unreachable",
            "recommendation": "Check OPENROUTER_API_KEY and GEMINI_API_KEY in Railway. Verify credit balances.",
        })
    elif ai_score < 15:
        risks.append({
            "type": "ai_provider_degraded",
            "severity": "high",
            "description": "AI provider redundancy is low",
            "recommendation": "Add at least one more AI provider key for fallback protection.",
        })

    # Database risk
    db_score = breakdown.get("database", {}).get("score", 20)
    if db_score < 10:
        risks.append({
            "type": "database_unreachable",
            "severity": "critical",
            "description": "Supabase database is not accessible",
            "recommendation": "Verify SUPABASE_URL and SUPABASE_SERVICE_KEY in Railway. Check Supabase status page.",
        })

    # Error rate risk
    err_score = breakdown.get("error_rate", {}).get("score", 20)
    if err_score < 5:
        risks.append({
            "type": "high_error_rate",
            "severity": "critical",
            "description": "Very high error rate detected in the last hour",
            "recommendation": "Run /admin_logs to identify the error pattern. Check Railway logs.",
        })
    elif err_score < 10:
        risks.append({
            "type": "elevated_error_rate",
            "severity": "high",
            "description": "Error rate is elevated",
            "recommendation": "Monitor closely. Run /testaudit for full diagnostic.",
        })

    # Low engagement risk
    user_score = breakdown.get("active_users", {}).get("score", 20)
    if user_score < 6:
        risks.append({
            "type": "low_engagement",
            "severity": "medium",
            "description": "User engagement has dropped significantly",
            "recommendation": "Consider re-engagement campaign. Community Manager will auto-start discussions.",
        })

    # Overall score risk
    if score < 35:
        risks.append({
            "type": "system_health_critical",
            "severity": "critical",
            "description": f"Overall system health is critical ({score}/100)",
            "recommendation": "Immediate attention required. Check all systems.",
        })

    return risks


# ── Backlog Management ────────────────────────────────────────────────────────

def add_backlog_item(
    title: str,
    description: str = "",
    category: str = "feature",
    priority: str = "medium",
    impact: str = "medium",
    difficulty: str = "medium",
    source: str = "testaudit",
    confidence: float = 0.8,
) -> dict | None:
    """Add an item to the product improvement backlog."""
    r = _sb_post("testaudit_backlog", {
        "title":       title,
        "description": description,
        "category":    category,
        "priority":    priority,
        "impact":      impact,
        "difficulty":  difficulty,
        "source":      source,
        "confidence":  confidence,
        "status":      "open",
    })
    if r and r.status_code in (200, 201):
        rows = r.json()
        return rows[0] if rows else None
    return None


def get_backlog(status: str = "open", limit: int = 20) -> list[dict]:
    """Fetch backlog items."""
    r = _sb_get("testaudit_backlog", {
        "status":  f"eq.{status}",
        "order":   "priority.desc,created_at.desc",
        "limit":   str(limit),
    })
    if r and r.status_code == 200:
        return r.json()
    return []


# ── CEO Approval Queue ────────────────────────────────────────────────────────

def queue_ceo_approval(
    action_type: str,
    title: str,
    description: str,
    payload: dict,
    confidence: float,
    risk_level: str = "medium",
) -> dict | None:
    """Queue an action for CEO approval."""
    r = _sb_post("ceo_approval_queue", {
        "action_type": action_type,
        "title":       title,
        "description": description,
        "payload":     payload,
        "confidence":  confidence,
        "risk_level":  risk_level,
        "status":      "pending",
    })
    if r and r.status_code in (200, 201):
        rows = r.json()
        return rows[0] if rows else None
    return None


def get_pending_approvals() -> list[dict]:
    """Get pending CEO approval requests."""
    r = _sb_get("ceo_approval_queue", {
        "status": "eq.pending",
        "order":  "created_at.desc",
        "limit":  "20",
    })
    if r and r.status_code == 200:
        return r.json()
    return []


# ── State accessors ───────────────────────────────────────────────────────────

def get_last_health() -> dict:
    """Return the most recently calculated health result."""
    return {
        "score":      _last_score,
        "tier":       _last_tier,
        "checked_at": datetime.fromtimestamp(_last_check, tz=timezone.utc).isoformat() if _last_check else None,
    }


# ── CEO Critical Alert (throttled) ───────────────────────────────────────────

def _notify_ceo_critical(critical_risks: list[dict], health: dict) -> None:
    """
    Send a direct Telegram alert to the CEO when critical risks are detected.
    Throttled: each risk type is alerted at most once per _ALERT_COOLDOWN_SECS.
    This implements EOS 5.10 — TestAudit interrupts CEO only for critical events.
    """
    now = time.time()
    # Filter to risks outside cooldown — do NOT stamp timestamps yet; stamp only after success
    new_risks = []
    for risk in critical_risks:
        rtype = risk.get("type", "unknown")
        last  = _last_alert_ts.get(rtype, 0.0)
        if now - last >= _ALERT_COOLDOWN_SECS:
            new_risks.append(risk)

    if not new_risks:
        log.debug("TestAudit: critical risks suppressed by cooldown — no CEO alert sent")
        return

    try:
        from services.decision_engine import notify_ceo
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"⚠️ <b>TestAudit — Critical Alert</b>",
            f"<i>{ts} · Health: {health.get('score', '?')}/100 ({health.get('tier', '?').upper()})</i>",
            "",
        ]
        for risk in new_risks[:3]:
            lines.append(f"<b>🔴 {risk.get('type', 'unknown').replace('_', ' ').title()}</b>")
            lines.append(f"{risk.get('description', '')}")
            rec = risk.get("recommendation", "")
            if rec:
                lines.append(f"<i>→ {rec}</i>")
            lines.append("")
        lines.append("<i>Run /testaudit for full diagnostics.</i>")
        sent = notify_ceo("Critical Risk Alert", "\n".join(lines))
        if sent:
            # Only record delivery timestamp after confirmed send — prevents muting on failure
            for risk in new_risks:
                _last_alert_ts[risk.get("type", "unknown")] = now
            log.info("TestAudit: CEO notified of %d critical risk(s)", len(new_risks))
        else:
            log.warning("TestAudit: CEO alert send failed — will retry next cycle")
    except Exception as exc:
        log.warning("TestAudit._notify_ceo_critical: %s", exc)


# ── Background Monitor Loop ───────────────────────────────────────────────────

def _monitor_loop() -> None:
    """The continuous monitoring daemon. Runs every _CHECK_INTERVAL seconds."""
    global _last_score, _last_tier, _last_check

    log.info("🧠 TestAudit intelligence core started — monitoring every %ds", _CHECK_INTERVAL)

    # Stagger first check by 60s so bot finishes startup first
    time.sleep(60)

    while _running:
        try:
            _do_health_cycle()
        except Exception as exc:
            log.error("testaudit_core monitor loop error: %s", exc)
        # Sleep in small increments so we can stop cleanly
        for _ in range(_CHECK_INTERVAL):
            if not _running:
                break
            time.sleep(1)


def _do_health_cycle() -> None:
    global _last_score, _last_tier, _last_check

    log.debug("TestAudit: running health cycle")
    health = calculate_health_score()
    _last_score = health["score"]
    _last_tier  = health["tier"]
    _last_check = time.time()

    _persist_health(health)

    risks = predict_risks(health)
    if risks:
        critical = [r for r in risks if r["severity"] == "critical"]
        high     = [r for r in risks if r["severity"] == "high"]
        if critical:
            log.warning(
                "TestAudit: %d critical risk(s) detected — score=%.1f tier=%s",
                len(critical), health["score"], health["tier"],
            )
            log_memory(
                "risk_alert",
                f"Critical risk detected — {critical[0]['type']}",
                detail={"score": health["score"], "risks": risks},
                category="operations",
                confidence=0.9,
                outcome="pending",
            )
            # Notify CEO via Telegram for critical risks (throttled to 1 alert per type per 2h)
            _notify_ceo_critical(critical, health)
        else:
            log_memory(
                "health_check",
                f"Health score: {health['score']:.1f} ({health['tier']})",
                detail={"score": health["score"], "tier": health["tier"]},
                category="operations",
                confidence=1.0,
                outcome="resolved",
            )
            # Log high-severity risks to memory without interrupting CEO
            if high:
                log_memory(
                    "risk_elevated",
                    f"Elevated risk: {high[0]['type']} — monitoring",
                    detail={"score": health["score"], "risks": high},
                    category="operations",
                    confidence=0.8,
                    outcome="pending",
                )
    else:
        log.debug("TestAudit: health check passed — score=%.1f tier=%s",
                  health["score"], health["tier"])

    # Check pending seller applications every cycle (non-blocking)
    try:
        _check_seller_applications()
    except Exception as exc:
        log.debug("_check_seller_applications: %s", exc)


# ── Seller Application Monitoring (FundzMarket integration) ──────────────────

def get_pending_seller_applications(limit: int = 20) -> list[dict]:
    """
    Fetch pending seller applications from FundzMarket's seller_applications table.
    Returns list of applications awaiting CEO/admin review.
    """
    r = _sb_get("seller_applications", {
        "status": "eq.pending",
        "order":  "created_at.desc",
        "limit":  str(limit),
    })
    if r and r.status_code == 200:
        return r.json()
    return []


def get_seller_application_stats() -> dict:
    """
    Return a summary of seller application status counts.
    Used by TestAudit health monitoring and CEO Office context.
    """
    stats = {"pending": 0, "approved": 0, "rejected": 0, "total": 0}
    for status in ("pending", "approved", "rejected"):
        r = _sb_get("seller_applications", {
            "status": f"eq.{status}",
            "select": "id",
        })
        if r and r.status_code == 200:
            count = len(r.json())
            stats[status] = count
            stats["total"] += count
    return stats



def _send_fundzmarket_dm(telegram_id: int, text: str) -> bool:
    """
    Send a Telegram DM to a FundzMarket user via the FundzMarket bot token.
    Requires FUNDZMARKET_BOT_TOKEN to be set in Railway environment variables.
    Silent no-op if the token is not configured.
    """
    try:
        from config.settings import FUNDZMARKET_BOT_TOKEN
        if not FUNDZMARKET_BOT_TOKEN:
            log.debug("_send_fundzmarket_dm: FUNDZMARKET_BOT_TOKEN not set — skipping notification")
            return False
        r = requests.post(
            f"https://api.telegram.org/bot{FUNDZMARKET_BOT_TOKEN}/sendMessage",
            json={
                "chat_id":    telegram_id,
                "text":       text,
                "parse_mode": "HTML",
            },
            timeout=10,
        )
        if r.status_code == 200:
            log.info("FundzMarket DM sent: telegram_id=%d", telegram_id)
            return True
        log.warning("_send_fundzmarket_dm: HTTP %s — %s", r.status_code, r.text[:80])
    except Exception as exc:
        log.warning("_send_fundzmarket_dm: %s", exc)
    return False


def approve_seller_application(application_id: str, reviewed_by: int = 0) -> bool:
    """
    Approve a seller application. Updates status and sets reviewed_at timestamp.
    Returns True on success.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        hdrs = dict(_hdrs())
        hdrs["Prefer"] = "return=representation"
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/seller_applications?id=eq.{application_id}",
            headers=hdrs,
            json={
                "status":       "approved",
                "reviewed_by":  reviewed_by,
                "reviewed_at":  datetime.now(timezone.utc).isoformat(),
            },
            timeout=_DB_TIMEOUT,
        )
        if r.status_code in (200, 204):
            log_memory(
                "seller_approved",
                f"Seller application {application_id} approved",
                detail={"application_id": application_id, "reviewed_by": reviewed_by},
                category="marketplace",
                confidence=1.0,
                outcome="resolved",
            )
            # Notify the seller via FundzMarket bot
            try:
                app_rows = []
                if SUPABASE_URL and SUPABASE_SERVICE_KEY:
                    import requests as _req
                    _r = _req.get(
                        f"{SUPABASE_URL}/rest/v1/seller_applications"
                        f"?id=eq.{application_id}&select=user_id,store_name",
                        headers=_hdrs(), timeout=_DB_TIMEOUT,
                    )
                    app_rows = _r.json() if _r.status_code == 200 else []
                if app_rows:
                    app = app_rows[0]
                    msg = (
                        f"🎉 <b>Congratulations! Your seller application has been approved.</b>\n\n"
                        f"Your store <b>{app.get('store_name', 'your store')}</b> is now live on FundzMarket.\n\n"
                        f"Tap <b>➕ Sell Product</b> to list your first product!"
                    )
                    _send_fundzmarket_dm(app["user_id"], msg)
            except Exception as _exc:
                log.debug("Seller approval notification: %s", _exc)
            return True
    except Exception as exc:
        log.warning("approve_seller_application: %s", exc)
    return False


def reject_seller_application(application_id: str, reason: str = "", reviewed_by: int = 0) -> bool:
    """
    Reject a seller application with an optional reason.
    Returns True on success.
    """
    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        return False
    try:
        hdrs = dict(_hdrs())
        hdrs["Prefer"] = "return=representation"
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/seller_applications?id=eq.{application_id}",
            headers=hdrs,
            json={
                "status":       "rejected",
                "reject_reason": reason,
                "reviewed_by":  reviewed_by,
                "reviewed_at":  datetime.now(timezone.utc).isoformat(),
            },
            timeout=_DB_TIMEOUT,
        )
        if r.status_code in (200, 204):
            log_memory(
                "seller_rejected",
                f"Seller application {application_id} rejected",
                detail={"application_id": application_id, "reason": reason},
                category="marketplace",
                confidence=1.0,
                outcome="resolved",
            )
            # Notify the seller via FundzMarket bot
            try:
                app_rows = []
                if SUPABASE_URL and SUPABASE_SERVICE_KEY:
                    import requests as _req
                    _r = _req.get(
                        f"{SUPABASE_URL}/rest/v1/seller_applications"
                        f"?id=eq.{application_id}&select=user_id,store_name",
                        headers=_hdrs(), timeout=_DB_TIMEOUT,
                    )
                    app_rows = _r.json() if _r.status_code == 200 else []
                if app_rows:
                    app = app_rows[0]
                    reason_text = reason.strip() if reason else "Application did not meet our current requirements."
                    msg = (
                        f"❌ <b>Seller application update</b>\n\n"
                        f"Unfortunately, your application for <b>{app.get('store_name', 'your store')}</b> "
                        f"was not approved at this time.\n\n"
                        f"<b>Reason:</b> {reason_text}\n\n"
                        f"<b>What to do next:</b>\n"
                        f"Review the feedback above, make the necessary changes, and re-apply by "
                        f"tapping <b>➕ Sell Product</b> in FundzMarket."
                    )
                    _send_fundzmarket_dm(app["user_id"], msg)
            except Exception as _exc:
                log.debug("Seller rejection notification: %s", _exc)
            return True
    except Exception as exc:
        log.warning("reject_seller_application: %s", exc)
    return False


def _check_seller_applications() -> None:
    """
    Check for new pending seller applications and notify CEO if any are waiting.
    Called from the background monitor loop. Throttled via normal CEO alert mechanism.
    """
    try:
        apps = get_pending_seller_applications(limit=5)
        if not apps:
            return
        # Only alert if there are unreviewed applications pending
        count = len(apps)
        log.info("TestAudit: %d pending seller application(s) waiting for review", count)
        log_memory(
            "seller_applications_pending",
            f"{count} seller application(s) awaiting review",
            detail={"count": count, "ids": [a.get("id") for a in apps]},
            category="marketplace",
            confidence=1.0,
            outcome="pending",
        )
        # Queue as CEO approval item if not already queued
        for app in apps[:3]:
            app_id  = app.get("id", "?")
            user_id = app.get("user_id", "?")
            name    = app.get("store_name") or app.get("business_name") or f"User {user_id}"
            queue_ceo_approval(
                action_type="seller_application_review",
                title=f"Seller Application: {name}",
                description=(
                    f"FundzMarket seller application from user {user_id} is pending review. "
                    f"Store name: {name}. "
                    f"Submitted: {app.get('created_at', '?')[:10]}."
                ),
                payload={"application_id": app_id, "user_id": user_id, "store_name": name},
                confidence=1.0,
                risk_level="low",
            )
    except Exception as exc:
        log.debug("_check_seller_applications: %s", exc)


# ── Start / Stop ──────────────────────────────────────────────────────────────

def start_testaudit_core() -> None:
    """Start the continuous intelligence background thread. Call once from post_init."""
    global _running, _thread
    if _running:
        log.debug("TestAudit core already running")
        return

    # Log constitutional authority on every startup
    if _CONSTITUTION_LOADED:
        log.info("📜 TestAudit operating under: %s", _constitution_version())
        log.info("📋 TestAudit role: %s", _MANDATE.get("role", "Chief Operations Manager"))
    else:
        log.warning("TestAudit: Constitution not loaded — using default KPI thresholds")

    _running = True
    _thread  = threading.Thread(target=_monitor_loop, daemon=True, name="testaudit-core")
    _thread.start()
    log.info("✅ TestAudit intelligence core thread started")


def stop_testaudit_core() -> None:
    global _running
    _running = False
    log.info("TestAudit core stopped")
