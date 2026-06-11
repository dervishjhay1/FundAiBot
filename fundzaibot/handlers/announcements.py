"""
FundzAiBot — Pinned announcement system.
Admin-only commands. The active announcement is displayed to every user on /start
as a compact Telegram-native card using <blockquote> (left blue accent line) and
pinned via pin_chat_message() — creating a true native Telegram sticky banner at
the top of each user's chat.

Admin commands:
  /pin <message>                    — create/replace the active announcement
  /unpin                            — remove the active announcement
  /updateannouncement <message>     — edit the current text
  /pinphoto <url|remove>            — attach/remove a banner image
  /listannouncements                — view announcement history
  /announce_channel                 — post the current announcement to channel
  /announce_group                   — post the current announcement to group
  /announce_both                    — post to both channel and group
"""

import asyncio
import html

from telegram import Bot, Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config.settings import (
    is_admin,
    TELEGRAM_CHANNEL_ID, TELEGRAM_CHANNEL_URL, TELEGRAM_CHANNEL_NAME,
    TELEGRAM_GROUP_ID, TELEGRAM_GROUP_URL, TELEGRAM_GROUP_NAME,
)
from services.database import (
    get_active_announcement, create_announcement, update_active_announcement,
    set_photo_on_announcement, deactivate_announcements, get_announcement_history,
)
from utils.helpers import time_ago
from utils.keyboards import (
    announcement_keyboard, announcement_keyboard_with_dismiss,
    admin_announcements_keyboard, back_to_menu, admin_main_menu,
)
from utils.logger import get_logger

log = get_logger(__name__)

# ── Default announcement (seeded on first startup) ─────────────────────────────

DEFAULT_ANNOUNCEMENT = (
    "📢 Announcement from FundzAiBot:\n\n"
    "⚠️ Note: @FundzAiBot is actively updated and improved daily to deliver "
    "better performance, features, and stability. If you experience any issues "
    "or notice a feature not working properly, please contact @Biodunfund for "
    "support and further assistance. 💙"
)

SUPPORT_URL = "https://t.me/Biodunfund"


# ── Card formatter ─────────────────────────────────────────────────────────────

def format_announcement_card(message: str) -> str:
    """
    Render an announcement as a compact Telegram-native card.
    Telegram's <blockquote> tag produces:
      • Left blue vertical accent line
      • Dark-tinted background container
      • Mobile-friendly compact sizing
      • Premium pinned notice look
    """
    escaped = html.escape(message)
    return (
        f"<blockquote>"
        f"📌 <b>Pinned Message</b>\n"
        f"▸ FundzAiBot\n\n"
        f"{escaped}"
        f"</blockquote>"
    )


# ── Sticky announcement delivery ──────────────────────────────────────────────

async def send_sticky_announcement(
    bot: Bot,
    chat_id: int | str,
    announcement: dict,
    *,
    pin: bool = True,
) -> int | None:
    """
    Send the active announcement to a chat and pin it to create a native
    Telegram sticky banner at the top.

    Returns the message_id of the sent message, or None on failure.
    The 'dismiss' button lets users unpin it themselves.
    """
    msg_text  = announcement.get("message", "")
    photo_url = announcement.get("photo_url")
    card      = format_announcement_card(msg_text)
    kbd       = announcement_keyboard_with_dismiss()

    sent_msg = None
    try:
        if photo_url:
            try:
                sent_msg = await bot.send_photo(
                    chat_id, photo=photo_url, caption=card,
                    parse_mode="HTML", reply_markup=kbd,
                )
            except TelegramError:
                sent_msg = await bot.send_message(
                    chat_id, card, parse_mode="HTML", reply_markup=kbd,
                )
        else:
            sent_msg = await bot.send_message(
                chat_id, card, parse_mode="HTML", reply_markup=kbd,
            )
    except Exception as exc:
        log.debug("Failed to send announcement to %s: %s", chat_id, exc)
        return None

    if not sent_msg:
        return None

    # Pin the message — creates the native Telegram sticky banner at the top
    if pin:
        try:
            await bot.pin_chat_message(
                chat_id=chat_id,
                message_id=sent_msg.message_id,
                disable_notification=True,   # silent pin — no "pinned a message" notification
            )
            log.debug("Announcement pinned in chat %s (msg_id=%s)", chat_id, sent_msg.message_id)
        except TelegramError as exc:
            # Pinning requires admin rights in groups/channels.
            # In private DMs it always works when bot has the right.
            log.debug("Could not pin announcement in %s: %s", chat_id, exc)

    return sent_msg.message_id


