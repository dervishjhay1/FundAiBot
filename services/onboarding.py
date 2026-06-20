"""
FundzAiBot — Onboarding service.
Handles all DB operations for the onboarding / community join system.
All functions are SYNCHRONOUS — wrap in run_in_executor from async handlers.
"""

import requests
from datetime import datetime
from typing import Any

from services.database import _headers, _url, _safe_get, _safe_post, _patch, _insert, add_bonus_credits
from utils.logger import get_logger

log = get_logger(__name__)

_DB_TIMEOUT = (5, 12)


# ── Onboarding state ──────────────────────────────────────────────────────────

def get_onboarding(user_id: int) -> dict | None:
    """Return the onboarding row for user_id, or None if not found."""
    try:
        r = _safe_get(
            f"{_url('onboarding')}?user_id=eq.{user_id}&limit=1",
            headers=_headers(),
        )
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None
    except Exception as exc:
        log.error("get_onboarding(%s): %s", user_id, exc)
        return None


def init_onboarding(user_id: int, referral_source: str = "direct") -> dict | None:
    """
    Create (or upsert) the onboarding row for a user.
    referral_source: 'direct' | 'bot' | 'channel' | 'group' | 'referral'
    Idempotent — safe to call multiple times.
    """
    try:
        headers = dict(_headers())
        headers["Prefer"] = "resolution=merge-duplicates,return=representation"
        url = f"{_url('onboarding')}?on_conflict=user_id"
        payload = {
            "user_id": user_id,
            "referral_source": referral_source,
            "onboarding_complete": False,
            "channel_joined": False,
            "group_joined": False,
            "channel_reward_given": False,
            "group_reward_given": False,
        }
        r = requests.post(url, headers=headers, json=payload, timeout=_DB_TIMEOUT)
        r.raise_for_status()
        data = r.json()
        return data[0] if data else None
    except Exception as exc:
        log.error("init_onboarding(%s): %s", user_id, exc)
        return None


def mark_channel_joined(user_id: int) -> None:
    try:
        _patch("onboarding", f"user_id=eq.{user_id}", {"channel_joined": True})
    except Exception as exc:
        log.error("mark_channel_joined(%s): %s", user_id, exc)


def mark_group_joined(user_id: int) -> None:
    try:
        _patch("onboarding", f"user_id=eq.{user_id}", {"group_joined": True})
    except Exception as exc:
        log.error("mark_group_joined(%s): %s", user_id, exc)


def mark_onboarding_complete(user_id: int) -> None:
    try:
        _patch("onboarding", f"user_id=eq.{user_id}", {
            "onboarding_complete": True,
            "completed_at": datetime.utcnow().isoformat(),
        })
    except Exception as exc:
        log.error("mark_onboarding_complete(%s): %s", user_id, exc)


def grant_channel_reward(user_id: int) -> bool:
    """Grant channel join reward if not already given. Returns True if granted."""
    try:
        row = get_onboarding(user_id)
        if not row or row.get("channel_reward_given"):
            return False
        from config.settings import ONBOARDING_CHANNEL_REWARD_CHAT, ONBOARDING_CHANNEL_REWARD_IMAGE
        add_bonus_credits(user_id, chat=ONBOARDING_CHANNEL_REWARD_CHAT, image=ONBOARDING_CHANNEL_REWARD_IMAGE)
        _patch("onboarding", f"user_id=eq.{user_id}", {"channel_reward_given": True})
        log.info("Channel join reward granted to user %s", user_id)
        return True
    except Exception as exc:
        log.error("grant_channel_reward(%s): %s", user_id, exc)
        return False


def grant_group_reward(user_id: int) -> bool:
    """Grant group join reward if not already given. Returns True if granted."""
    try:
        row = get_onboarding(user_id)
        if not row or row.get("group_reward_given"):
            return False
        from config.settings import ONBOARDING_GROUP_REWARD_CHAT, ONBOARDING_GROUP_REWARD_IMAGE
        add_bonus_credits(user_id, chat=ONBOARDING_GROUP_REWARD_CHAT, image=ONBOARDING_GROUP_REWARD_IMAGE)
        _patch("onboarding", f"user_id=eq.{user_id}", {"group_reward_given": True})
        log.info("Group join reward granted to user %s", user_id)
        return True
    except Exception as exc:
        log.error("grant_group_reward(%s): %s", user_id, exc)
        return False


def _is_onboarding_table_available() -> bool:
    """Return True if the onboarding table exists and is reachable in Supabase."""
    try:
        r = _safe_get(
            f"{_url('onboarding')}?limit=0",
            headers=_headers(),
        )
        return r.status_code == 200
    except Exception:
        return False


def needs_onboarding(user_id: int, is_new: bool) -> bool:
    """
    Returns True if the user should see the onboarding flow.

    New users always see it. For returning users:
      - If the onboarding table is unavailable (not yet created in Supabase),
        skip onboarding entirely — never trap returning users in an infinite loop.
      - If the table exists but this user has no row yet, show onboarding.
      - If the table exists and onboarding_complete=True, skip.
    """
    if is_new:
        return True

    # Guard: if the onboarding table doesn't exist yet, don't loop returning users.
    if not _is_onboarding_table_available():
        log.warning(
            "needs_onboarding: onboarding table unavailable — skipping for returning user %s. "
            "Run supabase_onboarding_schema.sql in Supabase SQL Editor to enable onboarding.",
            user_id,
        )
        return False

    row = get_onboarding(user_id)
    if not row:
        return True
    return not row.get("onboarding_complete", False)


def get_onboarding_stats() -> dict:
    """Return aggregate onboarding stats for the admin dashboard."""
    try:
        count_headers = {**_headers(), "Prefer": "count=exact"}
        total_r = _safe_get(f"{_url('onboarding')}?select=count", headers=count_headers)
        done_r  = _safe_get(f"{_url('onboarding')}?onboarding_complete=eq.true&select=count", headers=count_headers)
        ch_r    = _safe_get(f"{_url('onboarding')}?channel_joined=eq.true&select=count", headers=count_headers)
        grp_r   = _safe_get(f"{_url('onboarding')}?group_joined=eq.true&select=count", headers=count_headers)
        total   = int(total_r.headers.get("content-range", "0/0").split("/")[-1])
        done    = int(done_r.headers.get("content-range", "0/0").split("/")[-1])
        channel = int(ch_r.headers.get("content-range", "0/0").split("/")[-1])
        group   = int(grp_r.headers.get("content-range", "0/0").split("/")[-1])
        return {"total": total, "complete": done, "channel": channel, "group": group}
    except Exception as exc:
        log.error("get_onboarding_stats: %s", exc)
        return {"total": 0, "complete": 0, "channel": 0, "group": 0}
