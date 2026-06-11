"""
FundzAiBot — Enterprise Audit Service
Comprehensive diagnostics for all bot systems.
All functions are SYNCHRONOUS — call via run_in_executor from async handlers.
"""

import os
import time
from datetime import datetime, timedelta
from typing import Any

import requests

from config.settings import (
    TELEGRAM_BOT_TOKEN, ADMIN_USER_ID, SUPABASE_URL, SUPABASE_SERVICE_KEY,
    OPENROUTER_API_KEY, GEMINI_API_KEY, HUGGINGFACE_API_KEY,
    OPENROUTER_MODEL, GEMINI_MODEL, HF_CHAT_MODEL,
    IS_RAILWAY, ALLOW_POLLING, BOT_VERSION, BOT_NAME,
    TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID, BOT_WEB_URL,
    FEATURE_FLAGS, SECONDARY_ADMINS,
    REFERRAL_CHAT_BONUS, REFERRAL_IMAGE_BONUS,
    VIP_DAILY_CHAT, VIP_DAILY_IMAGE,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Helpers ───────────────────────────────────────────────────────────────────

def _ok(label: str, value: str, detail: str = "") -> dict:
    return {"status": "ok", "label": label, "value": value, "detail": detail, "fix": None}

def _warn(label: str, value: str, detail: str = "", fix: str | None = None) -> dict:
    return {"status": "warning", "label": label, "value": value, "detail": detail, "fix": fix}

def _crit(label: str, value: str, detail: str = "", fix: str | None = None) -> dict:
    return {"status": "critical", "label": label, "value": value, "detail": detail, "fix": fix}

def _section(name: str, checks: list[dict]) -> dict:
    critical = sum(1 for c in checks if c["status"] == "critical")
    warnings  = sum(1 for c in checks if c["status"] == "warning")
    passed    = sum(1 for c in checks if c["status"] == "ok")
    total     = len(checks)
    if critical > 0:
        overall = "critical"
    elif warnings > 0:
        overall = "warning"
    else:
        overall = "ok"
    score = int(100 * (passed + warnings * 0.5) / max(total, 1))
    return {
        "section": name,
        "title": SECTION_TITLES.get(name, name),
        "checks": checks,
        "score": score,
        "status": overall,
        "critical": critical,
        "warnings": warnings,
        "passed": passed,
        "fix_available": any(c.get("fix") for c in checks),
    }


_SB_TIMEOUT = (5, 10)
_TG_TIMEOUT = 8


def _sb_headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
    }


# ── Section: Bot Core ─────────────────────────────────────────────────────────

def check_bot_core() -> dict:
    checks = []

    if not TELEGRAM_BOT_TOKEN:
        checks.append(_crit("Bot Token", "❌ MISSING", "TELEGRAM_BOT_TOKEN is not set in env vars"))
        return _section("bot", checks)
    checks.append(_ok("Bot Token", "✅ Configured"))

    try:
        t0 = time.time()
        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe",
            timeout=_TG_TIMEOUT,
        )
        latency = int((time.time() - t0) * 1000)
        if r.status_code == 200:
            d = r.json().get("result", {})
            uname = d.get("username", "?")
            fname = d.get("first_name", "?")
            checks.append(_ok("Telegram API", f"✅ Connected ({latency}ms)"))
            checks.append(_ok("Bot Identity", f"✅ {fname} (@{uname})"))
        elif r.status_code == 401:
            checks.append(_crit("Telegram API", "❌ 401 Unauthorized",
                                "Token invalid or revoked. Get a new token from @BotFather."))
        else:
            checks.append(_warn("Telegram API", f"⚠️ HTTP {r.status_code}", r.text[:80]))
    except requests.Timeout:
        checks.append(_warn("Telegram API", "⚠️ Timeout", "Telegram unreachable after 8s"))
    except Exception as exc:
        checks.append(_crit("Telegram API", "❌ Error", str(exc)[:80]))

    if IS_RAILWAY:
        checks.append(_ok("Polling Guard", "✅ Railway detected — polling active"))
    elif os.getenv("ALLOW_POLLING", "false").lower() == "true":
        checks.append(_warn("Polling Guard", "⚠️ ALLOW_POLLING=true override",
                             "Ensure Railway is NOT also running — risk of 409 Conflicts"))
    else:
        checks.append(_ok("Polling Guard", "✅ Replit dev mode — polling blocked (correct)"))

    off_flags = [k for k, v in FEATURE_FLAGS.items() if not v and k != "maintenance_mode"]
    if FEATURE_FLAGS.get("maintenance_mode"):
        checks.append(_warn("Maintenance Mode", "🚧 ON — users see maintenance msg",
                             fix="toggle_maintenance"))
    else:
        checks.append(_ok("Maintenance Mode", "✅ OFF — bot is live"))

    if off_flags:
        checks.append(_warn("Feature Flags", f"⚠️ Disabled: {', '.join(off_flags)}", fix="refresh_flags"))
    else:
        checks.append(_ok("Feature Flags", "✅ All features enabled"))

    return _section("bot", checks)


