"""
FundzAiBot — Feature Request Engine (TestAudit role)

Collects, ranks, and recommends product improvements from multiple sources:
  • User feedback (/feedback command submissions)
  • Failed command patterns from error logs
  • Group discussion topic requests
  • Onboarding drop-off signals
  • Usage analytics anomalies (high demand, low availability)
  • Manual CEO submissions via /testaudit → Backlog

Ranking algorithm:
  • Frequency (how often requested)
  • Demand (how many distinct users)
  • Business value (VIP mentions, revenue impact)
  • Growth impact (referral or retention correlation)
  • Implementation difficulty (estimated)

Weekly recommendations are sent to the CEO every Monday via Executive Assistant.
All data persists to Supabase testaudit_backlog table.
"""

from __future__ import annotations

import hashlib
import threading
import time
from datetime import datetime, timezone, timedelta

import requests

from config.settings import (
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
    TELEGRAM_BOT_TOKEN, ADMIN_USER_ID,
    BOT_NAME,
)
from utils.logger import get_logger

log = get_logger(__name__)

_running: bool = False
_thread: threading.Thread | None = None

# In-memory deduplication cache (title hash → count)
_request_cache: dict[str, int] = {}
_CACHE_MAX = 500


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
        log.debug("feature_tracker._sb_post(%s): %s", table, exc)
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
        log.debug("feature_tracker._sb_get(%s): %s", table, exc)
        return None


# ── Public API — call from handlers ───────────────────────────────────────────

def track_feature_request(
    title: str,
    description: str = "",
    source: str = "user_feedback",
    user_id: int | None = None,
    category: str = "feature",
    priority: str = "medium",
    confidence: float = 0.75,
) -> None:
    """
    Record a feature request from any source.
    Call this from: feedback handler, error handler, group handler, admin.

    Sources:
      "user_feedback"   — /feedback command
      "error_pattern"   — repeated command failures
      "group_request"   — community discussion
      "onboarding_gap"  — onboarding dropout analysis
      "analytics"       — usage pattern detection
      "admin_manual"    — CEO-submitted via testaudit
    """
    if not title or len(title) < 3:
        return

    title = title.strip()[:200]
    key   = hashlib.md5(title.lower().encode()).hexdigest()[:16]

    # Increment in-memory counter
    _request_cache[key] = _request_cache.get(key, 0) + 1
    count = _request_cache.get(key, 1)

    # Prune cache if too large
    if len(_request_cache) > _CACHE_MAX:
        oldest = sorted(_request_cache.items(), key=lambda x: x[1])[:100]
        for k, _ in oldest:
            _request_cache.pop(k, None)

    # Adjust priority by demand
    if count >= 10:
        priority = "high"
    elif count >= 5:
        priority = "medium"

    # Map source to impact estimate
    impact_map = {
        "user_feedback":  "medium",
        "error_pattern":  "high",
        "group_request":  "medium",
        "onboarding_gap": "high",
        "analytics":      "high",
        "admin_manual":   "high",
    }
    impact = impact_map.get(source, "medium")

    _upsert_backlog(
        title=title,
        description=description or f"Requested via {source}. Demand count: {count}.",
        category=category,
        priority=priority,
        impact=impact,
        source=source,
        confidence=confidence,
    )

    log.debug("feature_tracker: tracked '%s' (source=%s, count=%d)", title, source, count)


def track_error_pattern(error_type: str, command: str | None = None, count: int = 1) -> None:
    """
    Automatically track repeated errors as potential feature gaps.
    Call from the error handler for common error types.
    """
    if count < 3:
        return  # only track if it's a pattern, not a one-off

    title = f"Fix {error_type}" + (f" in /{command}" if command else "")
    track_feature_request(
        title=title,
        description=(
            f"Error '{error_type}' occurred {count}+ times"
            + (f" in /{command}" if command else "") + ". "
            "This pattern may indicate a UX gap or missing feature."
        ),
        source="error_pattern",
        category="bug_fix",
        priority="high" if count >= 10 else "medium",
        confidence=0.82,
    )


def submit_ceo_request(
    title: str,
    description: str = "",
    priority: str = "high",
) -> bool:
    """
    CEO manually submits a feature request via /testaudit.
    These are always added with high confidence.
    """
    if not title:
        return False
    track_feature_request(
        title=title,
        description=description,
        source="admin_manual",
        category="feature",
        priority=priority,
        confidence=1.0,
    )
    return True


# ── Backlog upsert ────────────────────────────────────────────────────────────

def _upsert_backlog(
    title: str,
    description: str,
    category: str,
    priority: str,
    impact: str,
    source: str,
    confidence: float,
) -> None:
    """Add to backlog. If a similar title exists, just log the demand signal."""
    try:
        from services.testaudit_core import add_backlog_item
        add_backlog_item(
            title=title,
            description=description,
            category=category,
            priority=priority,
            impact=impact,
            difficulty="medium",
            source=source,
            confidence=confidence,
        )
    except Exception as exc:
        log.debug("feature_tracker._upsert_backlog: %s", exc)


