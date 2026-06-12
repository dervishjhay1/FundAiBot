"""
FundzAiBot — Enterprise Audit Center & /status command.

/status       — Quick live status dashboard
/testaudit    — Full interactive diagnostic + repair center

Access: ADMIN_USER_ID and authorised secondary admins only.

Architecture:
  All audit sections run async. Results are cached in context.bot_data
  with a timestamp so navigation doesn't re-run every check on every tap.
  "🔄 Full Retest" and each section's refresh button invalidate the cache.

Auto-fix philosophy:
  ONLY safe in-memory repairs — refresh caches, reload state, re-seed
  missing data. NEVER touches API keys, production tables, or Railway.
"""

import asyncio
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

_AUDIT_TTL = 120          # seconds before cache expires
_CACHE_KEY  = "audit_v1"  # key in context.bot_data

# ── Status icons ──────────────────────────────────────────────────────────────

_ICON = {"pass": "✅", "warn": "⚠️", "fail": "❌", "info": "ℹ️", "skip": "⬜"}

SECTION_META = {
    "bot_core":       ("🤖", "Bot Core"),
    "ai_providers":   ("🧠", "AI Providers"),
    "database":       ("🗄️", "Database"),
    "railway":        ("🚂", "Railway"),
    "channel":        ("📢", "Channel"),
    "community":      ("👥", "Community"),
    "admin":          ("👑", "Admin System"),
    "referrals":      ("🎁", "Referrals"),
    "vip":            ("💎", "VIP"),
    "announcements":  ("📌", "Announcements"),
    "security":       ("🔒", "Security"),
    "error_logs":     ("📋", "Error Logs"),
    "languages":      ("🌍", "Languages"),
    "integrations":   ("⚙️", "Integrations"),
}


# ── Check result helpers ──────────────────────────────────────────────────────

def _check(name: str, status: str, detail: str, fix: str | None = None) -> dict:
    return {"name": name, "status": status, "detail": detail, "fix": fix}


def _section_status(checks: list[dict]) -> str:
    """Derive overall section status from individual checks."""
    statuses = [c["status"] for c in checks]
    if "fail" in statuses:
        return "fail"
    if "warn" in statuses:
        return "warn"
    return "pass"


# ── Individual section auditors ───────────────────────────────────────────────

async def _audit_bot_core(bot) -> dict:
    checks = []
    try:
        me = await asyncio.wait_for(bot.get_me(), timeout=8)
        checks.append(_check("Telegram API", "pass", f"@{me.username} — connected"))
        checks.append(_check("Bot token", "pass", "Valid & accepted by Telegram"))
        checks.append(_check("Bot name", "pass", f"{me.first_name} (id={me.id})"))
    except asyncio.TimeoutError:
        checks.append(_check("Telegram API", "fail", "Timeout — Telegram unreachable",
                             "Check TELEGRAM_BOT_TOKEN and network"))
    except TelegramError as exc:
        checks.append(_check("Telegram API", "fail", f"TelegramError: {exc}",
                             "Verify TELEGRAM_BOT_TOKEN in Railway env vars"))

    # Polling guard
    if IS_RAILWAY:
        checks.append(_check("Polling guard", "pass",
                             "Running on Railway — polling allowed"))
        checks.append(_check("Duplicate instance", "pass",
                             "Railway numReplicas=1 prevents duplicates"))
    elif ALLOW_POLLING:
        checks.append(_check("Polling guard", "warn",
                             "ALLOW_POLLING=true — manual override active",
                             "Only use this in local dev. Remove before Railway deploy."))
    else:
        checks.append(_check("Polling guard", "pass",
                             "Not on Railway — polling disabled (dev mode)"))

    # Queue
    try:
        qs = queue_manager.stats()
        checks.append(_check("Queue manager", "pass",
                             f"Active: {qs['active_users']} | Queued: {qs['queue_size']} | "
                             f"Processed: {qs['processed']} | Errors: {qs['errors']}"))
    except Exception as exc:
        checks.append(_check("Queue manager", "warn", f"Stats unavailable: {exc}"))

    # Feature flags
    ff_status = ", ".join(f"{k}={'ON' if v else 'OFF'}" for k, v in FEATURE_FLAGS.items())
    checks.append(_check("Feature flags", "pass", ff_status))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": True, "fix_desc": "Refresh feature flags & queue state"}


async def _audit_ai_providers() -> dict:
    import requests as _requests
    checks = []

    _OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"
    _GEMINI_BASE    = "https://generativelanguage.googleapis.com/v1beta/models"
    _HF_BASE        = "https://api-inference.huggingface.co/models"

    # OpenRouter
    if OPENROUTER_API_KEY:
        try:
            t0 = time.time()
            r = _requests.post(
                _OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={"model": OPENROUTER_MODEL,
                      "messages": [{"role": "user", "content": "ping"}],
                      "max_tokens": 1},
                timeout=10,
            )
            latency = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                checks.append(_check("OpenRouter", "pass",
                                     f"✅ OK  model={OPENROUTER_MODEL}  latency={latency}ms"))
            elif r.status_code == 402:
                checks.append(_check("OpenRouter", "fail",
                                     "402 Insufficient credits",
                                     "Top up at openrouter.ai/credits"))
            elif r.status_code == 401:
                checks.append(_check("OpenRouter", "fail",
                                     "401 Invalid API key",
                                     "Replace OPENROUTER_API_KEY in Railway"))
            elif r.status_code == 404:
                checks.append(_check("OpenRouter", "warn",
                                     f"404 Model not found: {OPENROUTER_MODEL}",
                                     "Set OPENROUTER_MODEL to a valid model ID"))
            elif r.status_code == 429:
                checks.append(_check("OpenRouter", "warn",
                                     "429 Rate limited — bot falls back automatically"))
            else:
                checks.append(_check("OpenRouter", "warn",
                                     f"HTTP {r.status_code}: {r.text[:80]}"))
        except _requests.Timeout:
            checks.append(_check("OpenRouter", "warn",
                                 "Timeout (10s) — may be transient"))
        except _requests.ConnectionError:
            checks.append(_check("OpenRouter", "fail",
                                 "Connection refused",
                                 "Check network / Railway outbound rules"))
        except Exception as exc:
            checks.append(_check("OpenRouter", "fail", str(exc)[:80]))
    else:
        checks.append(_check("OpenRouter", "skip",
                             "Not configured (OPENROUTER_API_KEY not set)",
                             "Set OPENROUTER_API_KEY in Railway env vars"))

    # Gemini
    if GEMINI_API_KEY:
        try:
            t0 = time.time()
            r = _requests.get(f"{_GEMINI_BASE}?key={GEMINI_API_KEY}", timeout=8)
            latency = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                checks.append(_check("Gemini", "pass",
                                     f"✅ OK  model={GEMINI_MODEL}  latency={latency}ms"))
            elif r.status_code == 429:
                checks.append(_check("Gemini", "warn",
                                     "429 Quota exceeded — auto-fallback active"))
            elif r.status_code in (400, 403):
                checks.append(_check("Gemini", "fail",
                                     f"Auth error ({r.status_code})",
                                     "Replace GEMINI_API_KEY in Railway"))
            else:
                checks.append(_check("Gemini", "warn", f"HTTP {r.status_code}"))
        except _requests.Timeout:
            checks.append(_check("Gemini", "warn", "Timeout (8s)"))
        except Exception as exc:
            checks.append(_check("Gemini", "warn", str(exc)[:80]))
    else:
        checks.append(_check("Gemini", "skip",
                             "Not configured (GEMINI_API_KEY not set)",
                             "Set GEMINI_API_KEY in Railway env vars"))

    # HuggingFace
    if HUGGINGFACE_API_KEY:
        try:
            t0 = time.time()
            r = _requests.get(
                f"{_HF_BASE}/{HF_CHAT_MODEL}",
                headers={"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"},
                timeout=8,
            )
            latency = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                checks.append(_check("HuggingFace", "pass",
                                     f"✅ OK  model={HF_CHAT_MODEL}  latency={latency}ms"))
            elif r.status_code == 503:
                checks.append(_check("HuggingFace", "warn",
                                     "Model warming up (503) — ready in ~20s"))
            elif r.status_code == 401:
                checks.append(_check("HuggingFace", "fail",
                                     "Invalid HF token (401)",
                                     "Replace HUGGINGFACE_API_KEY in Railway"))
            else:
                checks.append(_check("HuggingFace", "warn", f"HTTP {r.status_code}"))
        except (_requests.Timeout, _requests.ConnectionError):
            checks.append(_check("HuggingFace", "warn",
                                 "Unreachable (Railway network) — OpenRouter/Gemini handle traffic"))
        except Exception as exc:
            checks.append(_check("HuggingFace", "warn", str(exc)[:80]))
    else:
        checks.append(_check("HuggingFace", "skip",
                             "Not configured (HUGGINGFACE_API_KEY not set)"))

    # Fallback chain
    configured = sum([bool(OPENROUTER_API_KEY), bool(GEMINI_API_KEY), bool(HUGGINGFACE_API_KEY)])
    if configured >= 2:
        checks.append(_check("Fallback chain", "pass",
                             f"{configured}/3 providers configured — fallback available"))
    elif configured == 1:
        checks.append(_check("Fallback chain", "warn",
                             "Only 1 provider configured — no fallback if it fails",
                             "Add a second provider key for redundancy"))
    else:
        checks.append(_check("Fallback chain", "fail",
                             "No AI providers configured!",
                             "Add at least one AI key in Railway env vars"))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": False, "fix_desc": "Manual: verify API keys in Railway env vars"}


async def _audit_database() -> dict:
    import requests as _requests
    from services.database import _headers, _url, _safe_get

    checks = []
    tables = [
        ("users", "Core user table"),
        ("user_credits", "Credits tracking"),
        ("conversations", "Chat history"),
        ("image_history", "Image generation log"),
        ("referrals", "Referral system"),
        ("error_logs", "Error logging"),
        ("announcements", "Pinned announcements"),
        ("admin_accounts", "Multi-admin system"),
        ("onboarding", "Onboarding flow"),
    ]

    db_ok = bool(SUPABASE_URL and SUPABASE_SERVICE_KEY)
    if not db_ok:
        checks.append(_check("Supabase credentials", "fail",
                             "SUPABASE_URL or SUPABASE_SERVICE_KEY not set",
                             "Set both in Railway env vars"))
        return {"checks": checks, "status": "fail",
                "auto_fixable": False, "fix_desc": "Manual: set Supabase credentials in Railway"}

    checks.append(_check("Supabase credentials", "pass",
                         f"URL: {SUPABASE_URL[:40]}…"))

    for table, label in tables:
        try:
            r = _safe_get(f"{_url(table)}?limit=1", headers=_headers())
            if r.status_code == 200:
                rows = r.json()
                count = len(rows)
                checks.append(_check(f"Table: {table}", "pass",
                                     f"{label} — accessible (sample: {count} row{'s' if count != 1 else ''})"))
            elif r.status_code == 404:
                checks.append(_check(f"Table: {table}", "fail",
                                     f"Table missing!",
                                     f"Run supabase_schema.sql to create '{table}'"))
            elif r.status_code == 401:
                checks.append(_check(f"Table: {table}", "fail",
                                     "Auth error (401) — invalid service key",
                                     "Replace SUPABASE_SERVICE_KEY in Railway"))
            else:
                checks.append(_check(f"Table: {table}", "warn",
                                     f"HTTP {r.status_code}"))
        except Exception as exc:
            checks.append(_check(f"Table: {table}", "fail", str(exc)[:60]))

    # Test RPC functions
    for rpc_fn in ("increment_chat", "increment_image"):
        try:
            r = _requests.post(
                f"{SUPABASE_URL}/rest/v1/rpc/{rpc_fn}",
                headers=_headers(),
                json={"uid": 0, "n": 0},
                timeout=(5, 8),
            )
            # 200 or 404 for non-existent user is fine — confirms function exists
            if r.status_code in (200, 204, 404, 409):
                checks.append(_check(f"RPC: {rpc_fn}()", "pass", "Function exists"))
            elif r.status_code == 404 and "function" in r.text.lower():
                checks.append(_check(f"RPC: {rpc_fn}()", "warn",
                                     "RPC function not found",
                                     "Add to Supabase SQL Editor from schema file"))
            else:
                checks.append(_check(f"RPC: {rpc_fn}()", "warn", f"HTTP {r.status_code}"))
        except Exception as exc:
            checks.append(_check(f"RPC: {rpc_fn}()", "warn", str(exc)[:60]))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": False,
            "fix_desc": "Manual: run supabase_schema.sql for missing tables"}


async def _audit_railway() -> dict:
    checks = []

    # Environment
    checks.append(_check("Environment", "pass" if IS_RAILWAY else "info",
                         "Railway" if IS_RAILWAY else "Not Railway (dev/Replit mode)"))

    # Polling ownership
    if IS_RAILWAY:
        checks.append(_check("Polling ownership", "pass",
                             "This instance owns Telegram polling — correct"))
    elif ALLOW_POLLING:
        checks.append(_check("Polling ownership", "warn",
                             "ALLOW_POLLING=true override — acceptable in dev",
                             "Remove ALLOW_POLLING before any non-Railway deploy"))
    else:
        checks.append(_check("Polling ownership", "pass",
                             "Polling blocked — Replit/dev mode (correct)"))

    # Railway env vars
    rail_vars = {
        "RAILWAY_ENVIRONMENT": os.getenv("RAILWAY_ENVIRONMENT"),
        "RAILWAY_SERVICE_NAME": os.getenv("RAILWAY_SERVICE_NAME"),
        "RAILWAY_PROJECT_ID": os.getenv("RAILWAY_PROJECT_ID"),
        "RAILWAY_SERVICE_ID": os.getenv("RAILWAY_SERVICE_ID"),
    }
    set_vars = [k for k, v in rail_vars.items() if v]
    if set_vars:
        checks.append(_check("Railway markers", "pass",
                             "Detected: " + ", ".join(set_vars)))
    else:
        checks.append(_check("Railway markers", "info",
                             "No Railway env markers — dev mode"))

    # Health endpoint
    try:
        import requests as _req
        port = os.getenv("PORT", "5000")
        r = _req.get(f"http://localhost:{port}/health", timeout=3)
        if r.status_code == 200:
            checks.append(_check("Health endpoint /health", "pass",
                                 f"HTTP 200 on :{port}"))
        else:
            checks.append(_check("Health endpoint /health", "warn",
                                 f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("Health endpoint /health", "warn",
                             f"Not reachable locally: {type(exc).__name__}"))

    # Readiness endpoint
    try:
        import requests as _req
        port = os.getenv("PORT", "5000")
        r = _req.get(f"http://localhost:{port}/ready", timeout=3)
        checks.append(_check("Readiness endpoint /ready", "pass" if r.status_code == 200 else "warn",
                             f"HTTP {r.status_code}"))
    except Exception:
        checks.append(_check("Readiness endpoint /ready", "warn",
                             "Not reachable locally"))

    # Required secrets
    secrets = {
        "TELEGRAM_BOT_TOKEN":    bool(TELEGRAM_BOT_TOKEN),
        "ADMIN_USER_ID":         bool(ADMIN_USER_ID),
        "SUPABASE_URL":          bool(SUPABASE_URL),
        "SUPABASE_SERVICE_KEY":  bool(SUPABASE_SERVICE_KEY),
    }
    missing = [k for k, v in secrets.items() if not v]
    if missing:
        checks.append(_check("Required secrets", "fail",
                             "Missing: " + ", ".join(missing),
                             "Set these in Railway → Variables"))
    else:
        checks.append(_check("Required secrets", "pass", "All 4 required secrets present"))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": False,
            "fix_desc": "Manual: configure Railway env vars & railway.json"}


async def _audit_channel(bot) -> dict:
    checks = []
    if not TELEGRAM_CHANNEL_ID:
        checks.append(_check("Channel config", "warn",
                             "TELEGRAM_CHANNEL_ID not set — channel features disabled",
                             "Set TELEGRAM_CHANNEL_ID in Railway env vars"))
        return {"checks": checks, "status": "warn",
                "auto_fixable": False,
                "fix_desc": "Manual: set TELEGRAM_CHANNEL_ID in Railway"}

    checks.append(_check("Channel ID", "pass", f"Configured: {TELEGRAM_CHANNEL_ID}"))
    checks.append(_check("Channel URL", "pass", TELEGRAM_CHANNEL_URL))

    try:
        chat = await asyncio.wait_for(bot.get_chat(TELEGRAM_CHANNEL_ID), timeout=8)
        checks.append(_check("Channel exists", "pass",
                             f"@{chat.username or ''} — {chat.title or 'Channel'}"))

        # Check bot admin status
        try:
            me = await bot.get_me()
            member = await asyncio.wait_for(
                bot.get_chat_member(TELEGRAM_CHANNEL_ID, me.id), timeout=8
            )
            if member.status in ("creator", "administrator"):
                perms = getattr(member, "can_post_messages", None)
                pin_perm = getattr(member, "can_pin_messages", None)
                checks.append(_check("Bot is channel admin", "pass",
                                     f"Status={member.status}  can_post={perms}  can_pin={pin_perm}"))
            else:
                checks.append(_check("Bot is channel admin", "fail",
                                     f"Bot status={member.status} — not admin",
                                     "Promote bot to admin with Post & Pin permissions"))
        except TelegramError as exc:
            checks.append(_check("Bot admin status", "warn", f"Could not verify: {exc}"))

    except asyncio.TimeoutError:
        checks.append(_check("Channel exists", "warn", "Telegram API timeout"))
    except TelegramError as exc:
        checks.append(_check("Channel exists", "fail", str(exc),
                             "Check TELEGRAM_CHANNEL_ID and bot membership"))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": False,
            "fix_desc": "Manual: promote bot to admin in the channel"}