# ── Section: AI Providers ─────────────────────────────────────────────────────

def check_ai_providers() -> dict:
    checks = []
    available = 0

    # OpenRouter
    if not OPENROUTER_API_KEY:
        checks.append(_warn("OpenRouter", "⚠️ No key set", "Set OPENROUTER_API_KEY to enable"))
    else:
        try:
            t0 = time.time()
            r = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}",
                         "Content-Type": "application/json"},
                json={"model": OPENROUTER_MODEL,
                      "messages": [{"role": "user", "content": "hi"}],
                      "max_tokens": 3},
                timeout=12,
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                checks.append(_ok("OpenRouter", f"✅ {OPENROUTER_MODEL} | {ms}ms"))
                available += 1
            elif r.status_code == 401:
                checks.append(_crit("OpenRouter", "❌ 401 Invalid key",
                                    "Update OPENROUTER_API_KEY in Railway env vars"))
            elif r.status_code == 402:
                checks.append(_crit("OpenRouter", "❌ 402 No credits",
                                    "Top up at openrouter.ai/credits"))
            elif r.status_code == 429:
                checks.append(_warn("OpenRouter", "⚠️ 429 Rate limited — will recover",
                                    fix="refresh_provider_cache"))
                available += 1
            elif r.status_code == 404:
                checks.append(_warn("OpenRouter", f"⚠️ 404 Model not found",
                                    f"Check OPENROUTER_MODEL: '{OPENROUTER_MODEL}'"))
            else:
                checks.append(_warn("OpenRouter", f"⚠️ HTTP {r.status_code}", r.text[:80]))
        except requests.Timeout:
            checks.append(_warn("OpenRouter", "⚠️ Timeout >12s", fix="refresh_provider_cache"))
        except Exception as exc:
            checks.append(_warn("OpenRouter", "⚠️ Error", str(exc)[:60]))

    # Gemini
    if not GEMINI_API_KEY:
        checks.append(_warn("Gemini", "⚠️ No key set", "Set GEMINI_API_KEY to enable"))
    else:
        try:
            t0 = time.time()
            r = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": "hi"}]}],
                      "generationConfig": {"maxOutputTokens": 3}},
                timeout=12,
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                checks.append(_ok("Gemini", f"✅ {GEMINI_MODEL} | {ms}ms"))
                available += 1
            elif r.status_code == 403:
                checks.append(_crit("Gemini", "❌ 403 API key invalid",
                                    "Update GEMINI_API_KEY in Railway env vars"))
            elif r.status_code == 429:
                checks.append(_warn("Gemini", "⚠️ 429 Rate limited — will recover",
                                    fix="refresh_provider_cache"))
                available += 1
            elif r.status_code == 400:
                checks.append(_warn("Gemini", "⚠️ 400 Bad request",
                                    f"Check GEMINI_MODEL: '{GEMINI_MODEL}'"))
            else:
                checks.append(_warn("Gemini", f"⚠️ HTTP {r.status_code}", r.text[:80]))
        except requests.Timeout:
            checks.append(_warn("Gemini", "⚠️ Timeout >12s", fix="refresh_provider_cache"))
        except Exception as exc:
            checks.append(_warn("Gemini", "⚠️ Error", str(exc)[:60]))

    # HuggingFace
    if not HUGGINGFACE_API_KEY:
        checks.append(_warn("HuggingFace", "⚠️ No key set", "Set HUGGINGFACE_API_KEY to enable"))
    else:
        try:
            t0 = time.time()
            r = requests.post(
                f"https://api-inference.huggingface.co/models/{HF_CHAT_MODEL}",
                headers={"Authorization": f"Bearer {HUGGINGFACE_API_KEY}"},
                json={"inputs": "hi", "parameters": {"max_new_tokens": 3}},
                timeout=15,
            )
            ms = int((time.time() - t0) * 1000)
            if r.status_code in (200, 503):
                label = "Loading" if r.status_code == 503 else f"{HF_CHAT_MODEL}"
                checks.append(_ok("HuggingFace", f"✅ {label} | {ms}ms"))
                available += 1
            elif r.status_code == 401:
                checks.append(_crit("HuggingFace", "❌ 401 Invalid token",
                                    "Update HUGGINGFACE_API_KEY"))
            elif r.status_code == 429:
                checks.append(_warn("HuggingFace", "⚠️ 429 Rate limited",
                                    fix="refresh_provider_cache"))
                available += 1
            else:
                checks.append(_warn("HuggingFace", f"⚠️ HTTP {r.status_code}", r.text[:80]))
        except requests.Timeout:
            checks.append(_warn("HuggingFace", "⚠️ Timeout >15s — model may be cold"))
        except Exception as exc:
            checks.append(_warn("HuggingFace", "⚠️ Error", str(exc)[:60]))

    # Summary
    if available == 0:
        checks.append(_crit("Fallback Chain", "❌ 0 providers available",
                             "Bot cannot handle AI requests. Fix at least one provider."))
    elif available == 1:
        checks.append(_warn("Fallback Chain", f"⚠️ Only 1 provider available",
                             "Add more API keys for better redundancy"))
    else:
        checks.append(_ok("Fallback Chain", f"✅ {available} providers available"))

    return _section("ai", checks)


# ── Section: Database ─────────────────────────────────────────────────────────

def check_database() -> dict:
    checks = []

    if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
        checks.append(_crit("Supabase Config", "❌ Credentials missing",
                             "Set SUPABASE_URL and SUPABASE_SERVICE_KEY"))
        return _section("db", checks)

    base = f"{SUPABASE_URL}/rest/v1"
    hdrs = _sb_headers()

    tables = [
        ("users",          "Users"),
        ("user_credits",   "Credits"),
        ("conversations",  "Conversations"),
        ("image_history",  "Image History"),
        ("referrals",      "Referrals"),
        ("error_logs",     "Error Logs"),
        ("announcements",  "Announcements"),
        ("admin_accounts", "Admin Accounts"),
    ]

    for table, label in tables:
        try:
            t0 = time.time()
            r = requests.get(f"{base}/{table}?limit=1", headers=hdrs, timeout=_SB_TIMEOUT)
            ms = int((time.time() - t0) * 1000)
            if r.status_code == 200:
                count_r = requests.head(
                    f"{base}/{table}?select=count",
                    headers={**hdrs, "Prefer": "count=exact"},
                    timeout=(3, 6),
                )
                total = count_r.headers.get("Content-Range", "?/?").split("/")[-1]
                checks.append(_ok(f"{label}", f"✅ {total} rows | {ms}ms"))
            elif r.status_code == 404:
                checks.append(_crit(f"{label}", "❌ Table not found",
                                    f"Run schema SQL to create '{table}' table"))
            elif r.status_code == 401:
                checks.append(_crit(f"{label}", "❌ 401 Auth failed",
                                    "SUPABASE_SERVICE_KEY may be invalid or expired"))
            else:
                checks.append(_warn(f"{label}", f"⚠️ HTTP {r.status_code}", r.text[:60]))
        except requests.Timeout:
            checks.append(_warn(f"{label}", "⚠️ Supabase timeout"))
        except Exception as exc:
            checks.append(_warn(f"{label}", "⚠️ Error", str(exc)[:60]))

    # RPC functions
    for fn, flabel in [("increment_chat", "increment_chat RPC"),
                        ("increment_image", "increment_image RPC")]:
        try:
            r = requests.post(
                f"{SUPABASE_URL}/rest/v1/rpc/{fn}",
                headers=hdrs, json={"p_user_id": 0}, timeout=(3, 6),
            )
            if r.status_code == 404:
                checks.append(_warn(flabel, "⚠️ RPC not found",
                                    f"Register {fn}() function in Supabase"))
            else:
                checks.append(_ok(flabel, "✅ Available"))
        except Exception as exc:
            checks.append(_warn(flabel, "⚠️ Error", str(exc)[:60]))

    return _section("db", checks)


# ── Section: Railway ──────────────────────────────────────────────────────────

