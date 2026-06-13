"""
FundzAiBot — Community group integration.

Provides:
  • Auto-welcome for new members (with ecosystem buttons)
  • /ai <question> command inside the group
  • @mention reply when bot is @tagged in group
  • Anti-spam filter with warning + auto-mute system

All handlers only activate in group/supergroup chats.
"""

import asyncio
import html
import re
from datetime import datetime, timedelta

from telegram import ChatPermissions, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import (
    BOT_NAME, FEATURE_FLAGS, TELEGRAM_CHANNEL_URL,
    TELEGRAM_GROUP_URL, is_admin,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Anti-spam config ──────────────────────────────────────────────────────────

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
_MUTE_HOURS   = 1


def _add_warning(user_id: int) -> int:
    now = datetime.utcnow()
    _WARN_STORE.setdefault(user_id, [])
    _WARN_STORE[user_id] = [
        t for t in _WARN_STORE[user_id] if now - t < timedelta(hours=24)
    ]
    _WARN_STORE[user_id].append(now)
    return len(_WARN_STORE[user_id])


# ── New member welcome ─────────────────────────────────────────────────────────

async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome new group members and direct them to the bot + channel."""
    message = update.message
    if not message or not message.new_chat_members:
        return

    chat = update.effective_chat
    if not chat:
        return

    bot_info = await context.bot.get_me()
    bot_uname = bot_info.username or BOT_NAME

    for member in message.new_chat_members:
        if member.is_bot:
            continue

        name      = html.escape(member.first_name or "there")
        chat_name = html.escape(chat.title or "FundzAi Community")

        text = (
            f"👋 <b>Welcome, {name}!</b>\n\n"
            f"You've joined <b>{chat_name}</b> — the official AI community "
            f"powered by {BOT_NAME}. 🚀\n\n"
            f"<b>What you can do here:</b>\n"
            f"💬 <code>/ai your question</code> — ask AI in this group\n"
            f"🤖 Chat privately with @{bot_uname} for full features\n\n"
            f"<b>Explore the ecosystem:</b>"
        )

        buttons: list[InlineKeyboardButton] = []
        if TELEGRAM_CHANNEL_URL:
            buttons.append(
                InlineKeyboardButton("📢 Official Channel", url=TELEGRAM_CHANNEL_URL)
            )
        buttons.append(
            InlineKeyboardButton(f"🤖 Open @{bot_uname}", url=f"https://t.me/{bot_uname}")
        )

        kbd = InlineKeyboardMarkup([buttons])

        try:
            await message.reply_text(text, parse_mode="HTML", reply_markup=kbd)
            log.info("Welcomed member %s in group %s", member.id, chat.id)
        except Exception as exc:
            log.warning("Welcome message failed: %s", exc)


# ── /ai command in groups ─────────────────────────────────────────────────────

async def group_ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ai <question> — AI answer directly inside the group."""
    if not FEATURE_FLAGS.get("chat_enabled", True):
        await update.message.reply_text("🚧 AI chat is temporarily unavailable.")
        return

    user  = update.effective_user
    query = " ".join(context.args or []).strip()

    if not query:
        await update.message.reply_text(
            "💡 <b>AI Chat in Group</b>\n\n"
            f"Usage: <code>/ai your question here</code>\n\n"
            f"Example: <code>/ai explain blockchain simply</code>",
            parse_mode="HTML",
        )
        return

    thinking = await update.message.reply_text("🤖 <i>Thinking…</i>", parse_mode="HTML")

    from services.ai_service import get_ai_response
    from services.database import get_or_create_user

    loop = asyncio.get_running_loop()
    try:
        db_user = await loop.run_in_executor(
            None,
            lambda: get_or_create_user(user.id, first_name=user.first_name or ""),
        )
        style = (db_user or {}).get("ai_style", "default")

        response, provider = await loop.run_in_executor(
            None,
            lambda: get_ai_response(
                [{"role": "user", "content": query}],
            ),
        )

        name  = html.escape(user.first_name or "User")
        reply = f"🤖 <b>AI Reply to {name}:</b>\n\n{response}"
        if len(reply) > 4000:
            reply = reply[:3900] + "\n\n<i>…(truncated)</i>"

        await thinking.edit_text(reply, parse_mode="HTML")
        log.info("Group /ai: user=%s chat=%s provider=%s", user.id,
                 update.effective_chat.id, provider)
    except Exception as exc:
        log.error("Group AI error: %s", exc)
        await thinking.edit_text("⚠️ AI is unavailable right now. Try again shortly.")


# ── @mention reply ────────────────────────────────────────────────────────────

async def mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Reply when the bot is @mentioned in a group message."""
    message = update.message
    if not message or not message.text:
        return

    user = update.effective_user
    if not user:
        return

    bot_info  = await context.bot.get_me()
    bot_uname = (bot_info.username or "").lower()
    text      = message.text

    mention_tag = f"@{bot_uname}".lower()
    if mention_tag not in text.lower():
        return

    question = re.sub(
        rf"@{re.escape(bot_uname)}", "", text, flags=re.IGNORECASE
    ).strip()

    if not question:
        name = html.escape(user.first_name or "there")
        await message.reply_text(
            f"👋 Hi {name}! Mention me with a question:\n"
            f"<code>@{bot_info.username} what is AI?</code>",
            parse_mode="HTML",
        )
        return

    if not FEATURE_FLAGS.get("chat_enabled", True):
        await message.reply_text("🚧 AI is temporarily unavailable.")
        return

    thinking = await message.reply_text("🤖 <i>Thinking…</i>", parse_mode="HTML")

    from services.ai_service import get_ai_response
    loop = asyncio.get_running_loop()
    try:
        response, provider = await loop.run_in_executor(
            None,
            lambda: get_ai_response([{"role": "user", "content": question}]),
        )
        name  = html.escape(user.first_name or "User")
        reply = f"🤖 <b>{name}:</b>\n\n{response}"
        if len(reply) > 4000:
            reply = reply[:3900] + "\n\n<i>…(truncated)</i>"
        await thinking.edit_text(reply, parse_mode="HTML")
        log.info("Group mention: user=%s chat=%s provider=%s",
                 user.id, update.effective_chat.id, provider)
    except Exception as exc:
        log.error("Mention handler error: %s", exc)
        await thinking.edit_text("⚠️ AI is unavailable right now.")


# ── Anti-spam filter ──────────────────────────────────────────────────────────

async def spam_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Filter scam/spam messages from non-admins in groups."""
    message = update.message
    if not message or not message.text:
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    # Always skip bot owners and secondary admins
    if is_admin(user.id):
        return

    # Skip Telegram chat admins
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
        log.warning("Deleted spam from user=%s chat=%s warns=%s", user.id, chat.id, warn_count)
    except Exception as exc:
        log.warning("Could not delete spam message: %s", exc)

    if warn_count >= _MAX_WARNINGS:
        until = int((datetime.utcnow() + timedelta(hours=_MUTE_HOURS)).timestamp())
        try:
            await context.bot.restrict_chat_member(
                chat.id, user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            await chat.send_message(
                f"🚫 <b>{name}</b> has been muted for {_MUTE_HOURS}h.\n"
                f"Reason: Repeated spam/scam ({warn_count} warnings).\n\n"
                f"<i>Please respect community guidelines.</i>",
                parse_mode="HTML",
            )
            _WARN_STORE.pop(user.id, None)
            log.warning("Muted user=%s for %dh in chat=%s", user.id, _MUTE_HOURS, chat.id)
        except Exception as exc:
            log.warning("Could not mute user=%s: %s", user.id, exc)
    else:
        try:
            await chat.send_message(
                f"⚠️ <b>{name}</b>, your message was removed for potential spam.\n"
                f"Warning <b>{warn_count}/{_MAX_WARNINGS}</b> — "
                f"another {_MAX_WARNINGS - warn_count} and you'll be muted.\n\n"
                f"<i>Keep this community spam-free. 🙏</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass
