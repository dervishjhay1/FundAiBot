"""
FundzAiBot — Group Handler

FundzAiBot is a user-facing AI assistant.
In group chats it:
  - Welcomes new members briefly
  - Enforces anti-spam (removes scam/invite-link messages)
  - Stays silent otherwise — AI conversations happen in private DMs

No TestAudit persona. No executive authority.
"""

from __future__ import annotations

import asyncio
import html
import re
import time

from telegram import ChatPermissions, Update
from telegram.ext import ContextTypes

from config.settings import BOT_NAME, FEATURE_FLAGS
from utils.logger import get_logger

log = get_logger(__name__)


# ── Anti-spam config ───────────────────────────────────────────────────────────

_SCAM_RE = re.compile(
    r"t\.me/[a-zA-Z0-9_]{3,}"
    r"|telegram\.me/"
    r"|bit\.ly/"
    r"|tinyurl\.com/"
    r"|(?:free\s+(?:btc|eth|usdt|crypto|money))"
    r"|(?:earn\s+\d+\s*(btc|eth|usdt|\$))"
    r"|(?:double\s+your\s+(money|bitcoin|crypto))"
    r"|(?:guaranteed\s+profit)"
    r"|(?:investment\s+returns?)",
    re.IGNORECASE,
)

_SPAM_WARNINGS: dict[int, list[float]] = {}
_WARN_WINDOW = 300   # 5-minute sliding window
_MUTE_AFTER  = 3     # mute after N violations in window


def _add_warning(user_id: int) -> int:
    now = time.time()
    recent = [t for t in _SPAM_WARNINGS.get(user_id, []) if now - t < _WARN_WINDOW]
    recent.append(now)
    _SPAM_WARNINGS[user_id] = recent
    return len(recent)


# ── New member welcome ─────────────────────────────────────────────────────────

async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Welcome new members joining the group with a brief message."""
    message = update.message
    if not message or not message.new_chat_members:
        return

    for member in message.new_chat_members:
        if member.is_bot:
            continue
        first_name = html.escape(member.first_name or "there")
        try:
            await message.reply_text(
                f"👋 Welcome, <b>{first_name}</b>!\n\n"
                f"Head to our bot in private to start chatting with <b>{BOT_NAME}</b> — "
                f"your AI assistant for writing, coding, business, and more. 🤖",
                parse_mode="HTML",
            )
        except Exception as exc:
            log.warning("Could not send welcome message: %s", exc)


# ── Anti-spam ─────────────────────────────────────────────────────────────────

async def spam_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete and optionally mute users posting scam/spam content in groups."""
    message = update.message or update.edited_message
    if not message or not message.text:
        return

    if not _SCAM_RE.search(message.text):
        return

    user = update.effective_user
    if not user:
        return

    # Report to HQ
    try:
        from services.hq_sync import event_spam_detected
        event_spam_detected(user.id, user.username, message.text)
    except Exception:
        pass

    try:
        await message.delete()
    except Exception:
        pass

    violations = _add_warning(user.id)

    if violations >= _MUTE_AFTER:
        try:
            await context.bot.restrict_chat_member(
                chat_id=message.chat_id,
                user_id=user.id,
                permissions=ChatPermissions(can_send_messages=False),
            )
            await asyncio.sleep(0.5)
            await context.bot.send_message(
                chat_id=message.chat_id,
                text=(
                    f"⚠️ <b>{html.escape(user.first_name or 'User')}</b> has been muted for repeated spam.\n"
                    f"Contact an admin to appeal."
                ),
                parse_mode="HTML",
            )
            log.warning("Muted spam user=%s in chat=%s", user.id, message.chat_id)
        except Exception as exc:
            log.warning("Could not mute user %s: %s", user.id, exc)
    else:
        try:
            warn_msg = await context.bot.send_message(
                chat_id=message.chat_id,
                text=f"⚠️ Spam or invite links are not allowed. Warning {violations}/{_MUTE_AFTER}.",
            )
            await asyncio.sleep(8)
            await warn_msg.delete()
        except Exception:
            pass


# ── Group AI / mention stubs (silent — AI only in private DMs) ────────────────

async def smart_community_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """FundzAiBot is silent in groups. AI conversations happen in private DMs."""
    return


async def group_ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Deprecated stub — AI is private DM only."""
    return


async def mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """When mentioned in group, politely redirect to private DM."""
    message = update.message
    if not message:
        return
    try:
        await message.reply_text(
            f"👋 I'm <b>{BOT_NAME}</b>! Start a private chat with me to use AI features.",
            parse_mode="HTML",
        )
    except Exception:
        pass