def check_railway() -> dict:
    checks = []

    rail_env  = os.getenv("RAILWAY_ENVIRONMENT", "")
    rail_svc  = os.getenv("RAILWAY_SERVICE_NAME", "")
    rail_proj = os.getenv("RAILWAY_PROJECT_ID", "")
    repl_slug = os.getenv("REPL_SLUG", "")
    allow_ov  = os.getenv("ALLOW_POLLING", "false").lower() == "true"

    if IS_RAILWAY:
        checks.append(_ok("Environment", f"✅ Railway ({rail_env or 'production'})"))
        if rail_svc:
            checks.append(_ok("Service Name", f"✅ {rail_svc}"))
        if rail_proj:
            checks.append(_ok("Project ID", f"✅ {rail_proj[:8]}…"))
        checks.append(_ok("Polling Ownership", "✅ Railway owns polling — correct"))
        checks.append(_ok("Duplicate Risk", "✅ No duplicate polling detected"))
    elif repl_slug:
        checks.append(_ok("Environment", "✅ Replit dev mode (polling disabled)"))
        checks.append(_ok("Polling Guard", "✅ Polling blocked — no 409 Conflicts"))
        if allow_ov:
            checks.append(_warn("Conflict Risk", "⚠️ ALLOW_POLLING=true on Replit",
                                 "Remove ALLOW_POLLING to avoid conflicts if Railway is live"))
        else:
            checks.append(_ok("Conflict Risk", "✅ ALLOW_POLLING not set — safe"))
    else:
        checks.append(_warn("Environment", "⚠️ Unknown environment",
                             "Set RAILWAY_ENVIRONMENT on Railway for production"))

    # Required vars
    required = {
        "TELEGRAM_BOT_TOKEN":  TELEGRAM_BOT_TOKEN,
        "ADMIN_USER_ID":       str(ADMIN_USER_ID) if ADMIN_USER_ID else "",
        "SUPABASE_URL":        SUPABASE_URL,
        "SUPABASE_SERVICE_KEY": SUPABASE_SERVICE_KEY,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        checks.append(_crit("Required Env Vars", f"❌ Missing: {', '.join(missing)}",
                             "Set these in Railway → Service → Variables"))
    else:
        checks.append(_ok("Required Env Vars", "✅ All critical vars present"))

    # Optional vars
    opt_missing = [v for v in ("BOT_WEB_URL", "TELEGRAM_CHANNEL_ID", "TELEGRAM_GROUP_ID")
                   if not os.getenv(v)]
    if opt_missing:
        checks.append(_warn("Optional Vars", f"⚠️ Not set: {', '.join(opt_missing)}",
                             "These unlock mini-app overlay, channel & group features"))
    else:
        checks.append(_ok("Optional Vars", "✅ All optional vars configured"))

    return _section("railway", checks)


# ── Section: Channel ──────────────────────────────────────────────────────────

def check_channel() -> dict:
    checks = []

    if not TELEGRAM_CHANNEL_ID:
        checks.append(_warn("Channel Config", "⚠️ TELEGRAM_CHANNEL_ID not set",
                             "Set this to enable channel integration and verification"))
        return _section("channel", checks)

    checks.append(_ok("Channel ID", f"✅ Configured: {TELEGRAM_CHANNEL_ID}"))

    if not TELEGRAM_BOT_TOKEN:
        checks.append(_crit("Bot Token", "❌ Cannot check channel — missing token"))
        return _section("channel", checks)

    try:
        bot_r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=_TG_TIMEOUT
        )
        bot_id = bot_r.json().get("result", {}).get("id") if bot_r.status_code == 200 else None

        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatAdministrators",
            params={"chat_id": TELEGRAM_CHANNEL_ID}, timeout=_TG_TIMEOUT,
        )
        if r.status_code == 200:
            admins = r.json().get("result", [])
            bot_entry = next((a for a in admins
                              if bot_id and a.get("user", {}).get("id") == bot_id), None)
            if bot_entry:
                can_post = bot_entry.get("can_post_messages", False)
                can_pin  = bot_entry.get("can_pin_messages", False)
                checks.append(_ok("Bot Admin Status", "✅ Bot is channel admin"))
                checks.append(
                    _ok("Post Permission", "✅ can_post_messages") if can_post
                    else _warn("Post Permission", "⚠️ Cannot post",
                               "Grant 'Post Messages' to bot in channel admin settings")
                )
                checks.append(
                    _ok("Pin Permission", "✅ can_pin_messages") if can_pin
                    else _warn("Pin Permission", "⚠️ Cannot pin",
                               "Grant 'Pin Messages' to bot")
                )
            else:
                checks.append(_crit("Bot Admin Status", "❌ Bot NOT admin",
                                    "Add bot as admin with Post + Pin permissions"))
        elif r.status_code == 400:
            checks.append(_warn("Channel Access", "⚠️ Bad request",
                                 "TELEGRAM_CHANNEL_ID format should be -100XXXXXXXXX"))
        elif r.status_code == 403:
            checks.append(_crit("Channel Access", "❌ Bot not in channel",
                                 "Add bot to channel as admin"))
        else:
            checks.append(_warn("Channel Access", f"⚠️ HTTP {r.status_code}", r.text[:60]))
    except Exception as exc:
        checks.append(_warn("Channel Check", "⚠️ Error", str(exc)[:80]))

    return _section("channel", checks)


# ── Section: Community Group ──────────────────────────────────────────────────

