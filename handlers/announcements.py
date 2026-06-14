"""
FundzAiBot — Sticky announcement system.
Admin-only commands. The active announcement is shown to every user on /start
as a premium Telegram-native blockquote card (left blue accent line, dark bg).

NOT Telegram's native pin_chat_message — this is a bot-rendered announcement card
that simulates the premium sticky announcement experience.
"""

import asyncio
import html

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin
from services.database import (
    get_active_announcement, create_announcement, update_active_announcement,
    set_photo_on_announcement, deactivate_announcements, get_announcement_history,
)
from utils.helpers import time_ago
from utils.keyboards import announcement_keyboard, back_to_menu, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)

# ── Default announcement (seeded on first startup) ────────────────────────────

DEFAULT_ANNOUNCEMENT = (
    "📢 Announcement from FundzAiBot:\n\n"
    "⚠️ FundzAiBot is actively updated daily with new AI features, "
    "stability improvements, and optimizations.\n\n"
    "💙 Join our official channels for updates, bonuses, support, "
    "and community access."
)

SUPPORT_URL = "https://t.me/Biodunfund"


# ── send_sticky_announcement ──────────────────────────────────────────────────

async def send_sticky_announcement(
    bot,
    user_id: int,
    ann: dict,
    pin: bool = False,
) -> None:
    """
    Send the active announcement card as a DM to a user.

    Used by:
      • /start (on every visit if an announcement is active)
      • Returning user flows

    The 'pin' parameter is accepted for backward compatibility but is ignored —
    Telegram does not support pinning messages in private chats via the bot API
    in a useful way. The blockquote card itself provides a visually distinct
    "pinned" feel.
    """
    if not ann:
        return

    message   = ann.get("message") or ""
    photo_url = ann.get("photo_url") or ""

    if not message:
        return

    card = format_announcement_card(message)

    try:
        if photo_url:
            await bot.send_photo(
                chat_id=user_id,
                photo=photo_url,
                caption=card,
                parse_mode="HTML",
                reply_markup=announcement_keyboard(SUPPORT_URL),
            )
        else:
            await bot.send_message(
                chat_id=user_id,
                text=card,
                parse_mode="HTML",
                reply_markup=announcement_keyboard(SUPPORT_URL),
            )
    except Exception as exc:
        log.debug("send_sticky_announcement to user=%s failed: %s", user_id, exc)


# ── Card formatter ────────────────────────────────────────────────────────────

def format_announcement_card(message: str, lang: str = "en") -> str:
    """
    Render a premium sticky announcement card using Telegram's <blockquote> tag.

    The <blockquote> element produces:
      • Left blue vertical accent line (Telegram native)
      • Dark-tinted background container
      • Mobile-friendly compact sizing
      • Rounded appearance on modern Telegram clients
      • Exactly the look premium bots use for sticky notices

    This deliberately avoids Telegram's native pin_chat_message() system.
    The result is a visually distinct, compact, professional announcement card
    that appears directly in the chat flow — above the main menu.
    """
    escaped = html.escape(message)

    # Translate labels based on user lang
    from services.language import get_string
    pin_label = get_string(lang, "pin_label")
    pin_from  = get_string(lang, "pin_from")

    return (
        f"<blockquote>"
        f"{pin_label}\n"
        f"{pin_from}\n\n"
        f"{escaped}"
        f"</blockquote>"
    )


# ── Guard ─────────────────────────────────────────────────────────────────────

def _guard(user) -> bool:
    return bool(user) and is_admin(user.id)