# ── Analytics Scanner ─────────────────────────────────────────────────────────

def _scan_error_patterns() -> None:
    """Scan error logs for recurring patterns and auto-add to backlog."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        r = _sb_get("error_logs", {
            "created_at": f"gte.{cutoff}",
            "select":     "error_type,message",
            "limit":      "500",
        })
        if not r or r.status_code != 200:
            return

        errors = r.json()
        error_counts: dict[str, int] = {}
        for e in errors:
            etype = (e.get("error_type") or "unknown")[:50]
            error_counts[etype] = error_counts.get(etype, 0) + 1

        for etype, count in error_counts.items():
            if count >= 5 and etype not in ("unhandled_exception",):
                track_error_pattern(etype, count=count)

    except Exception as exc:
        log.debug("feature_tracker._scan_error_patterns: %s", exc)


def _scan_onboarding_dropouts() -> None:
    """Identify onboarding gaps and flag as improvement opportunities."""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        users_r = _sb_get("users", {
            "created_at": f"gte.{cutoff}",
            "select":     "user_id",
        })
        if not users_r or users_r.status_code != 200:
            return

        total_new = len(users_r.json())
        if total_new < 10:
            return  # not enough signal

        convos_r = _sb_get("conversations", {
            "created_at": f"gte.{cutoff}",
            "role":       "eq.user",
            "select":     "user_id",
        })
        if not convos_r or convos_r.status_code != 200:
            return

        chatted_users = {str(r["user_id"]) for r in convos_r.json()}
        dropout_count = total_new - len(chatted_users)
        dropout_rate  = dropout_count / total_new if total_new > 0 else 0

        if dropout_rate > 0.4:  # >40% drop off without chatting
            track_feature_request(
                title="Improve onboarding flow — reduce dropout rate",
                description=(
                    f"{dropout_count}/{total_new} new users ({dropout_rate:.0%}) this week "
                    "registered but never sent a chat message. Onboarding may need improvement."
                ),
                source="onboarding_gap",
                category="ux",
                priority="high",
                confidence=0.88,
            )

    except Exception as exc:
        log.debug("feature_tracker._scan_onboarding_dropouts: %s", exc)


# ── Weekly Recommendations ────────────────────────────────────────────────────

def build_feature_recommendations() -> str:
    """Build a weekly feature recommendation summary for the CEO."""
    try:
        from services.testaudit_core import get_backlog
        items = get_backlog(status="open", limit=10)
    except Exception:
        items = []

    priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}

    lines = [
        f"🔬 <b>Feature Intelligence Report — {BOT_NAME}</b>",
        f"<i>{datetime.now(timezone.utc).strftime('%B %d, %Y')}</i>",
        "",
        f"<b>📋 Top Improvement Opportunities ({len(items)} open items)</b>",
        "",
    ]

    if not items:
        lines.append("✅ Backlog is empty — no open requests at this time.")
    else:
        for i, item in enumerate(items[:8], 1):
            pri  = item.get("priority", "medium")
            cat  = item.get("category", "feature")
            conf = item.get("confidence", 0.8)
            src  = item.get("source", "testaudit")
            lines.append(
                f"{i}. {priority_emoji.get(pri, '⚪')} <b>{item['title']}</b>"
            )
            lines.append(
                f"   [{cat.upper()}] · {pri} priority · {conf:.0%} confidence · from {src}"
            )
            if item.get("description"):
                lines.append(f"   <i>{item['description'][:100]}</i>")
            lines.append("")

    lines.append("<b>📌 Next Steps</b>")
    lines.append("  • Review /testaudit → 📋 Backlog to approve or dismiss items")
    lines.append("  • High-confidence, high-impact items are recommended for next sprint")
    lines.append("")
    lines.append("<i>TestAudit · Feature Intelligence Engine</i>")

    return "\n".join(lines)


# ── Background scanner ────────────────────────────────────────────────────────

def _scanner_loop() -> None:
    """Weekly background scan: error patterns and onboarding dropouts."""
    log.info("🔬 Feature Tracker started — weekly analytics scanner active")
    time.sleep(300)  # give bot time to start

    last_scan_date = ""

    while _running:
        try:
            now       = datetime.now(timezone.utc)
            today_str = now.strftime("%Y-%m-%d")

            # Run daily at 15:00 UTC
            if now.hour == 15 and now.minute < 10 and last_scan_date != today_str:
                log.info("Feature Tracker: running daily analytics scan")
                _scan_error_patterns()
                _scan_onboarding_dropouts()
                last_scan_date = today_str
                log.info("Feature Tracker: daily scan complete")

        except Exception as exc:
            log.error("feature_tracker scanner error: %s", exc)

        for _ in range(600):
            if not _running:
                break
            time.sleep(1)


def start_feature_tracker() -> None:
    global _running, _thread
    if _running:
        return
    _running = True
    _thread  = threading.Thread(
        target=_scanner_loop, daemon=True, name="feature-tracker"
    )
    _thread.start()
    log.info("✅ Feature Tracker started")


def stop_feature_tracker() -> None:
    global _running
    _running = False
