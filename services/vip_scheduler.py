"""
FundAiBot — VIP expiry scheduler.
Runs as a daemon thread. Checks for expired VIPs every hour,
downgrades them, and sends a Telegram notification.
"""

import asyncio
import threading
import time
from datetime import datetime

import requests

from config.settings import (
    SUPABASE_URL, SUPABASE_SERVICE_KEY,
    BOT_NAME, TELEGRAM_BOT_TOKEN,
)
from utils.logger import get_logger

log = get_logger(__name__)

_CHECK_INTERVAL = 3600      # check every 60 minutes
_NOTIFY_DAYS_BEFORE = 3     # warn user 3 days before expiry


# ── Supabase helpers (standalone so no circular imports) ──────────────────────

def _hdrs() -> dict:
    return {
        "apikey": SUPABASE_SERVICE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "return=representation",
    }


def _get_expired_vip_users() -> list[dict]:
    """Return VIP users whose vip_expires_at is in the past."""
    try:
        now_iso = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
        url = (
            f"{SUPABASE_URL}/rest/v1/users"
            f"?is_vip=eq.true"
            f"&vip_expires_at=lt.{now_iso}"
            f"&is_banned=eq.false"
            f"&select=user_id,first_name,vip_tier,vip_expires_at"
            f"&limit=200"
        )
        r = requests.get(url, headers=_hdrs(), timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("vip_scheduler._get_expired_vip_users: %s", exc)
        return []


def _get_expiring_soon_users() -> list[dict]:
    """Return VIP users expiring in the next NOTIFY_DAYS_BEFORE days."""
    try:
        from datetime import timedelta
        now = datetime.utcnow()
        window_end = (now + timedelta(days=_NOTIFY_DAYS_BEFORE)).strftime("%Y-%m-%dT%H:%M:%S")
        now_iso = now.strftime("%Y-%m-%dT%H:%M:%S")
        url = (
            f"{SUPABASE_URL}/rest/v1/users"
            f"?is_vip=eq.true"
            f"&vip_expires_at=gt.{now_iso}"
            f"&vip_expires_at=lt.{window_end}"
            f"&notifications=eq.true"
            f"&select=user_id,first_name,vip_tier,vip_expires_at"
            f"&limit=200"
        )
        r = requests.get(url, headers=_hdrs(), timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as exc:
        log.error("vip_scheduler._get_expiring_soon_users: %s", exc)
        return []


def _downgrade_user(user_id: int) -> None:
    """Strip VIP status from a user in Supabase."""
    try:
        url = f"{SUPABASE_URL}/rest/v1/users?user_id=eq.{user_id}"
        requests.patch(url, headers=_hdrs(), json={
            "is_vip": False,
            "vip_tier": None,
            "vip_expires_at": None,
        }, timeout=10)
    except Exception as exc:
        log.error("vip_scheduler._downgrade_user(%s): %s", user_id, exc)


def _already_notified(user_id: int) -> bool:
    """Check if we've already sent an expiry warning this cycle (via context store)."""
    return user_id in _notified_cache


_notified_cache: set[int] = set()


# ── Telegram notification helpers ─────────────────────────────────────────────

def _send_telegram_message(chat_id: int, text: str) -> None:
    """Fire-and-forget Telegram message using raw HTTP (no PTB needed)."""
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        requests.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
        }, timeout=10)
    except Exception as exc:
        log.warning("vip_scheduler._send_telegram_message(%s): %s", chat_id, exc)


# ── Core check logic ──────────────────────────────────────────────────────────

def _run_expiry_check() -> None:
    """One full pass: expire overdue VIPs and warn those expiring soon."""
    log.debug("VIP expiry check started.")

    # 1. Downgrade expired VIPs
    expired = _get_expired_vip_users()
    if expired:
        log.info("VIP expiry check: %d expired user(s) to downgrade.", len(expired))
    for user in expired:
        uid  = user["user_id"]
        name = user.get("first_name") or "there"
        tier = (user.get("vip_tier") or "VIP").capitalize()
        _downgrade_user(uid)
        log.info("VIP expired + downgraded: user_id=%s tier=%s", uid, tier)
        _send_telegram_message(
            uid,
            f"⏰ <b>Your {tier} VIP has expired</b>\n\n"
            f"Hi {name}! Your VIP subscription on {BOT_NAME} has ended.\n\n"
            f"You've been moved back to the free plan.\n\n"
            f"💎 <b>Renew anytime</b> with /subscribe — keep your streak going!\n\n"
            f"<i>Thank you for being a VIP member 🙏</i>"
        )

    # 2. Warn users expiring in ≤ 3 days
    expiring_soon = _get_expiring_soon_users()
    for user in expiring_soon:
        uid = user["user_id"]
        if _already_notified(uid):
            continue
        name = user.get("first_name") or "there"
        tier = (user.get("vip_tier") or "VIP").capitalize()
        try:
            exp_dt = datetime.fromisoformat(
                (user.get("vip_expires_at") or "").replace("Z", "+00:00")
            )
            days_left = max(0, (exp_dt.replace(tzinfo=None) - datetime.utcnow()).days)
        except Exception:
            days_left = _NOTIFY_DAYS_BEFORE

        _notified_cache.add(uid)
        _send_telegram_message(
            uid,
            f"⚠️ <b>{tier} VIP expiring in {days_left} day{'s' if days_left != 1 else ''}</b>\n\n"
            f"Hi {name}! Your {BOT_NAME} VIP subscription expires soon.\n\n"
            f"💎 Tap /subscribe to renew with Telegram Stars and keep your:\n"
            f"  • Increased daily limits\n"
            f"  • Priority AI queue\n"
            f"  • All VIP benefits\n\n"
            f"<i>Renew before it expires to avoid any interruption!</i>"
        )
        log.info("VIP expiry warning sent: user_id=%s days_left=%s", uid, days_left)

    log.debug("VIP expiry check complete. expired=%d warn=%d", len(expired), len(expiring_soon))


# ── Scheduler loop ────────────────────────────────────────────────────────────

def _scheduler_loop() -> None:
    """Background daemon loop. Runs a check immediately, then every hour."""
    log.info("VIP expiry scheduler started (interval=%ds).", _CHECK_INTERVAL)

    # First check after a 30-second warm-up delay (let bot fully start)
    time.sleep(30)

    while True:
        try:
            _run_expiry_check()
        except Exception as exc:
            log.error("VIP expiry check crashed: %s", exc, exc_info=True)
        time.sleep(_CHECK_INTERVAL)


def start_vip_scheduler() -> threading.Thread:
    """Start the VIP expiry scheduler as a background daemon thread."""
    t = threading.Thread(target=_scheduler_loop, name="vip-scheduler", daemon=True)
    t.start()
    log.info("VIP expiry scheduler thread started.")
    return t