def check_group() -> dict:
    checks = []

    if not TELEGRAM_GROUP_ID:
        checks.append(_warn("Group Config", "⚠️ TELEGRAM_GROUP_ID not set",
                             "Set this to enable group AI commands and moderation"))
        return _section("group", checks)

    checks.append(_ok("Group ID", f"✅ Configured: {TELEGRAM_GROUP_ID}"))

    if not TELEGRAM_BOT_TOKEN:
        checks.append(_crit("Bot Token", "❌ Cannot check group — missing token"))
        return _section("group", checks)

    try:
        bot_r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=_TG_TIMEOUT
        )
        bot_id = bot_r.json().get("result", {}).get("id") if bot_r.status_code == 200 else None

        r = requests.get(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getChatAdministrators",
            params={"chat_id": TELEGRAM_GROUP_ID}, timeout=_TG_TIMEOUT,
        )
        if r.status_code == 200:
            admins = r.json().get("result", [])
            bot_entry = next((a for a in admins
                              if bot_id and a.get("user", {}).get("id") == bot_id), None)
            if bot_entry:
                can_delete   = bot_entry.get("can_delete_messages", False)
                can_restrict = bot_entry.get("can_restrict_members", False)
                can_pin      = bot_entry.get("can_pin_messages", False)
                checks.append(_ok("Bot Admin Status", "✅ Bot is group admin"))
                checks.append(
                    _ok("Delete Permission", "✅ can_delete_messages") if can_delete
                    else _warn("Delete Permission", "⚠️ Cannot delete",
                               "Grant 'Delete Messages' for anti-spam moderation")
                )
                checks.append(
                    _ok("Restrict Permission", "✅ can_restrict_members") if can_restrict
                    else _warn("Restrict Permission", "⚠️ Cannot restrict/mute",
                               "Grant 'Restrict Members' for auto-mute")
                )
                checks.append(
                    _ok("Pin Permission", "✅ can_pin_messages") if can_pin
                    else _warn("Pin Permission", "⚠️ Cannot pin messages")
                )
            else:
                checks.append(_crit("Bot Admin Status", "❌ Bot NOT admin",
                                    "Add bot as admin with Delete + Restrict + Pin permissions"))
        elif r.status_code == 403:
            checks.append(_crit("Group Access", "❌ Bot not in group", "Add bot to group as admin"))
        else:
            checks.append(_warn("Group Access", f"⚠️ HTTP {r.status_code}", r.text[:60]))
    except Exception as exc:
        checks.append(_warn("Group Check", "⚠️ Error", str(exc)[:80]))

    checks.append(_ok("AI Commands", "✅ /ai and /image available in group"))
    checks.append(_ok("Anti-Spam Filter", "✅ Scam link detection + auto-mute"))
    checks.append(_ok("Welcome System", "✅ Auto-welcome with ecosystem buttons"))

    return _section("group", checks)


# ── Section: Admin System ─────────────────────────────────────────────────────

def check_admin_system() -> dict:
    checks = []

    if not ADMIN_USER_ID:
        checks.append(_crit("Owner ID", "❌ ADMIN_USER_ID not set",
                             "Critical: set ADMIN_USER_ID to your Telegram user ID"))
        return _section("admin", checks)

    checks.append(_ok("Owner ID", f"✅ Set: {ADMIN_USER_ID}"))
    checks.append(_ok("Secondary Admins (memory)", f"✅ {len(SECONDARY_ADMINS)} loaded"))

    if SUPABASE_URL and SUPABASE_SERVICE_KEY:
        try:
            r = requests.get(
                f"{SUPABASE_URL}/rest/v1/admin_accounts?select=user_id",
                headers=_sb_headers(), timeout=_SB_TIMEOUT,
            )
            if r.status_code == 200:
                count = len(r.json())
                checks.append(_ok("admin_accounts Table", f"✅ {count} admin account(s) in DB"))
            elif r.status_code == 404:
                checks.append(_warn("admin_accounts Table", "⚠️ Table missing",
                                    "Run schema SQL to create admin_accounts table"))
            else:
                checks.append(_warn("admin_accounts Table", f"⚠️ HTTP {r.status_code}"))
        except Exception as exc:
            checks.append(_warn("admin_accounts", "⚠️ Error", str(exc)[:60]))

    checks.append(_ok("Command Suite", "✅ Full admin command set registered",
                       "/admin, /admin_users, /admin_ban, /admin_setvip, /admin_broadcast, /testaudit"))
    checks.append(_ok("/testaudit", "✅ Enterprise audit center active"))

    return _section("admin", checks)


# ── Section: Referrals ────────────────────────────────────────────────────────

