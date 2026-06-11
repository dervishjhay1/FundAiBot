"""
FundzAiBot — Pinned Announcement command handlers.

Admin-only commands:
  /pin <message>               — set a new pinned announcement globally
  /unpin                       — remove the active pinned announcement
  /updateannouncement <message>— edit the current announcement text
  /pinphoto <url>              — attach a banner/image URL to the announcement
  /listannouncements           — show recent announcement history

All commands are gated by @admin_only.
"""

import asyncio
import html

from telegram import Update
from telegram.ext import ContextTypes

from services.announcements import (
    set_announcement,
    unpin_announcement,
    update_announcement_text,
    attach_photo,
    list_announcement_history,
)
from utils.admin_guard import admin_only
from utils.helpers import time_ago
from utils.keyboards import back_to_menu
from utils.logger import get_logger

log = get_logger(__name__)


@admin_only
async def pin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /pin <message>
    Set a new pinned announcement. Supports HTML formatting and emojis.
    """
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: /pin &lt;message&gt;\n\n"
            "Example:\n"
            "<code>/pin 🎉 New feature released! Check it out.</code>",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    message = " ".join(args)
    user_id = update.effective_user.id
    loop    = asyncio.get_running_loop()

    ok, result = await loop.run_in_executor(
        None, set_announcement, message, user_id, ""
    )
    await update.effective_message.reply_text(result, parse_mode="HTML", reply_markup=back_to_menu())
    if ok:
        log.info("Pinned announcement set by admin %s", user_id)


@admin_only
async def unpin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/unpin — Remove the active pinned announcement."""
    user_id = update.effective_user.id
    loop    = asyncio.get_running_loop()

    ok, result = await loop.run_in_executor(None, unpin_announcement, user_id)
    await update.effective_message.reply_text(result, parse_mode="HTML", reply_markup=back_to_menu())
    if ok:
        log.info("Announcement unpinned by admin %s", user_id)


@admin_only
async def update_announcement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /updateannouncement <message>
    Edit the text of the currently active announcement in place.
    """
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: /updateannouncement &lt;new message text&gt;",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    message = " ".join(args)
    user_id = update.effective_user.id
    loop    = asyncio.get_running_loop()

    ok, result = await loop.run_in_executor(
        None, update_announcement_text, message, user_id
    )
    await update.effective_message.reply_text(result, parse_mode="HTML", reply_markup=back_to_menu())
    if ok:
        log.info("Announcement text updated by admin %s", user_id)


@admin_only
async def pinphoto_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /pinphoto <url>
    Attach a banner image URL to the current active announcement.
    """
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: /pinphoto &lt;image_url&gt;\n\n"
            "Provide a direct image URL to attach to the active announcement.",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    photo_url = args[0].strip()
    user_id   = update.effective_user.id
    loop      = asyncio.get_running_loop()

    ok, result = await loop.run_in_executor(None, attach_photo, photo_url, user_id)
    await update.effective_message.reply_text(result, parse_mode="HTML", reply_markup=back_to_menu())
    if ok:
        log.info("Announcement photo set by admin %s", user_id)


@admin_only
async def listannouncements_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/listannouncements — Show the last 10 announcements with status and author."""
    loop    = asyncio.get_running_loop()
    history = await loop.run_in_executor(None, lambda: list_announcement_history(limit=10))

    if not history:
        await update.effective_message.reply_text(
            "📭 No announcement history yet.\n\nUse /pin &lt;message&gt; to create one.",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    lines = [f"<b>📋 Announcement History ({len(history)})</b>\n{'━' * 30}\n"]
    for i, row in enumerate(history, 1):
        status = "📌 Active" if row.get("is_active") else "🗃️ Archived"
        age    = time_ago(row.get("created_at", ""))
        text   = html.escape((row.get("message") or "")[:80])
        photo  = " 🖼️" if row.get("photo_url") else ""
        lines.append(
            f"<b>{i}.</b> {status}{photo}\n"
            f"   {text}{'…' if len(row.get('message','')) > 80 else ''}\n"
            f"   <i>{age}</i>\n"
        )

    await update.effective_message.reply_text(
        "\n".join(lines),
        parse_mode="HTML",
        reply_markup=back_to_menu(),
    )
    log.info("Announcement history viewed by admin %s", update.effective_user.id)