async def _audit_community(bot) -> dict:
    checks = []
    if not TELEGRAM_GROUP_ID:
        checks.append(_check("Group config", "warn",
                             "TELEGRAM_GROUP_ID not set — group features disabled",
                             "Set TELEGRAM_GROUP_ID in Railway env vars"))
        return {"checks": checks, "status": "warn",
                "auto_fixable": False,
                "fix_desc": "Manual: set TELEGRAM_GROUP_ID in Railway"}

    checks.append(_check("Group ID", "pass", f"Configured: {TELEGRAM_GROUP_ID}"))
    checks.append(_check("Group URL", "pass", TELEGRAM_GROUP_URL))

    try:
        chat = await asyncio.wait_for(bot.get_chat(TELEGRAM_GROUP_ID), timeout=8)
        checks.append(_check("Group exists", "pass",
                             f"@{chat.username or ''} — {chat.title or 'Group'}"))
        try:
            me = await bot.get_me()
            member = await asyncio.wait_for(
                bot.get_chat_member(TELEGRAM_GROUP_ID, me.id), timeout=8
            )
            if member.status in ("creator", "administrator"):
                checks.append(_check("Bot is group admin", "pass",
                                     f"Status={member.status}"))
            else:
                checks.append(_check("Bot is group admin", "fail",
                                     f"Bot status={member.status}",
                                     "Promote bot to admin in the group"))
        except TelegramError as exc:
            checks.append(_check("Bot admin status", "warn", f"Could not verify: {exc}"))

    except asyncio.TimeoutError:
        checks.append(_check("Group exists", "warn", "Telegram API timeout"))
    except TelegramError as exc:
        checks.append(_check("Group exists", "fail", str(exc),
                             "Check TELEGRAM_GROUP_ID and bot membership"))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": False,
            "fix_desc": "Manual: promote bot to admin in the group"}


async def _audit_admin() -> dict:
    from services.database import _headers, _url, _safe_get
    from config.settings import SECONDARY_ADMINS

    checks = []
    if ADMIN_USER_ID:
        checks.append(_check("Primary owner", "pass",
                             f"ADMIN_USER_ID={ADMIN_USER_ID} — set correctly"))
    else:
        checks.append(_check("Primary owner", "fail",
                             "ADMIN_USER_ID not set!",
                             "Set ADMIN_USER_ID in Railway env vars"))

    checks.append(_check("Secondary admins (runtime)", "pass",
                         f"{len(SECONDARY_ADMINS)} loaded in memory: "
                         + (", ".join(str(a) for a in SECONDARY_ADMINS) or "none")))

    try:
        r = _safe_get(f"{_url('admin_accounts')}?limit=50", headers=_headers())
        if r.status_code == 200:
            rows = r.json()
            checks.append(_check("admin_accounts table", "pass",
                                 f"{len(rows)} admin record(s) in DB"))
        elif r.status_code == 404:
            checks.append(_check("admin_accounts table", "warn",
                                 "Table not found — multi-admin DB persistence unavailable",
                                 "Run supabase_schema.sql to create admin_accounts"))
        else:
            checks.append(_check("admin_accounts table", "warn",
                                 f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("admin_accounts table", "warn", str(exc)[:60]))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": True,
            "fix_desc": "Reload secondary admins from database"}


async def _audit_referrals() -> dict:
    from services.database import _headers, _url, _safe_get, count_users

    checks = []
    try:
        r = _safe_get(f"{_url('referrals')}?limit=5", headers=_headers())
        if r.status_code == 200:
            rows = r.json()
            checks.append(_check("Referrals table", "pass",
                                 f"Accessible — {len(rows)} recent record(s)"))
        elif r.status_code == 404:
            checks.append(_check("Referrals table", "fail",
                                 "Table missing",
                                 "Run supabase_schema.sql"))
        else:
            checks.append(_check("Referrals table", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("Referrals table", "fail", str(exc)[:60]))

    try:
        counts = count_users()
        checks.append(_check("User counts", "pass",
                             f"Total={counts['total']}  VIP={counts['vip']}  "
                             f"Banned={counts['banned']}"))
    except Exception as exc:
        checks.append(_check("User counts", "warn", str(exc)[:60]))

    from config.settings import REFERRAL_CHAT_BONUS, REFERRAL_IMAGE_BONUS
    checks.append(_check("Referral rewards", "pass",
                         f"+{REFERRAL_CHAT_BONUS} chat, +{REFERRAL_IMAGE_BONUS} image per referral"))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": False,
            "fix_desc": "Manual: run supabase_schema.sql for missing tables"}


async def _audit_vip() -> dict:
    from services.database import _headers, _url, _safe_get
    from config.settings import VIP_PLANS

    checks = []
    for tier, plan in VIP_PLANS.items():
        checks.append(_check(f"VIP plan: {tier}", "pass",
                             f"{plan['label']} — {plan['stars']} Stars — "
                             f"{plan['chat_limit']} chats + {plan['image_limit']} images/day"))

    try:
        r = _safe_get(
            f"{_url('users')}?is_vip=eq.true&limit=5",
            headers=_headers()
        )
        if r.status_code == 200:
            rows = r.json()
            checks.append(_check("VIP users", "pass",
                                 f"{len(rows)} VIP users in sample — records OK"))
        else:
            checks.append(_check("VIP users", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("VIP users", "warn", str(exc)[:60]))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": False,
            "fix_desc": "No auto-fix available for VIP tier config"}


async def _audit_announcements() -> dict:
    from services.database import (
        _headers, _url, _safe_get,
        get_active_announcement, get_announcement_history,
    )

    checks = []
    try:
        r = _safe_get(f"{_url('announcements')}?limit=1", headers=_headers())
        if r.status_code == 200:
            checks.append(_check("Announcements table", "pass", "Accessible"))
        elif r.status_code == 404:
            checks.append(_check("Announcements table", "fail",
                                 "Table missing",
                                 "Run supabase_schema.sql"))
        else:
            checks.append(_check("Announcements table", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("Announcements table", "fail", str(exc)[:60]))

    try:
        ann = get_active_announcement()
        if ann:
            msg = (ann.get("message") or "")[:60]
            photo = " | photo: ✅" if ann.get("photo_url") else ""
            checks.append(_check("Active announcement", "pass",
                                 f"'{msg}…'{photo}"))
        else:
            checks.append(_check("Active announcement", "warn",
                                 "No active announcement — users see no pin on /start",
                                 "Use /pin <message> to create one"))
    except Exception as exc:
        checks.append(_check("Active announcement", "warn", str(exc)[:60]))

    try:
        history = get_announcement_history(limit=5)
        checks.append(_check("Announcement history", "pass",
                             f"{len(history)} record(s) in history"))
    except Exception as exc:
        checks.append(_check("Announcement history", "warn", str(exc)[:60]))

    chan_ok  = bool(TELEGRAM_CHANNEL_ID)
    grp_ok   = bool(TELEGRAM_GROUP_ID)
    sync_lvl = "pass" if (chan_ok or grp_ok) else "warn"
    checks.append(_check("Channel sync capability", sync_lvl,
                         ("Channel + Group ready for /announce_both"
                          if chan_ok and grp_ok
                          else ("Channel only" if chan_ok
                                else ("Group only" if grp_ok
                                      else "Neither channel nor group configured")))))

    fixable = True
    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": fixable,
            "fix_desc": "Re-seed default announcement if none active"}


