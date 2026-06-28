"""
FundzAiBot — Community group integration.

Architecture rule (final):
  The bot (FundzAiBot) is COMPLETELY SILENT inside Telegram groups.
  It MUST NOT reply to commands, @mentions, new-member joins, spam
  warnings, or any other group event.

  The only entity that communicates in the group is TestAudit
  (Community Operations Manager), which posts proactively via the
  CommunityManager background service.

  Private chat remains the full AI workspace — all personal features
  work exclusively there.

Responsibilities:
  • Record group activity so CommunityManager tracks engagement
  • Silently delete spam (no warning message posted)
  • Silently mute repeat spammers (no announcement posted)
  • Delegate new-member welcoming to community_manager.welcome_new_member()
  • Block (silently ignore) all personal commands in groups
"""

import re
from datetime import datetime, timedelta

from telegram import ChatPermissions, Update
from telegram.ext import ContextTypes

from config.settings import is_admin
from utils.logger import get_logger

log = get_logger(__name__)

# ── Anti-spam config ──────────────────────────────────────────────────────────

_SCAM_RE = re.compile(
    r"(?i)"
    r"t\.me/[a-zA-Z0-9_]{3,}"
    r"|telegram\.me/"
    r"|bit\.ly/"
    r"|tinyurl\.com/"
    r"|(free\s+(?:btc|eth|usdt|crypto|money))"
    r"|(earn\s+\d+\s*(btc|eth|usdt|\$))"
    r"|(double\s+your\s+(money|bitcoin|crypto))"
    r"|(guaranteed\s+profit)"
    r"|(investment\s+returns?)",
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


# ── Channel command guard ──────────────────────────────────────────────────────

async def _is_channel_admin(bot, chat_id, user_id: int) -> bool:
    """Check if a user is an admin in a channel/group."""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in ("administrator", "creator")
    except Exception:
        return False


async def channel_command_guard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Returns True (block) if this is a channel/group message from a non-admin.
    Bot only responds to admin commands in channels.
    Always returns False in private chats (no blocking).
    """
    chat = update.effective_chat
    if not chat:
        return False
    if chat.type == "private":
        return False
    user = update.effective_user
    if not user:
        return True  # block anonymous
    if is_admin(user.id):
        return False
    # Check if Telegram group admin
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status in ("administrator", "creator"):
            return False
    except Exception:
        pass
    return True  # block regular users from commands in groups/channels


# ── New member welcome ─────────────────────────────────────────────────────────

async def new_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Delegate new-member welcoming to the TestAudit Community Manager.
    FundzAiBot stays completely silent — TestAudit sends the welcome.
    """
    # Record activity so CommunityManager tracks engagement
    try:
        from services.community_manager import record_group_activity
        record_group_activity()
    except Exception:
        pass

    message = update.message
    if not message or not message.new_chat_members:
        return

    chat = update.effective_chat
    if not chat:
        return

    for member in message.new_chat_members:
        if member.is_bot:
            continue
        # TestAudit sends the welcome — bot itself produces no group message
        try:
            from services.community_manager import welcome_new_member
            await welcome_new_member(context.bot, member, chat)
        except Exception as exc:
            log.debug("community_manager.welcome_new_member failed: %s", exc)


# ── /ai command in groups ─────────────────────────────────────────────────────

async def group_ai_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /ai command in groups — bot stays completely silent.
    AI features are available exclusively in private chat with FundzAiBot.
    TestAudit is the only entity communicating in the group.
    """
    user = update.effective_user
    # Record activity for CommunityManager
    try:
        from services.community_manager import record_group_activity
        record_group_activity()
    except Exception:
        pass
    log.debug(
        "Group /ai silenced — user=%s chat=%s",
        user.id if user else "?",
        update.effective_chat.id if update.effective_chat else "?",
    )
    # Bot stays completely silent — no reply in group


# ── @mention reply ────────────────────────────────────────────────────────────

async def mention_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    @mention in groups — bot stays completely silent.
    Records the message for smart reply monitoring so TestAudit can
    assist the member if no human replies within ~2.5 minutes.
    """
    user    = update.effective_user
    message = update.message
    if not user or not message:
        return

    try:
        from services.community_manager import record_group_message
        text = message.text or message.caption or ""
        if text:
            reply_to_id = (
                message.reply_to_message.message_id
                if message.reply_to_message else None
            )
            record_group_message(
                message_id   = message.message_id,
                user_id      = user.id,
                text         = text,
                reply_to_id  = reply_to_id,
            )
    except Exception:
        pass
    # Bot stays completely silent — no reply to @mentions in groups


# ── Group command blocker ─────────────────────────────────────────────────────

async def group_command_blocker(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Silently ignore regular bot commands sent in groups by non-admins.
    The bot only handles /ai and @mentions in groups.
    Regular commands (/start, /help, etc.) are ignored without reply
    to keep the group feed clean.
    """
    user = update.effective_user
    if not user:
        return
    # Admins can still run commands in groups
    if is_admin(user.id):
        return
    # Check Telegram group admin status
    chat = update.effective_chat
    if chat:
        try:
            member = await context.bot.get_chat_member(chat.id, user.id)
            if member.status in ("administrator", "creator"):
                return
        except Exception:
            pass
    # Bot stays completely silent — no reply in group
    log.debug(
        "Group command silenced — user=%s cmd=%s chat=%s",
        user.id,
        (update.message.text or "")[:30] if update.message else "?",
        chat.id if chat else "?",
    )


# ── Anti-spam filter ──────────────────────────────────────────────────────────

async def spam_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Anti-spam filter — silently deletes spam and mutes repeat offenders.
    Bot produces NO warning or mute announcement in the group.
    TestAudit is the only entity allowed to communicate in groups;
    enforcement actions happen invisibly to keep the feed clean.
    """
    message = update.message
    if not message or not message.text:
        return

    user = update.effective_user
    chat = update.effective_chat
    if not user or not chat:
        return

    # Record activity for CommunityManager
    try:
        from services.community_manager import record_group_activity
        record_group_activity()
    except Exception:
        pass

    # Never filter the bot owner or secondary admins
    if is_admin(user.id):
        return

    # Never filter Telegram group admins/creators
    try:
        member = await context.bot.get_chat_member(chat.id, user.id)
        if member.status in ("administrator", "creator"):
            return
    except Exception:
        return

    text = message.text
    if not _SCAM_RE.search(text):
        # Not spam — track message so TestAudit can assist if no human replies
        try:
            from services.community_manager import record_group_message
            reply_to_id = (
                message.reply_to_message.message_id
                if message.reply_to_message else None
            )
            record_group_message(
                message_id  = message.message_id,
                user_id     = user.id,
                text        = text,
                reply_to_id = reply_to_id,
            )
        except Exception:
            pass
        return

    # Spam detected — delete silently, no group announcement
    warn_count = _add_warning(user.id)

    try:
        await message.delete()
        log.warning(
            "Spam deleted silently — user=%s chat=%s warnings=%d",
            user.id, chat.id, warn_count,
        )
    except Exception as exc:
        log.warning("spam_filter: delete failed user=%s: %s", user.id, exc)

    if warn_count >= _MAX_WARNINGS:
        # Mute silently — no announcement in group
        until = int((datetime.utcnow() + timedelta(hours=_MUTE_HOURS)).timestamp())
        try:
            await context.bot.restrict_chat_member(
                chat.id, user.id,
                permissions=ChatPermissions(can_send_messages=False),
                until_date=until,
            )
            _WARN_STORE.pop(user.id, None)
            log.warning(
                "Spam offender muted silently — user=%s %dh chat=%s",
                user.id, _MUTE_HOURS, chat.id,
            )
        except Exception as exc:
            log.warning("spam_filter: mute failed user=%s: %s", user.id, exc)
    # No else branch — no warning message posted in group
