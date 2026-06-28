"""
FundzAiBot — Group Handler (TestAudit Community Manager)

ARCHITECTURE: The main bot is COMPLETELY SILENT in group chats.
Only TestAudit (the Operations Manager) speaks inside groups.

No /ai command. No generic AI responses. No @mention AI replies.
Every group interaction goes through TestAudit's community manager persona.

TestAudit behaves like a human professional community manager:
  - Warm, conversational welcome messages
  - Time-appropriate greetings to keep the group alive
  - Smart response system: monitors questions, waits 2-3 min before assisting
  - Proactive engagement when the group goes quiet
  - Anti-spam enforcement remains active
  - @mentions route through TestAudit persona (not generic AI)

Handlers registered:
  group=0  new_member_handler    (STATUS_UPDATE)
  group=1  testaudit_mention_handler  (TEXT, GROUPS)
  group=2  spam_filter           (TEXT, GROUPS)
  group=3  smart_community_handler    (TEXT, GROUPS)
"""

from __future__ import annotations

import asyncio
import html
import re
import time
from datetime import datetime, timedelta

from telegram import (
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Update,
)
from telegram.ext import ContextTypes

from config.settings import BOT_NAME, FEATURE_FLAGS, TELEGRAM_CHANNEL_URL, is_admin
from utils.logger import get_logger

log = get_logger(__name__)


# ── Anti-spam config ───────────────────────────────────────────────────────────

_SCAM_RE = re.compile(
    r"t\.me/[a-zA-Z0-9_]{3,}"
    r"|telegram\.me/"
    r"|bit\.ly/"
    r"|tinyurl\.com/"
    r"|(?i)(free\s+(?:btc|eth|usdt|crypto|money))"
    r"|(?i)(earn\s+\d+\s*(btc|eth|usdt|\$))"
    r"|(?i)(double\s+your\s+(money|bitcoin|crypto))"
    r"|(?i)(guaranteed\s+profit)"
    r"|(?i)(investment\s+returns?)",
)

_WARN_STORE: dict[int, list[datetime]] = {}
_MAX_WARNINGS = 3
_MUTE_HOURS = 1


def _add_warning(user_id: int) -> int:
    now = datetime.utcnow()
    _WARN_STORE.setdefault(user_id, [])
    _WARN_STORE[user_id] = [
        t for t in _WARN_STORE[user_id] if now - t < timedelta(hours=24)
    ]
    _WARN_STORE[user_id].append(now)
    return len(_WARN_STORE[user_id])


# ── New member welcome (TestAudit Community Manager persona) ───────────────────

