"""
FundzAiBot — /start handler.
Registers users, handles referral deep-links, shows onboarding for new users,
then shows the main menu with the active sticky announcement.

The announcement is sent via send_sticky_announcement() which:
  1. Sends the card with support + dismiss buttons
  2. Calls bot.pin_chat_message() → creates the native Telegram sticky banner
"""

import asyncio
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS
from services.database import get_or_create_user, get_user, record_referral, set_system_prompt, ensure_credits
from services.onboarding import get_onboarding, init_onboarding, needs_onboarding
from utils.keyboards import main_menu, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)

WELCOME_BACK = """\
👋 <b>Welcome back, {name}!</b>

What shall we do today?\
"""

WELCOME_ADMIN = """\
🛡️ <b>Welcome back, Admin!</b>

<b>FundzAiBot Control Centre</b>

You have <b>full access</b> — unlimited chats, unlimited images, all admin controls.

<b>Bot Status:</b>
  💬 Chat: {chat_status}
  🎨 Images: {image_status}
  🚧 Maintenance: {maint_status}
  🌐 New Users: {users_status}

Use the panel below to manage your bot.\
"""


def _source_from_args(args: list[str]) -> str:
    """Determine referral_source from /start deep-link args."""
    if not args:
        return "direct"
    code = args[0]
    if code.startswith("CHAN"):
        return "channel"
    if code.startswith("GRP"):
        return "group"
    if code.startswith("REF"):
        return "referral"
    return "bot"


async def _show_announcement(update: Update, context, uid: int) -> None:
    """
    Show the active pinned announcement with native Telegram sticky pinning.
    Uses send_sticky_announcement() which sends the card + pins it.
    Falls back silently if no announcement or any error.
    """
    try:
        from services.database import get_active_announcement
        from handlers.announcements import send_sticky_announcement

        loop = asyncio.get_running_loop()
        ann = await loop.run_in_executor(None, get_active_announcement)
        if ann:
            await send_sticky_announcement(context.bot, uid, ann, pin=True)
    except Exception as exc:
        log.debug("Announcement display skipped for user %s: %s", uid, exc)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    uid   = user.id
    admin = is_admin(uid)

    # ── Admin welcome — bypass all normal flow ─────────────────────────────────
    if admin:
        ff = FEATURE_FLAGS
        text = WELCOME_ADMIN.format(
            chat_status  = "✅ ON" if ff["chat_enabled"]      else "❌ OFF",
            image_status = "✅ ON" if ff["image_enabled"]     else "❌ OFF",
            maint_status = "🚧 ON" if ff["maintenance_mode"]  else "✅ OFF",
            users_status = "✅ ON" if ff["new_users_enabled"] else "❌ OFF",
        )
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: get_or_create_user(uid, first_name=user.first_name or "", username=user.username or ""),
        )
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_main_menu())
        log.info("/start admin=%s", uid)
        return

    # ── New user registration gate ─────────────────────────────────────────────
    tg_user = {
        "first_name": user.first_name or "",
        "last_name":  user.last_name or "",
        "username":   user.username or "",
    }

    loop = asyncio.get_running_loop()
    existing = await loop.run_in_executor(None, get_user, uid)
    is_new = existing is None

    if is_new and not FEATURE_FLAGS["new_users_enabled"]:
        await update.message.reply_text(
            "🚫 <b>New registrations are currently paused.</b>\n\n"
            "Please try again later.",
            parse_mode="HTML",
        )
        return

    # Maintenance gate for regular users
    if FEATURE_FLAGS["maintenance_mode"]:
        await update.message.reply_text(
            "🚧 <b>FundzAiBot is under maintenance.</b>\n\nWe'll be back shortly!",
            parse_mode="HTML",
        )
        return

    # Register user if new
    db_user = await loop.run_in_executor(None, lambda: get_or_create_user(uid, **tg_user))

    # ── Referral deep-link handling ────────────────────────────────────────────
    source = _source_from_args(context.args or [])

    if context.args:
        code = context.args[0]
        if code.startswith("REF"):
            try:
                referrer_id = int(code[3:])
                if await loop.run_in_executor(None, record_referral, referrer_id, uid):
                    await update.message.reply_text(
                        "🎁 <b>Referral bonus applied!</b>\n"
                        "Your friend earned +10 chat &amp; +2 image credits.",
                        parse_mode="HTML",
                    )
            except (ValueError, Exception) as exc:
                log.debug("Bad referral code %s: %s", code, exc)

    # ── Onboarding check ───────────────────────────────────────────────────────
    show_onboard = await loop.run_in_executor(None, needs_onboarding, uid, is_new)

    if show_onboard:
        await loop.run_in_executor(None, init_onboarding, uid, source)
        from handlers.onboarding import show_onboarding
        await show_onboarding(update, context, source=source)
        log.info("/start user=%s new=%s → onboarding shown", uid, is_new)
        return

    # ── Returning user — normal flow ───────────────────────────────────────────
    style = (db_user or {}).get("ai_style", "default")
    await loop.run_in_executor(None, set_system_prompt, uid, style)

    name = user.first_name or "friend"
    text = WELCOME_BACK.format(name=name)

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())

    # Show the active announcement with native sticky pin
    await _show_announcement(update, context, uid)

    log.info("/start user=%s new=%s → main menu", uid, is_new)
