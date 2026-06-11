"""
FundzAiBot — Announcement service.

Stores a single "active" pinned announcement plus a history log in Supabase.
All functions are SYNCHRONOUS — call via run_in_executor from async handlers.

Table DDL (run once in Supabase SQL Editor):
  See supabase_announcements_schema.sql
"""

import requests as _req

from config.settings import SUPABASE_URL, SUPABASE_SERVICE_KEY
from utils.logger import get_logger

log = get_logger(__name__)

_DB_TIMEOUT = (5, 12)


def _headers() -> dict:
    return {
        "apikey":        SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "return=representation",
    }


def _url(table: str) -> str:
    return f"{SUPABASE_URL}/rest/v1/{table}"


# ── Default announcement shown until admin sets a custom one ──────────────────

DEFAULT_ANNOUNCEMENT = (
    "📢 <b>Announcement from FundzAiBot:</b>\n\n"
    "⚠️ Note: @FundzAiBot is actively updated and improved daily to deliver "
    "better performance, features, and stability. If you experience any issues "
    "or notice a feature not working properly, please contact @Biodunfund for "
    "support and further assistance. 💙"
)


# ── CRUD ──────────────────────────────────────────────────────────────────────

def get_active_announcement() -> dict | None:
    """
    Return the current active announcement row, or None if none is set.
    Falls back gracefully if the table doesn't exist yet.
    """
    try:
        r = _req.get(
            f"{_url('announcements')}?is_active=eq.true&order=created_at.desc&limit=1",
            headers=_headers(),
            timeout=_DB_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            return data[0] if data else None
        if r.status_code in (400, 404) or "does not exist" in r.text:
            log.debug("announcements table missing — returning default")
            return None
        log.warning("get_active_announcement: HTTP %s", r.status_code)
        return None
    except Exception as exc:
        log.warning("get_active_announcement: %s", exc)
        return None


def set_announcement(message: str, set_by: int, photo_url: str = "") -> tuple[bool, str]:
    """
    Deactivate any current announcement and insert a new active one.
    Returns (success, result_message).
    """
    try:
        # Deactivate all existing active announcements
        _req.patch(
            f"{_url('announcements')}?is_active=eq.true",
            headers=_headers(),
            json={"is_active": False},
            timeout=_DB_TIMEOUT,
        )
        # Insert new active announcement
        r = _req.post(
            _url("announcements"),
            headers=_headers(),
            json={
                "message":   message,
                "photo_url": photo_url,
                "is_active": True,
                "set_by":    set_by,
            },
            timeout=_DB_TIMEOUT,
        )
        if r.status_code in (200, 201):
            log.info("Announcement set by admin %s", set_by)
            return True, "✅ Pinned announcement updated."
        return False, f"❌ DB error: HTTP {r.status_code}"
    except Exception as exc:
        log.error("set_announcement: %s", exc)
        return False, f"❌ Error: {exc}"


def unpin_announcement(unset_by: int) -> tuple[bool, str]:
    """Deactivate all active announcements."""
    try:
        r = _req.patch(
            f"{_url('announcements')}?is_active=eq.true",
            headers=_headers(),
            json={"is_active": False},
            timeout=_DB_TIMEOUT,
        )
        if r.status_code in (200, 204):
            log.info("Announcement unpinned by admin %s", unset_by)
            return True, "✅ Pinned announcement removed."
        return False, f"❌ DB error: HTTP {r.status_code}"
    except Exception as exc:
        log.error("unpin_announcement: %s", exc)
        return False, f"❌ Error: {exc}"


def update_announcement_text(message: str, updated_by: int) -> tuple[bool, str]:
    """Edit the text of the currently active announcement in place."""
    try:
        r = _req.patch(
            f"{_url('announcements')}?is_active=eq.true",
            headers=_headers(),
            json={"message": message},
            timeout=_DB_TIMEOUT,
        )
        if r.status_code in (200, 204):
            log.info("Announcement text updated by admin %s", updated_by)
            return True, "✅ Announcement text updated."
        return False, f"❌ No active announcement to update. Use /pin first."
    except Exception as exc:
        log.error("update_announcement_text: %s", exc)
        return False, f"❌ Error: {exc}"


def attach_photo(photo_url: str, updated_by: int) -> tuple[bool, str]:
    """Attach a photo URL to the active announcement."""
    try:
        r = _req.patch(
            f"{_url('announcements')}?is_active=eq.true",
            headers=_headers(),
            json={"photo_url": photo_url},
            timeout=_DB_TIMEOUT,
        )
        if r.status_code in (200, 204):
            log.info("Announcement photo attached by admin %s", updated_by)
            return True, "✅ Photo attached to announcement."
        return False, "❌ No active announcement. Use /pin first."
    except Exception as exc:
        log.error("attach_photo: %s", exc)
        return False, f"❌ Error: {exc}"


def list_announcement_history(limit: int = 10) -> list[dict]:
    """Return recent announcements ordered newest first."""
    try:
        r = _req.get(
            f"{_url('announcements')}?order=created_at.desc&limit={limit}",
            headers=_headers(),
            timeout=_DB_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json()
        return []
    except Exception as exc:
        log.error("list_announcement_history: %s", exc)
        return []


def bootstrap_announcements() -> None:
    """Seed the default announcement if the table is empty. Non-fatal."""
    try:
        r = _req.get(
            f"{_url('announcements')}?limit=1",
            headers=_headers(),
            timeout=_DB_TIMEOUT,
        )
        if r.status_code == 200 and not r.json():
            # Table exists but is empty — seed the default
            _req.post(
                _url("announcements"),
                headers=_headers(),
                json={
                    "message":   DEFAULT_ANNOUNCEMENT,
                    "photo_url": "",
                    "is_active": True,
                    "set_by":    0,
                },
                timeout=_DB_TIMEOUT,
            )
            log.info("Default announcement seeded.")
        elif r.status_code in (400, 404) or "does not exist" in r.text:
            log.warning(
                "announcements table missing — run supabase_announcements_schema.sql. "
                "Default announcement will be shown statically."
            )
    except Exception as exc:
        log.warning("bootstrap_announcements (non-fatal): %s", exc)