# ── Channel / Group posting ────────────────────────────────────────────────────

async def post_to_channel(bot: Bot, announcement: dict) -> tuple[bool, str]:
    """
    Post the active announcement to the configured Telegram channel.
    Returns (success, status_message).
    """
    if not TELEGRAM_CHANNEL_ID:
        return False, "❌ TELEGRAM_CHANNEL_ID is not configured."
    try:
        msg_id = await send_sticky_announcement(bot, TELEGRAM_CHANNEL_ID, announcement, pin=True)
        if msg_id:
            return True, f"✅ Posted to {TELEGRAM_CHANNEL_NAME} (msg_id={msg_id})"
        return False, "⚠️ Message sent but could not pin."
    except TelegramError as exc:
        log.error("post_to_channel: %s", exc)
        return False, f"❌ Telegram error: {str(exc)[:100]}"
    except Exception as exc:
        log.error("post_to_channel unexpected: %s", exc)
        return False, f"❌ {type(exc).__name__}: {str(exc)[:80]}"


async def post_to_group(bot: Bot, announcement: dict) -> tuple[bool, str]:
    """
    Post the active announcement to the configured Telegram group.
    Returns (success, status_message).
    """
    if not TELEGRAM_GROUP_ID:
        return False, "❌ TELEGRAM_GROUP_ID is not configured."
    try:
        msg_id = await send_sticky_announcement(bot, TELEGRAM_GROUP_ID, announcement, pin=True)
        if msg_id:
            return True, f"✅ Posted to {TELEGRAM_GROUP_NAME} (msg_id={msg_id})"
        return False, "⚠️ Message sent but could not pin."
    except TelegramError as exc:
        log.error("post_to_group: %s", exc)
        return False, f"❌ Telegram error: {str(exc)[:100]}"
    except Exception as exc:
        log.error("post_to_group unexpected: %s", exc)
        return False, f"❌ {type(exc).__name__}: {str(exc)[:80]}"


# ── Guard ──────────────────────────────────────────────────────────────────────

def _guard(user) -> bool:
    return bool(user) and is_admin(user.id)


# ── /pin <message> ─────────────────────────────────────────────────────────────

async def pin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pin <message> — Create/replace the active pinned announcement."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "📌 <b>Pin an Announcement</b>\n\n"
            "Usage: <code>/pin &lt;message&gt;</code>\n\n"
            "Example:\n"
            "<code>/pin 🎉 FundzAiBot v2.4 is live! New features inside.</code>\n\n"
            "<i>Users see this as a native pinned banner on /start. Previous pin is replaced.</i>\n\n"
            "<b>Auto-posting commands:</b>\n"
            "• <code>/announce_channel</code> — post to channel\n"
            "• <code>/announce_group</code> — post to group\n"
            "• <code>/announce_both</code> — post to both",
            parse_mode="HTML",
        )
        return

    message = " ".join(args)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: create_announcement(message, created_by=user.id))

    # Show admin a live preview
    preview = format_announcement_card(message)
    await update.effective_message.reply_text(
        f"✅ <b>Announcement pinned!</b>\n\n"
        f"<i>Preview (what users see on /start with native sticky banner):</i>\n\n"
        f"{preview}\n\n"
        f"Use the buttons below to post to channel/group:",
        parse_mode="HTML",
        reply_markup=admin_announcements_keyboard(),
    )
    log.info("Admin pinned announcement: user=%s chars=%d", user.id, len(message))


# ── /unpin ─────────────────────────────────────────────────────────────────────