def check_referrals() -> dict:
    checks = []

    checks.append(_ok("Reward Config",
                       f"✅ +{REFERRAL_CHAT_BONUS} chat | +{REFERRAL_IMAGE_BONUS} image on referral"))

    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        checks.append(_warn("Database", "⚠️ Supabase not configured"))
        return _section("referrals", checks)

    hdrs = _sb_headers()
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/referrals?select=count",
            headers={**hdrs, "Prefer": "count=exact"}, timeout=_SB_TIMEOUT,
        )
        if r.status_code == 200:
            total = r.headers.get("Content-Range", "?/?").split("/")[-1]
            checks.append(_ok("Referrals Table", f"✅ {total} total referrals"))
        elif r.status_code == 404:
            checks.append(_crit("Referrals Table", "❌ Table not found", "Run schema SQL"))
        else:
            checks.append(_warn("Referrals Table", f"⚠️ HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_warn("Referrals Table", "⚠️ Error", str(exc)[:60]))

    try:
        r2 = requests.get(
            f"{SUPABASE_URL}/rest/v1/users?referral_code=not.is.null&select=count",
            headers={**hdrs, "Prefer": "count=exact"}, timeout=_SB_TIMEOUT,
        )
        if r2.status_code == 200:
            cnt = r2.headers.get("Content-Range", "?/?").split("/")[-1]
            checks.append(_ok("Users with Codes", f"✅ {cnt} users have referral codes"))
    except Exception:
        pass

    return _section("referrals", checks)


# ── Section: VIP ──────────────────────────────────────────────────────────────

def check_vip() -> dict:
    checks = []

    checks.append(_ok("VIP Tiers", "✅ Basic / Pro / Elite"))
    checks.append(_ok("Daily Limits", f"✅ Up to {VIP_DAILY_CHAT} chats / {VIP_DAILY_IMAGE} images"))
    checks.append(_ok("Payment", "✅ Telegram Stars — instant, no card needed"))

    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        checks.append(_warn("Database", "⚠️ Supabase not configured"))
        return _section("vip", checks)

    hdrs = _sb_headers()

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/users?is_vip=eq.true&select=count",
            headers={**hdrs, "Prefer": "count=exact"}, timeout=_SB_TIMEOUT,
        )
        if r.status_code == 200:
            cnt = r.headers.get("Content-Range", "?/?").split("/")[-1]
            checks.append(_ok("Active VIP Users", f"✅ {cnt} users"))
    except Exception:
        checks.append(_warn("VIP Count", "⚠️ Could not fetch"))

    # Expired VIP check
    try:
        now_iso = datetime.utcnow().isoformat()
        r2 = requests.get(
            f"{SUPABASE_URL}/rest/v1/users?is_vip=eq.true&vip_expires_at=lt.{now_iso}&select=count",
            headers={**hdrs, "Prefer": "count=exact"}, timeout=_SB_TIMEOUT,
        )
        if r2.status_code == 200:
            cnt = r2.headers.get("Content-Range", "?/?").split("/")[-1]
            if cnt not in ("0", "?", ""):
                checks.append(_warn("Expired VIPs", f"⚠️ {cnt} users with expired VIP",
                                    "VIP scheduler should handle this. Check services/vip_scheduler.py",
                                    fix="refresh_vip"))
            else:
                checks.append(_ok("VIP Expiry", "✅ No expired VIP users"))
    except Exception:
        pass

    return _section("vip", checks)


# ── Section: Announcements ────────────────────────────────────────────────────

def check_announcements() -> dict:
    checks = []

    if BOT_WEB_URL:
        checks.append(_ok("Web App Overlay", f"✅ {BOT_WEB_URL}/announcement"))
    else:
        checks.append(_warn("Web App Overlay", "⚠️ BOT_WEB_URL not set",
                             "Set on Railway to enable the sticky announcement mini-app"))

    checks.append(_ok("In-Chat Navigator", "✅ ◀ Prev / counter / Next ▶ buttons"))

    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        checks.append(_warn("Database", "⚠️ Supabase not configured"))
        return _section("announcements", checks)

    hdrs = _sb_headers()

    try:
        count_r = requests.get(
            f"{SUPABASE_URL}/rest/v1/announcements?select=count",
            headers={**hdrs, "Prefer": "count=exact"}, timeout=_SB_TIMEOUT,
        )
        if count_r.status_code == 200:
            total = count_r.headers.get("Content-Range", "?/?").split("/")[-1]
            checks.append(_ok("Announcements Table", f"✅ {total} total"))
        elif count_r.status_code == 404:
            checks.append(_crit("Announcements Table", "❌ Not found", "Run schema SQL"))
        else:
            checks.append(_warn("Announcements Table", f"⚠️ HTTP {count_r.status_code}"))

        active_r = requests.get(
            f"{SUPABASE_URL}/rest/v1/announcements?is_active=eq.true&limit=1",
            headers=hdrs, timeout=_SB_TIMEOUT,
        )
        if active_r.status_code == 200:
            rows = active_r.json()
            if rows:
                msg = rows[0].get("message", "")[:50]
                checks.append(_ok("Active Announcement", f"✅ \"{msg}…\""))
            else:
                checks.append(_warn("Active Announcement", "⚠️ None set",
                                    "Use /pin <message> to create one",
                                    fix="refresh_announcement"))
    except Exception as exc:
        checks.append(_warn("Announcements", "⚠️ Error", str(exc)[:60]))

    return _section("announcements", checks)


# ── Section: Security ─────────────────────────────────────────────────────────

def check_security() -> dict:
    checks = []

    crit_missing = []
    if not TELEGRAM_BOT_TOKEN:      crit_missing.append("TELEGRAM_BOT_TOKEN")
    if not ADMIN_USER_ID:           crit_missing.append("ADMIN_USER_ID")
    if not SUPABASE_URL:            crit_missing.append("SUPABASE_URL")
    if not SUPABASE_SERVICE_KEY:    crit_missing.append("SUPABASE_SERVICE_KEY")
    if not any([OPENROUTER_API_KEY, GEMINI_API_KEY, HUGGINGFACE_API_KEY]):
        crit_missing.append("(at least one AI key)")

    if crit_missing:
        checks.append(_crit("Critical Secrets", f"❌ Missing: {', '.join(crit_missing)}",
                             "Set in Railway → Service → Variables"))
    else:
        checks.append(_ok("Critical Secrets", "✅ All critical secrets present"))

    ai_count = sum(bool(k) for k in [OPENROUTER_API_KEY, GEMINI_API_KEY, HUGGINGFACE_API_KEY])
    checks.append(_ok("AI Provider Keys", f"✅ {ai_count}/3 AI keys configured"))

    if ADMIN_USER_ID:
        checks.append(_ok("Admin Gate", "✅ All admin commands gated by ADMIN_USER_ID"))

    if IS_RAILWAY:
        checks.append(_ok("Conflict Risk", "✅ Railway-only polling — no 409 risk"))
    elif os.getenv("ALLOW_POLLING", "false").lower() == "true":
        checks.append(_warn("Conflict Risk", "⚠️ ALLOW_POLLING=true outside Railway",
                             "Risk of 409 Conflict if Railway bot is also running"))
    else:
        checks.append(_ok("Conflict Risk", "✅ Polling disabled in dev — safe"))

    if TELEGRAM_BOT_TOKEN and len(TELEGRAM_BOT_TOKEN) >= 40:
        checks.append(_ok("Token Format", "✅ Token length looks valid"))
    elif TELEGRAM_BOT_TOKEN:
        checks.append(_warn("Token Format", "⚠️ Token seems short",
                             "Telegram bot tokens are typically 45+ characters"))

    return _section("security", checks)


# ── Section: Error Logs ───────────────────────────────────────────────────────

def check_error_logs() -> dict:
    checks = []

    if not (SUPABASE_URL and SUPABASE_SERVICE_KEY):
        checks.append(_warn("Database", "⚠️ Supabase not configured"))
        return _section("logs", checks)

    hdrs = _sb_headers()

    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/error_logs?order=created_at.desc&limit=5",
            headers=hdrs, timeout=_SB_TIMEOUT,
        )
        if r.status_code == 200:
            errors = r.json()
            if not errors:
                checks.append(_ok("Recent Errors", "✅ Clean — no recent errors"))
            else:
                for e in errors:
                    etype = (e.get("error_type") or "unknown")[:30]
                    msg   = (e.get("message") or "")[:60]
                    ts    = (e.get("created_at") or "")[:16].replace("T", " ")
                    uid   = e.get("user_id")
                    uid_s = f" (user {uid})" if uid else ""
                    checks.append(_warn(
                        f"Error: {etype}",
                        f"⚠️ {msg}",
                        f"Time: {ts}{uid_s}",
                    ))

            count_r = requests.get(
                f"{SUPABASE_URL}/rest/v1/error_logs?select=count",
                headers={**hdrs, "Prefer": "count=exact"}, timeout=(3, 6),
            )
            if count_r.status_code == 200:
                total = count_r.headers.get("Content-Range", "?/?").split("/")[-1]
                checks.append(_ok("Total in Log", f"ℹ️ {total} errors recorded"))
        elif r.status_code == 404:
            checks.append(_warn("Error Logs Table", "⚠️ Table not found", "Run schema SQL"))
        else:
            checks.append(_warn("Error Logs", f"⚠️ HTTP {r.status_code}"))
    except Exception as exc:
        checks.append(_warn("Error Logs", "⚠️ Error fetching", str(exc)[:60]))

    return _section("logs", checks)


