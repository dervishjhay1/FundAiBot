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

Phase 1 upgrade: membership gate applied to ALL premium commands via
require_membership() decorator, not just /start.
"""

import asyncio
import functools
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.error import TelegramError
from telegram.ext import ContextTypes

from config.settings import (
    TELEGRAM_CHANNEL_ID,
    TELEGRAM_CHANNEL_NAME,
    TELEGRAM_CHANNEL_URL,
    TELEGRAM_GROUP_ID,
    TELEGRAM_GROUP_NAME,
    TELEGRAM_GROUP_URL,
    MEMBERSHIP_GATE_ENABLED,
    is_admin,
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


def membership_gate_keyboard() -> InlineKeyboardMarkup:
    """
    Keyboard shown when a user fails the membership gate on a premium command.
    Gives them direct join links + a verify button.
    """
    rows = []
    if TELEGRAM_CHANNEL_ID and TELEGRAM_CHANNEL_URL:
        rows.append([
            InlineKeyboardButton(
                f"📢 Join {TELEGRAM_CHANNEL_NAME or 'Channel'}",
                url=TELEGRAM_CHANNEL_URL,
            )
        ])
    if TELEGRAM_GROUP_ID and TELEGRAM_GROUP_URL:
        rows.append([
            InlineKeyboardButton(
                f"👥 Join {TELEGRAM_GROUP_NAME or 'Community'}",
                url=TELEGRAM_GROUP_URL,
            )
        ])
    rows.append([
        InlineKeyboardButton("✅ I've Joined — Verify", callback_data="membership:verify"),
    ])
    rows.append([
        InlineKeyboardButton("🏠 Main Menu", callback_data="menu:back"),
    ])
    return InlineKeyboardMarkup(rows)


# ── Membership gate decorator ──────────────────────────────────────────────────

def require_membership(func):
    """
    Decorator: gate any premium command behind membership verification.

    Rules:
    - Only active when MEMBERSHIP_GATE_ENABLED=true AND at least one community
      (channel or group) is configured.
    - Admins always bypass the gate.
    - Private chats only — groups/channels are not gated.
    - On failure: show a friendly join prompt with direct links + verify button.
    - Cache is respected (5 min TTL) to avoid hammering Telegram API.
    """
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        # Only gate when feature is enabled and communities are configured
        if not MEMBERSHIP_GATE_ENABLED:
            return await func(update, context)
        if not (TELEGRAM_CHANNEL_ID or TELEGRAM_GROUP_ID):
            return await func(update, context)

        user = update.effective_user
        if not user:
            return await func(update, context)

        # Admins always bypass
        if is_admin(user.id):
            return await func(update, context)

        # Only gate private chats
        chat = update.effective_chat
        if chat and chat.type != "private":
            return await func(update, context)

        # Check membership
        status = await check_membership(context.bot, user.id, context.bot_data)

        if status["all_ok"]:
            return await func(update, context)

        # Build gate message
        status_line = join_status_text(status)
        parts = []
        if not status["channel"] and status["need_channel"]:
            parts.append(f"📢 <b>{TELEGRAM_CHANNEL_NAME or 'Official Channel'}</b>")
        if not status["group"] and status["need_group"]:
            parts.append(f"👥 <b>{TELEGRAM_GROUP_NAME or 'Community Group'}</b>")
        missing_str = " and ".join(parts)

        gate_text = (
            f"🔒 <b>Community Membership Required</b>\n\n"
            f"To use FundzAiBot's features, you need to join our community first.\n\n"
            f"<b>Please join:</b>\n{chr(10).join('  ' + p for p in parts)}\n\n"
        )
        if status_line:
            gate_text += f"<b>Your status:</b> {status_line}\n\n"
        gate_text += (
            f"After joining, tap <b>✅ I've Joined — Verify</b> to unlock access instantly! 🚀"
        )

        msg = update.effective_message
        if msg:
            await msg.reply_text(
                gate_text,
                parse_mode="HTML",
                reply_markup=membership_gate_keyboard(),
            )
        log.info(
            "Membership gate blocked: user=%s command=%s channel=%s group=%s",
            user.id, func.__name__, status["channel"], status["group"],
        )
        return None

    return wrapper


# ── Membership verify callback ──────────────────────────────────────────────────

async def handle_membership_verify_callback(query, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Callback: user tapped '✅ I've Joined — Verify' on the membership gate.
    Re-checks membership (clears cache first), then either unlocks or re-shows gate.
    """
    user = query.from_user
    # Force fresh check
    clear_membership_cache(user.id, context.bot_data)
    status = await check_membership(context.bot, user.id, context.bot_data)

    if status["all_ok"]:
        await query.answer("✅ Verified! Full access unlocked.", show_alert=False)
        try:
            from utils.keyboards import main_menu
            await query.edit_message_text(
                f"✅ <b>Membership verified!</b>\n\n"
                f"Welcome to FundzAiBot! 🚀 All features are now unlocked.\n\n"
                f"Tap any command or use the menu below:",
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
        except Exception:
            pass
    else:
        status_line = join_status_text(status)
        await query.answer(
            "⚠️ We couldn't verify your membership yet.\n"
            "Please join using the links above, then try again!",
            show_alert=True,
        )
        try:
            await query.edit_message_reply_markup(
                reply_markup=membership_gate_keyboard()
            )
        except Exception:
            pass
    log.info(
        "Membership verify callback: user=%s all_ok=%s", user.id, status["all_ok"]
    )


# ── ChatMemberHandler — detect when users leave channel/group ─────────────────

async def membership_change_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """
    Triggered whenever a user's status changes in the channel or group.
    Clears their membership cache so the next command re-checks correctly.
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