async def unpin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unpin — Remove the active pinned announcement."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    loop = asyncio.get_running_loop()
    current = await loop.run_in_executor(None, get_active_announcement)
    if not current:
        await update.effective_message.reply_text(
            "ℹ️ No announcement is currently pinned.",
            reply_markup=admin_main_menu(),
        )
        return

    await loop.run_in_executor(None, deactivate_announcements)
    await update.effective_message.reply_text(
        "✅ <b>Announcement unpinned.</b>\n\n"
        "Users will no longer see a pinned card on /start.",
        parse_mode="HTML",
        reply_markup=admin_main_menu(),
    )
    log.info("Admin unpinned announcement: user=%s", user.id)


# ── /updateannouncement <message> ──────────────────────────────────────────────

async def updateannouncement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/updateannouncement <message> — Edit the current active announcement text."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: <code>/updateannouncement &lt;new message text&gt;</code>",
            parse_mode="HTML",
        )
        return

    message = " ".join(args)
    loop = asyncio.get_running_loop()
    current = await loop.run_in_executor(None, get_active_announcement)

    if not current:
        await update.effective_message.reply_text(
            "⚠️ No active announcement to update.\n"
            "Use <code>/pin &lt;message&gt;</code> to create one first.",
            parse_mode="HTML",
        )
        return

    await loop.run_in_executor(None, lambda: update_active_announcement(message))
    preview = format_announcement_card(message)
    await update.effective_message.reply_text(
        f"✅ <b>Announcement updated!</b>\n\n"
        f"<i>Live preview:</i>\n\n{preview}",
        parse_mode="HTML",
        reply_markup=admin_announcements_keyboard(),
    )
    log.info("Admin updated announcement: user=%s", user.id)


# ── /pinphoto <url|remove> ─────────────────────────────────────────────────────

async def pinphoto_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pinphoto <url|remove> — Attach or remove a banner image from the active announcement."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "  <code>/pinphoto &lt;image_url&gt;</code> — attach banner\n"
            "  <code>/pinphoto remove</code> — clear banner",
            parse_mode="HTML",
        )
        return

    raw = args[0].strip()
    url: str | None = None if raw.lower() == "remove" else raw

    loop = asyncio.get_running_loop()
    current = await loop.run_in_executor(None, get_active_announcement)
    if not current:
        await update.effective_message.reply_text(
            "⚠️ No active announcement. Create one with /pin first."
        )
        return

    await loop.run_in_executor(None, lambda: set_photo_on_announcement(url))

    if url:
        try:
            caption = format_announcement_card(current.get("message", ""))
            await update.effective_message.reply_photo(
                photo=url,
                caption=caption,
                parse_mode="HTML",
                reply_markup=admin_announcements_keyboard(),
            )
        except Exception:
            await update.effective_message.reply_text(
                f"⚠️ URL saved but could not preview — confirm it is a direct image link.\n"
                f"<code>{html.escape(url)}</code>",
                parse_mode="HTML",
                reply_markup=admin_announcements_keyboard(),
            )
    else:
        await update.effective_message.reply_text(
            "✅ Banner image removed from announcement.",
            reply_markup=admin_announcements_keyboard(),
        )
    log.info("Admin pinphoto: user=%s url=%s", user.id, url)


# ── /announce_channel ──────────────────────────────────────────────────────────

async def announce_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/announce_channel — Post the current active announcement to the Telegram channel."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    loop = asyncio.get_running_loop()
    ann = await loop.run_in_executor(None, get_active_announcement)
    if not ann:
        await update.effective_message.reply_text(
            "⚠️ No active announcement to post.\n"
            "Create one with <code>/pin &lt;message&gt;</code> first.",
            parse_mode="HTML",
        )
        return

    if not TELEGRAM_CHANNEL_ID:
        await update.effective_message.reply_text(
            "❌ <code>TELEGRAM_CHANNEL_ID</code> is not configured.\n"
            "Set it in Railway environment variables.",
            parse_mode="HTML",
        )
        return

    await update.effective_message.reply_text("📤 Posting to channel…")
    ok, status = await post_to_channel(context.bot, ann)
    await update.effective_message.reply_text(
        f"<b>Channel Post Result:</b>\n{status}",
        parse_mode="HTML",
        reply_markup=admin_announcements_keyboard(),
    )
    log.info("Admin posted to channel: user=%s ok=%s", user.id, ok)


