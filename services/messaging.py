"""
FundzAiBot — Centralised Message Routing

ARCHITECTURE RULE (FINAL — DO NOT BYPASS):
  Three environments. Three completely independent delivery paths.
  These functions MUST NEVER call each other automatically.

  ┌─────────────────────┬────────────────────────────────────────────┐
  │ Function            │ Destination                                │
  ├─────────────────────┼────────────────────────────────────────────┤
  │ send_channel_post() │ TELEGRAM_CHANNEL_ID only                   │
  │ send_group_message()│ TELEGRAM_GROUP_ID only                     │
  │ send_private_message│ user_id (private DM) only                  │
  └─────────────────────┴────────────────────────────────────────────┘

EVENT ROUTING MATRIX:
  • New member joins          → send_group_message()
  • Daily educational content → send_channel_post()
  • CEO / admin report        → send_private_message()
  • User command reply        → send_private_message()
  • Group discussion starter  → send_group_message()
  • Company announcement      → send_channel_post()

No function in this module may import or invoke any other routing
function in this module. Cross-environment message delivery is
ALWAYS an explicit, deliberate admin action only.
"""

from __future__ import annotations

import requests
from utils.logger import get_logger
from config.settings import TELEGRAM_BOT_TOKEN, TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID

log = get_logger(__name__)

_API = "https://api.telegram.org/bot"
_TIMEOUT = (5, 15)


# ── Private helper ─────────────────────────────────────────────────────────────

def _send(chat_id: str | int, text: str, **kwargs) -> dict | None:
    """
    Low-level Telegram sendMessage. Returns parsed JSON on success, None on failure.
    Internal only — callers must use the three public routing functions below.
    """
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        log.debug("messaging._send: missing token or chat_id=%s", chat_id)
        return None
    try:
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", **kwargs}
        r = requests.post(
            f"{_API}{TELEGRAM_BOT_TOKEN}/sendMessage",
            json=payload,
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("result")
        log.warning("messaging._send chat_id=%s HTTP %d: %s", chat_id, r.status_code, r.text[:120])
    except Exception as exc:
        log.warning("messaging._send chat_id=%s error: %s", chat_id, exc)
    return None


def _send_photo(chat_id: str | int, photo: str, caption: str = "", **kwargs) -> dict | None:
    """Low-level sendPhoto. Internal only."""
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        return None
    try:
        payload = {"chat_id": chat_id, "photo": photo, "caption": caption, "parse_mode": "HTML", **kwargs}
        r = requests.post(
            f"{_API}{TELEGRAM_BOT_TOKEN}/sendPhoto",
            json=payload,
            timeout=_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json().get("result")
        log.warning("messaging._send_photo chat_id=%s HTTP %d: %s", chat_id, r.status_code, r.text[:120])
    except Exception as exc:
        log.warning("messaging._send_photo chat_id=%s error: %s", chat_id, exc)
    return None


# ── PUBLIC ROUTING FUNCTIONS ───────────────────────────────────────────────────
# Each function routes to exactly ONE destination. They NEVER call each other.

def send_channel_post(
    text: str,
    photo_url: str = "",
    reply_markup: dict | None = None,
) -> dict | None:
    """
    Publish a post to the OFFICIAL CHANNEL only.

    Use for:
      • Product announcements
      • Daily AI tips
      • Feature releases
      • Educational content
      • Company milestones
      • TestAudit scheduled content

    NEVER use for group messages or private DMs.
    NEVER call send_group_message() or send_private_message() from here.
    """
    if not TELEGRAM_CHANNEL_ID:
        log.debug("send_channel_post: TELEGRAM_CHANNEL_ID not configured")
        return None

    kwargs = {}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup

    if photo_url:
        result = _send_photo(TELEGRAM_CHANNEL_ID, photo_url, caption=text, **kwargs)
    else:
        result = _send(TELEGRAM_CHANNEL_ID, text, **kwargs)

    if result:
        log.info("Channel post sent (msg_id=%s)", result.get("message_id"))
    return result


def send_group_message(
    text: str,
    reply_to_message_id: int | None = None,
    reply_markup: dict | None = None,
) -> dict | None:
    """
    Send a message to the COMMUNITY GROUP only.

    Use for:
      • Welcome messages for new members
      • Discussion starters when group is quiet
      • Moderation actions that need visibility
      • Community engagement questions

    NEVER use for channel content or private DMs.
    NEVER call send_channel_post() or send_private_message() from here.
    """
    if not TELEGRAM_GROUP_ID:
        log.debug("send_group_message: TELEGRAM_GROUP_ID not configured")
        return None

    kwargs = {}
    if reply_to_message_id:
        kwargs["reply_to_message_id"] = reply_to_message_id
    if reply_markup:
        kwargs["reply_markup"] = reply_markup

    result = _send(TELEGRAM_GROUP_ID, text, **kwargs)
    if result:
        log.info("Group message sent (msg_id=%s)", result.get("message_id"))
    return result


def send_private_message(
    user_id: int,
    text: str,
    reply_markup: dict | None = None,
    photo_url: str = "",
) -> dict | None:
    """
    Send a private DM to a specific user.

    Use for:
      • CEO reports and alerts
      • User command replies
      • Customer success follow-ups
      • Admin notifications
      • Inactive user re-engagement

    NEVER use for group posts or channel content.
    NEVER call send_channel_post() or send_group_message() from here.
    """
    if not user_id:
        log.debug("send_private_message: user_id not provided")
        return None

    kwargs = {}
    if reply_markup:
        kwargs["reply_markup"] = reply_markup

    if photo_url:
        result = _send_photo(user_id, photo_url, caption=text, **kwargs)
    else:
        result = _send(user_id, text, **kwargs)

    if result:
        log.debug("Private message sent to user=%s (msg_id=%s)", user_id, result.get("message_id"))
    return result
