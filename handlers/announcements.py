"""
FundzAiBot — Sticky announcement system.
Admin-only commands. The active announcement is shown to users on /start
as a premium Telegram-native blockquote card (left blue accent line, dark bg).

NOT Telegram's native pin_chat_message — this is a bot-rendered announcement card
that simulates the premium sticky announcement experience.

Phase 5 upgrade:
  - Smart show: returning users only see announcements they haven't seen
    (tracked via seen_announcement_id in user data / bot_data)
  - Priority override: ANNOUNCEMENT_PRIORITY=high always shows regardless
  - Scheduled announcement support: schedule_at field, show only after that time
  - /schedule_announcement <ISO datetime> <message> command added
"""

import asyncio
import html
import time

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

# ── Seen-announcement tracking (in-memory per bot session) ────────────────────
# Maps user_id → set of announcement IDs seen this session.
# On bot restart the set clears — users see the latest announcement again
# on their next /start. This is intentional: new session = fresh check.
_SEEN_ANNOUNCEMENTS: dict[int, set] = {}

_ANN_CACHE_KEY = "ann_seen_v1"  # key in bot_data for persistent cross-restart tracking


def _has_seen(user_id: int, ann_id, bot_data: dict) -> bool:
    """Check if a user has already seen a given announcement this session."""
    seen = bot_data.get(_ANN_CACHE_KEY, {})
    return ann_id in seen.get(user_id, set())


def _mark_seen(user_id: int, ann_id, bot_data: dict) -> None:
    """Mark an announcement as seen for a user."""
    seen = bot_data.setdefault(_ANN_CACHE_KEY, {})
    seen.setdefault(user_id, set()).add(ann_id)


def _is_scheduled_ready(ann: dict) -> bool:
    """Return True if the announcement's schedule_at has passed (or is not set)."""
    schedule_at = ann.get("schedule_at")
    if not schedule_at:
        return True
    try:
        from datetime import datetime, timezone
        if isinstance(schedule_at, str):
            dt = datetime.fromisoformat(schedule_at.replace("Z", "+00:00"))
        else:
            dt = schedule_at
        return datetime.now(timezone.utc) >= dt
    except Exception:
        return True  # parsing failure → show it


def _is_high_priority(ann: dict) -> bool:
    """Return True for announcements that should always show (priority=high)."""
    return (ann.get("priority") or "").lower() == "high"


# ── Smart announcement show logic ──────────────────────────────────────────────

async def maybe_show_announcement(
    bot,
    user_id: int,
    is_new_user: bool,
    bot_data: dict,
) -> bool:
    """
    Show the active announcement to a user — smart logic:
      - New users: always show (first visit).
      - Returning users: only show if they haven't seen this announcement yet,
        OR if the announcement is high-priority.
      - Scheduled announcements: only show after schedule_at has passed.
    Returns True if an announcement was shown, False otherwise.
    """
    try:
        loop = asyncio.get_running_loop()
        ann = await loop.run_in_executor(None, get_active_announcement)
        if not ann:
            return False

        # Check schedule
        if not _is_scheduled_ready(ann):
            return False

        ann_id = ann.get("id") or ann.get("created_at") or "default"

        # Returning users: skip if already seen AND not high-priority
        if not is_new_user and not _is_high_priority(ann):
            if _has_seen(user_id, ann_id, bot_data):
                log.debug("Announcement skip (already seen): user=%s ann=%s", user_id, ann_id)
                return False

        # Show it
        await send_sticky_announcement(bot, user_id, ann)
        _mark_seen(user_id, ann_id, bot_data)
        return True

    except Exception as exc:
        log.debug("maybe_show_announcement skipped: %s", exc)
        return False


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
      • /start (smart: only new/unseen announcements)
      • Returning user flows
      • Admin push commands (/announce_channel, /announce_group, /announce_both)

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
            "<code>/pin 🎉 FundzAiBot v4.0 is live! New features inside.</code>\n\n"
            "<i>Users see this on /start. Previous pin is replaced.</i>\n\n"
            "<b>Priority flag:</b>\n"
            "<code>/pin_priority &lt;message&gt;</code> — Always shows, even if user already saw it.",
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


# ── /pin_priority <message> ───────────────────────────────────────────────────