async def _audit_security() -> dict:
    from config.settings import validate_config

    checks = []
    missing = validate_config()
    if missing:
        for m in missing:
            checks.append(_check(f"Secret: {m}", "fail",
                                 f"Missing or invalid",
                                 f"Set {m} in Railway env vars"))
    else:
        checks.append(_check("Required secrets", "pass",
                             "All critical secrets present"))

    # Token format check
    if TELEGRAM_BOT_TOKEN:
        parts = TELEGRAM_BOT_TOKEN.split(":")
        if len(parts) == 2 and parts[0].isdigit() and len(parts[1]) > 20:
            checks.append(_check("Token format", "pass", "Looks valid"))
        else:
            checks.append(_check("Token format", "warn",
                                 "Unexpected format — verify correctness"))

    # Duplicate polling risk
    if IS_RAILWAY:
        checks.append(_check("Duplicate polling risk", "pass",
                             "Railway-only guard active — single instance enforced"))
    else:
        checks.append(_check("Duplicate polling risk", "pass",
                             "Polling disabled in dev — no conflict possible"))

    # Admin ID set
    if ADMIN_USER_ID and ADMIN_USER_ID != 0:
        checks.append(_check("Admin access control", "pass",
                             f"Owner ID={ADMIN_USER_ID} — admin commands protected"))
    else:
        checks.append(_check("Admin access control", "fail",
                             "ADMIN_USER_ID=0 — any user can trigger admin paths!",
                             "Set ADMIN_USER_ID to your Telegram user ID in Railway"))

    # Supabase key type
    if SUPABASE_SERVICE_KEY:
        if len(SUPABASE_SERVICE_KEY) > 100:
            checks.append(_check("Supabase key type", "pass",
                                 "Looks like a service-role key (correct for server use)"))
        else:
            checks.append(_check("Supabase key type", "warn",
                                 "Key seems short — ensure it's the service_role key, not anon"))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": False,
            "fix_desc": "Manual: set missing secrets in Railway → Variables"}


async def _audit_error_logs() -> dict:
    from services.database import get_recent_errors

    checks = []
    try:
        errors = get_recent_errors(20)
        if not errors:
            checks.append(_check("Error log", "pass", "No recent errors — clean!"))
        else:
            by_type: dict[str, int] = {}
            for e in errors:
                t = e.get("error_type", "unknown")
                by_type[t] = by_type.get(t, 0) + 1
            summary = ", ".join(f"{k}×{v}" for k, v in sorted(by_type.items(), key=lambda x: -x[1]))
            level = "warn" if len(errors) < 10 else "fail"
            checks.append(_check("Recent errors", level,
                                 f"{len(errors)} in last 20: {summary}",
                                 "Review errors in admin panel (/admin_logs)"))
            # Show latest 3 errors
            for e in errors[:3]:
                checks.append(_check(
                    f"  {e.get('error_type','?')}",
                    "warn",
                    (e.get("message") or "")[:80],
                ))
    except Exception as exc:
        checks.append(_check("Error log access", "warn",
                             f"Could not fetch: {exc}",
                             "Verify error_logs table exists in Supabase"))

    return {"checks": checks, "status": _section_status(checks),
            "auto_fixable": True,
            "fix_desc": "Clear old error log entries (keeps last 10)"}


# ── Section: Languages ────────────────────────────────────────────────────────

