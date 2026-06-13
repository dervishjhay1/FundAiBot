"""
FundzAiBot — /start handler.

Flow:
  Admin   → bypass everything → admin welcome screen
  New user → maintenance/new-user gate → register → language detect → referral
           → force-join check (channel + group) → onboarding → main menu
  Returning user → force-join soft-reminder → main menu

Force-join:
  If TELEGRAM_CHANNEL_ID or TELEGRAM_GROUP_ID is set in env, new users must
  join both before proceeding.  A "Verify Access" inline button triggers the
  membership:verify callback.  Returning users who have left get a soft DM
  reminder (from membership_change_handler) but are never hard-blocked.

Language detection:
  For brand-new users, Telegram's language_code is detected and saved
  automatically before showing onboarding, so their first interaction is in
  their own language.  Users can always change later with /language.
"""

import asyncio
import html

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS, TELEGRAM_CHANNEL_NAME, TELEGRAM_GROUP_NAME
from services.database import get_or_create_user, get_user, record_referral, set_system_prompt, ensure_credits
from services.onboarding import get_onboarding, init_onboarding, needs_onboarding
from services.language import get_string, get_user_language, detect_language, save_user_language, can_use_language
from utils.keyboards import main_menu, admin_main_menu, join_screen_keyboard
from utils.logger import get_logger

log = get_logger(__name__)


# ── Helpers ────────────────────────────────────────────────────────────────────

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
    Show the active pinned announcement card on /start.
    Falls back silently on any error.
    """
    try:
        from services.database import get_active_announcement
        from handlers.announcements import send_sticky_announcement

        loop = asyncio.get_running_loop()
        ann = await loop.run_in_executor(None, get_active_announcement)
        if ann:
            await send_sticky_announcement(context.bot, uid, ann)
    except Exception as exc:
        log.debug("Announcement display skipped for user %s: %s", uid, exc)


def _build_join_screen(first_name: str) -> str:
    """Build the welcome + force-join screen text for new unverified users."""
    chan = TELEGRAM_CHANNEL_NAME or "FundzAi Channel"
    grp  = TELEGRAM_GROUP_NAME  or "FundzAi Community"
    name = html.escape(first_name or "there")
    return (
        f"👋 <b>Welcome to FundzAiBot, {name}!</b>\n\n"
        f"Your AI-powered assistant for:\n"
        f"• 🤖 AI Chat & Image Generation\n"
        f"• 💎 VIP Features & Priority Access\n"
        f"• 👥 Community & Support\n"
        f"• 🎁 Referral Rewards\n\n"
        f"<b>Before continuing, please join our ecosystem:</b>\n\n"
        f"📢 <b>{chan}</b> — official announcements & updates\n"
        f"👥 <b>{grp}</b> — community, support & discussions\n\n"
        f"<i>Tap the buttons below to join, then tap ✅ Verify Access</i>"
    )


# ── Main handler ───────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    if not user:
        return

    uid   = user.id
    admin = is_admin(uid)
    loop  = asyncio.get_running_loop()

    # ── Admin welcome — bypass all gates ──────────────────────────────────────
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
        "last_name":  user.last_name  or "",
        "username":   user.username   or "",
    }

    existing = await loop.run_in_executor(None, get_user, uid)
    is_new   = existing is None

    if is_new and not FEATURE_FLAGS["new_users_enabled"]:
        await update.message.reply_text(
            "🚫 <b>New registrations are currently paused.</b>\n\nPlease try again later.",
            parse_mode="HTML",
        )
        return

    if FEATURE_FLAGS["maintenance_mode"]:
        await update.message.reply_text(
            "🚧 <b>FundzAiBot is under maintenance.</b>\n\nWe'll be back shortly!",
            parse_mode="HTML",
        )
        return

    # Register / fetch user
    db_user = await loop.run_in_executor(None, lambda: get_or_create_user(uid, **tg_user))

    # ── Language auto-detection for new users ─────────────────────────────────
    if is_new:
        detected = detect_language(user.language_code)
        if detected != "en" and can_use_language(detected, db_user, uid):
            await loop.run_in_executor(None, lambda: save_user_language(uid, detected))
            db_user = await loop.run_in_executor(None, lambda: get_or_create_user(uid, **tg_user))
            log.info("Auto-detected language: user=%s lang=%s (tg=%s)", uid, detected, user.language_code)

    lang = get_user_language(db_user, uid)

    # ── Referral deep-link handling ───────────────────────────────────────────
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

    # ── Force-join gate for NEW users ─────────────────────────────────────────
    if is_new:
        from config.settings import TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID
        force_join_enabled = bool(TELEGRAM_CHANNEL_ID or TELEGRAM_GROUP_ID)

        if force_join_enabled:
            from handlers.membership import check_membership
            status = await check_membership(context.bot, uid, context.bot_data)

            if not status["all_ok"]:
                await update.message.reply_text(
                    _build_join_screen(user.first_name or "there"),
                    parse_mode="HTML",
                    reply_markup=join_screen_keyboard(),
                )
                log.info("/start user=%s new=True → force-join screen (chan=%s grp=%s)",
                         uid, status["channel"], status["group"])
                return

    # ── Onboarding check ──────────────────────────────────────────────────────
    show_onboard = await loop.run_in_executor(None, needs_onboarding, uid, is_new)

    if show_onboard:
        await loop.run_in_executor(None, init_onboarding, uid, source)
        from handlers.onboarding import show_onboarding
        await show_onboarding(update, context, source=source)
        log.info("/start user=%s new=%s → onboarding shown", uid, is_new)
        return

    # ── Returning user — show main menu ───────────────────────────────────────
    style = (db_user or {}).get("ai_style", "default")
    await loop.run_in_executor(None, set_system_prompt, uid, style)

    name = html.escape(user.first_name or "friend")
    text = get_string(lang, "welcome_back", name=name)

    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_menu())

    # Show active announcement card
    await _show_announcement(update, context, uid)

    log.info("/start user=%s new=%s → main menu", uid, is_new)
