"""
FundzAiBot — Master inline-keyboard callback dispatcher.
Version 5.0.1 — Executive callbacks removed.

Only user-facing callbacks remain.
Administrative authority belongs to Fundz Company Headquarters.
"""

import asyncio

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin
from services.database import (
    get_or_create_user, update_user, set_system_prompt,
    clear_conversation, get_recent_errors, count_users,
    get_total_stats, get_all_users, get_all_images,
)
from services.language import get_user_language, get_string
from services.queue_manager import queue_manager
from utils.helpers import time_ago, format_number
from utils.keyboards import (
    main_menu,
    ai_styles_menu, image_styles_menu,
    settings_menu, vip_menu, back_to_menu,
)
from utils.logger import get_logger

log = get_logger(__name__)


async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    user = update.effective_user
    if not user:
        await query.answer()
        return

    data: str = query.data or ""
    admin = is_admin(user.id)
    log.debug("Callback: user=%s admin=%s data=%s", user.id, admin, data)

    # ── Membership verify callback ────────────────────────────────────────────
    if data == "membership:verify":
        await query.answer("Checking membership…")
        from handlers.membership import handle_membership_verify_callback
        await handle_membership_verify_callback(query, context)
        return

    # ── VIP purchase ──────────────────────────────────────────────────────────
    if data.startswith("vip:"):
        plan = data[4:]
        from handlers.payment import handle_vip_purchase_callback
        await handle_vip_purchase_callback(query, plan, context)
        return

    # ── Image style selection ─────────────────────────────────────────────────
    if data.startswith("imgstyle:"):
        style = data[9:]
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, update_user, user.id, {"image_style": style})
        await query.answer(f"Image style set to: {style.capitalize()}")
        try:
            await query.edit_message_text(
                f"🎨 Image style set to <b>{style.capitalize()}</b>.\n\nSend me a description to generate an image!",
                parse_mode="HTML",
                reply_markup=back_to_menu(),
            )
        except Exception:
            pass
        return

    # ── AI style selection ────────────────────────────────────────────────────
    if data.startswith("style:"):
        style = data[6:]
        loop = asyncio.get_running_loop()
        style_prompts = {
            "default":   "You are a helpful, knowledgeable AI assistant.",
            "teacher":   "You are a patient, encouraging teacher who explains concepts clearly with examples.",
            "comedian":  "You are a witty, playful assistant who keeps things light and entertaining.",
            "scientist": "You are a rigorous, precise scientist who explains things accurately with evidence.",
            "writer":    "You are a skilled creative writer who crafts beautiful, engaging prose.",
            "business":  "You are a sharp business advisor who gives practical, actionable advice.",
            "coder":     "You are an expert software engineer who writes clean, efficient code.",
            "creative":  "You are a wildly creative thinker who sees unconventional solutions.",
        }
        prompt = style_prompts.get(style, style_prompts["default"])
        await loop.run_in_executor(None, update_user, user.id, {"ai_style": style})
        await loop.run_in_executor(None, set_system_prompt, user.id, prompt)
        await query.answer(f"Style: {style.capitalize()}")
        try:
            await query.edit_message_text(
                f"🤖 AI style set to <b>{style.capitalize()}</b>.\n\nStart chatting and I'll respond in this style!",
                parse_mode="HTML",
                reply_markup=back_to_menu(),
            )
        except Exception:
            pass
        return

    # ── Language selection ────────────────────────────────────────────────────
    if data.startswith("lang:"):
        lang_code = data[5:]
        from services.language import set_user_language
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, set_user_language, user.id, lang_code)
        await query.answer(f"Language updated!")
        # Report to HQ
        try:
            from services.hq_sync import event_language_change
            event_language_change(user.id, user.username, lang_code)
        except Exception:
            pass
        try:
            await query.edit_message_text(
                f"🌐 Language set to <b>{lang_code}</b>.",
                parse_mode="HTML",
                reply_markup=back_to_menu(),
            )
        except Exception:
            pass
        return

    # ── Onboarding callbacks ──────────────────────────────────────────────────
    if data.startswith("onboard:"):
        from handlers.onboarding import handle_onboarding_callback
        await handle_onboarding_callback(query, context)
        return

    # ── Announcement callbacks ────────────────────────────────────────────────
    if data.startswith("ann:"):
        # Announcement buttons — just acknowledge and dismiss
        await query.answer()
        try:
            await query.edit_message_reply_markup(reply_markup=None)
        except Exception:
            pass
        return

    # ── Main menu routing ─────────────────────────────────────────────────────
    if data == "menu:back" or data == "menu:main":
        loop = asyncio.get_running_loop()
        u = await loop.run_in_executor(None, get_or_create_user, user.id, {
            "username":   user.username,
            "first_name": user.first_name,
            "last_name":  user.last_name,
        })
        lang = await loop.run_in_executor(None, get_user_language, user.id)
        welcome = get_string("welcome_back", lang).format(name=user.first_name or "there")
        try:
            await query.edit_message_text(
                welcome,
                parse_mode="HTML",
                reply_markup=main_menu(),
            )
        except Exception:
            pass
        return

    if data == "menu:chat":
        try:
            await query.edit_message_text(
                "🤖 <b>AI Chat</b>\n\nJust send me any message and I'll respond.\n\n"
                "Use /style to change my personality, /clear to reset history.",
                parse_mode="HTML",
                reply_markup=back_to_menu(),
            )
        except Exception:
            pass
        return

    if data == "menu:image":
        try:
            await query.edit_message_text(
                "🎨 <b>Image Generation</b>\n\nUse /image &lt;description&gt; to generate an image.\n\n"
                "Example: <code>/image a futuristic city at sunset</code>",
                parse_mode="HTML",
                reply_markup=back_to_menu(),
            )
        except Exception:
            pass
        return

    if data == "menu:profile":
        await query.answer()
        from handlers.profile import profile_handler
        await profile_handler(update, context)
        return

    if data == "menu:stats":
        await query.answer()
        from handlers.profile import stats_handler
        await stats_handler(update, context)
        return

    if data == "menu:referral":
        await query.answer()
        from handlers.profile import referral_handler
        await referral_handler(update, context)
        return

    if data == "menu:settings":
        loop = asyncio.get_running_loop()
        u = await loop.run_in_executor(None, get_or_create_user, user.id, {})
        style = u.get("ai_style", "default") if u else "default"
        try:
            await query.edit_message_text(
                "⚙️ <b>Settings</b>",
                parse_mode="HTML",
                reply_markup=settings_menu(current_style=style),
            )
        except Exception:
            pass
        return

    if data == "menu:vip":
        try:
            await query.edit_message_text(
                "💎 <b>VIP Plans</b>\n\nUpgrade to unlock more daily credits and priority AI access.",
                parse_mode="HTML",
                reply_markup=vip_menu(),
            )
        except Exception:
            pass
        return

    if data == "menu:language":
        from handlers.language import language_handler
        await query.answer()
        # Redirect to the language command handler
        await language_handler(update, context)
        return

    if data == "menu:help":
        from handlers.help import HELP_TEXT
        from config.settings import BOT_NAME
        try:
            await query.edit_message_text(
                HELP_TEXT.format(bot=BOT_NAME),
                parse_mode="HTML",
                reply_markup=back_to_menu(),
            )
        except Exception:
            pass
        return

    # ── Settings sub-actions ──────────────────────────────────────────────────
    if data == "settings:clear_history":
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, clear_conversation, user.id)
        await query.answer("Chat history cleared!")
        try:
            await query.edit_message_text(
                "🗑️ Chat history cleared. Start fresh!",
                reply_markup=back_to_menu(),
            )
        except Exception:
            pass
        return

    if data == "settings:toggle_notif":
        loop = asyncio.get_running_loop()
        u = await loop.run_in_executor(None, get_or_create_user, user.id, {})
        current = u.get("notifications_enabled", True) if u else True
        new_val = not current
        await loop.run_in_executor(None, update_user, user.id, {"notifications_enabled": new_val})
        await query.answer("Notifications " + ("enabled" if new_val else "disabled"))
        return

    if data == "settings:export":
        await query.answer("Your data is stored securely. Contact support to request an export.", show_alert=True)
        return

    # ── AI style submenu ──────────────────────────────────────────────────────
    if data == "menu:styles":
        try:
            await query.edit_message_text(
                "🎭 <b>AI Personality Styles</b>\n\nChoose how I respond to you:",
                parse_mode="HTML",
                reply_markup=ai_styles_menu(),
            )
        except Exception:
            pass
        return

    # ── Queue status (for any user) ───────────────────────────────────────────
    if data == "queue:status":
        q = queue_manager.stats()
        await query.answer(f"Queue: {q['queue_size']} pending", show_alert=False)
        return

    # ── Unknown callback — silent dismiss ─────────────────────────────────────
    log.debug("Unhandled callback data=%s from user=%s", data, user.id)
    await query.answer()
