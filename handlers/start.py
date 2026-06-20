"""
FundzAiBot — /start handler.
Registers users, handles referral deep-links, shows onboarding for new users,
then shows the main menu. Admin gets a dedicated welcome screen and panel button.

Language detection:
  For brand-new users, Telegram's language_code is detected and saved automatically
  before showing onboarding, so their first interaction is in their own language.
  Users can always change later with /language.

All text is served in the user's chosen language.
"""

import asyncio
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS
from services.database import get_or_create_user, get_user, record_referral, set_system_prompt, ensure_credits
from services.onboarding import get_onboarding, init_onboarding, needs_onboarding
from services.language import get_string, get_user_language, detect_language, save_user_language, can_use_language
from utils.keyboards import main_menu, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)


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

    try:
        await _start_handler_inner(update, context, uid, admin)
    except Exception as exc:
        log.error("start_handler unhandled exception for user=%s: %s", uid, exc, exc_info=True)
        try:
            await update.message.reply_text(
                "👋 <b>Welcome to FundzAiBot!</b>\n\n"
                "Something went wrong during startup. Please try /start again in a moment.",
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
        except Exception as final_exc:
            log.error("start_handler: could not send fallback reply: %s", final_exc)


async def _start_handler_inner(
    update: Update, context: ContextTypes.DEFAULT_TYPE, uid: int, admin: bool
) -> None:
    user = update.effective_user
    loop = asyncio.get_running_loop()

    # ── Admin welcome — bypass all normal flow ─────────────────────────────────
    if admin:
        ff = FEATURE_FLAGS
        db_user = await loop.run_in_executor(
            None,
            lambda: get_or_create_user(uid, first_name=user.first_name or "", username=user.username or ""),
        )
        lang = get_user_language(db_user, uid)
        text = get_string(
            lang, "welcome_admin",
            chat_status  = "✅ ON" if ff["chat_enabled"]      else "❌ OFF",
            image_status = "✅ ON" if ff["image_enabled"]     else "❌ OFF",
            maint_status = "🚧 ON" if ff["maintenance_mode"]  else "✅ OFF",
            users_status = "✅ ON" if ff["new_users_enabled"] else "❌ OFF",
        )
        await update.message.reply_text(text, parse_mode="HTML", reply_markup=admin_main_menu())
        log.info("/start admin=%s lang=%s", uid, lang)
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

    # ── Language auto-detection for new users ──────────────────────────────────
    if is_new:
        detected = detect_language(user.language_code)
        if detected != "en" and can_use_language(detected, db_user, uid):
            await loop.run_in_executor(None, lambda: save_user_language(uid, detected))
            db_user = await loop.run_in_executor(None, lambda: get_or_create_user(uid, **tg_user))
            log.info("Auto-detected language: user=%s lang=%s (tg=%s)", uid, detected, user.language_code)

    lang = get_user_language(db_user, uid)

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
    # FIXED: WELCOME_BACK constant was never defined in this file.
    # Using the language-aware get_string() call instead (same key used in callbacks.py).
    text = get_string(lang, "welcome_back", name=name)

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())

    # Show the active announcement with native sticky pin
    await _show_announcement(update, context, uid)

    log.info("/start user=%s new=%s → main menu", uid, is_new)
