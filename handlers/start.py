"""
FundzAiBot — /start handler.
Registers users, handles referral deep-links, shows onboarding for new users,
then shows the main menu. Admin gets a dedicated welcome screen and panel button.
All text is served in the user's chosen language.
"""

import asyncio
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS, FREE_DAILY_CHAT, FREE_DAILY_IMAGE
from services.database import get_or_create_user, get_user, record_referral, set_system_prompt, ensure_credits
from services.onboarding import get_onboarding, init_onboarding, needs_onboarding
from services.language import get_string, get_user_language
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


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    uid = user.id
    admin = is_admin(uid)

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

    existing = await loop.run_in_executor(None, get_user, uid)
    is_new = existing is None

    if is_new and not FEATURE_FLAGS["new_users_enabled"]:
        await update.message.reply_text(
            "🚫 <b>New registrations are currently paused.</b>\n\nPlease try again later.",
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
                        get_string(lang, "referral_bonus"),
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
    text = get_string(lang, "welcome_back", name=name)

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())

    # ── Show sticky announcement card if one is active ─────────────────────────
    try:
        from services.database import get_active_announcement, get_announcement_history
        from handlers.announcements import format_announcement_card
        from utils.keyboards import announcement_keyboard
        ann = await loop.run_in_executor(None, get_active_announcement)
        if ann:
            # Count total announcements for nav buttons
            history   = await loop.run_in_executor(None, lambda: get_announcement_history(limit=10))
            ann_count = len(history)
            msg       = ann.get("message", "")
            photo_url = ann.get("photo_url")
            card      = format_announcement_card(msg, lang=lang)
            kbd       = announcement_keyboard(ann_count=ann_count, ann_idx=0)
            if photo_url:
                try:
                    await update.message.reply_photo(
                        photo=photo_url, caption=card,
                        parse_mode="HTML", reply_markup=kbd,
                    )
                except Exception:
                    await update.message.reply_text(card, parse_mode="HTML", reply_markup=kbd)
            else:
                await update.message.reply_text(card, parse_mode="HTML", reply_markup=kbd)
    except Exception as ann_exc:
        log.debug("Announcement display skipped: %s", ann_exc)

    log.info("/start user=%s new=%s lang=%s → main menu", uid, is_new, lang)