async def pin_priority_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/pin_priority <message> — Pin a HIGH PRIORITY announcement (always shows)."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "📌 <b>Pin High-Priority Announcement</b>\n\n"
            "Usage: <code>/pin_priority &lt;message&gt;</code>\n\n"
            "<i>High-priority announcements show on EVERY /start, "
            "even if the user already saw them.</i>",
            parse_mode="HTML",
        )
        return

    message = " ".join(args)
    loop = asyncio.get_running_loop()

    # Create announcement then set priority=high
    await loop.run_in_executor(None, lambda: create_announcement(message, created_by=user.id))

    # Try to set priority on the active announcement
    try:
        from services.database import _safe_patch, _headers, _url
        await loop.run_in_executor(
            None,
            lambda: _safe_patch(
                f"{_url('announcements')}?is_active=eq.true",
                headers=_headers(),
                json={"priority": "high"},
            )
        )
    except Exception as exc:
        log.warning("Could not set priority on announcement: %s", exc)

    history   = await loop.run_in_executor(None, lambda: get_announcement_history(limit=10))
    ann_count = len(history)
    preview   = format_announcement_card(message)
    await update.effective_message.reply_text(
        f"✅ <b>HIGH PRIORITY Announcement pinned!</b>\n\n"
        f"⚡ This will show on every /start, even for users who already saw it.\n\n"
        f"<i>Preview:</i>\n\n{preview}",
        parse_mode="HTML",
        reply_markup=announcement_keyboard(SUPPORT_URL, ann_count=ann_count, ann_idx=0),
    )
    log.info("Admin pinned HIGH PRIORITY announcement: user=%s", user.id)


# ── /schedule_announcement <ISO datetime> <message> ──────────────────────────

async def schedule_announcement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/schedule_announcement <YYYY-MM-DDTHH:MM> <message> — Schedule a future announcement."""
    user = update.effective_user
    if not _guard(user):
        await update.effective_message.reply_text("⛔ Admin only.")
        return

    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "🗓️ <b>Schedule an Announcement</b>\n\n"
            "Usage:\n"
            "<code>/schedule_announcement 2026-06-15T14:00 🎉 New feature is live!</code>\n\n"
            "Datetime format: <code>YYYY-MM-DDTHH:MM</code> (UTC)\n\n"
            "<i>The announcement will be created now but shown to users only "
            "after the scheduled time.</i>",
            parse_mode="HTML",
        )
        return

    dt_str  = args[0].strip()
    message = " ".join(args[1:])

    # Validate datetime
    try:
        from datetime import datetime, timezone
        # Accept YYYY-MM-DDTHH:MM or YYYY-MM-DD HH:MM
        dt_str_clean = dt_str.replace(" ", "T")
        if len(dt_str_clean) == 16:
            dt_str_clean += ":00"
        schedule_dt = datetime.fromisoformat(dt_str_clean).replace(tzinfo=timezone.utc)
    except ValueError:
        await update.effective_message.reply_text(
            "❌ Invalid datetime format. Use: <code>YYYY-MM-DDTHH:MM</code>\n"
            "Example: <code>2026-06-20T09:00</code>",
            parse_mode="HTML",
        )
        return

    loop = asyncio.get_running_loop()

    # Create the announcement with schedule_at set
    await loop.run_in_executor(None, lambda: create_announcement(message, created_by=user.id))

    # Try to set schedule_at on the DB record
    try:
        from services.database import _safe_patch, _headers, _url
        await loop.run_in_executor(
            None,
            lambda: _safe_patch(
                f"{_url('announcements')}?is_active=eq.true",
                headers=_headers(),
                json={"schedule_at": schedule_dt.isoformat(), "is_active": False},
            )
        )
        scheduled_ok = True
    except Exception as exc:
        log.warning("Could not set schedule_at: %s", exc)
        scheduled_ok = False

    from datetime import timezone as tz
    formatted_dt = schedule_dt.strftime("%Y-%m-%d %H:%M UTC")

    if scheduled_ok:
        await update.effective_message.reply_text(
            f"🗓️ <b>Announcement Scheduled!</b>\n\n"
            f"📅 Will appear on: <b>{formatted_dt}</b>\n\n"
            f"<i>Message preview:</i>\n{format_announcement_card(message)}\n\n"
            f"<i>Note: The announcement is stored but inactive until the scheduled time.</i>",
            parse_mode="HTML",
        )
    else:
        await update.effective_message.reply_text(
            f"⚠️ <b>Announcement created</b> (schedule_at field not persisted — "
            f"upgrade your DB schema with the schedule_at column to enable scheduling).\n\n"
            f"The announcement is active now. Target time was: {formatted_dt}",
            parse_mode="HTML",
        )
    log.info("Admin scheduled announcement: user=%s dt=%s", user.id, formatted_dt)


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
        priority = " ⚡HIGH" if (a.get("priority") or "").lower() == "high" else ""
        scheduled = ""
        if a.get("schedule_at"):
            scheduled = f" 🗓️ scheduled"
        preview = html.escape((a.get("message") or "")[:90])
        if len(a.get("message") or "") > 90:
            preview += "…"
        photo   = " 🖼️" if a.get("photo_url") else ""
        lines.append(
            f"{i}. [{status}]{priority}{scheduled}{photo}\n"
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
