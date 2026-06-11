"""
FundAiBot — Multi-admin management service.

Architecture:
  - OWNER_USER_ID (ADMIN_USER_ID env var) is the permanent, irremovable super-admin.
  - Additional admins are persisted in a Supabase `admins` table.
  - An in-memory set (60 s TTL) prevents a DB round-trip on every message.
  - All public functions are SYNCHRONOUS — call via run_in_executor from async handlers.

Table DDL (run once in Supabase SQL Editor):
  CREATE TABLE IF NOT EXISTS admins (
      user_id    BIGINT PRIMARY KEY,
      role       TEXT NOT NULL DEFAULT 'admin',   -- 'owner' | 'admin'
      added_by   BIGINT,
      username   TEXT,
      created_at TIMESTAMPTZ DEFAULT NOW()
  );
"""

import time
import threading
import requests as _req

from config.settings import OWNER_USER_ID, SUPABASE_URL, SUPABASE_SERVICE_KEY
from utils.logger import get_logger

log = get_logger(__name__)

# ── In-memory cache ───────────────────────────────────────────────────────────

_CACHE_TTL   = 60        # seconds before we re-query Supabase
_lock        = threading.Lock()
_admin_ids:  set[int] = set()
_loaded_at:  float    = 0.0


def _headers() -> dict:
    return {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def _is_stale() -> bool:
    return (time.time() - _loaded_at) > _CACHE_TTL


def _refresh_cache() -> None:
    """Pull admin IDs from Supabase into the in-memory set."""
    global _admin_ids, _loaded_at
    try:
        r = _req.get(
            f"{SUPABASE_URL}/rest/v1/admins?select=user_id",
            headers=_headers(),
            timeout=(5, 10),
        )
        if r.status_code == 200:
            ids: set[int] = {int(row["user_id"]) for row in r.json()}
            ids.add(OWNER_USER_ID)
            with _lock:
                _admin_ids = ids
                _loaded_at = time.time()
            log.debug("Admin cache refreshed — %d admins", len(ids))
        elif r.status_code in (400, 404) and (
            "does not exist" in r.text or "relation" in r.text
        ):
            with _lock:
                _admin_ids = {OWNER_USER_ID}
                _loaded_at = time.time()
            log.warning("admins table missing — only owner has admin access.")
        else:
            log.warning("Admin cache refresh: HTTP %s — %s", r.status_code, r.text[:80])
            with _lock:
                if not _admin_ids:
                    _admin_ids = {OWNER_USER_ID}
                _loaded_at = time.time()
    except Exception as exc:
        log.warning("Admin cache refresh failed: %s", exc)
        with _lock:
            if not _admin_ids:
                _admin_ids = {OWNER_USER_ID}
            _loaded_at = time.time()


# ── Bootstrap ─────────────────────────────────────────────────────────────────

def bootstrap_admins() -> None:
    """
    Ensure the owner row exists in the admins table and prime the cache.
    Called once at bot startup. Non-fatal if Supabase is unreachable.
    """
    try:
        h = dict(_headers())
        h["Prefer"] = "resolution=merge-duplicates,return=representation"
        r = _req.post(
            f"{SUPABASE_URL}/rest/v1/admins?on_conflict=user_id",
            headers=h,
            json={
                "user_id":  OWNER_USER_ID,
                "role":     "owner",
                "added_by": OWNER_USER_ID,
            },
            timeout=(5, 10),
        )
        if r.status_code in (200, 201):
            log.info("Admin bootstrap: owner %s confirmed in admins table.", OWNER_USER_ID)
        elif "does not exist" in r.text or r.status_code == 404:
            log.warning(
                "admins table not found. Run supabase_admin_schema.sql in Supabase SQL Editor.\n"
                "Only ADMIN_USER_ID=%s has admin access until then.", OWNER_USER_ID
            )
        else:
            log.warning("Admin bootstrap: HTTP %s — %s", r.status_code, r.text[:120])
    except Exception as exc:
        log.warning("Admin bootstrap (non-fatal): %s", exc)
    _refresh_cache()


# ── Public permission API ─────────────────────────────────────────────────────

def is_admin(user_id: int) -> bool:
    """
    Fast check using in-memory cache.
    Always True for OWNER_USER_ID regardless of DB state.
    Re-queries Supabase when the cache is older than CACHE_TTL seconds.
    """
    if user_id == OWNER_USER_ID:
        return True
    if _is_stale():
        _refresh_cache()
    with _lock:
        return user_id in _admin_ids


def is_owner(user_id: int) -> bool:
    """True only for the permanent owner (ADMIN_USER_ID env var)."""
    return user_id == OWNER_USER_ID


def get_admin_ids() -> set[int]:
    """Return a snapshot of the current admin ID set."""
    if _is_stale():
        _refresh_cache()
    with _lock:
        return set(_admin_ids)


# ── Admin CRUD ────────────────────────────────────────────────────────────────

def add_admin(user_id: int, added_by: int, username: str = "") -> tuple[bool, str]:
    """
    Promote a user to admin. Only callable by the owner.
    Returns (success: bool, message: str).
    """
    if user_id == OWNER_USER_ID:
        return False, "That user is already the permanent owner."
    try:
        h = dict(_headers())
        h["Prefer"] = "resolution=merge-duplicates,return=representation"
        r = _req.post(
            f"{SUPABASE_URL}/rest/v1/admins?on_conflict=user_id",
            headers=h,
            json={
                "user_id":  user_id,
                "role":     "admin",
                "added_by": added_by,
                "username": username or "",
            },
            timeout=(5, 10),
        )
        if r.status_code in (200, 201):
            with _lock:
                _admin_ids.add(user_id)
            log.info("Admin added: %s by owner %s", user_id, added_by)
            return True, f"✅ User <code>{user_id}</code> is now an admin."
        else:
            msg = ""
            try:
                msg = r.json().get("message", r.text[:100])
            except Exception:
                msg = r.text[:100]
            return False, f"❌ DB error: {msg}"
    except Exception as exc:
        log.error("add_admin(%s): %s", user_id, exc)
        return False, f"❌ Error: {exc}"


def remove_admin(user_id: int) -> tuple[bool, str]:
    """
    Remove an admin. The owner cannot be removed.
    Returns (success: bool, message: str).
    """
    if user_id == OWNER_USER_ID:
        return False, "❌ Cannot remove the permanent owner."
    try:
        r = _req.delete(
            f"{SUPABASE_URL}/rest/v1/admins?user_id=eq.{user_id}&role=neq.owner",
            headers=_headers(),
            timeout=(5, 10),
        )
        if r.status_code in (200, 204):
            with _lock:
                _admin_ids.discard(user_id)
            log.info("Admin removed: %s", user_id)
            return True, f"✅ User <code>{user_id}</code> is no longer an admin."
        else:
            return False, f"❌ DB error: HTTP {r.status_code}"
    except Exception as exc:
        log.error("remove_admin(%s): %s", user_id, exc)
        return False, f"❌ Error: {exc}"


def list_admins() -> list[dict]:
    """Return all admin rows from the DB, ordered by creation date."""
    try:
        r = _req.get(
            f"{SUPABASE_URL}/rest/v1/admins?order=created_at.asc&select=user_id,role,username,added_by,created_at",
            headers=_headers(),
            timeout=(5, 10),
        )
        if r.status_code == 200:
            return r.json()
        log.warning("list_admins: HTTP %s", r.status_code)
        return []
    except Exception as exc:
        log.error("list_admins: %s", exc)
        return []


def invalidate_cache() -> None:
    """Force a cache refresh on the next is_admin() call."""
    global _loaded_at
    with _lock:
        _loaded_at = 0.0