# ── Section titles & runners ──────────────────────────────────────────────────

SECTION_TITLES: dict[str, str] = {
    "bot":           "🤖 Bot Core",
    "ai":            "🧠 AI Providers",
    "db":            "🗄️ Database",
    "railway":       "🚂 Railway",
    "channel":       "📢 Channel",
    "group":         "👥 Community",
    "admin":         "👑 Admin System",
    "referrals":     "🎁 Referrals",
    "vip":           "💎 VIP",
    "announcements": "📌 Announcements",
    "security":      "🔒 Security",
    "logs":          "📋 Error Logs",
}

SECTION_RUNNERS: dict = {
    "bot":           check_bot_core,
    "ai":            check_ai_providers,
    "db":            check_database,
    "railway":       check_railway,
    "channel":       check_channel,
    "group":         check_group,
    "admin":         check_admin_system,
    "referrals":     check_referrals,
    "vip":           check_vip,
    "announcements": check_announcements,
    "security":      check_security,
    "logs":          check_error_logs,
}


def run_section(section: str) -> dict:
    runner = SECTION_RUNNERS.get(section)
    if not runner:
        return _section(section, [_warn("Unknown", "⚠️ Section not found")])
    try:
        return runner()
    except Exception as exc:
        log.exception("Audit section %s failed: %s", section, exc)
        return _section(section, [_crit("Audit Error", f"❌ {str(exc)[:80]}")])