# ── /pin <message> ────────────────────────────────────────────────────────────

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
            "<code>/pin 🎉 FundzAiBot v2.3 is live! New features inside.</code>\n\n"
            "<i>Users see this on /start. Previous pin is replaced.</i>",
            parse_mode="HTML",
        )
        return

    message = " ".join(args)
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, lambda: create_announcement(message, created_by=user.id))

    history   = await loop.run_in_executor(None, lambda: get_announcement_history(limit=10))
    ann_count = len(history)
    preview   = format_announcement_card(message)
    await update.effective_message.reply_text(
        f"✅ <b>Announcement pinned!</b>\n\n"
        f"<i>Preview (what users see on /start):</i>\n\n"
        f"{preview}",
        parse_mode="HTML",
        reply_markup=announcement_keyboard(SUPPORT_URL, ann_count=ann_count, ann_idx=0),
    )
    log.info("Admin pinned announcement: user=%s chars=%d", user.id, len(message))


# ── /unpin ────────────────────────────────────────────────────────────────────

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


# ── /updateannouncement <message> ─────────────────────────────────────────────

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
    history   = await loop.run_in_executor(None, lambda: get_announcement_history(limit=10))
    ann_count = len(history)
    preview   = format_announcement_card(message)
    await update.effective_message.reply_text(
        f"✅ <b>Announcement updated!</b>\n\n"
        f"<i>Live preview:</i>\n\n{preview}",
        parse_mode="HTML",
        reply_markup=announcement_keyboard(SUPPORT_URL, ann_count=ann_count, ann_idx=0),
    )
    log.info("Admin updated announcement: user=%s", user.id)


# ── /pinphoto <url|remove> ────────────────────────────────────────────────────