async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    TestAudit welcomes new group members warmly and naturally.
    No robotic templates. Feels like a real community manager.
    """
    message = update.message
    if not message or not message.new_chat_members:
        return

    chat = update.effective_chat
    if not chat:
        return

    bot_info = await context.bot.get_me()
    bot_uname = bot_info.username or BOT_NAME

    from services.community_manager import get_welcome_message, record_group_post

    for member in message.new_chat_members:
        if member.is_bot:
            continue

        name = html.escape(member.first_name or "there")
        welcome_text = get_welcome_message(name)

        buttons: list[InlineKeyboardButton] = []
        if TELEGRAM_CHANNEL_URL:
            buttons.append(
                InlineKeyboardButton("📢 Official Channel", url=TELEGRAM_CHANNEL_URL)
            )
        buttons.append(
            InlineKeyboardButton(
                f"🤖 Try @{bot_uname}", url=f"https://t.me/{bot_uname}"
            )
        )

        kbd = InlineKeyboardMarkup([buttons]) if buttons else None

        try:
            await message.reply_text(
                welcome_text,
                parse_mode="HTML",
                reply_markup=kbd,
            )
            record_group_post(chat.id)
            log.info("Welcomed member %s in group %s", member.id, chat.id)
        except Exception as exc:
            log.warning("Welcome message failed: %s", exc)


# ── @mention handler (TestAudit persona — not generic AI) ─────────────────────

async def testaudit_mention_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    When the bot is @mentioned in a group, TestAudit responds.
    Conversational, helpful, short — not a generic AI chatbot response.
    Main bot persona is suppressed; only TestAudit speaks.
    """
    message = update.message
    if not message or not message.text:
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or user.is_bot:
        return

    bot_info = await context.bot.get_me()
    bot_uname = (bot_info.username or "").lower()
    text = message.text

    mention_tag = f"@{bot_uname}".lower()
    if mention_tag not in text.lower():
        return

    if not FEATURE_FLAGS.get("chat_enabled", True):
        return

    question = re.sub(
        rf"@{re.escape(bot_uname)}", "", text, flags=re.IGNORECASE
    ).strip()

    name = html.escape(user.first_name or "there")

    if not question:
        await message.reply_text(
            f"Hey {name}! 👋 What can I help you with?",
            parse_mode="HTML",
        )
        return

    from services.ai_service import get_ai_response
    from services.community_manager import (
        build_community_manager_system_prompt,
        record_group_post,
        record_human_activity,
    )

    record_human_activity(chat.id)

    messages = [
        {"role": "system", "content": build_community_manager_system_prompt()},
        {"role": "user", "content": question},
    ]

    loop = asyncio.get_running_loop()
    try:
        response, provider = await loop.run_in_executor(
            None,
            lambda: get_ai_response(messages),
        )

        if not response or len(response.strip()) < 5:
            await message.reply_text(
                f"Hey {name}, happy to help! Could you give me a bit more detail? 🙂"
            )
            return

        # Keep group responses concise — truncate if AI was verbose
        if len(response) > 800:
            response = response[:750] + "…"

        await message.reply_text(response, parse_mode="HTML")
        record_group_post(chat.id)
        log.info(
            "Mention reply sent: user=%s chat=%s provider=%s",
            user.id, chat.id, provider,
        )

    except Exception as exc:
        log.error("Mention handler error: %s", exc)
        await message.reply_text(
            f"Hey {name}, I'm having a brief moment of difficulty. Try again shortly? 🙏"
        )


# ── Anti-spam filter ───────────────────────────────────────────────────────────

async def spam_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Filter scam/spam messages from non-admins in groups."""
    message = update.message
    if not message or not message.text:
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    if is_admin(user.id):
        return

    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status in ("administrator", "creator"):
            return
    except Exception:
        return

    text = message.text
    if not _SCAM_RE.search(text):
        return

    warn_count = _add_warning(user.id)
    name = html.escape(user.first_name or "User")

    try:
        await message.delete()
        log.warning(
            "Deleted spam: user=%s chat=%s warnings=%s", user.id, chat.id, warn_count
        )
    except Exception as exc:
        log.warning("Could not delete spam: %s", exc)

    if warn_count >= _MAX_WARNINGS:
        until = int(
            (datetime.utcnow() + timedelta(hours=_MUTE_HOURS)).timestamp()
        )
        try:
            await context.bot.restrict_chat_member(
                chat.id,
                user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await chat.send_message(
                f"🚫 <b>{name}</b> has been muted for {_MUTE_HOURS}h.\n"
                f"Reason: Repeated spam/scam ({warn_count} warnings).\n\n"
                f"<i>Please keep this community safe. 🙏</i>",
                parse_mode="HTML",
            )
            _WARN_STORE.pop(user.id, None)
            log.warning(
                "Muted user=%s for %dh in chat=%s", user.id, _MUTE_HOURS, chat.id
            )
        except Exception as exc:
            log.warning("Could not mute user=%s: %s", user.id, exc)
    else:
        try:
            await chat.send_message(
                f"⚠️ <b>{name}</b>, your message was removed (potential spam).\n"
                f"Warning <b>{warn_count}/{_MAX_WARNINGS}</b> — "
                f"{_MAX_WARNINGS - warn_count} more and you'll be muted.\n\n"
                f"<i>Keep this community spam-free. 🙏</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass


# ── Smart community handler (TestAudit — unanswered question detector) ─────────

async def smart_community_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    TestAudit Smart Response System.

    For every group message:
    1. Record human activity (resets silence/engagement timers)
    2. Mark any replied-to message as answered
    3. If message looks like a question/help request → register for monitoring
    4. Schedule a deferred check: if still unanswered after ~2.5 min → step in

    Never interrupts active conversations. Humans always go first.
    """
    message = update.message
    if not message or not message.text:
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat or user.is_bot:
        return

    if not FEATURE_FLAGS.get("chat_enabled", True):
        return

    text = message.text.strip()
    if len(text) < 8:
        return

    from services.community_manager import (
        record_human_activity,
        register_message,
        mark_replied,
        is_actionable_message,
    )
    from services.dm_operations import register_group_chat

    # Register this group for proactive engagement monitoring
    register_group_chat(chat.id)

    # Record this as human activity (resets engagement/silence timers)
    record_human_activity(chat.id)

    # If this is a reply to another message, mark original as answered
    if message.reply_to_message:
        mark_replied(chat.id, message.reply_to_message.message_id)
    else:
        # New top-level message in active chat — mark all recent as replied
        mark_replied(chat.id)

    # Only monitor actionable messages (questions, help requests)
    if is_actionable_message(text):
        register_message(chat.id, message.message_id, text, user.first_name or "there")
        asyncio.create_task(
            _deferred_smart_reply(chat.id, message.message_id, context)
        )