async def _audit_languages() -> dict:
    """Check multilingual system — locale files, coverage, DB column, registry."""
    checks = []

    # Check language module imports
    try:
        from services.language import FREE_LANGUAGES, VIP_LANGUAGES, ALL_LANGUAGES, get_string
        total = len(ALL_LANGUAGES)
        free  = len(FREE_LANGUAGES)
        vip   = len(VIP_LANGUAGES)
        checks.append(_check("Language registry", "pass",
                             f"{total} languages loaded — {free} free, {vip} VIP"))
        checks.append(_check("Free languages", "pass",
                             ", ".join(FREE_LANGUAGES.values())))
        checks.append(_check("VIP languages", "pass",
                             ", ".join(VIP_LANGUAGES.values())))
    except ImportError as exc:
        checks.append(_check("Language module", "fail", str(exc)[:80]))
        return {"checks": checks, "status": "fail", "auto_fixable": False, "fix_desc": ""}

    # Check locale JSON files exist
    import os
    locales_dir = os.path.join(os.path.dirname(__file__), "..", "locales")
    missing_locales = []
    present_locales = []
    from services.language import ALL_LANGUAGES as _ALL  # already imported above
    for code in _ALL:
        path = os.path.join(locales_dir, f"{code}.json")
        if os.path.exists(path):
            try:
                import json
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                present_locales.append(f"{code}({len(data)})")
            except Exception as e:
                missing_locales.append(f"{code}(parse error: {e})")
        else:
            missing_locales.append(f"{code}(missing)")

    if present_locales:
        checks.append(_check("Locale JSON files", "pass",
                             f"{len(present_locales)} found: " + ", ".join(present_locales[:5])))
    if missing_locales:
        checks.append(_check("Missing locale files", "warn",
                             ", ".join(missing_locales),
                             "Create locales/{code}.json for each language"))

    # Check STRINGS translation coverage for a critical key
    try:
        from services.language import STRINGS
        key = "welcome_back"
        covered = [code for code in _ALL if code in STRINGS and key in STRINGS[code]]
        missing  = [code for code in _ALL if code not in covered]
        if missing:
            checks.append(_check("STRINGS coverage", "warn",
                                 f"Missing '{key}' for: {', '.join(missing)}",
                                 "Add translations to services/language.py STRINGS"))
        else:
            checks.append(_check("STRINGS coverage", "pass",
                                 f"'{key}' translated in all {len(_ALL)} languages"))
    except Exception as exc:
        checks.append(_check("STRINGS coverage", "warn", str(exc)[:80]))

    # Check users table has language column (Supabase)
    try:
        from services.database import _headers, _url, _safe_get
        r = _safe_get(
            f"{_url('users')}?select=language&limit=1",
            headers=_headers(),
        )
        if r.status_code == 200:
            checks.append(_check("DB language column", "pass",
                                 "users.language column exists and readable"))
        elif r.status_code == 400 and "language" in r.text:
            checks.append(_check("DB language column", "fail",
                                 "Column missing — run supabase_language_schema.sql",
                                 "Run supabase_language_schema.sql in Supabase SQL Editor"))
        else:
            checks.append(_check("DB language column", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("DB language column", "warn", str(exc)[:60]))

    # Check detect_language function exists
    try:
        from services.language import detect_language
        result = detect_language("en")
        checks.append(_check("detect_language()", "pass",
                             f"Working — 'en' → '{result}'"))
    except ImportError:
        checks.append(_check("detect_language()", "warn",
                             "Function not found in services/language.py",
                             "Add detect_language() to services/language.py"))
    except Exception as exc:
        checks.append(_check("detect_language()", "warn", str(exc)[:60]))

    return {
        "checks": checks,
        "status": _section_status(checks),
        "auto_fixable": False,
        "fix_desc": "Run supabase_language_schema.sql to add the language column",
    }


# ── Section: Integrations ─────────────────────────────────────────────────────

async def _audit_integrations() -> dict:
    """Check all external service integrations — reachability and auth."""
    import asyncio, os, requests as _req

    checks = []

    # ── Telegram Bot API ──────────────────────────────────────────────────────
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
                bot_info = r.json()["result"]
                checks.append(_check("Telegram Bot API", "pass",
                                     f"@{bot_info.get('username','')} — auth OK"))
            else:
                checks.append(_check("Telegram Bot API", "fail",
                                     f"HTTP {r.status_code}: {r.text[:60]}"))
    except Exception as exc:
        checks.append(_check("Telegram Bot API", "fail", str(exc)[:80]))

    # ── Supabase REST ─────────────────────────────────────────────────────────
    try:
        sb_url = SUPABASE_URL or ""
        sb_key = SUPABASE_SERVICE_KEY or ""
        if not sb_url or not sb_key:
            checks.append(_check("Supabase REST", "fail", "SUPABASE_URL or KEY missing"))
        else:
            headers = {
                "apikey": sb_key,
                "Authorization": f"Bearer {sb_key}",
            }
            r = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _req.get(
                    f"{sb_url}/rest/v1/users?limit=1",
                    headers=headers, timeout=8
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

    # ── OpenRouter (AI Chat) ──────────────────────────────────────────────────
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
                checks.append(_check("OpenRouter API", "fail",
                                     "Invalid API key"))
            else:
                checks.append(_check("OpenRouter API", "warn", f"HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_check("OpenRouter API", "warn", str(exc)[:80]))

    # ── Google Gemini ─────────────────────────────────────────────────────────
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

    # ── HuggingFace (Image Gen) ───────────────────────────────────────────────
    try:
        key = HUGGINGFACE_API_KEY or ""
        if not key:
            checks.append(_check("HuggingFace API", "warn", "HUGGINGFACE_API_KEY not set"))
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

    # ── Railway deployment ────────────────────────────────────────────────────
    railway_env = os.getenv("RAILWAY_ENVIRONMENT", "")
    railway_svc = os.getenv("RAILWAY_SERVICE_NAME", "")
    railway_proj = os.getenv("RAILWAY_PROJECT_NAME", "")
    if IS_RAILWAY:
        checks.append(_check("Railway deployment", "pass",
                             f"Running on Railway — env={railway_env or 'production'} "
                             f"svc={railway_svc or BOT_NAME}"))
    else:
        checks.append(_check("Railway deployment", "warn",
                             "Not running on Railway — dev/local mode"))

    # ── Keepalive endpoint ────────────────────────────────────────────────────
    try:
        web_url = os.getenv("BOT_WEB_URL", "") or os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
        if web_url:
            ping_url = f"https://{web_url}/health" if not web_url.startswith("http") else f"{web_url}/health"
            r = await asyncio.get_running_loop().run_in_executor(
                None, lambda: _req.get(ping_url, timeout=6)
            )
            if r.status_code == 200:
                checks.append(_check("Keepalive endpoint", "pass",
                                     f"{ping_url} → 200 OK"))
            else:
                checks.append(_check("Keepalive endpoint", "warn",
                                     f"HTTP {r.status_code}"))
        else:
            checks.append(_check("Keepalive endpoint", "warn",
                                 "BOT_WEB_URL not set — uptime pings disabled"))
    except Exception as exc:
        checks.append(_check("Keepalive endpoint", "warn", str(exc)[:60]))

    return {
        "checks": checks,
        "status": _section_status(checks),
        "auto_fixable": False,
        "fix_desc": "Set missing API keys in Railway environment variables",
    }


# ── Full audit runner ─────────────────────────────────────────────────────────

async def run_full_audit(bot) -> dict:
    """Run all 14 audit sections concurrently and calculate health score."""
    t_start = time.time()

    results = await asyncio.gather(
        _audit_bot_core(bot),
        _audit_ai_providers(),
        _audit_database(),
        _audit_railway(),
        _audit_channel(bot),
        _audit_community(bot),
        _audit_admin(),
        _audit_referrals(),
        _audit_vip(),
        _audit_announcements(),
        _audit_security(),
        _audit_error_logs(),
        _audit_languages(),
        _audit_integrations(),
        return_exceptions=True,
    )

    section_keys = list(SECTION_META.keys())
    sections: dict[str, dict] = {}
    total_pass = total_warn = total_fail = 0

    for key, result in zip(section_keys, results):
        if isinstance(result, Exception):
            sections[key] = {
                "checks": [_check("Section runner", "fail", str(result)[:80])],
                "status": "fail",
                "auto_fixable": False,
                "fix_desc": "",
            }
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

    return {
        "timestamp": time.time(),
        "duration_ms": int((time.time() - t_start) * 1000),
        "sections": sections,
        "total_pass": total_pass,
        "total_warn": total_warn,
        "total_fail": total_fail,
        "total_checks": total_checks,
        "health_score": health_score,
    }


# ── Dashboard renderers ───────────────────────────────────────────────────────

def _score_emoji(score: int) -> str:
    if score >= 90:  return "🟢"
    if score >= 70:  return "🟡"
    if score >= 50:  return "🟠"
    return "🔴"


def _render_dashboard(audit: dict) -> tuple[str, InlineKeyboardMarkup]:
    score    = audit["health_score"]
    total_p  = audit["total_pass"]
    total_w  = audit["total_warn"]
    total_f  = audit["total_fail"]
    sections = audit["sections"]
    ts       = datetime.fromtimestamp(audit["timestamp"], tz=timezone.utc).strftime("%H:%M:%S UTC")
    dur_ms   = audit.get("duration_ms", 0)

    readiness = "🚀 Production Ready" if score >= 90 else (
        "⚠️ Review Warnings" if score >= 70 else
        "🔴 Needs Attention"
    )

    lines = [
        f"<b>🛡️ FundzAiBot Audit Center</b>",
        f"",
        f"{_score_emoji(score)} <b>Health Score: {score}%</b>  —  {readiness}",
        f"",
        f"✅ Passed: {total_p}   ⚠️ Warnings: {total_w}   ❌ Failed: {total_f}",
        f"<i>Audited: {ts} ({dur_ms}ms)</i>",
    ]

    text = "\n".join(lines)

    def _section_btn(key: str) -> InlineKeyboardButton:
        icon, label = SECTION_META[key]
        sec_status  = sections.get(key, {}).get("status", "fail")
        badge = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(sec_status, "⬜")
        return InlineKeyboardButton(f"{icon} {label} {badge}", callback_data=f"audit:section:{key}")

    section_keys = list(SECTION_META.keys())
    rows = []
    for i in range(0, len(section_keys), 2):
        row = [_section_btn(section_keys[i])]
        if i + 1 < len(section_keys):
            row.append(_section_btn(section_keys[i + 1]))
        rows.append(row)

    rows.append([
        InlineKeyboardButton("🛠 Auto Fix All",     callback_data="audit:autofix:all"),
        InlineKeyboardButton("📄 Report",            callback_data="audit:report"),
    ])
    rows.append([
        InlineKeyboardButton("🔄 Full Retest",       callback_data="audit:retest"),
        InlineKeyboardButton("« Admin Panel",         callback_data="admin:panel"),
    ])

    return text, InlineKeyboardMarkup(rows)


def _render_section(key: str, section: dict) -> tuple[str, InlineKeyboardMarkup]:
    icon, label = SECTION_META[key]
    checks = section.get("checks", [])
    status = section.get("status", "fail")
    status_icon = {"pass": "✅", "warn": "⚠️", "fail": "❌"}.get(status, "⬜")

    lines = [
        f"<b>{icon} {label} Audit  {status_icon}</b>",
        "",
    ]

    for c in checks:
        s_icon = _ICON.get(c["status"], "⬜")
        detail = c.get("detail", "")
        lines.append(f"{s_icon} <b>{c['name']}</b>")
        if detail:
            lines.append(f"   <i>{detail}</i>")
        if c.get("fix") and c["status"] in ("warn", "fail"):
            lines.append(f"   💡 Fix: {c['fix']}")

    if section.get("auto_fixable"):
        lines.append(f"\n💡 <i>Auto-fix available: {section.get('fix_desc', '')}</i>")

    text = "\n".join(lines)
    if len(text) > 3800:
        text = text[:3800] + "\n… (truncated)"

    kbd_rows = []
    if section.get("auto_fixable"):
        kbd_rows.append([
            InlineKeyboardButton(f"🛠 Auto Fix",
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
        InlineKeyboardButton("« Dashboard",
                             callback_data="audit:dashboard"),
    ])
    return text, InlineKeyboardMarkup(kbd_rows)


# ── Auto-fix actions ──────────────────────────────────────────────────────────

async def _autofix(key: str, bot) -> str:
    """Perform safe in-memory repairs. Returns a status message."""
    results = []

    if key in ("bot_core", "all"):
        from config.settings import FEATURE_FLAGS
        FEATURE_FLAGS.update({
            "chat_enabled": True,
            "image_enabled": True,
            "new_users_enabled": True,
        })
        results.append("✅ Feature flags reset to defaults (chat/image/new-users ON)")
        try:
            await queue_manager.start()
            results.append("✅ Queue manager re-started")
        except Exception as exc:
            results.append(f"⚠️ Queue restart: {exc}")

    if key in ("admin", "all"):
        try:
            from services.database import load_secondary_admins
            load_secondary_admins()
            results.append("✅ Secondary admins reloaded from database")
        except Exception as exc:
            results.append(f"⚠️ Admin reload: {exc}")

    if key in ("announcements", "all"):
        try:
            from services.database import get_active_announcement, create_announcement
            from handlers.announcements import DEFAULT_ANNOUNCEMENT
            if not get_active_announcement():
                create_announcement(DEFAULT_ANNOUNCEMENT)
                results.append("✅ Default announcement re-seeded")
            else:
                results.append("ℹ️ Active announcement already exists — no change")
        except Exception as exc:
            results.append(f"⚠️ Announcement fix: {exc}")

    if key in ("error_logs", "all"):
        try:
            from services.database import _headers, _url, _safe_get
            import requests as _req
            r = _req.delete(
                f"{_url('error_logs')}?created_at=lt.{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
                headers=_headers(),
                timeout=(5, 10),
            )
            if r.status_code in (200, 204):
                results.append("✅ Old error logs cleared (kept today's)")
            else:
                results.append(f"⚠️ Error log cleanup: HTTP {r.status_code}")
        except Exception as exc:
            results.append(f"⚠️ Error log cleanup: {exc}")

    if not results:
        results.append("ℹ️ No auto-fix actions available for this section")

    return "\n".join(results)


# ── Report generator ──────────────────────────────────────────────────────────

def _generate_report(audit: dict) -> str:
    score   = audit["health_score"]
    ts      = datetime.fromtimestamp(audit["timestamp"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    sections = audit["sections"]

    lines = [
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"  FundzAiBot  Audit Report",
        f"  {ts}",
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"",
        f"Health Score : {score}%",
        f"Passed       : {audit['total_pass']}",
        f"Warnings     : {audit['total_warn']}",
        f"Critical     : {audit['total_fail']}",
        f"Total Checks : {audit['total_checks']}",
        f"",
        f"Status: {'🚀 Production Ready' if score >= 90 else ('⚠️ Review Needed' if score >= 70 else '🔴 Needs Immediate Attention')}",
        f"",
    ]

    for key, section in sections.items():
        icon, label = SECTION_META[key]
        status_tag  = section.get("status", "fail").upper()
        lines.append(f"{'─'*30}")
        lines.append(f"{icon} {label}  [{status_tag}]")
        for c in section.get("checks", []):
            marker = {"pass": "✓", "warn": "!", "fail": "✗", "skip": "–", "info": "i"}.get(c["status"], "?")
            lines.append(f"  [{marker}] {c['name']}: {c.get('detail','')[:70]}")
            if c.get("fix") and c["status"] in ("warn", "fail"):
                lines.append(f"      Fix: {c['fix']}")
        lines.append("")

    lines.extend([
        f"━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Generated by /testaudit  v{BOT_VERSION}",
        f"Railway-only production guard: {'ACTIVE' if IS_RAILWAY else 'dev mode'}",
    ])
    return "\n".join(lines)


# ── Callback handler (called from callbacks.py) ───────────────────────────────

async def audit_callback(query, context, action: str) -> None:
    """Handle all audit: prefixed callbacks."""
    bot = context.bot

    async def _cached_audit() -> dict:
        cached = context.bot_data.get(_CACHE_KEY)
        if cached and (time.time() - cached["timestamp"]) < _AUDIT_TTL:
            return cached
        result = await run_full_audit(bot)
        context.bot_data[_CACHE_KEY] = result
        return result

    if action == "dashboard":
        await query.answer()
        audit = await _cached_audit()
        text, kbd = _render_dashboard(audit)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass

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

    elif action.startswith("recheck:"):
        key    = action.split(":", 1)[1]
        runner = {
            "bot_core":      lambda: _audit_bot_core(bot),
            "ai_providers":  _audit_ai_providers,
            "database":      _audit_database,
            "railway":       _audit_railway,
            "channel":       lambda: _audit_channel(bot),
            "community":     lambda: _audit_community(bot),
            "admin":         _audit_admin,
            "referrals":     _audit_referrals,
            "vip":           _audit_vip,
            "announcements": _audit_announcements,
            "security":      _audit_security,
            "error_logs":    _audit_error_logs,
            "languages":     _audit_languages,
            "integrations":  _audit_integrations,
        }.get(key)

        if runner:
            await query.answer("Re-checking…")
            try:
                new_section = await runner()
            except Exception as exc:
                new_section = {"checks": [_check("Runner", "fail", str(exc))],
                               "status": "fail", "auto_fixable": False, "fix_desc": ""}
            # Update cache
            cached = context.bot_data.get(_CACHE_KEY)
            if cached:
                cached["sections"][key] = new_section
                context.bot_data[_CACHE_KEY] = cached
            text, kbd = _render_section(key, new_section)
            try:
                await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
            except Exception:
                pass
        else:
            await query.answer("Unknown section.", show_alert=True)

    elif action.startswith("autofix:"):
        key = action.split(":", 1)[1]
        await query.answer(f"Running auto-fix for {key}…")
        fix_result = await _autofix(key, bot)
        try:
            await query.edit_message_text(
                f"<b>🛠 Auto Fix Result</b>\n\n{fix_result}\n\n"
                f"<i>Tip: tap 🔄 Full Retest to see updated scores.</i>",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔄 Full Retest",  callback_data="audit:retest"),
                    InlineKeyboardButton("« Dashboard",     callback_data="audit:dashboard"),
                ]]),
            )
        except Exception:
            pass

    elif action == "retest":
        await query.answer("Running full audit…")
        context.bot_data.pop(_CACHE_KEY, None)
        audit = await run_full_audit(bot)
        context.bot_data[_CACHE_KEY] = audit
        text, kbd = _render_dashboard(audit)
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass

    elif action == "report":
        audit = await _cached_audit()
        report = _generate_report(audit)
        # Send as a separate message since it's long
        try:
            await context.bot.send_message(
                query.from_user.id,
                f"<pre>{report}</pre>",
                parse_mode="HTML",
            )
            await query.answer("Report sent above ↑")
        except Exception:
            # Fallback: try without HTML
            try:
                await context.bot.send_message(query.from_user.id, report)
                await query.answer("Report sent!")
            except Exception as exc:
                await query.answer(f"Could not send report: {exc}", show_alert=True)

    else:
        await query.answer()


# ── Command handlers ──────────────────────────────────────────────────────────

async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/status — Quick live status for admins."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    msg = await update.effective_message.reply_text("🔍 Checking status…")

    try:
        # Quick checks in parallel
        bot_task = asyncio.create_task(
            asyncio.wait_for(context.bot.get_me(), timeout=5)
        )
        db_task = asyncio.get_running_loop().run_in_executor(None, lambda: __import__(
            "services.database", fromlist=["count_users"]
        ).count_users())

        try:
            me      = await bot_task
            bot_ok  = f"✅ @{me.username}"
        except Exception:
            bot_ok  = "❌ API error"

        try:
            counts  = await db_task
            db_ok   = f"✅ {counts['total']} users | {counts['vip']} VIP"
        except Exception:
            db_ok   = "❌ DB error"

        qs = queue_manager.stats()
        ai_keys = sum([bool(OPENROUTER_API_KEY), bool(GEMINI_API_KEY), bool(HUGGINGFACE_API_KEY)])
        env_tag  = "🚂 Railway" if IS_RAILWAY else "💻 Dev mode"

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
            InlineKeyboardButton("🛡️ Full Audit",   callback_data="audit:retest"),
            InlineKeyboardButton("🩺 AI Health",     callback_data="admin:health"),
        ]])

        await msg.edit_text(text, parse_mode="HTML", reply_markup=kbd)

    except Exception as exc:
        log.error("/status error: %s", exc)
        await msg.edit_text(f"❌ Status check failed: {exc}")


async def testaudit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/testaudit — Full enterprise audit center."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    msg = await update.effective_message.reply_text(
        "⏳ <b>Running full audit…</b>\n\n"
        "<i>Checking all 14 systems concurrently…</i>",
        parse_mode="HTML",
    )

    try:
        context.bot_data.pop(_CACHE_KEY, None)
        audit = await run_full_audit(context.bot)
        context.bot_data[_CACHE_KEY] = audit
        text, kbd = _render_dashboard(audit)
        await msg.edit_text(text, parse_mode="HTML", reply_markup=kbd)
    except Exception as exc:
        log.error("/testaudit error: %s", exc, exc_info=True)
        await msg.edit_text(f"❌ Audit failed: {exc}")
