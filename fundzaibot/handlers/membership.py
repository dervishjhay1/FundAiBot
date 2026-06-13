"""
FundzAiBot — Force-join membership verification.

Checks that a user is a member of both the official channel and community group
before allowing full bot access.  Results are cached in context.bot_data for
5 minutes to avoid hammering the Telegram API.

If TELEGRAM_CHANNEL_ID or TELEGRAM_GROUP_ID is not set in env, that check is
skipped (verified = True for that entity).

Architecture decision: network/API errors default to True (don't block users
on Telegram outages).  Only an explicit "left" / "kicked" / "banned" status
blocks access.
"""

import asyncio
import time

from telegram import Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config.settings import (
    TELEGRAM_CHANNEL_ID,
    TELEGRAM_CHANNEL_NAME,
    TELEGRAM_CHANNEL_URL,
    TELEGRAM_GROUP_ID,
    TELEGRAM_GROUP_NAME,
    TELEGRAM_GROUP_URL,
)
from utils.logger import get_logger

log = get_logger(__name__)

_CACHE_TTL = 300          # seconds — re-check every 5 minutes
_CACHE_KEY = "membership_v1"

# Statuses that mean "not a member"
_NOT_MEMBER = {"left", "kicked", "banned"}


# ── Core membership check ──────────────────────────────────────────────────────

async def check_membership(bot, user_id: int, bot_data: dict) -> dict:
    """
    Check if user is a member of the configured channel and group.

    Returns:
        {
            "channel": bool,
            "group":   bool,
            "all_ok":  bool,
            "need_channel": bool,   # True only when CHANNEL_ID is set
            "need_group":   bool,   # True only when GROUP_ID is set
        }
    """
    cache = bot_data.setdefault(_CACHE_KEY, {})
    entry = cache.get(user_id)
    if entry and (time.time() - entry["ts"]) < _CACHE_TTL:
        return entry["result"]

    need_channel = bool(TELEGRAM_CHANNEL_ID)
    need_group   = bool(TELEGRAM_GROUP_ID)

    channel_ok = True
    group_ok   = True

    if need_channel:
        try:
            member = await asyncio.wait_for(
                bot.get_chat_member(TELEGRAM_CHANNEL_ID, user_id),
                timeout=6,
            )
            channel_ok = member.status not in _NOT_MEMBER
        except TelegramError as exc:
            # Chat not found or user never interacted → treat as not a member
            if "chat not found" in str(exc).lower() or "user not found" in str(exc).lower():
                channel_ok = False
            else:
                log.warning("Membership check channel error for %s: %s", user_id, exc)
                channel_ok = True   # network blip → don't block
        except Exception as exc:
            log.warning("Membership check channel unexpected error for %s: %s", user_id, exc)
            channel_ok = True       # network blip → don't block

    if need_group:
        try:
            member = await asyncio.wait_for(
                bot.get_chat_member(TELEGRAM_GROUP_ID, user_id),
                timeout=6,
            )
            group_ok = member.status not in _NOT_MEMBER
        except TelegramError as exc:
            if "chat not found" in str(exc).lower() or "user not found" in str(exc).lower():
                group_ok = False
            else:
                log.warning("Membership check group error for %s: %s", user_id, exc)
                group_ok = True
        except Exception as exc:
            log.warning("Membership check group unexpected error for %s: %s", user_id, exc)
            group_ok = True

    result = {
        "channel":      channel_ok,
        "group":        group_ok,
        "all_ok":       channel_ok and group_ok,
        "need_channel": need_channel,
        "need_group":   need_group,
    }

    cache[user_id] = {"ts": time.time(), "result": result}
    log.debug("Membership cache set: user=%s %s", user_id, result)
    return result


def clear_membership_cache(user_id: int, bot_data: dict) -> None:
    """Force a fresh check on the next call."""
    bot_data.get(_CACHE_KEY, {}).pop(user_id, None)


# ── Join-screen message builders ───────────────────────────────────────────────

def join_status_text(status: dict) -> str:
    """
    Build the inline status line shown under the verification prompt.
    e.g. "📢 Channel: ✅   👥 Community: ❌"
    """
    chan_icon  = "✅" if status["channel"] else "❌"
    group_icon = "✅" if status["group"]   else "❌"
    parts = []
    if status["need_channel"]:
        name = TELEGRAM_CHANNEL_NAME or "Channel"
        parts.append(f"📢 {name}: {chan_icon}")
    if status["need_group"]:
        name = TELEGRAM_GROUP_NAME or "Community"
        parts.append(f"👥 {name}: {group_icon}")
    return "   ".join(parts) if parts else ""


# ── ChatMemberHandler — detect when users leave channel/group ─────────────────

async def membership_change_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Triggered whenever a user's status changes in the channel or group.
    Clears their membership cache so the next /start re-checks correctly.
    Optionally DMs them a reminder when they leave.
    """
    chat_member = update.chat_member
    if not chat_member:
        return

    user   = chat_member.new_chat_member.user
    status = chat_member.new_chat_member.status
    chat   = chat_member.chat

    # Clear cache so next check is fresh
    clear_membership_cache(user.id, context.bot_data)

    # If user LEFT the channel or group — send a soft DM reminder
    if status in _NOT_MEMBER:
        chat_name = chat.title or "our community"
        log.info("User %s left/kicked from chat %s (%s)", user.id, chat.id, chat_name)

        # Try to DM the user a reminder (fails silently if they blocked the bot)
        try:
            await context.bot.send_message(
                chat_id=user.id,
                text=(
                    f"👋 <b>Hey {user.first_name or 'there'}!</b>\n\n"
                    f"We noticed you left <b>{chat_name}</b>.\n\n"
                    f"To keep full access to FundzAiBot's features, please stay in "
                    f"our official channel and community group.\n\n"
                    f"Rejoin and tap /start to verify — it only takes a second! 🚀"
                ),
                parse_mode="HTML",
            )
        except Exception:
            pass   # User may have blocked the bot
