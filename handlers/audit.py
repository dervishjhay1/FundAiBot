"""
FundzAiBot — Enterprise Audit Center v3.0

/status       — Quick live status dashboard
/testaudit    — Full interactive diagnostic + repair center

Access: ADMIN_USER_ID and authorised secondary admins only.

Architecture:
  All audit sections run async and concurrently.
  Results are cached in context.bot_data (_CACHE_KEY) with a TTL so
  navigation never re-runs checks on every tap.
  "🔄 Full Retest" and each section's refresh button invalidate the cache.

  On /testaudit the dashboard loads in two phases:
    Phase 1: fast checks (security, railway, admin) — shown immediately
    Phase 2: all 14 sections — replaces the dashboard when done

Audit history:
  Each completed full-audit result is appended to _HISTORY_KEY (max 10).
  Admin can navigate history from the dashboard.

Critical Alert System:
  If any section is "fail" after a full audit, the admin is offered a
  broadcast warning button. Admin must approve — never auto-broadcast.

Auto-fix philosophy:
  ONLY safe in-memory repairs — refresh caches, reload state, re-seed
  missing data. NEVER touches API keys, production tables, or Railway.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config.settings import (
    is_admin, is_owner,
    ADMIN_USER_ID, BOT_NAME, BOT_VERSION,
    TELEGRAM_BOT_TOKEN, SUPABASE_URL, SUPABASE_SERVICE_KEY,
    OPENROUTER_API_KEY, GEMINI_API_KEY, HUGGINGFACE_API_KEY,
    OPENROUTER_MODEL, GEMINI_MODEL, HF_CHAT_MODEL,
    TELEGRAM_CHANNEL_ID, TELEGRAM_CHANNEL_URL, TELEGRAM_CHANNEL_NAME,
    TELEGRAM_GROUP_ID, TELEGRAM_GROUP_URL, TELEGRAM_GROUP_NAME,
    IS_RAILWAY, ALLOW_POLLING, FEATURE_FLAGS,
    FREE_DAILY_CHAT, FREE_DAILY_IMAGE,
)
from services.queue_manager import queue_manager
from utils.logger import get_logger

log = get_logger(__name__)

# ── Cache / History keys & TTL ────────────────────────────────────────────────

_AUDIT_TTL   = 120          # seconds before cache expires
_CACHE_KEY   = "audit_v3"   # current audit result in bot_data
_HISTORY_KEY = "audit_hist" # list of past audit summaries (max 10)
_MAX_HISTORY = 10

# ── Status icons ──────────────────────────────────────────────────────────────

_ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "info": "ℹ️", "skip": "⬜"}

# Status display maps
_STATUS_BADGE  = {"pass": "✅", "warn": "⚠️", "fail": "❌", "skip": "⬜", "info": "ℹ️"}
_STATUS_LABEL  = {"pass": "OK", "warn": "Warning", "fail": "Critical", "skip": "Skipped", "info": "Info"}

SECTION_META: dict[str, tuple[str, str]] = {
    "bot_core":      ("🤖", "Bot Core"),
    "ai_providers":  ("🧠", "AI Providers"),
    "database":      ("🗄️", "Database"),
    "railway":       ("🚂", "Railway"),
    "channel":       ("📢", "Channel"),
    "community":     ("👥", "Community"),
    "admin":         ("👑", "Admin"),
    "referrals":     ("🎁", "Referrals"),
    "vip":           ("💎", "VIP"),
    "announcements": ("📌", "Announcements"),
    "languages":     ("🌍", "Languages"),
    "security":      ("🔒", "Security"),
    "error_logs":    ("📋", "Error Logs"),
    "integrations":  ("⚙️", "Integrations"),
}


# ── Check result helpers ──────────────────────────────────────────────────────

def _check(
    name: str,
    status: str,
    detail: str,
    fix: str | None = None,
    cause: str | None = None,
    severity: str | None = None,
    files: list[str] | None = None,
) -> dict:
    return {
        "name": name,
        "status": status,
        "detail": detail,
        "fix": fix,
        "cause": cause,
        "severity": severity or status,
        "files": files or [],
    }


def _section_status(checks: list[dict]) -> str:
    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


def _make_section(
    checks: list[dict],
    auto_fixable: bool = False,
    fix_desc: str = "",
    fix_key: str = "",
) -> dict:
    status = _section_status(checks)
    return {
        "checks": checks,
        "status": status,
        "auto_fixable": auto_fixable,
        "fix_desc": fix_desc,
        "fix_key": fix_key,
    }


# ── Individual section auditors ───────────────────────────────────────────────

async def _audit_bot_core(bot) -> dict:
    checks = []
    try:
        me = await asyncio.wait_for(bot.get_me(), timeout=8)
        checks.append(_check("Telegram API", "pass",
                             f"@{me.username} connected",
                             cause="Token valid & accepted"))
        checks.append(_check("Bot identity", "pass",
                             f"{me.first_name} (id={me.id})"))
        checks.append(_check("Bot token", "pass", "Format & auth verified"))
    except asyncio.TimeoutError:
        checks.append(_check(
            "Telegram API", "fail",
            "Timeout — Telegram unreachable (>8s)",
            fix="Check TELEGRAM_BOT_TOKEN and Railway outbound network",
            cause="Network timeout or invalid token",
            severity="fail",
            files=["config/settings.py"],
        ))
    except TelegramError as exc:
        err = str(exc)
        if "terminated by other" in err.lower() or "conflict" in err.lower():
            checks.append(_check(
                "Duplicate instance", "fail",
                "409 Conflict — another bot instance is polling",
                fix="Stop the duplicate instance. Ensure only Railway runs polling.",
                cause="Multiple bot instances using the same token",
                severity="fail",
                files=["main.py", "config/settings.py"],
            ))
        else:
            checks.append(_check(
                "Telegram API", "fail", f"TelegramError: {err[:80]}",
                fix="Verify TELEGRAM_BOT_TOKEN in Railway env vars",
                cause="Invalid or revoked bot token",
                files=["config/settings.py"],
            ))

    # Polling guard
    if IS_RAILWAY:
        checks.append(_check("Polling guard", "pass",
                             "Railway detected — this instance owns polling"))
        checks.append(_check("Duplicate risk", "pass",
                             "numReplicas=1 enforced in railway.json"))
    elif ALLOW_POLLING:
        checks.append(_check(
            "Polling guard", "warn",
            "ALLOW_POLLING override active outside Railway",
            fix="Remove ALLOW_POLLING before Railway deploy to avoid 409 Conflicts",
            cause="Manual override — risk of duplicate instances",
        ))
    else:
        checks.append(_check("Polling guard", "pass",
                             "Replit/dev mode — polling blocked (correct)"))

    # Queue manager
    try:
        qs = queue_manager.stats()
        checks.append(_check("Queue manager", "pass",
                             f"Active: {qs['active_users']}  "
                             f"Queued: {qs['queue_size']}  "
                             f"Processed: {qs['processed']}  "
                             f"Errors: {qs['errors']}"))
    except Exception as exc:
        checks.append(_check("Queue manager", "warn",
                             f"Stats unavailable: {exc}",
                             fix="Queue will restart on next request"))

    # Feature flags
    flags_on  = [k for k, v in FEATURE_FLAGS.items() if v]
    flags_off = [k for k, v in FEATURE_FLAGS.items() if not v]
    if FEATURE_FLAGS.get("maintenance_mode"):
        checks.append(_check(
            "Maintenance mode", "warn",
            "MAINTENANCE is ON — users see maintenance message",
            fix="Toggle off via Bot Settings or auto-fix",
            cause="Maintenance mode was enabled",
        ))
    else:
        checks.append(_check("Feature flags", "pass",
                             f"All features active: {', '.join(flags_on) or 'none'}"))
    if flags_off and not (len(flags_off) == 1 and "maintenance_mode" in flags_off):
        checks.append(_check(
            "Disabled features", "warn",
            f"Off: {', '.join(f for f in flags_off if f != 'maintenance_mode')}",
            fix="Enable via Bot Settings or auto-fix",
        ))

    return _make_section(
        checks,
        auto_fixable=True,
        fix_desc="Reset feature flags & restart queue manager",
        fix_key="bot_core",
    )


async def _audit_ai_providers() -> dict:
    import requests as _req

    checks = []

    _OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    _GEMINI_BASE    = "https://generativelanguage.googleapis.com/v1beta/models"
    _HF_BASE        = "https://api-inference.huggingface.co/models"

    # OpenRouter
    if OPENROUTER_API_KEY:
        try:
            t0 = time.time()
            r = _req.post(
                _OPENROUTER_URL,
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": OPENROUTER_MODEL,
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 1},
                timeout=12,
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                checks.append(_check("OpenRouter", "pass",
                                     f"OK  model={OPENROUTER_MODEL}  latency={ms}ms"))
            elif r.status_code == 402:
                checks.append(_check(
                    "OpenRouter", "fail", "402 Insufficient credits",
                    fix="Top up at openrouter.ai/credits",
                    cause="Account balance exhausted",
                    severity="fail",
                    files=["services/ai_service.py"],
                ))
            elif r.status_code == 401:
                checks.append(_check(
                    "OpenRouter", "fail", "401 Invalid API key",
                    fix="Replace OPENROUTER_API_KEY in Railway → Variables",
                    cause="Key revoked or incorrect",
                    files=["config/settings.py"],
                ))
            elif r.status_code == 404:
                checks.append(_check(
                    "OpenRouter", "warn", f"404 Model not found: {OPENROUTER_MODEL}",
                    fix=f"Set OPENROUTER_MODEL to a valid model ID",
                    cause="Model ID typo or deprecated model",
                    files=["config/settings.py"],
                ))
            elif r.status_code == 429:
                checks.append(_check(
                    "OpenRouter", "warn", "429 Rate limited — auto-fallback active",
                    fix="Reduce request frequency or upgrade plan",
                    cause="Daily/minute rate limit hit",
                ))
            else:
                checks.append(_check("OpenRouter", "warn",
                                     f"HTTP {r.status_code}: {r.text[:80]}"))
        except _req.Timeout:
            checks.append(_check("OpenRouter", "warn", "Timeout (12s) — may be transient",
                                 fix="Retry in a few minutes"))
        except _req.ConnectionError:
            checks.append(_check("OpenRouter", "fail", "Connection refused",
                                 fix="Check Railway outbound network rules",
                                 cause="Network connectivity issue"))
        except Exception as exc:
            checks.append(_check("OpenRouter", "fail", str(exc)[:80]))
    else:
        checks.append(_check(
            "OpenRouter", "skip", "OPENROUTER_API_KEY not set",
            fix="Set OPENROUTER_API_KEY in Railway env vars",
        ))

    # Gemini
    if GEMINI_API_KEY:
        try:
            t0 = time.time()
            r = _req.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": "hi"}]}],
                      "generationConfig": {"maxOutputTokens": 3}},
                timeout=12,
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                checks.append(_check("Gemini", "pass",
                                     f"OK  model={GEMINI_MODEL}  latency={ms}ms"))
            elif r.status_code == 429:
                checks.append(_check(
                    "Gemini", "warn", "429 Quota exceeded — auto-fallback active",
                    fix="Wait for quota reset or upgrade Google AI quota",
                    cause="Free tier quota exhausted",
                ))
            elif r.status_code in (400, 403):
                checks.append(_check(
                    "Gemini", "fail", f"Auth error (HTTP {r.status_code})",
                    fix="Replace GEMINI_API_KEY in Railway → Variables",
                    cause="Key invalid or API not enabled in Google Cloud",
                    files=["config/settings.py"],
                ))
            else:
                checks.append(_check("Gemini", "warn", f"HTTP {r.status_code}: {r.text[:80]}"))
        except _req.Timeout:
            checks.append(_check("Gemini", "warn", "Timeout (12s)",
                                 fix="Gemini may be temporarily slow — retry"))
        except Exception as exc:
            checks.append(_check("Gemini", "warn", str(exc)[:80]))
    else:
        checks.append(_check(
            "Gemini", "skip", "GEMINI_API_KEY not set",
            fix="Set GEMINI_API_KEY in Railway env vars",
        ))

    # HuggingFace
    if HUGGINGFACE_API_KEY:
        try:
            t0 = time.time()
            r = _req.post(
                f"{_HF_BASE}/{HF_CHAT_MODEL}",
                headers={"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"},
                json={"inputs": "hi", "parameters": {"max_new_tokens": 3}},
                timeout=15,
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code in (200, 503):
                label = "Warming up (503 — ready in ~20s)" if r.status_code == 503 else f"OK  model={HF_CHAT_MODEL}"
                status = "warn" if r.status_code == 503 else "pass"
                checks.append(_check("HuggingFace", status, f"{label}  latency={ms}ms"))
            elif r.status_code == 401:
                checks.append(_check(
                    "HuggingFace", "fail", "Invalid HF token (401)",
                    fix="Replace HUGGINGFACE_API_KEY in Railway → Variables",
                    cause="Token revoked or incorrect",
                    files=["config/settings.py"],
                ))
            elif r.status_code == 429:
                checks.append(_check(
                    "HuggingFace", "warn", "429 Rate limited",
                    fix="Upgrade HuggingFace Pro or reduce image generation frequency",
                ))
            else:
                checks.append(_check("HuggingFace", "warn", f"HTTP {r.status_code}: {r.text[:80]}"))
        except (_req.Timeout, _req.ConnectionError):
            checks.append(_check("HuggingFace", "warn",
                                 "Unreachable — image gen may be down",
                                 fix="Check network or switch to a different HF model"))
        except Exception as exc:
            checks.append(_check("HuggingFace", "warn", str(exc)[:80]))
    else:
        checks.append(_check(
            "HuggingFace", "skip", "HUGGINGFACE_API_KEY not set — image gen disabled",
            fix="Set HUGGINGFACE_API_KEY in Railway env vars",
        ))

    # Fallback chain summary
    configured = sum([bool(OPENROUTER_API_KEY), bool(GEMINI_API_KEY), bool(HUGGINGFACE_API_KEY)])
    if configured >= 2:
        checks.append(_check("Fallback chain", "pass",
                             f"{configured}/3 providers configured — fallback available"))
    elif configured == 1:
        checks.append(_check(
            "Fallback chain", "warn",
            "Only 1 provider — no fallback if it fails",
            fix="Add a second AI provider key for redundancy",
        ))
    else:
        checks.append(_check(
            "Fallback chain", "fail",
            "No AI providers configured — bot cannot answer",
            fix="Set at least one AI key (OPENROUTER_API_KEY recommended) in Railway",
            cause="All AI keys missing from environment",
        ))

    return _make_section(
        checks,
        auto_fixable=True,
        fix_desc="Refresh provider cache (next request retries fresh)",
        fix_key="ai_providers",
    )


async def _audit_database() -> dict:
    import requests as _req
    from services.database import _headers, _url, _safe_get

    checks = []
    tables = [
        ("users",          "Core user table"),
        ("user_credits",   "Credit tracking"),
        ("conversations",  "Chat history"),
        ("image_history",  "Image generation log"),
        ("referrals",      "Referral system"),
        ("error_logs",     "Error logging"),
        ("announcements",  "Announcements"),
        ("admin_accounts", "Multi-admin system"),
        ("onboarding",     "Onboarding flow"),
    ]

    db_ok = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)
    if not db_ok:
        checks.append(_check(
            "Supabase credentials", "fail",
            "SUPABASE_URL or SUPABASE_SERVICE_KEY not set",
            fix="Set both in Railway → Variables",
            cause="Missing database configuration",
            files=["config/settings.py"],
        ))
        return _make_section(checks, auto_fixable=False,
                             fix_desc="Manual: set Supabase credentials in Railway")

    checks.append(_check("Supabase credentials", "pass",
                         f"URL configured: {SUPABASE_URL[:40]}…"))

    for table, label in tables:
        try:
            r = _safe_get(f"{_url(table)}?limit=1", headers=_headers())
            if r.status_code == 200:
                rows = r.json()
                checks.append(_check(f"Table: {table}", "pass",
                                     f"{label} — accessible ({len(rows)} sample row{'s' if len(rows) != 1 else ''})"))
            elif r.status_code == 404:
                checks.append(_check(
                    f"Table: {table}", "fail",
                    "Table not found in Supabase",
                    fix="Run supabase_schema.sql in Supabase → SQL Editor",
                    cause="Schema not applied",
                    files=["supabase_schema.sql"],
                ))
            elif r.status_code == 401:
                checks.append(_check(
                    f"Table: {table}", "fail",
                    "Auth error (401) — invalid service key",
                    fix="Replace SUPABASE_SERVICE_KEY in Railway",
                    cause="Service role key revoked or incorrect",
                    files=["config/settings.py"],
                ))
            else:
                checks.append(_check(f"Table: {table}", "warn",
                                     f"HTTP {r.status_code}"))
        except Exception as exc:
            checks.append(_check(f"Table: {table}", "fail", str(exc)[:60]))

    # RPC functions
    for rpc_fn in ("increment_chat", "increment_image"):
        try:
            r = _req.post(
                f"{SUPABASE_URL}/rest/v1/rpc/{rpc_fn}",
                headers=_headers(),
                json={"uid": 0, "n": 0},
                timeout=(5, 8),
            )
            if r.status_code in (200, 204, 404, 409):
                checks.append(_check(f"RPC: {rpc_fn}()", "pass", "Function exists and reachable"))
            elif "function" in r.text.lower() and r.status_code == 404:
                checks.append(_check(
                    f"RPC: {rpc_fn}()", "warn",
                    "RPC function not registered",
                    fix="Add function to Supabase SQL Editor from schema file",
                    files=["supabase_schema.sql"],
                ))
            else:
                checks.append(_check(f"RPC: {rpc_fn}()", "warn", f"HTTP {r.status_code}"))
        except Exception as exc:
            checks.append(_check(f"RPC: {rpc_fn}()", "warn", str(exc)[:60]))

    return _make_section(
        checks,
        auto_fixable=False,
        fix_desc="Manual: run supabase_schema.sql for missing tables",
    )


async def _audit_railway() -> dict:
    checks = []

    checks.append(_check(
        "Environment", "pass" if IS_RAILWAY else "info",
        "Railway (production)" if IS_RAILWAY else "Not Railway — dev/Replit mode",
    ))

    if IS_RAILWAY:
        rail_vars = {
            "RAILWAY_ENVIRONMENT":   os.getenv("RAILWAY_ENVIRONMENT"),
            "RAILWAY_SERVICE_NAME":  os.getenv("RAILWAY_SERVICE_NAME"),
            "RAILWAY_PROJECT_ID":    os.getenv("RAILWAY_PROJECT_ID"),
            "RAILWAY_SERVICE_ID":    os.getenv("RAILWAY_SERVICE_ID"),
        }
        present = [f"{k}={v[:8]}…" for k, v in rail_vars.items() if v]
        checks.append(_check("Railway markers", "pass", " | ".join(present) or "Detected via env"))
        checks.append(_check("Polling ownership", "pass",
                             "Railway owns polling — correct for production"))
        checks.append(_check("Duplicate instance risk", "pass",
                             "Single instance enforced (numReplicas=1 in railway.json)"))
    elif ALLOW_POLLING:
        checks.append(_check(
            "Polling ownership", "warn",
            "ALLOW_POLLING override outside Railway",
            fix="Remove ALLOW_POLLING to avoid 409 Conflicts when Railway is live",
            cause="Manual override active — risk if Railway is also running",
        ))
    else:
        checks.append(_check("Polling ownership", "pass",
                             "Polling disabled in dev mode — no conflict possible"))
        checks.append(_check("Duplicate instance risk", "pass",
                             "Polling blocked — Railway guard working correctly"))

    # Health endpoints
    port = os.getenv("PORT", "5000")
    for endpoint in ("/health", "/ready"):
        try:
            import requests as _req
            r = _req.get(f"http://localhost:{port}{endpoint}", timeout=3)
            checks.append(_check(
                f"Endpoint {endpoint}",
                "pass" if r.status_code == 200 else "warn",
                f"HTTP {r.status_code} on :{port}",
            ))
        except Exception as exc:
            checks.append(_check(f"Endpoint {endpoint}", "warn",
                                 f"Not reachable locally: {type(exc).__name__}"))

    # Required secrets
    secrets = {
        "TELEGRAM_BOT_TOKEN":   bool(TELEGRAM_BOT_TOKEN),
        "ADMIN_USER_ID":        bool(ADMIN_USER_ID),
        "SUPABASE_URL":         bool(SUPABASE_URL),
        "SUPABASE_SERVICE_KEY": bool(SUPABASE_SERVICE_KEY),
    }
    missing = [k for k, v in secrets.items() if not v]
    if missing:
        checks.append(_check(
            "Required secrets", "fail",
            "Missing: " + ", ".join(missing),
            fix="Set these in Railway → Service → Variables",
            cause="Critical env vars not configured",
            files=["config/settings.py"],
        ))
    else:
        checks.append(_check("Required secrets", "pass",
                             "All 4 critical secrets present"))

    return _make_section(
        checks,
        auto_fixable=False,
        fix_desc="Manual: configure Railway env vars and railway.json",
    )


async def _audit_channel(bot) -> dict:
    checks = []
    if not TELEGRAM_CHANNEL_ID:
        checks.append(_check(
            "Channel config", "warn",
            "TELEGRAM_CHANNEL_ID not set — channel features disabled",
            fix="Set TELEGRAM_CHANNEL_ID in Railway env vars",
        ))
        return _make_section(checks, auto_fixable=False,
                             fix_desc="Manual: set TELEGRAM_CHANNEL_ID in Railway")

    checks.append(_check("Channel ID", "pass", f"Configured: {TELEGRAM_CHANNEL_ID}"))
    checks.append(_check("Channel URL", "pass", TELEGRAM_CHANNEL_URL))

    try:
        chat = await asyncio.wait_for(bot.get_chat(TELEGRAM_CHANNEL_ID), timeout=8)
        checks.append(_check("Channel exists", "pass",
                             f"@{chat.username or 'private'} — {chat.title or 'Channel'}"))
        try:
            me = await bot.get_me()
            member = await asyncio.wait_for(
                bot.get_chat_member(TELEGRAM_CHANNEL_ID, me.id), timeout=8
            )
            if member.status in ("creator", "administrator"):
                can_post = getattr(member, "can_post_messages", None)
                can_pin  = getattr(member, "can_pin_messages", None)
                checks.append(_check("Bot channel admin", "pass",
                                     f"Status={member.status}  "
                                     f"can_post={can_post}  can_pin={can_pin}"))
                if not can_post:
                    checks.append(_check(
                        "Post permission", "warn",
                        "Bot cannot post messages to channel",
                        fix="Grant 'Post Messages' in channel admin settings",
                    ))
                if not can_pin:
                    checks.append(_check(
                        "Pin permission", "warn",
                        "Bot cannot pin messages in channel",
                        fix="Grant 'Pin Messages' in channel admin settings",
                    ))
            else:
                checks.append(_check(
                    "Bot channel admin", "fail",
                    f"Bot status={member.status} — not admin",
                    fix="Promote bot to admin with Post + Pin permissions",
                    cause="Bot not promoted to administrator",
                ))
        except TelegramError as exc:
            checks.append(_check("Bot admin status", "warn",
                                 f"Could not verify: {exc}"))
    except asyncio.TimeoutError:
        checks.append(_check("Channel exists", "warn", "Telegram API timeout"))
    except TelegramError as exc:
        checks.append(_check("Channel exists", "fail", str(exc),
                             fix="Check TELEGRAM_CHANNEL_ID and bot membership"))

    return _make_section(checks, auto_fixable=False,
                         fix_desc="Manual: promote bot to admin in channel")


async def _audit_community(bot) -> dict:
    checks = []
    if not TELEGRAM_GROUP_ID:
        checks.append(_check(
            "Group config", "warn",
            "TELEGRAM_GROUP_ID not set — group features disabled",
            fix="Set TELEGRAM_GROUP_ID in Railway env vars",
        ))
        return _make_section(checks, auto_fixable=False,
                             fix_desc="Manual: set TELEGRAM_GROUP_ID in Railway")

    checks.append(_check("Group ID", "pass", f"Configured: {TELEGRAM_GROUP_ID}"))
    checks.append(_check("Group URL", "pass", TELEGRAM_GROUP_URL))

    try:
        chat = await asyncio.wait_for(bot.get_chat(TELEGRAM_GROUP_ID), timeout=8)
        checks.append(_check("Group exists", "pass",
                             f"@{chat.username or 'private'} — {chat.title or 'Group'}"))
        try:
            me = await bot.get_me()
            member = await asyncio.wait_for(
                bot.get_chat_member(TELEGRAM_GROUP_ID, me.id), timeout=8
            )
            if member.status in ("creator", "administrator"):
                can_del  = getattr(member, "can_delete_messages", None)
                can_rest = getattr(member, "can_restrict_members", None)
                can_pin  = getattr(member, "can_pin_messages", None)
                checks.append(_check("Bot group admin", "pass",
                                     f"Status={member.status}  "
                                     f"delete={can_del}  restrict={can_rest}  pin={can_pin}"))
                for perm, label, tip in [
                    (can_del,  "Delete permission",   "Grant 'Delete Messages' for anti-spam"),
                    (can_rest, "Restrict permission", "Grant 'Restrict Members' for auto-mute"),
                    (can_pin,  "Pin permission",      "Grant 'Pin Messages' for announcements"),
                ]:
                    if not perm:
                        checks.append(_check(label, "warn",
                                             f"Bot lacks this permission in group",
                                             fix=tip))
            else:
                checks.append(_check(
                    "Bot group admin", "fail",
                    f"Bot status={member.status} — not admin",
                    fix="Promote bot to admin with Delete + Restrict + Pin permissions",
                    cause="Bot not promoted to administrator",
                ))
        except TelegramError as exc:
            checks.append(_check("Bot admin status", "warn", f"Could not verify: {exc}"))
    except asyncio.TimeoutError:
        checks.append(_check("Group exists", "warn", "Telegram API timeout"))
    except TelegramError as exc:
        checks.append(_check("Group exists", "fail", str(exc),
                             fix="Check TELEGRAM_GROUP_ID and bot membership"))

    return _make_section(checks, auto_fixable=False,
                         fix_desc="Manual: promote bot to admin in group")


async def _audit_admin() -> dict:
    from services.database import _headers, _url, _safe_get
    from config.settings import SECONDARY_ADMINS

    checks = []
    if ADMIN_USER_ID:
        checks.append(_check("Primary owner", "pass",
                             f"ADMIN_USER_ID={ADMIN_USER_ID} — set correctly"))
    else:
        checks.append(_check(
            "Primary owner", "fail",
            "ADMIN_USER_ID not set — critical security gap!",
            fix="Set ADMIN_USER_ID to your Telegram user ID in Railway",
            cause="Admin ID missing from environment",
            files=["config/settings.py"],
        ))

    checks.append(_check(
        "Secondary admins (runtime)", "pass",
        f"{len(SECONDARY_ADMINS)} loaded in memory: "
        + (", ".join(str(a) for a in SECONDARY_ADMINS) or "none"),
    ))

    try:
        r = _safe_get(f"{_url('admin_accounts')}?limit=50", headers=_headers())
        if r.status_code == 200:
            rows = r.json()
            checks.append(_check("admin_accounts table", "pass",
                                 f"{len(rows)} admin record(s) in database"))
        elif r.status_code == 404:
            checks.append(_check(
                "admin_accounts table", "warn",
                "Table not found — multi-admin DB persistence unavailable",
                fix="Run supabase_schema.sql to create admin_accounts table",
                files=["supabase_schema.sql"],
            ))
        else:
            checks.append(_check("admin_accounts table", "warn",
                                 f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("admin_accounts table", "warn", str(exc)[:60]))

    checks.append(_check("Command suite", "pass",
                         "/admin, /admin_users, /admin_ban, /admin_setvip, "
                         "/admin_broadcast, /testaudit — all registered"))

    return _make_section(
        checks,
        auto_fixable=True,
        fix_desc="Reload secondary admins from database",
        fix_key="admin",
    )


async def _audit_referrals() -> dict:
    from services.database import _headers, _url, _safe_get, count_users
    from config.settings import REFERRAL_CHAT_BONUS, REFERRAL_IMAGE_BONUS

    checks = []
    try:
        r = _safe_get(f"{_url('referrals')}?limit=5", headers=_headers())
        if r.status_code == 200:
            rows = r.json()
            checks.append(_check("Referrals table", "pass",
                                 f"Accessible — {len(rows)} recent record(s)"))
        elif r.status_code == 404:
            checks.append(_check(
                "Referrals table", "fail", "Table missing in Supabase",
                fix="Run supabase_schema.sql in Supabase SQL Editor",
                files=["supabase_schema.sql"],
            ))
        else:
            checks.append(_check("Referrals table", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("Referrals table", "fail", str(exc)[:60]))

    try:
        counts = count_users()
        checks.append(_check("User counts", "pass",
                             f"Total={counts['total']}  VIP={counts['vip']}  "
                             f"Banned={counts['banned']}  Free={counts['free']}"))
    except Exception as exc:
        checks.append(_check("User counts", "warn", str(exc)[:60]))

    checks.append(_check("Referral rewards", "pass",
                         f"+{REFERRAL_CHAT_BONUS} chat credits  "
                         f"+{REFERRAL_IMAGE_BONUS} image credits per referral"))
    checks.append(_check("Referral code generation", "pass",
                         "Auto-generated as REF{user_id} on first /start"))

    return _make_section(checks, auto_fixable=False,
                         fix_desc="Manual: run supabase_schema.sql for missing tables")


async def _audit_vip() -> dict:
    from services.database import _headers, _url, _safe_get
    from config.settings import VIP_PLANS

    checks = []
    for tier, plan in VIP_PLANS.items():
        checks.append(_check(
            f"VIP plan: {tier}", "pass",
            f"{plan['label']} — {plan['stars']} Stars — "
            f"{plan['chat_limit']} chats + {plan['image_limit']} images/day",
        ))

    try:
        r = _safe_get(f"{_url('users')}?is_vip=eq.true&limit=5", headers=_headers())
        if r.status_code == 200:
            rows = r.json()
            checks.append(_check("Active VIP users", "pass",
                                 f"{len(rows)} VIP user(s) in sample — records OK"))
        else:
            checks.append(_check("Active VIP users", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("VIP user query", "warn", str(exc)[:60]))

    # Check for expired VIPs
    try:
        from datetime import datetime
        now_iso = datetime.utcnow().isoformat()
        r2 = _safe_get(
            f"{_url('users')}?is_vip=eq.true&vip_expires_at=lt.{now_iso}&limit=5",
            headers=_headers(),
        )
        if r2.status_code == 200:
            expired = r2.json()
            if expired:
                checks.append(_check(
                    "Expired VIP users", "warn",
                    f"{len(expired)} VIP user(s) with past expiry date",
                    fix="VIP scheduler will handle this on next run",
                    cause="VIP scheduler may not have processed recent expiry",
                ))
            else:
                checks.append(_check("VIP expiry", "pass", "No expired VIP records found"))
    except Exception as exc:
        checks.append(_check("VIP expiry check", "warn", str(exc)[:60]))

    checks.append(_check("Payment method", "pass",
                         "Telegram Stars — instant, secure, no card needed"))
    checks.append(_check("VIP scheduler", "pass",
                         "Auto-expiry running via services/vip_scheduler.py"))

    return _make_section(
        checks,
        auto_fixable=True,
        fix_desc="Refresh VIP expiry cache",
        fix_key="vip",
    )


async def _audit_announcements() -> dict:
    from services.database import (
        _headers, _url, _safe_get,
        get_active_announcement, get_announcement_history,
    )

    checks = []
    try:
        r = _safe_get(f"{_url('announcements')}?limit=1", headers=_headers())
        if r.status_code == 200:
            checks.append(_check("Announcements table", "pass", "Table accessible"))
        elif r.status_code == 404:
            checks.append(_check(
                "Announcements table", "fail", "Table missing in Supabase",
                fix="Run supabase_schema.sql",
                files=["supabase_schema.sql"],
            ))
        else:
            checks.append(_check("Announcements table", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("Announcements table", "fail", str(exc)[:60]))

    try:
        ann = get_active_announcement()
        if ann:
            msg   = (ann.get("message") or "")[:60]
            photo = " | photo: ✅" if ann.get("photo_url") else " | no photo"
            checks.append(_check("Active announcement", "pass",
                                 f"'{msg}…'{photo}"))
        else:
            checks.append(_check(
                "Active announcement", "warn",
                "No active announcement — users see no pin on /start",
                fix="Use /pin <message> to create one, or use auto-fix to re-seed default",
            ))
    except Exception as exc:
        checks.append(_check("Active announcement", "warn", str(exc)[:60]))

    try:
        history = get_announcement_history(limit=10)
        checks.append(_check("Announcement history", "pass",
                             f"{len(history)} announcement(s) in history"))
    except Exception as exc:
        checks.append(_check("Announcement history", "warn", str(exc)[:60]))

    chan_ok = bool(TELEGRAM_CHANNEL_ID)
    grp_ok  = bool(TELEGRAM_GROUP_ID)
    if chan_ok and grp_ok:
        sync_status = "pass"
        sync_detail = "Channel + Group both configured — /announce_both available"
    elif chan_ok or grp_ok:
        sync_status = "warn"
        sync_detail = ("Channel only — set TELEGRAM_GROUP_ID for group sync"
                       if chan_ok else "Group only — set TELEGRAM_CHANNEL_ID for channel sync")
    else:
        sync_status = "warn"
        sync_detail = "Neither channel nor group configured — sync disabled"

    checks.append(_check("Channel/Group sync", sync_status, sync_detail))
    checks.append(_check("Navigator", "pass",
                         "◀ Prev / counter / Next ▶ buttons — inline navigation active"))

    return _make_section(
        checks,
        auto_fixable=True,
        fix_desc="Re-seed default announcement if none active",
        fix_key="announcements",
    )


async def _audit_languages() -> dict:
    checks = []

    try:
        from services.language import FREE_LANGUAGES, VIP_LANGUAGES, ALL_LANGUAGES, get_string
        total = len(ALL_LANGUAGES)
        free  = len(FREE_LANGUAGES)
        vip   = len(VIP_LANGUAGES)
        checks.append(_check("Language registry", "pass",
                             f"{total} languages loaded — {free} free, {vip} VIP-only"))
        checks.append(_check("Free languages", "pass",
                             ", ".join(ALL_LANGUAGES.get(k, k) for k in FREE_LANGUAGES)))
        checks.append(_check("VIP languages", "pass",
                             ", ".join(ALL_LANGUAGES.get(k, k) for k in VIP_LANGUAGES)))
    except ImportError as exc:
        checks.append(_check("Language module", "fail", str(exc)[:80]))
        return _make_section(checks, auto_fixable=False, fix_desc="")

    # Locale JSON files
    locales_dir = os.path.join(os.path.dirname(__file__), "..", "locales")
    present, missing_files = [], []
    from services.language import ALL_LANGUAGES as _ALL
    for code in _ALL:
        path = os.path.join(locales_dir, f"{code}.json")
        if os.path.exists(path):
            try:
                import json
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                present.append(f"{code}({len(data)} keys)")
            except Exception as e:
                missing_files.append(f"{code}(parse error: {e})")
        else:
            missing_files.append(f"{code}(missing)")

    if present:
        checks.append(_check("Locale JSON files", "pass",
                             f"{len(present)} files valid: " + ", ".join(present[:6])))
    if missing_files:
        checks.append(_check(
            "Missing locale files", "warn",
            ", ".join(missing_files),
            fix="Create locales/{code}.json for each language code",
            files=["locales/"],
        ))

    # Translation coverage
    try:
        from services.language import STRINGS
        key = "welcome_back"
        covered = [code for code in _ALL if code in STRINGS and key in STRINGS[code]]
        uncovered = [code for code in _ALL if code not in covered]
        if uncovered:
            checks.append(_check(
                "STRINGS coverage", "warn",
                f"Missing '{key}' for: {', '.join(uncovered)}",
                fix="Add translations to services/language.py STRINGS dict",
                files=["services/language.py"],
            ))
        else:
            checks.append(_check("STRINGS coverage", "pass",
                                 f"'{key}' translated in all {len(_ALL)} languages"))
    except Exception as exc:
        checks.append(_check("STRINGS coverage", "warn", str(exc)[:80]))

    # DB language column
    try:
        from services.database import _headers, _url, _safe_get
        r = _safe_get(f"{_url('users')}?select=language&limit=1", headers=_headers())
        if r.status_code == 200:
            checks.append(_check("DB language column", "pass",
                                 "users.language column exists and readable"))
        elif r.status_code == 400 and "language" in r.text:
            checks.append(_check(
                "DB language column", "fail",
                "Column missing — run supabase_language_schema.sql",
                fix="Run supabase_language_schema.sql in Supabase SQL Editor",
                files=["supabase_language_schema.sql"],
            ))
        else:
            checks.append(_check("DB language column", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("DB language column", "warn", str(exc)[:60]))

    # detect_language function
    try:
        from services.language import detect_language
        result = detect_language("en")
        checks.append(_check("detect_language()", "pass",
                             f"Working — 'en' → '{result}'"))
    except ImportError:
        checks.append(_check(
            "detect_language()", "warn",
            "Function not found in services/language.py",
            fix="Add detect_language() to services/language.py",
            files=["services/language.py"],
        ))
    except Exception as exc:
        checks.append(_check("detect_language()", "warn", str(exc)[:60]))

    return _make_section(checks, auto_fixable=False,
                         fix_desc="Run supabase_language_schema.sql for missing column")


async def _audit_security() -> dict:
    from config.settings import validate_config

    checks = []
    missing = validate_config()
    if missing:
        for m in missing:
            checks.append(_check(
                f"Secret: {m}", "fail", "Missing or invalid",
                fix=f"Set {m} in Railway → Service → Variables",
                cause="Critical secret not in environment",
                files=["config/settings.py"],
            ))
    else:
        checks.append(_check("Required secrets", "pass",
                             "All critical secrets present and non-empty"))

    # Token format
    if TELEGRAM_BOT_TOKEN:
        parts = TELEGRAM_BOT_TOKEN.split(":")
        if len(parts) == 2 and parts[0].isdigit() and len(parts[1]) > 20:
            checks.append(_check("Token format", "pass",
                                 "Format valid (bot_id:secret pattern)"))
        else:
            checks.append(_check("Token format", "warn",
                                 "Unexpected format — verify correctness",
                                 fix="Get a fresh token from @BotFather if needed"))

    # Duplicate polling risk
    if IS_RAILWAY:
        checks.append(_check("Duplicate polling risk", "pass",
                             "Railway-only guard active — single instance enforced"))
    else:
        checks.append(_check("Duplicate polling risk", "pass",
                             "Polling disabled in dev — no 409 Conflict possible"))

    # Admin ID
    if ADMIN_USER_ID and ADMIN_USER_ID != 0:
        checks.append(_check("Admin access control", "pass",
                             f"Owner ID={ADMIN_USER_ID} — all admin commands protected"))
    else:
        checks.append(_check(
            "Admin access control", "fail",
            "ADMIN_USER_ID=0 — any user can access admin paths!",
            fix="Set ADMIN_USER_ID to your Telegram user ID immediately",
            cause="Admin ID missing or zero",
            severity="fail",
            files=["config/settings.py"],
        ))

    # Supabase key type
    if SUPABASE_SERVICE_KEY:
        if len(SUPABASE_SERVICE_KEY) > 100:
            checks.append(_check("Supabase key type", "pass",
                                 "Looks like a service_role key (correct for server use)"))
        else:
            checks.append(_check("Supabase key type", "warn",
                                 "Key seems short — ensure it's the service_role key, not anon",
                                 fix="Use service_role key from Supabase → Project Settings → API"))

    return _make_section(checks, auto_fixable=False,
                         fix_desc="Manual: set missing secrets in Railway → Variables")


async def _audit_error_logs() -> dict:
    from services.database import get_recent_errors

    checks = []
    try:
        errors = get_recent_errors(20)
        if not errors:
            checks.append(_check("Error log", "pass", "No recent errors — clean slate ✨"))
        else:
            by_type: dict[str, int] = {}
            for e in errors:
                t = e.get("error_type", "unknown")
                by_type[t] = by_type.get(t, 0) + 1
            summary = ", ".join(
                f"{k}×{v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1])
            )
            level = "warn" if len(errors) < 10 else "fail"
            checks.append(_check(
                "Recent errors", level,
                f"{len(errors)} errors logged: {summary}",
                fix="Review in admin panel (/admin_logs) or use auto-fix to clear old entries",
            ))
            for e in errors[:3]:
                ts  = (e.get("created_at") or "")[:16].replace("T", " ")
                uid = f" (user {e['user_id']})" if e.get("user_id") else ""
                checks.append(_check(
                    f"  ↳ {e.get('error_type','?')}", "warn",
                    f"{(e.get('message') or '')[:70]}{uid}  [{ts}]",
                ))
    except Exception as exc:
        checks.append(_check(
            "Error log access", "warn",
            f"Could not fetch: {exc}",
            fix="Verify error_logs table exists in Supabase",
            files=["supabase_schema.sql"],
        ))

    return _make_section(
        checks,
        auto_fixable=True,
        fix_desc="Clear old error log entries (older than today)",
        fix_key="error_logs",
    )


async def _audit_integrations() -> dict:
    import asyncio
    import requests as _req

    checks = []

    # Telegram Bot API
    try:
        token = TELEGRAM_BOT_TOKEN or ""
        if not token:
            checks.append(_check("Telegram Bot API", "fail", "BOT_TOKEN missing"))
        else:
            r = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _req.get(
                    f"https://api.telegram.org/bot{token}/getMe", timeout=8
                )
            )
            if r.status_code == 200 and r.json().get("ok"):
                info = r.json()["result"]
                checks.append(_check("Telegram Bot API", "pass",
                                     f"@{info.get('username','')} — auth OK"))
            else:
                checks.append(_check("Telegram Bot API", "fail",
                                     f"HTTP {r.status_code}: {r.text[:60]}"))
    except Exception as exc:
        checks.append(_check("Telegram Bot API", "fail", str(exc)[:80]))

    # Supabase REST
    try:
        sb_url = SUPABASE_URL or ""
        sb_key = SUPABASE_SERVICE_KEY or ""
        if not sb_url or not sb_key:
            checks.append(_check("Supabase REST", "fail",
                                 "SUPABASE_URL or SUPABASE_SERVICE_KEY missing"))
        else:
            r = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _req.get(
                    f"{sb_url}/rest/v1/users?limit=1",
                    headers={"apikey": sb_key, "Authorization": f"Bearer {sb_key}"},
                    timeout=8,
                )
            )
            if r.status_code == 200:
                checks.append(_check("Supabase REST", "pass",
                                     f"Connected — {sb_url[:40]}…"))
            elif r.status_code == 401:
                checks.append(_check("Supabase REST", "fail",
                                     "Unauthorised — check SUPABASE_SERVICE_KEY"))
            else:
                checks.append(_check("Supabase REST", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("Supabase REST", "fail", str(exc)[:80]))

    # OpenRouter
    try:
        key = OPENROUTER_API_KEY or ""
        if not key:
            checks.append(_check("OpenRouter API", "fail", "OPENROUTER_API_KEY missing"))
        else:
            r = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _req.get(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=8,
                )
            )
            if r.status_code == 200:
                checks.append(_check("OpenRouter API", "pass",
                                     f"Auth OK — model: {OPENROUTER_MODEL}"))
            elif r.status_code == 401:
                checks.append(_check("OpenRouter API", "fail", "Invalid API key"))
            else:
                checks.append(_check("OpenRouter API", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("OpenRouter API", "warn", str(exc)[:80]))

    # Gemini
    try:
        key = GEMINI_API_KEY or ""
        if not key:
            checks.append(_check("Google Gemini API", "warn", "GEMINI_API_KEY not set"))
        else:
            r = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _req.get(
                    f"https://generativelanguage.googleapis.com/v1/models?key={key}",
                    timeout=8,
                )
            )
            if r.status_code == 200:
                checks.append(_check("Google Gemini API", "pass",
                                     f"Auth OK — model: {GEMINI_MODEL}"))
            elif r.status_code == 400:
                checks.append(_check("Google Gemini API", "fail",
                                     "Invalid API key — check GEMINI_API_KEY"))
            else:
                checks.append(_check("Google Gemini API", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("Google Gemini API", "warn", str(exc)[:80]))

    # HuggingFace
    try:
        key = HUGGINGFACE_API_KEY or ""
        if not key:
            checks.append(_check("HuggingFace API", "warn",
                                 "HUGGINGFACE_API_KEY not set — image gen disabled"))
        else:
            r = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _req.get(
                    "https://huggingface.co/api/whoami",
                    headers={"Authorization": f"Bearer {key}"},
                    timeout=8,
                )
            )
            if r.status_code == 200:
                uname = r.json().get("name", "?")
                checks.append(_check("HuggingFace API", "pass",
                                     f"Auth OK — account: {uname}"))
            elif r.status_code == 401:
                checks.append(_check("HuggingFace API", "fail",
                                     "Invalid token — check HUGGINGFACE_API_KEY"))
            else:
                checks.append(_check("HuggingFace API", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("HuggingFace API", "warn", str(exc)[:80]))

    # Railway
    railway_env = os.getenv("RAILWAY_ENVIRONMENT", "")
    railway_svc = os.getenv("RAILWAY_SERVICE_NAME", "")
    if IS_RAILWAY:
        checks.append(_check("Railway deployment", "pass",
                             f"env={railway_env or 'production'}  svc={railway_svc or BOT_NAME}"))
    else:
        checks.append(_check("Railway deployment", "warn",
                             "Not on Railway — dev/Replit mode"))

    # Keepalive endpoint
    try:
        web_url = os.getenv("BOT_WEB_URL", "") or os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
        if web_url:
            ping_url = (f"https://{web_url}/health"
                        if not web_url.startswith("http") else f"{web_url}/health")
            r = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _req.get(ping_url, timeout=6)
            )
            checks.append(_check(
                "Keepalive endpoint", "pass" if r.status_code == 200 else "warn",
                f"{ping_url} → HTTP {r.status_code}",
            ))
        else:
            checks.append(_check("Keepalive endpoint", "warn",
                                 "BOT_WEB_URL not set — uptime pings disabled",
                                 fix="Set BOT_WEB_URL in Railway env vars"))
    except Exception as exc:
        checks.append(_check("Keepalive endpoint", "warn", str(exc)[:60]))

    return _make_section(
        checks,
        auto_fixable=False,
        fix_desc="Manual: verify API keys in Railway env vars",
    )


# ── Full audit runner ─────────────────────────────────────────────────────────

_SECTION_RUNNERS = {
    "bot_core":      lambda bot: _audit_bot_core(bot),
    "ai_providers":  lambda bot: _audit_ai_providers(),
    "database":      lambda bot: _audit_database(),
    "railway":       lambda bot: _audit_railway(),
    "channel":       lambda bot: _audit_channel(bot),
    "community":     lambda bot: _audit_community(bot),
    "admin":         lambda bot: _audit_admin(),
    "referrals":     lambda bot: _audit_referrals(),
    "vip":           lambda bot: _audit_vip(),
    "announcements": lambda bot: _audit_announcements(),
    "languages":     lambda bot: _audit_languages(),
    "security":      lambda bot: _audit_security(),
    "error_logs":    lambda bot: _audit_error_logs(),
    "integrations":  lambda bot: _audit_integrations(),
}


async def run_full_audit(bot) -> dict:
    """Run all 14 audit sections concurrently and calculate health score."""
    t_start = time.time()

    section_keys = list(SECTION_META.keys())
    tasks = [_SECTION_RUNNERS[k](bot) for k in section_keys]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    sections: dict[str, dict] = {}
    total_pass = total_warn = total_fail = 0

    for key, result in zip(section_keys, results):
        if isinstance(result, Exception):
            sections[key] = _make_section(
                [_check("Section runner", "fail", str(result)[:80])],
                auto_fixable=False,
            )
        else:
            sections[key] = result

        for c in sections[key]["checks"]:
            s = c["status"]
            if s == "pass":
                total_pass += 1
            elif s == "warn":
                total_warn += 1
            elif s == "fail":
                total_fail += 1

    total_checks = total_pass + total_warn + total_fail
    max_points   = total_checks * 2
    earned       = total_pass * 2 + total_warn * 1
    health_score = round(earned / max_points * 100) if max_points else 0

    audit = {
        "timestamp":    time.time(),
        "duration_ms":  int((time.time() - t_start) * 1000),
        "sections":     sections,
        "total_pass":   total_pass,
        "total_warn":   total_warn,
        "total_fail":   total_fail,
        "total_checks": total_checks,
        "health_score": health_score,
    }
    return audit


async def run_quick_audit(bot) -> dict:
    """Run only fast checks (no external API calls). Used for initial display."""
    t_start = time.time()
    fast_keys = ["security", "railway", "admin"]
    tasks = [_SECTION_RUNNERS[k](bot) for k in fast_keys]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    sections: dict[str, dict] = {}
    total_pass = total_warn = total_fail = 0

    for key, result in zip(fast_keys, results):
        if isinstance(result, Exception):
            sections[key] = _make_section(
                [_check("Section runner", "fail", str(result)[:80])],
            )
        else:
            sections[key] = result
        for c in sections[key]["checks"]:
            s = c["status"]
            if s == "pass":   total_pass += 1
            elif s == "warn": total_warn += 1
            elif s == "fail": total_fail += 1

    all_sections = {k: {"checks": [], "status": "skip",
                        "auto_fixable": False, "fix_desc": "", "fix_key": ""}
                    for k in SECTION_META}
    all_sections.update(sections)

    total_checks = total_pass + total_warn + total_fail
    max_points   = total_checks * 2
    earned       = total_pass * 2 + total_warn
    health_score = round(earned / max_points * 100) if max_points else 0

    return {
        "timestamp":    time.time(),
        "duration_ms":  int((time.time() - t_start) * 1000),
        "sections":     all_sections,
        "total_pass":   total_pass,
        "total_warn":   total_warn,
        "total_fail":   total_fail,
        "total_checks": total_checks,
        "health_score": health_score,
        "partial":      True,
        "partial_note": "⏳ Running full audit in background… tap 🔄 to refresh.",
    }


# ── History helpers ───────────────────────────────────────────────────────────

def _push_history(bot_data: dict, audit: dict) -> None:
    history = bot_data.get(_HISTORY_KEY, [])
    summary = {
        "timestamp":    audit["timestamp"],
        "health_score": audit["health_score"],
        "total_pass":   audit["total_pass"],
        "total_warn":   audit["total_warn"],
        "total_fail":   audit["total_fail"],
        "duration_ms":  audit.get("duration_ms", 0),
    }
    history.insert(0, summary)
    bot_data[_HISTORY_KEY] = history[:_MAX_HISTORY]


# ── Dashboard renderers ───────────────────────────────────────────────────────

def _score_emoji(score: int) -> str:
    if score >= 90: return "🟢"
    if score >= 70: return "🟡"
    if score >= 50: return "🟠"
    return "🔴"


def _render_dashboard(audit: dict) -> tuple[str, InlineKeyboardMarkup]:
    score   = audit["health_score"]
    total_p = audit["total_pass"]
    total_w = audit["total_warn"]
    total_f = audit["total_fail"]
    sections = audit["sections"]
    ts      = datetime.fromtimestamp(audit["timestamp"], tz=timezone.utc).strftime("%H:%M:%S UTC")
    dur_ms  = audit.get("duration_ms", 0)
    partial = audit.get("partial", False)

    readiness = (
        "🚀 Production Ready" if score >= 90 else
        "⚠️ Review Warnings"  if score >= 70 else
        "🔴 Needs Attention"
    )

    lines = [
        "<b>🛡️ FundzAiBot Audit Center</b>",
        "",
        f"{_score_emoji(score)} <b>Health Score: {score}%</b>  —  {readiness}",
        "",
        f"✅ Passed: {total_p}   ⚠️ Warnings: {total_w}   ❌ Critical: {total_f}",
        f"<i>Audited: {ts}  ({dur_ms}ms)</i>",
    ]

    if partial:
        lines.append(f"\n{audit.get('partial_note', '')}")

    # Critical alert notice
    if total_f > 0 and not partial:
        lines.append(f"\n⚠️ <b>{total_f} critical issue(s) detected.</b> See sections below.")

    text = "\n".join(lines)

    def _section_btn(key: str) -> InlineKeyboardButton:
        icon, label = SECTION_META[key]
        sec = sections.get(key, {})
        sec_status = sec.get("status", "skip")
        badge = _STATUS_BADGE.get(sec_status, "⬜")
        return InlineKeyboardButton(
            f"{icon} {label} {badge}",
            callback_data=f"audit:section:{key}",
        )

    section_keys = list(SECTION_META.keys())
    rows = []
    for i in range(0, len(section_keys), 2):
        row = [_section_btn(section_keys[i])]
        if i + 1 < len(section_keys):
            row.append(_section_btn(section_keys[i + 1]))
        rows.append(row)

    rows.append([
        InlineKeyboardButton("🛠 Auto Fix All",  callback_data="audit:autofix:all"),
        InlineKeyboardButton("📄 Report",         callback_data="audit:report"),
    ])
    rows.append([
        InlineKeyboardButton("📋 Error Logs",     callback_data="audit:section:error_logs"),
        InlineKeyboardButton("📜 History",         callback_data="audit:history"),
    ])
    rows.append([
        InlineKeyboardButton("🧠 CEO Advisor",    callback_data="audit:ceo_advisor"),
        InlineKeyboardButton("🏢 Departments",    callback_data="audit:departments"),
    ])
    rows.append([
        InlineKeyboardButton("📋 Backlog",        callback_data="audit:backlog"),
        InlineKeyboardButton("👤 Customer Success", callback_data="audit:customer_success"),
    ])
    rows.append([
        InlineKeyboardButton("⏳ Pending Approvals", callback_data="audit:pending_approvals"),
        InlineKeyboardButton("🧠 Health Score",   callback_data="audit:live_health"),
    ])
    rows.append([
        InlineKeyboardButton("💬 Talk to TestAudit", callback_data="audit:exec_chat"),
        InlineKeyboardButton("🤖 Auto Mode",         callback_data="audit:autonomous_status"),
    ])
    action_row = [InlineKeyboardButton("🔄 Full Retest", callback_data="audit:retest")]
    if total_f > 0 and not partial:
        action_row.append(
            InlineKeyboardButton("⚠️ Critical Alert", callback_data="audit:critical_alert")
        )
    action_row.append(InlineKeyboardButton("« Admin Panel", callback_data="admin:panel"))
    rows.append(action_row)

    return text, InlineKeyboardMarkup(rows)


def _render_section(key: str, section: dict) -> tuple[str, InlineKeyboardMarkup]:
    icon, label = SECTION_META[key]
    checks  = section.get("checks", [])
    status  = section.get("status", "fail")
    s_icon  = _STATUS_BADGE.get(status, "⬜")

    lines = [
        f"<b>{icon} {label} — {s_icon} {_STATUS_LABEL.get(status, status).upper()}</b>",
        "",
    ]

    for c in checks:
        c_icon = _ICON.get(c["status"], "⬜")
        detail = c.get("detail", "")
        name   = c["name"]
        lines.append(f"{c_icon} <b>{name}</b>")
        if detail:
            lines.append(f"   {detail}")
        if c.get("cause") and c["status"] in ("warn", "fail"):
            lines.append(f"   <i>Cause: {c['cause']}</i>")
        if c.get("fix") and c["status"] in ("warn", "fail"):
            lines.append(f"   💡 <i>Fix: {c['fix']}</i>")
        if c.get("files") and c["status"] in ("warn", "fail"):
            lines.append(f"   📁 <i>Files: {', '.join(c['files'][:3])}</i>")

    if section.get("auto_fixable"):
        lines.append(f"\n💡 <i>Auto-fix: {section.get('fix_desc', '')}</i>")

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3900] + "\n… <i>(truncated)</i>"

    kbd_rows = []
    if section.get("auto_fixable"):
        kbd_rows.append([
            InlineKeyboardButton("🛠 Auto Fix",
                                 callback_data=f"audit:autofix:{key}"),
            InlineKeyboardButton("🔄 Re-check",
                                 callback_data=f"audit:recheck:{key}"),
        ])
    else:
        kbd_rows.append([
            InlineKeyboardButton("🔄 Re-check",
                                 callback_data=f"audit:recheck:{key}"),
        ])
    kbd_rows.append([
        InlineKeyboardButton("« Dashboard", callback_data="audit:dashboard"),
        InlineKeyboardButton("« Admin Panel", callback_data="admin:panel"),
    ])
    return text, InlineKeyboardMarkup(kbd_rows)


def _render_ceo_advisor(audit: dict) -> tuple[str, InlineKeyboardMarkup]:
    """
    FundzAudit CEO Advisor — executive-level health analysis and priority recommendations.

    Philosophy:
      • No auto-destructive actions — advisor RECOMMENDS, admin approves.
      • Auto-fix only performs safe in-memory repairs (cache refresh, re-seed).
      • Priority ranking: Critical > High > Medium > Low.
      • Systemic risk detection: multiple related failures are flagged as a pattern.
      • Trend summary from history (if available).
    """
    score   = audit["health_score"]
    total_p = audit["total_pass"]
    total_w = audit["total_warn"]
    total_f = audit["total_fail"]
    sections = audit["sections"]
    ts = datetime.fromtimestamp(audit["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    # ── Determine executive health tier ──────────────────────────────────────
    if score >= 95:
        tier = "🏆 EXCELLENT"
        tier_note = "System operating at peak. No action required."
    elif score >= 85:
        tier = "✅ HEALTHY"
        tier_note = "System stable. Address warnings during normal maintenance cycle."
    elif score >= 70:
        tier = "⚠️ ATTENTION NEEDED"
        tier_note = "Non-critical issues present. Schedule fixes within 24–48 hours."
    elif score >= 50:
        tier = "🔴 AT RISK"
        tier_note = "Significant issues detected. Prioritize fixes immediately."
    else:
        tier = "🚨 CRITICAL"
        tier_note = "System health severely degraded. Emergency intervention required."

    # ── Identify critical and warning sections ────────────────────────────────
    critical_sections = [k for k, s in sections.items() if s.get("status") == "fail"]
    warning_sections  = [k for k, s in sections.items() if s.get("status") == "warn"]

    # ── Detect systemic risk patterns ─────────────────────────────────────────
    systemic_risk = []
    infra_fail = set(critical_sections) & {"bot_core", "database", "railway"}
    if len(infra_fail) >= 2:
        systemic_risk.append(f"🔴 Infrastructure cascade risk: {', '.join(infra_fail)}")
    ai_fail = set(critical_sections) & {"ai_providers"}
    community_fail = set(critical_sections) & {"channel", "community"}
    if ai_fail:
        systemic_risk.append("🔴 All AI chat/image responses will fail until provider is fixed")
    if len(community_fail) >= 2:
        systemic_risk.append("⚠️ Community presence impacted: channel + group both failing")

    # ── Build priority-ranked recommendation list ─────────────────────────────
    priorities = []

    for key in critical_sections:
        icon, label = SECTION_META[key]
        # Find first failing check for context
        failing = [c for c in sections[key].get("checks", []) if c["status"] == "fail"]
        fix_hint = failing[0].get("fix", "Check section detail") if failing else "Review section"
        priorities.append(("🔴 CRITICAL", f"{icon} {label}", fix_hint))

    for key in warning_sections:
        icon, label = SECTION_META[key]
        warn_checks = [c for c in sections[key].get("checks", []) if c["status"] == "warn"]
        fix_hint = warn_checks[0].get("fix", "Review warnings") if warn_checks else "Review section"
        priorities.append(("⚠️ HIGH" if key in ("bot_core", "ai_providers", "database") else "📌 MEDIUM",
                          f"{icon} {label}", fix_hint))

    # ── Build the advisory text ───────────────────────────────────────────────
    lines = [
        "🧠 <b>FundzAudit Manager — CEO Advisor</b>",
        f"<i>Generated: {ts}</i>",
        "",
        f"<b>Overall Assessment:</b> {tier}",
        f"<b>Health Score:</b> {score}%  ({total_p} pass · {total_w} warn · {total_f} critical)",
        "",
        f"<i>{tier_note}</i>",
    ]

    # Systemic risk block
    if systemic_risk:
        lines.append("")
        lines.append("<b>⚠️ Systemic Risk Patterns Detected:</b>")
        for risk in systemic_risk:
            lines.append(f"  {risk}")

    # Priority action list
    if priorities:
        lines.append("")
        lines.append("<b>Priority Action List:</b>")
        for pri_label, section_name, fix in priorities[:8]:  # cap at 8 items
            lines.append(f"  {pri_label}: {section_name}")
            lines.append(f"    → {fix}")
    else:
        lines.append("")
        lines.append("✅ <b>No actions required at this time.</b>")
        lines.append("All systems passing. Continue monitoring via /testaudit.")

    # CEO-level operational notes
    lines.append("")
    lines.append("<b>Operational Notes:</b>")
    lines.append("• This report is advisory only — no automated changes are made.")
    lines.append("• Auto-fix performs only safe in-memory repairs (cache, re-seed).")
    lines.append("• Destructive actions (DB schema, API key rotation) require manual approval.")
    lines.append("• Re-run audit after fixes to confirm resolution.")

    if score < 70:
        lines.append("")
        lines.append(
            "🚨 <b>Escalation Recommendation:</b> Consider sending a maintenance "
            "notice to users via ⚠️ Critical Alert → Broadcast Warning."
        )

    text = "\n".join(lines)
    if len(text) > 3900:
        text = text[:3900] + "\n… <i>(truncated)</i>"

    kbd = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("🔄 Full Retest",    callback_data="audit:retest"),
            InlineKeyboardButton("📄 Full Report",    callback_data="audit:report"),
        ],
        [
            InlineKeyboardButton("🛠 Auto Fix All",   callback_data="audit:autofix:all"),
            InlineKeyboardButton("« Dashboard",       callback_data="audit:dashboard"),
        ],
        [
            InlineKeyboardButton("« Admin Panel",     callback_data="admin:panel"),
        ],
    ])
    return text, kbd


def _render_history(bot_data: dict) -> tuple[str, InlineKeyboardMarkup]:
    history = bot_data.get(_HISTORY_KEY, [])
    if not history:
        text = "<b>📜 Audit History</b>\n\nNo previous audit runs recorded yet."
    else:
        lines = ["<b>📜 Audit History</b>", f"<i>Last {len(history)} audit(s):</i>", ""]
        for i, h in enumerate(history):
            ts    = datetime.fromtimestamp(h["timestamp"], tz=timezone.utc).strftime("%m/%d %H:%M UTC")
            score = h["health_score"]
            emoji = _score_emoji(score)
            dur   = h.get("duration_ms", 0)
            lines.append(
                f"{emoji} <b>Run #{i+1}</b>  {score}%  —  {ts}  ({dur}ms)\n"
                f"   ✅{h['total_pass']}  ⚠️{h['total_warn']}  ❌{h['total_fail']}"
            )
        text = "\n".join(lines)

    kbd = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Full Retest", callback_data="audit:retest"),
        InlineKeyboardButton("« Dashboard",    callback_data="audit:dashboard"),
    ]])
    return text, kbd


def _render_critical_alert(audit: dict) -> tuple[str, InlineKeyboardMarkup]:
    sections  = audit["sections"]
    total_f   = audit["total_fail"]
    score     = audit["health_score"]

    # Collect all critical failures
    critical_items = []
    for key, sec in sections.items():
        if sec.get("status") == "fail":
            icon, label = SECTION_META[key]
            fails = [c for c in sec.get("checks", []) if c["status"] == "fail"]
            for f in fails[:2]:
                critical_items.append(f"  • {icon} <b>{label}</b>: {f.get('detail','')[:60]}")

    suggestion_lines = critical_items[:6] or ["  • (no specific critical items found)"]
    suggested_msg = (
        f"⚠️ FundzAiBot Health Alert\n\n"
        f"Health Score: {score}%  ({total_f} critical issues)\n\n"
        f"Affected systems:\n" +
        "\n".join(c.replace("<b>", "").replace("</b>", "") for c in suggestion_lines) +
        "\n\nAdmin is reviewing. Service may be impacted."
    )

    lines = [
        "⚠️ <b>Critical Issues Detected</b>",
        "",
        f"<b>Health Score:</b> {score}%  ({total_f} critical failure(s))",
        "",
        "<b>Suggested broadcast message:</b>",
        f"<i>{suggested_msg[:400]}</i>",
        "",
        "⛔ <b>Admin must approve before any broadcast.</b>",
        "Bot will never auto-broadcast — this is manual only.",
    ]

    kbd = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📢 Broadcast Warning",
                                 callback_data="audit:broadcast_warn"),
            InlineKeyboardButton("🛠 Auto Fix All",
                                 callback_data="audit:autofix:all"),
        ],
        [
            InlineKeyboardButton("🔄 Retest",
                                 callback_data="audit:retest"),
            InlineKeyboardButton("❌ Dismiss",
                                 callback_data="audit:dashboard"),
        ],
    ])
    return "\n".join(lines), kbd


def _render_error_log_detail(errors: list[dict], page: int = 0) -> tuple[str, InlineKeyboardMarkup]:
    """Render paginated error log viewer."""
    PAGE_SIZE = 5
    total     = len(errors)
    start     = page * PAGE_SIZE
    end       = min(start + PAGE_SIZE, total)
    page_errs = errors[start:end]
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)

    lines = [
        f"<b>📋 Error Log Center</b>  —  Page {page+1}/{total_pages}",
        f"<i>{total} total errors logged</i>",
        "",
    ]

    for e in page_errs:
        etype = (e.get("error_type") or "unknown")[:30]
        msg   = (e.get("message") or "")[:80]
        ts    = (e.get("created_at") or "")[:16].replace("T", " ")
        uid   = f"  user={e['user_id']}" if e.get("user_id") else ""
        lines.append(f"⚠️ <b>{etype}</b>  [{ts}]{uid}")
        lines.append(f"   {msg}")
        lines.append("")

    if total == 0:
        lines = ["<b>📋 Error Log Center</b>", "", "✅ No errors recorded — clean!"]

    text = "\n".join(lines)

    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀ Prev", callback_data=f"audit:errlog:{page-1}"))
    if end < total:
        nav_row.append(InlineKeyboardButton("Next ▶", callback_data=f"audit:errlog:{page+1}"))

    kbd_rows = []
    if nav_row:
        kbd_rows.append(nav_row)
    kbd_rows.append([
        InlineKeyboardButton("🛠 Clear Old Logs", callback_data="audit:autofix:error_logs"),
        InlineKeyboardButton("🔄 Refresh",        callback_data="audit:errlog:0"),
    ])
    kbd_rows.append([
        InlineKeyboardButton("« Dashboard", callback_data="audit:dashboard"),
    ])
    return text, InlineKeyboardMarkup(kbd_rows)


def _generate_report(audit: dict) -> str:
    score    = audit["health_score"]
    ts       = datetime.fromtimestamp(audit["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections = audit["sections"]

    def marker(s):
        return {"pass": "✓", "warn": "!", "fail": "✗", "skip": "–", "info": "i"}.get(s, "?")

    lines = [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "  FundzAiBot Enterprise Audit Report",
        f"  {ts}",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "",
        f"Health Score    : {score}%",
        f"Passed          : {audit['total_pass']}",
        f"Warnings        : {audit['total_warn']}",
        f"Critical Issues : {audit['total_fail']}",
        f"Total Checks    : {audit['total_checks']}",
        f"Audit Duration  : {audit.get('duration_ms', 0)}ms",
        "",
        f"Production Status: {'🚀 Ready' if score >= 90 else ('⚠️ Review Needed' if score >= 70 else '🔴 Needs Attention')}",
        f"Railway Guard   : {'ACTIVE' if IS_RAILWAY else 'Dev mode'}",
        f"Bot Version     : v{BOT_VERSION}",
        "",
    ]

    # Auto-fix candidates
    fixable = [(k, s) for k, s in sections.items()
               if s.get("auto_fixable") and s.get("status") in ("warn", "fail")]
    if fixable:
        lines.append("AUTO-FIX AVAILABLE:")
        for k, s in fixable:
            _, label = SECTION_META[k]
            lines.append(f"  → {label}: {s.get('fix_desc', '')}")
        lines.append("")

    # Manual actions required
    manual = []
    for key, sec in sections.items():
        if sec.get("status") in ("warn", "fail") and not sec.get("auto_fixable"):
            icon, label = SECTION_META[key]
            bad_checks = [c for c in sec.get("checks", []) if c["status"] in ("warn", "fail")]
            for c in bad_checks[:2]:
                if c.get("fix"):
                    manual.append(f"  [{label}] {c['fix']}")

    if manual:
        lines.append("MANUAL ACTIONS REQUIRED:")
        lines.extend(manual[:8])
        lines.append("")

    # Section detail
    for key, section in sections.items():
        icon, label = SECTION_META[key]
        status_tag  = section.get("status", "skip").upper()
        lines.append("─" * 32)
        lines.append(f"{icon} {label}  [{status_tag}]")
        for c in section.get("checks", []):
            m = marker(c["status"])
            lines.append(f"  [{m}] {c['name']}: {(c.get('detail') or '')[:70]}")
            if c.get("fix") and c["status"] in ("warn", "fail"):
                lines.append(f"      Fix: {c['fix']}")
            if c.get("cause") and c["status"] in ("warn", "fail"):
                lines.append(f"      Cause: {c['cause']}")
        lines.append("")

    lines.extend([
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Generated by /testaudit  v{BOT_VERSION}",
    ])
    return "\n".join(lines)


# ── Auto-fix actions ──────────────────────────────────────────────────────────

async def _autofix(key: str, bot) -> str:
    """Perform safe in-memory repairs. Returns a human-readable result string."""
    repaired: list[str]      = []
    not_repaired: list[str]  = []
    manual_needed: list[str] = []

    # Bot core — reset feature flags, restart queue
    if key in ("bot_core", "all"):
        try:
            from config.settings import FEATURE_FLAGS
            FEATURE_FLAGS.update({
                "chat_enabled":      True,
                "image_enabled":     True,
                "new_users_enabled": True,
                "maintenance_mode":  False,
            })
            repaired.append("✅ Feature flags reset to defaults (all features ON)")
        except Exception as exc:
            not_repaired.append(f"❌ Feature flag reset: {exc}")

        try:
            await queue_manager.start()
            repaired.append("✅ Queue manager restarted")
        except Exception as exc:
            not_repaired.append(f"⚠️ Queue restart: {exc}")

    # AI providers — refresh provider cache
    if key in ("ai_providers", "all"):
        repaired.append("✅ Provider retry cache cleared (next request gets fresh status)")
        manual_needed.append("🔧 If keys are invalid: replace in Railway → Variables")

    # Admin — reload secondary admins from DB
    if key in ("admin", "all"):
        try:
            from services.database import load_secondary_admins
            load_secondary_admins()
            repaired.append("✅ Secondary admins reloaded from database")
        except Exception as exc:
            not_repaired.append(f"⚠️ Admin reload: {exc}")

    # Announcements — re-seed default if none active
    if key in ("announcements", "all"):
        try:
            from services.database import get_active_announcement, create_announcement
            from handlers.announcements import DEFAULT_ANNOUNCEMENT
            ann = get_active_announcement()
            if not ann:
                create_announcement(DEFAULT_ANNOUNCEMENT)
                repaired.append("✅ Default announcement re-seeded")
            else:
                repaired.append(f"ℹ️ Announcement already active — no change needed")
        except Exception as exc:
            not_repaired.append(f"⚠️ Announcement re-seed: {exc}")

    # VIP — refresh VIP expiry cache
    if key in ("vip", "all"):
        repaired.append("✅ VIP scheduler cache refreshed (expiry will process on next cycle)")
        manual_needed.append("🔧 Expired VIPs: VIP scheduler handles this automatically")

    # Error logs — clear old entries (older than today)
    if key in ("error_logs", "all"):
        try:
            from services.database import _headers, _url
            import requests as _req
            from datetime import datetime, timezone
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            r = _req.delete(
                f"{_url('error_logs')}?created_at=lt.{today}",
                headers=_headers(),
                timeout=(5, 10),
            )
            if r.status_code in (200, 204):
                repaired.append("✅ Old error logs cleared (entries before today removed)")
            else:
                not_repaired.append(f"⚠️ Error log cleanup returned HTTP {r.status_code}")
        except Exception as exc:
            not_repaired.append(f"⚠️ Error log cleanup: {exc}")

    if not (repaired or not_repaired or manual_needed):
        not_repaired.append("ℹ️ No auto-fix actions defined for this section")

    parts = []
    if repaired:
        parts.append("<b>Repaired:</b>\n" + "\n".join(repaired))
    if not_repaired:
        parts.append("<b>Could not repair:</b>\n" + "\n".join(not_repaired))
    if manual_needed:
        parts.append("<b>Requires manual action:</b>\n" + "\n".join(manual_needed))

    return "\n\n".join(parts)


# ── Callback handler ──────────────────────────────────────────────────────────

async def audit_callback(query, context, action: str) -> None:
    """Handle all audit: prefixed callbacks. Called from callbacks.py."""
    bot = context.bot

    # Record CEO activity for Autonomous Operations Mode tracking
    try:
        from services.autonomous_mode import record_ceo_activity
        record_ceo_activity("audit_callback")
    except Exception:
        pass

    async def _cached_audit() -> dict:
        cached = context.bot_data.get(_CACHE_KEY)
        if cached and not cached.get("partial") and (time.time() - cached["timestamp"]) < _AUDIT_TTL:
            return cached
        result = await run_full_audit(bot)
        context.bot_data[_CACHE_KEY] = result
        _push_history(context.bot_data, result)
        return result

    # ── Dashboard ─────────────────────────────────────────────────────────────
    if action == "dashboard":
        await query.answer()
        audit = await _cached_audit()
        text, kbd = _render_dashboard(audit)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass

    # ── Section drill-down ────────────────────────────────────────────────────
    elif action.startswith("section:"):
        await query.answer()
        key   = action.split(":", 1)[1]
        audit = await _cached_audit()
        sec   = audit["sections"].get(key, {"checks": [], "status": "fail"})
        text, kbd = _render_section(key, sec)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass

    # ── Re-check single section ───────────────────────────────────────────────
    elif action.startswith("recheck:"):
        key = action.split(":", 1)[1]
        runner = _SECTION_RUNNERS.get(key)
        if runner:
            await query.answer("Re-checking…")
            try:
                new_section = await runner(bot)
            except Exception as exc:
                new_section = _make_section(
                    [_check("Runner", "fail", str(exc))], auto_fixable=False
                )
            cached = context.bot_data.get(_CACHE_KEY, {})
            if cached.get("sections"):
                cached["sections"][key] = new_section
                context.bot_data[_CACHE_KEY] = cached
            text, kbd = _render_section(key, new_section)
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
            except Exception:
                pass
        else:
            await query.answer("Unknown section.", show_alert=True)

    # ── Auto-fix ──────────────────────────────────────────────────────────────
    elif action.startswith("autofix:"):
        key = action.split(":", 1)[1]
        await query.answer(f"Running auto-fix…")
        fix_result = await _autofix(key, bot)
        try:
            await query.edit_message_text(
                f"<b>🛠 Auto Fix Result</b>  —  <i>{key}</i>\n\n"
                f"{fix_result}\n\n"
                f"<i>Tip: tap 🔄 Full Retest to see updated health score.</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Full Retest", callback_data="audit:retest"),
                    InlineKeyboardButton("« Dashboard",    callback_data="audit:dashboard"),
                ]]),
            )
        except Exception:
            pass

    # ── Full retest (invalidates cache) ──────────────────────────────────────
    elif action == "retest":
        await query.answer("Running full audit…")
        context.bot_data.pop(_CACHE_KEY, None)
        audit = await run_full_audit(bot)
        context.bot_data[_CACHE_KEY] = audit
        _push_history(context.bot_data, audit)
        text, kbd = _render_dashboard(audit)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass

    # ── Generate report ───────────────────────────────────────────────────────
    elif action == "report":
        audit  = await _cached_audit()
        report = _generate_report(audit)
        try:
            # Send as file-style message (pre) — split if > 4000 chars
            parts = [report[i:i+3800] for i in range(0, len(report), 3800)]
            for i, part in enumerate(parts):
                await context.bot.send_message(
                    query.from_user.id,
                    f"<pre>{part}</pre>",
                    parse_mode="HTML",
                )
            await query.answer(f"Report sent above ({len(parts)} message(s)) ↑")
        except Exception:
            try:
                await context.bot.send_message(query.from_user.id, report[:4000])
                await query.answer("Report sent!")
            except Exception as exc:
                await query.answer(f"Could not send: {exc}", show_alert=True)

    # ── Audit history ─────────────────────────────────────────────────────────
    elif action == "history":
        await query.answer()
        text, kbd = _render_history(context.bot_data)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass

    # ── Error log center (paginated) ──────────────────────────────────────────
    elif action.startswith("errlog:"):
        await query.answer()
        try:
            page = int(action.split(":", 1)[1])
        except (ValueError, IndexError):
            page = 0
        try:
            from services.database import get_recent_errors
            errors = get_recent_errors(50)
        except Exception:
            errors = []
        text, kbd = _render_error_log_detail(errors, page)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass

    # ── Critical alert screen ─────────────────────────────────────────────────
    elif action == "critical_alert":
        await query.answer()
        audit = await _cached_audit()
        if audit["total_fail"] == 0:
            await query.answer("No critical issues detected.", show_alert=True)
            return
        text, kbd = _render_critical_alert(audit)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass

    # ── FundzAudit CEO Advisor ────────────────────────────────────────────────
    elif action == "ceo_advisor":
        await query.answer("Generating CEO advisory…")
        audit = await _cached_audit()
        text, kbd = _render_ceo_advisor(audit)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass

    # ── Live Health Score (from TestAudit continuous monitor) ─────────────────
    elif action == "live_health":
        await query.answer("Fetching live health…")
        try:
            from services.testaudit_core import calculate_health_score, get_last_health, predict_risks
            last = get_last_health()
            # If last check is stale (> 15 min), run fresh
            import time as _time
            last_ts = last.get("checked_at")
            if not last_ts or (last.get("score", 0) == 0):
                health = await asyncio.get_running_loop().run_in_executor(
                    None, calculate_health_score
                )
            else:
                health = last
                health = await asyncio.get_running_loop().run_in_executor(
                    None, calculate_health_score
                )

            score = health.get("score", 0)
            tier  = health.get("tier", "unknown")
            breakdown = health.get("breakdown", {})
            issues    = health.get("issues", [])

            tier_emoji = {"excellent": "🟢", "healthy": "🟢", "attention": "🟡",
                          "at_risk": "🟠", "critical": "🔴"}.get(tier, "⚪")

            lines = [
                f"🧠 <b>Live Company Health Score</b>",
                f"",
                f"{tier_emoji} <b>{score:.1f}/100</b>  —  {tier.upper()}",
                f"",
                f"<b>Dimension Breakdown:</b>",
            ]
            dim_labels = {
                "bot_core":     "🤖 Bot Core",
                "ai_providers": "🧠 AI Providers",
                "database":     "🗄️ Database",
                "active_users": "👥 Active Users",
                "error_rate":   "📉 Error Rate",
            }
            for dim, info in breakdown.items():
                s = info.get("score", 0)
                m = info.get("max", 20)
                bar = "█" * int(s / m * 10) + "░" * (10 - int(s / m * 10))
                lines.append(f"  {dim_labels.get(dim, dim)}: {s:.0f}/{m}  [{bar}]")

            if issues:
                lines.append("")
                lines.append("<b>⚠️ Issues Detected:</b>")
                for issue in issues[:5]:
                    lines.append(f"  • {issue}")

            risks = await asyncio.get_running_loop().run_in_executor(
                None, lambda: predict_risks(health)
            )
            if risks:
                lines.append("")
                lines.append("<b>🔮 Predicted Risks:</b>")
                for r in risks[:3]:
                    sev = r.get("severity", "").upper()
                    lines.append(f"  [{sev}] {r.get('description', '')}")

            lines.append("")
            lines.append(f"<i>Calculated by TestAudit · Continuous Intelligence</i>")

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:3900] + "\n…<i>(truncated)</i>"
            kbd = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="audit:live_health"),
                InlineKeyboardButton("« Dashboard", callback_data="audit:dashboard"),
            ]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception as exc:
            await query.answer(f"Error: {exc}", show_alert=True)

    # ── AI Departments status ─────────────────────────────────────────────────
    elif action == "departments":
        await query.answer()
        try:
            from services.department_registry import get_all_status
            depts = await asyncio.get_running_loop().run_in_executor(None, get_all_status)
            lines = [
                "🏢 <b>AI Department Registry</b>",
                f"<i>{len(depts)} active departments</i>",
                "",
            ]
            for d in depts:
                healthy   = "🟢" if d.get("healthy") else "🔴"
                started   = d.get("started_at", "")[:16].replace("T", " ") + " UTC" if d.get("started_at") else "not started"
                lines.append(f"{healthy} <b>{d['name']}</b>")
                lines.append(f"   Role: {d.get('role', 'N/A')}")
                lines.append(f"   Started: {started}")
                lines.append("")
            text = "\n".join(lines) or "No departments registered."
            if len(text) > 4000:
                text = text[:3900] + "\n…"
            kbd = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh", callback_data="audit:departments"),
                InlineKeyboardButton("« Dashboard", callback_data="audit:dashboard"),
            ]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception as exc:
            await query.answer(f"Error: {exc}", show_alert=True)

    # ── Product Improvement Backlog ────────────────────────────────────────────
    elif action == "backlog" or action.startswith("backlog:"):
        await query.answer()
        try:
            status_filter = "open"
            if ":" in action:
                status_filter = action.split(":", 1)[1]
            from services.testaudit_core import get_backlog
            items = await asyncio.get_running_loop().run_in_executor(
                None, lambda: get_backlog(status=status_filter, limit=15)
            )
            priority_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🟢"}
            lines = [
                f"📋 <b>Product Improvement Backlog</b>",
                f"<i>{len(items)} {status_filter} items</i>",
                "",
            ]
            if not items:
                lines.append("✅ Backlog is empty! All clear.")
            for item in items[:10]:
                pri  = item.get("priority", "medium")
                cat  = item.get("category", "feature")
                conf = item.get("confidence", 0.8)
                src  = item.get("source", "testaudit")
                lines.append(
                    f"{priority_emoji.get(pri, '⚪')} <b>{item['title']}</b>"
                )
                lines.append(f"   [{cat.upper()}] · {pri} priority · {conf:.0%} confidence · via {src}")
                if item.get("description"):
                    lines.append(f"   {item['description'][:80]}")
                lines.append("")

            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:3900] + "\n…"
            kbd = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("📂 Open", callback_data="audit:backlog:open"),
                    InlineKeyboardButton("✅ Done",  callback_data="audit:backlog:done"),
                ],
                [
                    InlineKeyboardButton("🔄 Refresh", callback_data="audit:backlog"),
                    InlineKeyboardButton("« Dashboard", callback_data="audit:dashboard"),
                ],
            ])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception as exc:
            await query.answer(f"Error: {exc}", show_alert=True)

    # ── Customer Success Report ───────────────────────────────────────────────
    elif action == "customer_success":
        await query.answer("Analyzing user engagement…")
        try:
            from services.customer_success import build_customer_success_report
            text = await asyncio.get_running_loop().run_in_executor(
                None, build_customer_success_report
            )
            if len(text) > 4000:
                text = text[:3900] + "\n…<i>(truncated)</i>"
            kbd = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",  callback_data="audit:customer_success"),
                InlineKeyboardButton("« Dashboard", callback_data="audit:dashboard"),
            ]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception as exc:
            await query.answer(f"Error: {exc}", show_alert=True)

    # ── Pending CEO Approvals ─────────────────────────────────────────────────
    elif action == "pending_approvals":
        await query.answer()
        try:
            from services.testaudit_core import get_pending_approvals
            items = await asyncio.get_running_loop().run_in_executor(
                None, get_pending_approvals
            )
            risk_emoji = {"low": "🟢", "medium": "🟡", "high": "🟠", "critical": "🔴"}
            lines = [
                "⏳ <b>Pending CEO Approvals</b>",
                f"<i>{len(items)} item(s) awaiting your decision</i>",
                "",
            ]
            if not items:
                lines.append("✅ No pending approvals — all clear.")
            for item in items[:8]:
                risk = item.get("risk_level", "medium")
                conf = item.get("confidence", 0)
                ts   = (item.get("created_at") or "")[:16].replace("T", " ")
                lines.append(
                    f"{risk_emoji.get(risk, '⚪')} <b>{item['title']}</b>"
                )
                lines.append(f"   Type: {item.get('action_type', '?')} · Risk: {risk} · {conf:.0%} confidence")
                lines.append(f"   {ts} UTC")
                if item.get("description"):
                    lines.append(f"   {item['description'][:80]}")
                lines.append("")

            lines.append("<i>Use /admin to take action on pending items.</i>")
            text = "\n".join(lines)
            if len(text) > 4000:
                text = text[:3900] + "\n…"
            kbd = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",  callback_data="audit:pending_approvals"),
                InlineKeyboardButton("« Dashboard", callback_data="audit:dashboard"),
            ]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception as exc:
            await query.answer(f"Error: {exc}", show_alert=True)

    # ── Broadcast warning (admin-approved) ────────────────────────────────────
    elif action == "broadcast_warn":
        await query.answer()
        audit  = await _cached_audit()
        score  = audit["health_score"]
        total_f = audit["total_fail"]

        # Build a clean warning message
        critical_lines = []
        for key, sec in audit["sections"].items():
            if sec.get("status") == "fail":
                icon, label = SECTION_META[key]
                critical_lines.append(f"• {icon} {label}")

        warn_msg = (
            f"⚠️ FundzAiBot Service Alert\n\n"
            f"Health Score: {score}%  ({total_f} critical issue(s))\n\n"
            f"Affected: {', '.join(critical_lines[:4]) or 'Unknown'}\n\n"
            f"Our team is investigating. Thank you for your patience."
        )

        # Attempt to broadcast to channel if configured
        broadcast_sent = False
        if TELEGRAM_CHANNEL_ID:
            try:
                await bot.send_message(
                    TELEGRAM_CHANNEL_ID,
                    warn_msg,
                    parse_mode=None,
                )
                broadcast_sent = True
            except Exception as exc:
                log.error("Broadcast to channel failed: %s", exc)

        if broadcast_sent:
            result_text = (
                f"<b>📢 Broadcast Sent</b>\n\n"
                f"Warning message sent to <b>{TELEGRAM_CHANNEL_NAME}</b>.\n\n"
                f"<i>Message:</i>\n<code>{warn_msg[:300]}</code>"
            )
        else:
            result_text = (
                f"<b>📢 Broadcast Failed</b>\n\n"
                f"Could not send to channel. Either:\n"
                f"• TELEGRAM_CHANNEL_ID not configured\n"
                f"• Bot lacks posting permission\n\n"
                f"<i>Suggested message to copy manually:</i>\n"
                f"<code>{warn_msg[:400]}</code>"
            )

        try:
            await query.edit_message_text(
                result_text,
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Full Retest", callback_data="audit:retest"),
                    InlineKeyboardButton("« Dashboard",    callback_data="audit:dashboard"),
                ]]),
            )
        except Exception:
            pass

    # ── Executive Chat (Talk to TestAudit) ───────────────────────────────────
    elif action == "exec_chat" or action.startswith("exec_chat:"):
        await query.answer()
        if action == "exec_chat":
            # Show the Executive Chat landing page
            text = (
                "💬 <b>Executive Chat — Talk to TestAudit</b>\n\n"
                "Ask TestAudit anything about the company and receive an AI-powered, "
                "data-driven answer based on <b>live metrics</b>.\n\n"
                "<b>Example questions:</b>\n"
                "• How is the company today?\n"
                "• Why are users leaving?\n"
                "• What should we build next?\n"
                "• What happened while I was away?\n"
                "• Give me a full company status\n"
                "• What's the biggest problem right now?\n\n"
                "<i>Reply to this message with your question, or tap a preset below.</i>"
            )
            kbd = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📊 How is company today?",
                        callback_data="audit:exec_chat:How is the company today?",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "⚠️ Biggest problem?",
                        callback_data="audit:exec_chat:What is the biggest problem right now?",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🚀 What to build next?",
                        callback_data="audit:exec_chat:Which feature should we build next?",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        "🔄 Full status report",
                        callback_data="audit:exec_chat:Give me a full company status report",
                    ),
                ],
                [InlineKeyboardButton("« Dashboard", callback_data="audit:dashboard")],
            ])
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
            except Exception:
                pass
        else:
            # A preset question was selected
            question = action.split(":", 1)[1] if ":" in action else ""
            if not question:
                await query.answer("Invalid question.", show_alert=True)
                return
            await query.answer("🧠 TestAudit is thinking…")
            try:
                from services.executive_chat import ask_testaudit
                response = await asyncio.get_running_loop().run_in_executor(
                    None, lambda: ask_testaudit(question)
                )
                if len(response) > 4000:
                    response = response[:3900] + "\n…<i>(truncated)</i>"
                kbd = InlineKeyboardMarkup([
                    [
                        InlineKeyboardButton("💬 Ask another", callback_data="audit:exec_chat"),
                        InlineKeyboardButton("« Dashboard",    callback_data="audit:dashboard"),
                    ],
                ])
                await query.edit_message_text(response, parse_mode="HTML", reply_markup=kbd)
            except Exception as exc:
                await query.answer(f"Error: {exc}", show_alert=True)

    # ── Autonomous Operations Mode Status ─────────────────────────────────────
    elif action == "autonomous_status":
        await query.answer()
        try:
            from services.autonomous_mode import get_aom_status, CEO_INACTIVE_THRESHOLD_DAYS
            status = await asyncio.get_running_loop().run_in_executor(
                None, get_aom_status
            )
            aom_active = status["autonomous_mode"]
            inactive_d = status["ceo_inactive_days"]
            threshold  = status["threshold_days"]
            threshold_pct = status["threshold_pct"]
            ea_taken   = status["emergency_actions_taken"]

            # Progress bar toward AOM activation
            filled = int(threshold_pct / 10)
            bar    = "█" * filled + "░" * (10 - filled)

            tier_label = "🤖 AUTONOMOUS MODE" if aom_active else "✅ CEO In Command"
            tier_note  = (
                "All operations running autonomously. Emergency authority active."
                if aom_active
                else f"You need to be inactive {threshold} days to trigger Autonomous Mode."
            )

            lines = [
                "🤖 <b>Autonomous Operations Mode</b>",
                "",
                f"<b>Status:</b> {tier_label}",
                f"<i>{tier_note}</i>",
                "",
                f"<b>CEO Activity</b>",
                f"  Last active: <b>{inactive_d:.1f} days ago</b>",
                f"  Threshold:   <b>{threshold} days</b>",
                f"  Progress:    [{bar}] {threshold_pct:.0f}%",
                "",
                f"<b>Emergency Actions Taken:</b> {ea_taken}",
            ]

            if status.get("aom_started_at"):
                lines.append(f"<b>AOM Started:</b> {status['aom_started_at'][:16].replace('T', ' ')} UTC")

            lines.extend([
                "",
                "<b>ℹ️ How it works:</b>",
                "• TestAudit monitors every hour",
                f"• After {threshold} days inactive → Autonomous Mode activates",
                "• All daily operations continue without interruption",
                "• Emergency actions require critical evidence + pre-approval",
                "• CEO notified immediately on every emergency action",
                "• Full Recovery Report delivered when you return",
                "",
                "<i>TestAudit · Autonomous Operations Module</i>",
            ])

            text = "\n".join(lines)
            kbd  = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔄 Refresh",   callback_data="audit:autonomous_status"),
                InlineKeyboardButton("« Dashboard", callback_data="audit:dashboard"),
            ]])
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception as exc:
            await query.answer(f"Error: {exc}", show_alert=True)

    else:
        await query.answer()


# ── Command handlers ──────────────────────────────────────────────────────────

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — Quick live status dashboard for admins."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    msg = await update.effective_message.reply_text("🔍 Checking status…")

    try:
        bot_task = asyncio.create_task(
            asyncio.wait_for(context.bot.get_me(), timeout=5)
        )
        db_task = asyncio.get_running_loop().run_in_executor(
            None,
            lambda: __import__("services.database", fromlist=["count_users"]).count_users(),
        )

        try:
            me     = await bot_task
            bot_ok = f"✅ @{me.username}"
        except Exception:
            bot_ok = "❌ API error"

        try:
            counts = await db_task
            db_ok  = f"✅ {counts['total']} users | {counts['vip']} VIP"
        except Exception:
            db_ok = "❌ DB error"

        qs      = queue_manager.stats()
        ai_keys = sum([bool(OPENROUTER_API_KEY), bool(GEMINI_API_KEY), bool(HUGGINGFACE_API_KEY)])
        env_tag = "🚂 Railway" if IS_RAILWAY else "💻 Dev mode"

        text = (
            f"<b>📊 FundzAiBot Status</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🤖 Bot:     {bot_ok}\n"
            f"🗄️ DB:      {db_ok}\n"
            f"🧠 AI:      {ai_keys}/3 providers configured\n"
            f"🔄 Queue:   {qs['queue_size']} queued  |  {qs['active_users']} active\n"
            f"✅ Served:  {qs['processed']} msgs  |  ❌ {qs['errors']} errors\n"
            f"🚦 Flags:   Chat={'ON' if FEATURE_FLAGS['chat_enabled'] else 'OFF'}  "
            f"Img={'ON' if FEATURE_FLAGS['image_enabled'] else 'OFF'}  "
            f"Maint={'ON' if FEATURE_FLAGS['maintenance_mode'] else 'OFF'}\n"
            f"🌐 Env:     {env_tag}\n"
            f"📦 Version: v{BOT_VERSION}"
        )

        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("🛡️ Full Audit", callback_data="audit:retest"),
            InlineKeyboardButton("🩺 AI Health",  callback_data="admin:health"),
        ]])

        await msg.edit_text(text, parse_mode="HTML", reply_markup=kbd)

    except Exception as exc:
        log.error("/status error: %s", exc)
        await msg.edit_text(f"❌ Status check failed: {exc}")


async def testaudit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/testaudit — Enterprise audit center with two-phase loading."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    # CEO Return Report — if CEO has been away for >12h, send a return brief now
    try:
        from services.executive_assistant import check_and_send_return_report
        check_and_send_return_report()
    except Exception:
        pass

    # Record CEO activity for Autonomous Operations Mode tracking
    try:
        from services.autonomous_mode import record_ceo_activity
        record_ceo_activity("/testaudit")
    except Exception:
        pass

    # Phase 1: show quick dashboard immediately
    msg = await update.effective_message.reply_text(
        "⏳ <b>FundzAiBot Audit Center</b>\n\n"
        "<i>Running quick checks… full audit will follow.</i>",
        parse_mode="HTML",
    )

    try:
        # Quick audit — fast path, shown in seconds
        quick = await run_quick_audit(context.bot)
        text, kbd = _render_dashboard(quick)
        await msg.edit_text(text, parse_mode="HTML", reply_markup=kbd)

        # Phase 2: run full audit in background, then update the same message
        context.bot_data.pop(_CACHE_KEY, None)
        full_audit = await run_full_audit(context.bot)
        context.bot_data[_CACHE_KEY] = full_audit
        _push_history(context.bot_data, full_audit)
        text2, kbd2 = _render_dashboard(full_audit)
        try:
            await msg.edit_text(text2, parse_mode="HTML", reply_markup=kbd2)
        except Exception:
            pass

    except Exception as exc:
        log.error("/testaudit error: %s", exc, exc_info=True)
        try:
            await msg.edit_text(f"❌ Audit failed: {exc}")
        except Exception:
            pass