async def pinphoto_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pinphoto <url|remove> — Attach or remove a banner image."""
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
                reply_markup=announcement_keyboard(SUPPORT_URL),
            )
        except Exception:
            await update.effective_message.reply_text(
                f"⚠️ URL saved but could not preview — confirm it is a direct image link.\n"
                f"<code>{html.escape(url)}</code>",
                parse_mode="HTML",
                reply_markup=admin_main_menu(),
            )
    else:
        await update.effective_message.reply_text(
            "✅ Banner image removed from announcement.",
            reply_markup=admin_main_menu(),
        )
    log.info("Admin pinphoto: user=%s url=%s", user.id, url)


# ── /listannouncements ────────────────────────────────────────────────────────

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
        reply_markup=back_to_menu(),
    )


# ── /announce_channel ─────────────────────────────────────────────────────────

async def announce_channel_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/announce_channel — Push the active announcement to the Telegram channel."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    from config.settings import TELEGRAM_CHANNEL_ID
    if not TELEGRAM_CHANNEL_ID:
        await update.effective_message.reply_text(
            "⚠️ <b>TELEGRAM_CHANNEL_ID is not set.</b>\n\n"
            "Add it to Railway environment variables and redeploy.",
            parse_mode="HTML",
        )
        return

    loop = asyncio.get_running_loop()
    ann  = await loop.run_in_executor(None, get_active_announcement)
    if not ann:
        await update.effective_message.reply_text(
            "📭 <b>No active announcement.</b>\n\n"
            "Create one first with <code>/pin &lt;message&gt;</code>",
            parse_mode="HTML",
        )
        return

    card      = format_announcement_card(ann.get("message", ""))
    photo_url = ann.get("photo_url") or ""

    try:
        if photo_url:
            await context.bot.send_photo(
                chat_id=TELEGRAM_CHANNEL_ID,
                photo=photo_url,
                caption=card,
                parse_mode="HTML",
                reply_markup=announcement_keyboard(SUPPORT_URL),
            )
        else:
            await context.bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=card,
                parse_mode="HTML",
                reply_markup=announcement_keyboard(SUPPORT_URL),
            )
        await update.effective_message.reply_text(
            "✅ <b>Announcement sent to channel!</b>",
            parse_mode="HTML",
        )
        log.info("Announcement pushed to channel by admin %s", user.id)
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Failed to send to channel:\n<code>{html.escape(str(exc))}</code>\n\n"
            "Make sure the bot is an admin in the channel.",
            parse_mode="HTML",
        )
        log.error("announce_channel: %s", exc)


# ── /announce_group ───────────────────────────────────────────────────────────

async def announce_group_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/announce_group — Push the active announcement to the Telegram group."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    from config.settings import TELEGRAM_GROUP_ID
    if not TELEGRAM_GROUP_ID:
        await update.effective_message.reply_text(
            "⚠️ <b>TELEGRAM_GROUP_ID is not set.</b>\n\n"
            "Add it to Railway environment variables and redeploy.",
            parse_mode="HTML",
        )
        return

    loop = asyncio.get_running_loop()
    ann  = await loop.run_in_executor(None, get_active_announcement)
    if not ann:
        await update.effective_message.reply_text(
            "📭 <b>No active announcement.</b>\n\n"
            "Create one first with <code>/pin &lt;message&gt;</code>",
            parse_mode="HTML",
        )
        return

    card      = format_announcement_card(ann.get("message", ""))
    photo_url = ann.get("photo_url") or ""

    try:
        if photo_url:
            await context.bot.send_photo(
                chat_id=TELEGRAM_GROUP_ID,
                photo=photo_url,
                caption=card,
                parse_mode="HTML",
                reply_markup=announcement_keyboard(SUPPORT_URL),
            )
        else:
            await context.bot.send_message(
                chat_id=TELEGRAM_GROUP_ID,
                text=card,
                parse_mode="HTML",
                reply_markup=announcement_keyboard(SUPPORT_URL),
            )
        await update.effective_message.reply_text(
            "✅ <b>Announcement sent to group!</b>",
            parse_mode="HTML",
        )
        log.info("Announcement pushed to group by admin %s", user.id)
    except Exception as exc:
        await update.effective_message.reply_text(
            f"❌ Failed to send to group:\n<code>{html.escape(str(exc))}</code>\n\n"
            "Make sure the bot is an admin in the group.",
            parse_mode="HTML",
        )
        log.error("announce_group: %s", exc)


# ── /announce_both ────────────────────────────────────────────────────────────

async def announce_both_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/announce_both — Push the active announcement to both channel and group."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    from config.settings import TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID
    if not TELEGRAM_CHANNEL_ID and not TELEGRAM_GROUP_ID:
        await update.effective_message.reply_text(
            "⚠️ <b>Neither TELEGRAM_CHANNEL_ID nor TELEGRAM_GROUP_ID is set.</b>\n\n"
            "Add them to Railway environment variables and redeploy.",
            parse_mode="HTML",
        )
        return

    loop = asyncio.get_running_loop()
    ann  = await loop.run_in_executor(None, get_active_announcement)
    if not ann:
        await update.effective_message.reply_text(
            "📭 <b>No active announcement.</b>\n\n"
            "Create one first with <code>/pin &lt;message&gt;</code>",
            parse_mode="HTML",
        )
        return

    card      = format_announcement_card(ann.get("message", ""))
    photo_url = ann.get("photo_url") or ""
    results   = []

    for label, chat_id in [("channel", TELEGRAM_CHANNEL_ID), ("group", TELEGRAM_GROUP_ID)]:
        if not chat_id:
            results.append(f"⏭️ {label.capitalize()}: skipped (ID not configured)")
            continue
        try:
            if photo_url:
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=photo_url,
                    caption=card,
                    parse_mode="HTML",
                    reply_markup=announcement_keyboard(SUPPORT_URL),
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=card,
                    parse_mode="HTML",
                    reply_markup=announcement_keyboard(SUPPORT_URL),
                )
            results.append(f"✅ {label.capitalize()}: sent")
            log.info("Announcement pushed to %s by admin %s", label, user.id)
        except Exception as exc:
            results.append(f"❌ {label.capitalize()}: {html.escape(str(exc))}")
            log.error("announce_both → %s: %s", label, exc)

    await update.effective_message.reply_text(
        "<b>📢 Broadcast Results:</b>\n\n" + "\n".join(results),
        parse_mode="HTML",
    )