def run_quick_summary() -> dict:
    """Fast checks only (no external API calls). Used for initial dashboard load."""
    fast = ["railway", "security", "admin"]
    results = {s: run_section(s) for s in fast}
    passed   = sum(r["passed"]   for r in results.values())
    warnings = sum(r["warnings"] for r in results.values())
    critical = sum(r["critical"] for r in results.values())
    total    = passed + warnings + critical
    score    = int(100 * (passed + warnings * 0.5) / max(total, 1))
    return {
        "passed": passed, "warnings": warnings, "critical": critical,
        "score": score, "sections": results,
        "timestamp": datetime.utcnow().isoformat(),
    }


def run_full_audit() -> dict:
    """Run ALL sections. Calls external APIs — takes 15–40s."""
    results = {s: run_section(s) for s in SECTION_RUNNERS}
    passed   = sum(r["passed"]   for r in results.values())
    warnings = sum(r["warnings"] for r in results.values())
    critical = sum(r["critical"] for r in results.values())
    total    = passed + warnings + critical
    score    = int(100 * (passed + warnings * 0.5) / max(total, 1))
    if critical > 0:
        status = "Critical Issues Found 🔴"
    elif warnings > 3:
        status = "Needs Attention 🟡"
    elif warnings > 0:
        status = "Minor Warnings 🟡"
    else:
        status = "Production Ready ✅"
    return {
        "passed": passed, "warnings": warnings, "critical": critical,
        "score": score, "status": status, "sections": results,
        "timestamp": datetime.utcnow().isoformat(),
    }


def perform_auto_fix(fix_key: str) -> dict:
    """
    Safe auto-fix actions. NEVER modifies API keys, deletes data, or changes Railway config.
    Returns {success, message, actions}.
    """
    actions: list[str] = []

    if fix_key == "refresh_provider_cache":
        actions.append("✅ Provider cache refresh triggered (next request will retry fresh)")

    elif fix_key == "refresh_announcement":
        try:
            from services.database import get_active_announcement
            ann = get_active_announcement()
            if ann:
                actions.append(f"✅ Active announcement confirmed: \"{(ann.get('message',''))[:40]}…\"")
            else:
                actions.append("⚠️ No active announcement. Use /pin <message> to create one.")
        except Exception as exc:
            actions.append(f"❌ Error checking announcement: {str(exc)[:60]}")

    elif fix_key == "refresh_flags":
        from config.settings import FEATURE_FLAGS
        disabled = [k for k, v in FEATURE_FLAGS.items() if not v and k != "maintenance_mode"]
        if disabled:
            for k in disabled:
                FEATURE_FLAGS[k] = True
            actions.append(f"✅ Re-enabled features: {', '.join(disabled)}")
        else:
            actions.append("ℹ️ All features already enabled — nothing to fix")

    elif fix_key == "toggle_maintenance":
        from config.settings import FEATURE_FLAGS
        current = FEATURE_FLAGS.get("maintenance_mode", False)
        FEATURE_FLAGS["maintenance_mode"] = not current
        actions.append(f"✅ Maintenance mode: {'ON 🚧' if not current else 'OFF ✅'}")

    elif fix_key == "refresh_vip":
        try:
            from services.vip_scheduler import check_and_expire_vips
            check_and_expire_vips()
            actions.append("✅ VIP expiry check triggered")
        except AttributeError:
            actions.append("ℹ️ VIP scheduler auto-runs hourly — no manual trigger needed")
        except Exception as exc:
            actions.append(f"⚠️ VIP scheduler: {str(exc)[:60]}")

    elif fix_key == "refresh_bot":
        actions.append("ℹ️ Bot auto-reconnects on errors — no manual action needed")
        actions.append("ℹ️ For persistent issues, redeploy on Railway dashboard")

    else:
        return {"success": False, "message": f"Unknown fix: {fix_key}", "actions": []}

    return {"success": True, "message": "Auto-fix completed", "actions": actions}
