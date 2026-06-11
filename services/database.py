"""
FundAiBot — Supabase database service.
All user, referral, history, credit, and log operations go through here.

All functions are SYNCHRONOUS (use requests).
Call them from async handlers via run_in_executor where response time matters.
"""

import json
import time
from datetime import datetime, date, timedelta
from typing import Any

import requests

from config.settings import SUPABASE_URL, SUPABASE_SERVICE_KEY, FREE_DAILY_CHAT, FREE_DAILY_IMAGE
from utils.logger import get_logger

log = get_logger(__name__)

# ── Supabase REST client ──────────────────────────────────────────────────────

_DB_TIMEOUT = (5, 12)   # (connect_timeout, read_timeout) in seconds


def _headers() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


def _rpc(fn: str, params: dict) -> Any:
    url = f"{SUPABASE_URL}/rest/v1/rpc/{fn}"
    r = requests.post(url, headers=_headers(), json=params, timeout=_DB_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _safe_get(url: str, **kwargs) -> requests.Response:
    """GET with retry on transient errors."""
    last_exc = None
    for attempt in range(3):
        try:
            return requests.get(url, timeout=_DB_TIMEOUT, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise last_exc


def _safe_post(url: str, **kwargs) -> requests.Response:
    """POST with retry on transient errors."""
    last_exc = None
    for attempt in range(3):
        try:
            return requests.post(url, timeout=_DB_TIMEOUT, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise last_exc


def _safe_patch(url: str, **kwargs) -> requests.Response:
    """PATCH with retry on transient errors."""
    last_exc = None
    for attempt in range(3):
        try:
            return requests.patch(url, timeout=_DB_TIMEOUT, **kwargs)
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_exc = exc
            if attempt < 2:
                time.sleep(0.5 * (attempt + 1))
    raise last_exc


def _upsert(table: str, data: dict, on_conflict: str = "user_id") -> dict | None:
    headers = dict(_headers())
    headers["Prefer"] = "resolution=merge-duplicates,return=representation"
    r = _safe_post(
        f"{_url(table)}?on_conflict={on_conflict}",
        headers=headers,
        json=data,
    )
    r.raise_for_status()
    result = r.json()
    return result[0] if result else None


def _insert(table: str, data: dict) -> dict | None:
    r = _safe_post(_url(table), headers=_headers(), json=data)
    r.raise_for_status()
    result = r.json()
    return result[0] if isinstance(result, list) and result else None


def _patch(table: str, filters: str, data: dict) -> None:
    r = _safe_patch(f"{_url(table)}?{filters}", headers=_headers(), json=data)
    r.raise_for_status()


# ── Schema bootstrap ──────────────────────────────────────────────────────────

def bootstrap_schema() -> None:
    """
    Check all required tables exist via the Supabase REST API.
    Tables must be created once via the Supabase SQL Editor using supabase_schema.sql.
    Logs a clear warning for any missing table but does NOT crash the bot.
    """
    tables = ["users", "user_credits", "conversations", "image_history", "referrals", "error_logs"]
    missing = []
    for table in tables:
        try:
            r = _safe_get(f"{_url(table)}?limit=1", headers=_headers())
            if r.status_code == 200:
                pass
            elif "does not exist" in r.text or r.status_code == 404:
                missing.append(table)
                log.warning("Table '%s' missing — run supabase_schema.sql in Supabase SQL Editor.", table)
            else:
                log.warning("Table '%s' check returned %s: %s", table, r.status_code, r.text[:100])
        except Exception as exc:
            log.warning("Could not check table '%s': %s", table, exc)

    if missing:
        log.warning(
            "⚠️  %d table(s) missing: %s\n"
            "   Open Supabase → SQL Editor → paste supabase_schema.sql → Run",
            len(missing), ", ".join(missing),
        )
    else:
        log.info("✅ All Supabase tables verified.")


# ── VIP expiry helper ─────────────────────────────────────────────────────────

def check_and_fix_vip_expiry(user: dict) -> bool:
    """
    Check if a VIP user's subscription has expired.
    If expired, downgrade them in the DB and return False.
    Returns True if the user is genuinely VIP (not expired).
    """
    if not user.get("is_vip"):
        return False
    expires_at = user.get("vip_expires_at")
    if not expires_at:
        return True  # No expiry set → assume perpetual (admin-granted)
    try:
        # Handle both Z-suffix and offset-aware strings
        exp_str = expires_at.replace("Z", "").replace("+00:00", "")
        exp_dt = datetime.fromisoformat(exp_str)
        if datetime.utcnow() > exp_dt:
            uid = user.get("user_id")
            log.info("VIP expired for user %s — downgrading to free", uid)
            update_user(uid, is_vip=False, vip_tier=None, vip_expires_at=None)
            return False
    except Exception as exc:
        log.warning("VIP expiry check failed for user %s: %s", user.get("user_id"), exc)
    return True


# ── User operations ───────────────────────────────────────────────────────────

def get_user(user_id: int) -> dict | None:
    try:
        r = _safe_get(
            f"{_url('users')}?user_id=eq.{user_id}&limit=1",
            headers=_headers(),
        )
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None
    except Exception as exc:
        log.error("get_user(%s): %s", user_id, exc)
        return None


def upsert_user(user_id: int, **fields) -> dict | None:
    try:
        payload = {
            "user_id": user_id,
            "referral_code": f"REF{user_id}",
            "last_seen": datetime.utcnow().isoformat(),
            **fields,
        }
        return _upsert("users", payload, on_conflict="user_id")
    except Exception as exc:
        log.error("upsert_user(%s): %s", user_id, exc)
        return None


def get_or_create_user(user_id: int, first_name: str = "", last_name: str = "", username: str = "") -> dict:
    user = get_user(user_id)
    if not user:
        upsert_user(user_id, first_name=first_name, last_name=last_name, username=username)
        ensure_credits(user_id)
        user = get_user(user_id) or {}
    else:
        upsert_user(user_id, first_name=first_name, last_name=last_name, username=username)
    return user


def update_user(user_id: int, **fields) -> None:
    try:
        _patch("users", f"user_id=eq.{user_id}", {**fields, "last_seen": datetime.utcnow().isoformat()})
    except Exception as exc:
        log.error("update_user(%s): %s", user_id, exc)


def ban_user(user_id: int, reason: str = "", banned: bool = True) -> None:
    update_user(user_id, is_banned=banned, ban_reason=reason if banned else None)


def activate_vip(user_id: int, tier: str = "basic", days: int = 30) -> None:
    """Activate VIP for a user after successful Telegram Stars payment."""
    expires_at = (datetime.utcnow() + timedelta(days=days)).isoformat()
    update_user(
        user_id,
        is_vip=True,
        vip_tier=tier,
        vip_expires_at=expires_at,
    )
    log.info("VIP activated: user=%s tier=%s days=%s expires=%s", user_id, tier, days, expires_at)


def get_all_users(limit: int = 200, offset: int = 0) -> list[dict]:
    try:
        r = _safe_get(
            f"{_url('users')}?order=created_at.desc&limit={limit}&offset={offset}",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("get_all_users: %s", exc)
        return []


def get_banned_users() -> list[dict]:
    try:
        r = _safe_get(f"{_url('users')}?is_banned=eq.true&limit=100", headers=_headers())
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("get_banned_users: %s", exc)
        return []


def count_users() -> dict:
    try:
        count_headers = {**_headers(), "Prefer": "count=exact"}
        total_r = _safe_get(f"{_url('users')}?select=count", headers=count_headers)
        vip_r = _safe_get(f"{_url('users')}?is_vip=eq.true&select=count", headers=count_headers)
        ban_r = _safe_get(f"{_url('users')}?is_banned=eq.true&select=count", headers=count_headers)
        total = int(total_r.headers.get("content-range", "0/0").split("/")[-1])
        vip = int(vip_r.headers.get("content-range", "0/0").split("/")[-1])
        banned = int(ban_r.headers.get("content-range", "0/0").split("/")[-1])
        return {"total": total, "vip": vip, "banned": banned, "free": total - vip}
    except Exception as exc:
        log.error("count_users: %s", exc)
        return {"total": 0, "vip": 0, "banned": 0, "free": 0}


# ── Credit operations ─────────────────────────────────────────────────────────

def ensure_credits(user_id: int) -> None:
    """Ensure a credit row exists for user_id."""
    try:
        _upsert("user_credits", {
            "user_id": user_id,
            "chat_today": 0,
            "image_today": 0,
            "chat_total": 0,
            "image_total": 0,
            "bonus_chat": 0,
            "bonus_image": 0,
            "last_reset": date.today().isoformat(),
        }, on_conflict="user_id")
    except Exception as exc:
        log.error("ensure_credits(%s): %s", user_id, exc)

# Keep private alias for internal callers
_ensure_credits = ensure_credits


def get_credits(user_id: int) -> dict:
    try:
        r = _safe_get(f"{_url('user_credits')}?user_id=eq.{user_id}&limit=1", headers=_headers())
        r.raise_for_status()
        data = r.json()
        if data:
            row = data[0]
            if row.get("last_reset") != date.today().isoformat():
                _reset_daily(user_id)
                row["chat_today"] = 0
                row["image_today"] = 0
            return row
        ensure_credits(user_id)
        return {"chat_today": 0, "image_today": 0, "chat_total": 0, "image_total": 0, "bonus_chat": 0, "bonus_image": 0}
    except Exception as exc:
        log.error("get_credits(%s): %s", user_id, exc)
        return {"chat_today": 0, "image_today": 0, "chat_total": 0, "image_total": 0, "bonus_chat": 0, "bonus_image": 0}


def _reset_daily(user_id: int) -> None:
    try:
        _patch("user_credits", f"user_id=eq.{user_id}", {
            "chat_today": 0,
            "image_today": 0,
            "last_reset": date.today().isoformat(),
        })
    except Exception as exc:
        log.error("_reset_daily(%s): %s", user_id, exc)


def can_use_chat(user_id: int, is_vip: bool) -> tuple[bool, str]:
    from config.settings import FREE_DAILY_CHAT, VIP_DAILY_CHAT, is_admin
    if is_admin(user_id):
        return True, ""  # Admin has unlimited chat
    credits = get_credits(user_id)
    limit = VIP_DAILY_CHAT if is_vip else FREE_DAILY_CHAT
    used = credits.get("chat_today", 0)
    bonus = credits.get("bonus_chat", 0)
    effective_limit = limit + bonus
    if used >= effective_limit:
        return False, f"Daily chat limit reached ({used}/{effective_limit}). Resets at midnight UTC."
    return True, ""


def can_use_image(user_id: int, is_vip: bool) -> tuple[bool, str]:
    from config.settings import FREE_DAILY_IMAGE, VIP_DAILY_IMAGE, is_admin
    if is_admin(user_id):
        return True, ""  # Admin has unlimited image generation
    credits = get_credits(user_id)
    limit = VIP_DAILY_IMAGE if is_vip else FREE_DAILY_IMAGE
    used = credits.get("image_today", 0)
    bonus = credits.get("bonus_image", 0)
    effective_limit = limit + bonus
    if used >= effective_limit:
        return False, f"Daily image limit reached ({used}/{effective_limit}). Resets at midnight UTC."
    return True, ""


def increment_chat(user_id: int) -> None:
    """
    Increment chat_today and chat_total.
    Uses Supabase RPC for atomic increment when available, falls back to read+patch.
    """
    try:
        # Try atomic RPC first (requires the RPC function from supabase_schema.sql)
        _rpc("increment_chat", {"uid": user_id})
        return
    except Exception:
        pass
    # Fallback: read-then-patch (may lose counts under high concurrency, acceptable for low-volume bots)
    try:
        credits = get_credits(user_id)
        _patch("user_credits", f"user_id=eq.{user_id}", {
            "chat_today": credits.get("chat_today", 0) + 1,
            "chat_total": credits.get("chat_total", 0) + 1,
        })
    except Exception as exc:
        log.error("increment_chat(%s): %s", user_id, exc)


def increment_image(user_id: int) -> None:
    """
    Increment image_today and image_total.
    Uses Supabase RPC for atomic increment when available, falls back to read+patch.
    """
    try:
        _rpc("increment_image", {"uid": user_id})
        return
    except Exception:
        pass
    try:
        credits = get_credits(user_id)
        _patch("user_credits", f"user_id=eq.{user_id}", {
            "image_today": credits.get("image_today", 0) + 1,
            "image_total": credits.get("image_total", 0) + 1,
        })
    except Exception as exc:
        log.error("increment_image(%s): %s", user_id, exc)


def add_bonus_credits(user_id: int, chat: int = 0, image: int = 0) -> None:
    try:
        credits = get_credits(user_id)
        _patch("user_credits", f"user_id=eq.{user_id}", {
            "bonus_chat": credits.get("bonus_chat", 0) + chat,
            "bonus_image": credits.get("bonus_image", 0) + image,
        })
    except Exception as exc:
        log.error("add_bonus_credits(%s): %s", user_id, exc)


def set_bonus_credits(user_id: int, chat: int | None = None, image: int | None = None) -> None:
    try:
        data = {}
        if chat is not None:
            data["bonus_chat"] = chat
        if image is not None:
            data["bonus_image"] = image
        if data:
            _patch("user_credits", f"user_id=eq.{user_id}", data)
    except Exception as exc:
        log.error("set_bonus_credits(%s): %s", user_id, exc)


def get_total_stats() -> dict:
    try:
        r = _safe_get(f"{_url('user_credits')}?select=chat_total,image_total&limit=10000", headers=_headers())
        r.raise_for_status()
        rows = r.json()
        return {
            "total_chats": sum(row.get("chat_total", 0) for row in rows),
            "total_images": sum(row.get("image_total", 0) for row in rows),
        }
    except Exception as exc:
        log.error("get_total_stats: %s", exc)
        return {"total_chats": 0, "total_images": 0}


# ── Conversation memory ───────────────────────────────────────────────────────

def save_message(user_id: int, role: str, content: str) -> None:
    try:
        _insert("conversations", {
            "user_id": user_id,
            "role": role,
            "content": content[:4000],
        })
    except Exception as exc:
        log.error("save_message(%s): %s", user_id, exc)


def get_conversation(user_id: int, limit: int = 20) -> list[dict]:
    try:
        r = _safe_get(
            f"{_url('conversations')}?user_id=eq.{user_id}&order=created_at.desc&limit={limit}",
            headers=_headers(),
        )
        r.raise_for_status()
        rows = r.json()
        return [{"role": m["role"], "content": m["content"]} for m in reversed(rows)]
    except Exception as exc:
        log.error("get_conversation(%s): %s", user_id, exc)
        return []


def clear_conversation(user_id: int) -> None:
    try:
        requests.delete(
            f"{_url('conversations')}?user_id=eq.{user_id}",
            headers=_headers(),
            timeout=_DB_TIMEOUT,
        )
    except Exception as exc:
        log.error("clear_conversation(%s): %s", user_id, exc)


def set_system_prompt(user_id: int, style: str) -> None:
    style_prompts = {
        "default":  "You are FundAiBot, a helpful, smart, and friendly AI assistant. Be concise and clear.",
        "teacher":  "You are FundAiBot in Teacher mode. Explain everything step by step with examples, as if teaching a curious student.",
        "comedian": "You are FundAiBot in Comedian mode. Be witty, funny, and entertaining while still being genuinely helpful. Add jokes naturally.",
        "scientist":"You are FundAiBot in Scientist mode. Provide precise, evidence-based, technical answers referencing scientific concepts.",
        "writer":   "You are FundAiBot in Writer mode. Respond with creative, eloquent language, rich descriptions, and storytelling flair.",
        "business": "You are FundAiBot in Business mode. Be concise, professional, and focused on practical, actionable business advice.",
        "coder":    "You are FundAiBot in Coder mode. Focus on clean, well-commented code solutions. Always explain your code.",
        "creative": "You are FundAiBot in Creative mode. Think outside the box. Be imaginative, original, and inspiring in all responses.",
    }
    prompt = style_prompts.get(style, style_prompts["default"])
    clear_conversation(user_id)
    save_message(user_id, "system", prompt)


# ── Image history ─────────────────────────────────────────────────────────────

def save_image(user_id: int, prompt: str, style: str, model: str, image_url: str = "") -> None:
    try:
        _insert("image_history", {
            "user_id": user_id,
            "prompt": prompt[:500],
            "style": style,
            "model": model,
            "image_url": image_url,
        })
    except Exception as exc:
        log.error("save_image(%s): %s", user_id, exc)


def get_image_history(user_id: int, limit: int = 10) -> list[dict]:
    try:
        r = _safe_get(
            f"{_url('image_history')}?user_id=eq.{user_id}&order=created_at.desc&limit={limit}",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("get_image_history(%s): %s", user_id, exc)
        return []


def get_all_images(limit: int = 50) -> list[dict]:
    try:
        r = _safe_get(
            f"{_url('image_history')}?order=created_at.desc&limit={limit}",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("get_all_images: %s", exc)
        return []


# ── Referral operations ───────────────────────────────────────────────────────

def record_referral(referrer_id: int, new_user_id: int) -> bool:
    from config.settings import is_admin
    if referrer_id == new_user_id:
        return False
    # Admin cannot be a referrer or referred — no reward manipulation
    if is_admin(referrer_id) or is_admin(new_user_id):
        log.debug("Referral skipped — admin involved (referrer=%s referred=%s)", referrer_id, new_user_id)
        return False
    try:
        existing = _safe_get(
            f"{_url('referrals')}?referred_id=eq.{new_user_id}&limit=1",
            headers=_headers(),
        ).json()
        if existing:
            return False
        _insert("referrals", {"referrer_id": referrer_id, "referred_id": new_user_id})
        add_bonus_credits(referrer_id, chat=10, image=2)
        log.info("Referral: %s → %s", referrer_id, new_user_id)
        return True
    except Exception as exc:
        log.error("record_referral: %s", exc)
        return False


def get_referral_count(user_id: int) -> int:
    try:
        r = _safe_get(
            f"{_url('referrals')}?referrer_id=eq.{user_id}&select=count",
            headers={**_headers(), "Prefer": "count=exact"},
        )
        return int(r.headers.get("content-range", "0/0").split("/")[-1])
    except Exception as exc:
        log.error("get_referral_count(%s): %s", user_id, exc)
        return 0


def get_referrals(user_id: int) -> list[dict]:
    try:
        r = _safe_get(
            f"{_url('referrals')}?referrer_id=eq.{user_id}&order=created_at.desc&limit=50",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("get_referrals(%s): %s", user_id, exc)
        return []


# ── Error logging ─────────────────────────────────────────────────────────────

def log_error(error_type: str, message: str, user_id: int | None = None, context: dict | None = None) -> None:
    try:
        _insert("error_logs", {
            "user_id": user_id,
            "error_type": error_type,
            "message": str(message)[:2000],
            "context": json.dumps(context or {}),
        })
    except Exception:
        pass


def get_recent_errors(limit: int = 20) -> list[dict]:
    try:
        r = _safe_get(
            f"{_url('error_logs')}?order=created_at.desc&limit={limit}",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("get_recent_errors: %s", exc)
        return []


def clear_error_logs() -> int:
    """Delete all rows from error_logs. Returns count deleted."""
    try:
        count_r = _safe_get(
            f"{_url('error_logs')}?select=count",
            headers={**_headers(), "Prefer": "count=exact"},
        )
        count = int(count_r.headers.get("content-range", "0/0").split("/")[-1])
        requests.delete(
            f"{_url('error_logs')}?id=gt.0",
            headers=_headers(),
            timeout=_DB_TIMEOUT,
        )
        log.info("Cleared %d error log entries", count)
        return count
    except Exception as exc:
        log.error("clear_error_logs: %s", exc)
        return 0


# ── Admin account management (multi-admin) ────────────────────────────────────

def get_admin_accounts() -> list[dict]:
    """Return all secondary admin rows from Supabase."""
    try:
        r = _safe_get(
            f"{_url('admin_accounts')}?order=created_at.asc",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("get_admin_accounts: %s", exc)
        return []


def load_secondary_admins() -> set[int]:
    """Load secondary admin IDs from Supabase into config.settings.SECONDARY_ADMINS."""
    from config import settings as _s
    try:
        rows = get_admin_accounts()
        ids = {int(row["user_id"]) for row in rows}
        _s.SECONDARY_ADMINS.clear()
        _s.SECONDARY_ADMINS.update(ids)
        log.info("Loaded %d secondary admin(s) from DB", len(ids))
        return ids
    except Exception as exc:
        log.error("load_secondary_admins: %s", exc)
        return set()


def add_admin_account(user_id: int, added_by: int) -> bool:
    """Add a secondary admin (persisted in Supabase + in-memory set)."""
    from config import settings as _s
    try:
        _upsert(
            "admin_accounts",
            {"user_id": user_id, "added_by": added_by},
            on_conflict="user_id",
        )
        _s.SECONDARY_ADMINS.add(int(user_id))
        log.info("Admin account added: user=%s by=%s", user_id, added_by)
        return True
    except Exception as exc:
        log.error("add_admin_account(%s): %s", user_id, exc)
        return False


def remove_admin_account(user_id: int) -> bool:
    """Remove a secondary admin (persisted in Supabase + in-memory set)."""
    from config import settings as _s
    try:
        requests.delete(
            f"{_url('admin_accounts')}?user_id=eq.{user_id}",
            headers=_headers(),
            timeout=_DB_TIMEOUT,
        )
        _s.SECONDARY_ADMINS.discard(int(user_id))
        log.info("Admin account removed: user=%s", user_id)
        return True
    except Exception as exc:
        log.error("remove_admin_account(%s): %s", user_id, exc)
        return False


# ── Daily usage reset (admin) ─────────────────────────────────────────────────

def reset_daily_usage(user_id: int) -> None:
    """Reset a user's today chat/image counters to 0."""
    try:
        _patch("user_credits", f"user_id=eq.{user_id}", {
            "chat_today":  0,
            "image_today": 0,
            "last_reset":  date.today().isoformat(),
        })
    except Exception as exc:
        log.error("reset_daily_usage(%s): %s", user_id, exc)


# ── Announcements ─────────────────────────────────────────────────────────────

def get_active_announcement() -> dict | None:
    """Return the currently pinned announcement, or None."""
    try:
        r = _safe_get(
            f"{_url('announcements')}?is_active=eq.true&order=created_at.desc&limit=1",
            headers=_headers(),
        )
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None
    except Exception as exc:
        log.error("get_active_announcement: %s", exc)
        return None


def create_announcement(
    message: str,
    photo_url: str | None = None,
    created_by: int | None = None,
) -> dict | None:
    """Deactivate existing pins then create a new active announcement."""
    deactivate_announcements()
    try:
        row = _insert("announcements", {
            "message":    message[:4000],
            "photo_url":  photo_url,
            "is_active":  True,
            "created_by": created_by,
        })
        return row
    except Exception as exc:
        log.error("create_announcement: %s", exc)
        return None


def update_active_announcement(message: str) -> bool:
    """Edit the text of the currently active announcement."""
    try:
        _patch("announcements", "is_active=eq.true", {
            "message":    message[:4000],
            "updated_at": datetime.utcnow().isoformat(),
        })
        return True
    except Exception as exc:
        log.error("update_active_announcement: %s", exc)
        return False


def set_photo_on_announcement(photo_url: str | None) -> bool:
    """Attach or remove a photo URL from the active announcement."""
    try:
        _patch("announcements", "is_active=eq.true", {
            "photo_url":  photo_url,
            "updated_at": datetime.utcnow().isoformat(),
        })
        return True
    except Exception as exc:
        log.error("set_photo_on_announcement: %s", exc)
        return False


def deactivate_announcements() -> None:
    """Mark all active announcements as inactive."""
    try:
        _patch("announcements", "is_active=eq.true", {"is_active": False})
    except Exception as exc:
        log.error("deactivate_announcements: %s", exc)


def get_announcement_history(limit: int = 10) -> list[dict]:
    try:
        r = _safe_get(
            f"{_url('announcements')}?order=created_at.desc&limit={limit}",
            headers=_headers(),
        )
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("get_announcement_history: %s", exc)
        return []