# ── /announce_group ────────────────────────────────────────────────────────────

async def announce_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/announce_group — Post the current active announcement to the Telegram group."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    loop = asyncio.get_running_loop()
    ann = await loop.run_in_executor(None, get_active_announcement)
    if not ann:
        await update.effective_message.reply_text(
            "⚠️ No active announcement to post.\n"
            "Create one with <code>/pin &lt;message&gt;</code> first.",
            parse_mode="HTML",
        )
        return

    if not TELEGRAM_GROUP_ID:
        await update.effective_message.reply_text(
            "❌ <code>TELEGRAM_GROUP_ID</code> is not configured.\n"
            "Set it in Railway environment variables.",
            parse_mode="HTML",
        )
        return

    await update.effective_message.reply_text("📤 Posting to group…")
    ok, status = await post_to_group(context.bot, ann)
    await update.effective_message.reply_text(
        f"<b>Group Post Result:</b>\n{status}",
        parse_mode="HTML",
        reply_markup=admin_announcements_keyboard(),
    )
    log.info("Admin posted to group: user=%s ok=%s", user.id, ok)


# ── /announce_both ─────────────────────────────────────────────────────────────

async def announce_both_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/announce_both — Post the current active announcement to BOTH channel and group."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    loop = asyncio.get_running_loop()
    ann = await loop.run_in_executor(None, get_active_announcement)
    if not ann:
        await update.effective_message.reply_text(
            "⚠️ No active announcement to post.\n"
            "Create one with <code>/pin &lt;message&gt;</code> first.",
            parse_mode="HTML",
        )
        return

    if not TELEGRAM_CHANNEL_ID and not TELEGRAM_GROUP_ID:
        await update.effective_message.reply_text(
            "❌ Neither TELEGRAM_CHANNEL_ID nor TELEGRAM_GROUP_ID are configured.\n"
            "Set them in Railway environment variables.",
            parse_mode="HTML",
        )
        return

    await update.effective_message.reply_text("📤 Posting to channel and group…")

    results = []
    if TELEGRAM_CHANNEL_ID:
        ok, status = await post_to_channel(context.bot, ann)
        results.append(f"📢 Channel: {status}")

    if TELEGRAM_GROUP_ID:
        ok, status = await post_to_group(context.bot, ann)
        results.append(f"👥 Group: {status}")

    await update.effective_message.reply_text(
        "<b>Broadcast Result:</b>\n" + "\n".join(results),
        parse_mode="HTML",
        reply_markup=admin_announcements_keyboard(),
    )
    log.info("Admin posted to both: user=%s", user.id)


# ── /listannouncements ─────────────────────────────────────────────────────────

async def listannouncements_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/listannouncements — View announcement history (latest 10)."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    loop = asyncio.get_running_loop()
    history = await loop.run_in_executor(None, lambda: get_announcement_history(limit=10))

    if not history:
        await update.effective_message.reply_text(
            "📭 No announcements yet.\n\n"
            "Use <code>/pin &lt;message&gt;</code> to create your first one.",
            parse_mode="HTML",
        )
        return

    lines = ["📌 <b>Announcement History</b>\n"]
    for i, a in enumerate(history, 1):
        status  = "🟢 ACTIVE" if a.get("is_active") else "⚫ archived"
        preview = html.escape((a.get("message") or "")[:90])
        if len(a.get("message") or "") > 90:
            preview += "…"
        photo   = " 🖼️" if a.get("photo_url") else ""
        lines.append(
            f"{i}. [{status}]{photo}\n"
            f"   {preview}\n"
            f"   <i>{time_ago(a.get('created_at', ''))}</i>"
        )

    await update.effective_message.reply_text(
        "\n\n".join(lines),
        parse_mode="HTML",
        reply_markup=admin_announcements_keyboard(),
    )