async def _deferred_smart_reply(
    chat_id: int, msg_id: int, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Wait 2.5 minutes. If the message is still unanswered and no human has
    replied since — step in as TestAudit with a helpful, conversational response.
    """
    await asyncio.sleep(150)  # 2.5 minutes

    from services.community_manager import (
        get_unanswered_messages,
        can_post_in_group,
        record_group_post,
        build_community_manager_system_prompt,
        get_support_interjection,
        seconds_silent,
    )

    # Re-check: still unanswered?
    unanswered = get_unanswered_messages(chat_id)
    target = next((m for m in unanswered if m["id"] == msg_id), None)

    if not target:
        return  # Human replied — stay quiet

    # Don't step in if TestAudit just posted
    if not can_post_in_group(chat_id, min_gap=90):
        return

    # Don't step in if the group has been active (conversation is ongoing)
    # If humans have posted in the last 60 seconds, they're handling it
    if seconds_silent(chat_id) < 60:
        return

    question_text = target["text"]

    from services.ai_service import get_ai_response

    system_prompt = build_community_manager_system_prompt()
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": question_text},
    ]

    loop = asyncio.get_event_loop()
    try:
        response, provider = await loop.run_in_executor(
            None,
            lambda: get_ai_response(messages),
        )

        if not response or len(response.strip()) < 10:
            return

        # Keep group responses concise
        if len(response) > 900:
            response = response[:850] + "…"

        # Natural interjection prefix (not robotic "Here is the answer:")
        prefix = get_support_interjection()
        final = f"{prefix}{response}"

        await context.bot.send_message(
            chat_id,
            final,
            parse_mode="HTML",
            reply_to_message_id=msg_id,
        )
        record_group_post(chat_id)
        log.info(
            "Smart reply sent: chat=%s msg=%s provider=%s",
            chat_id, msg_id, provider,
        )

    except Exception as exc:
        log.warning("Smart reply failed: %s", exc)


# ── Proactive engagement (called by background scheduler) ─────────────────────

async def send_proactive_engagement(bot, chat_id: int) -> bool:
    """
    TestAudit initiates a natural conversation when the group has been silent.
    Called by the background engagement scheduler in dm_operations.py.
    Returns True if a message was sent.
    """
    from services.community_manager import (
        can_engage_proactively,
        get_time_greeting,
        get_engagement_prompt,
        record_proactive_engagement,
        record_group_post,
    )

    if not can_engage_proactively(chat_id):
        return False

    if not FEATURE_FLAGS.get("chat_enabled", True):
        return False

    # Morning/afternoon/evening greeting first, else engagement prompt
    message_text = get_time_greeting() or get_engagement_prompt()

    try:
        await bot.send_message(chat_id, message_text, parse_mode="HTML")
        record_proactive_engagement(chat_id)
        record_group_post(chat_id)
        log.info("Proactive engagement sent to chat=%s", chat_id)
        return True
    except Exception as exc:
        log.warning("Proactive engagement failed for chat=%s: %s", chat_id, exc)
        return False


# ── Legacy stubs — kept for import compatibility; bot is now silent in groups ──

async def group_ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    DEPRECATED: Main bot is now silent in groups.
    TestAudit handles all group interaction via smart_community_handler.
    This stub is kept to avoid import errors if referenced elsewhere.
    """
    return


async def mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    DEPRECATED: Replaced by testaudit_mention_handler.
    Kept for import compatibility.
    """
    await testaudit_mention_handler(update, context)
