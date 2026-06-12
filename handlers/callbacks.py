"""
FundzAiBot — Master inline-keyboard callback dispatcher.
Admin gets special routing: admin panel, bot settings, feature flag toggles.
VIP menu is blocked for admin. Onboarding callbacks routed here too.
Language selection callbacks handled here.
"""

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import ADMIN_USER_ID, is_admin
from services.database import (
    get_or_create_user, update_user, set_system_prompt,
    clear_conversation, get_recent_errors, count_users,
    get_total_stats, get_all_users, get_all_images,
)
from services.language import get_user_language, get_string
from services.queue_manager import queue_manager
from utils.helpers import time_ago, format_number
from utils.keyboards import (
    main_menu, admin_main_menu, admin_panel_keyboard,
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

    # ── Enterprise Audit Center (/testaudit callbacks) ────────────────────────
    if data.startswith("audit:"):
        if not admin:
            await query.answer("⛔ Admin only.", show_alert=True)
            return
        action = data[len("audit:"):]
        from handlers.audit import audit_callback
        await audit_callback(query, context, action)
        return

    # ── Announcement navigator (◀ Prev / Next ▶ on announcement card) ──────────
    if data.startswith("announce:nav:"):
        await query.answer()
        try:
            target_idx = int(data.split(":")[-1])
        except (ValueError, IndexError):
            return
        import asyncio
        loop = asyncio.get_running_loop()
        from services.database import get_announcement_history
        from handlers.announcements import format_announcement_card
        from utils.keyboards import announcement_keyboard
        from services.language import get_user_language

        db_user2 = await loop.run_in_executor(
            None, lambda: get_or_create_user(user.id, first_name=user.first_name or "")
        )
        lang = get_user_language(db_user2, user.id)

        history = await loop.run_in_executor(None, lambda: get_announcement_history(limit=10))
        if not history or target_idx < 0 or target_idx >= len(history):
            await query.answer("No more announcements.", show_alert=True)
            return

        ann       = history[target_idx]
        msg_text  = ann.get("message", "")
        photo_url = ann.get("photo_url")
        card      = format_announcement_card(msg_text, lang=lang)
        kbd       = announcement_keyboard(ann_count=len(history), ann_idx=target_idx)

        if photo_url:
            try:
                await context.bot.send_photo(
                    user.id,
                    photo=photo_url,
                    caption=card,
                    parse_mode="HTML",
                    reply_markup=kbd,
                )
                try:
                    await query.delete_message()
                except Exception:
                    pass
                return
            except Exception:
                pass

        try:
            await query.edit_message_text(card, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            await context.bot.send_message(user.id, card, parse_mode="HTML", reply_markup=kbd)
        return

    # ── Image retouch mode selection ──────────────────────────────────────────
    if data.startswith("retouch:"):
        mode = data.split(":", 1)[1]
        from handlers.retouch import handle_retouch_callback
        await handle_retouch_callback(update, context, mode)
        return

    # ── Language detection (first-start prompt) ─────────────────────────────
    if data.startswith("lang_detect:"):
        action = data.split(":", 1)[1]
        from handlers.language import handle_lang_detect_callback
        await handle_lang_detect_callback(query, user.id, action, context)
        return

        # ── Language selection ────────────────────────────────────────────────────
    if data.startswith("lang:"):
        from handlers.language import handle_language_callback
        await handle_language_callback(query, user.id, context)
        return

    # ── Onboarding callbacks ──────────────────────────────────────────────────
    if data.startswith("onboarding:"):
        action = data.split(":", 1)[1]
        from handlers.onboarding import handle_onboarding_verify, handle_onboarding_continue
        if action == "verify":
            await query.answer()
            await handle_onboarding_verify(query, context)
        elif action == "continue":
            await handle_onboarding_continue(query, context)
        else:
            await query.answer()
        return

    # ── Admin panel shortcut (from admin_main_menu button) ────────────────────
    if data == "admin:panel":
        if not admin:
            await query.answer("Admin only.", show_alert=True)
            return
        await query.answer()
        from handlers.admin import handle_admin_panel_callback
        await handle_admin_panel_callback(query, context)
        return

    # ── Admin back to home ────────────────────────────────────────────────────
    if data == "admin:back_home":
        await query.answer()
        await query.edit_message_text(
            "🏠 <b>Main Menu</b>\n\nChoose an option:",
            parse_mode="HTML",
            reply_markup=admin_main_menu() if admin else main_menu(),
        )
        return

    # ── Bot settings panel ────────────────────────────────────────────────────
    if data == "admin:botsettings":
        if not admin:
            await query.answer("Admin only.", show_alert=True)
            return
        await query.answer()
        from handlers.admin import handle_bot_settings_callback
        await handle_bot_settings_callback(query)
        return

    # ── Bot settings toggles ──────────────────────────────────────────────────
    if data.startswith("botsetting:"):
        if not admin:
            await query.answer("Admin only.", show_alert=True)
            return
        flag_key = data.split(":", 1)[1]
        from handlers.admin import handle_botsetting_toggle
        await handle_botsetting_toggle(query, flag_key)
        return

    # ── Admin announcement panel ──────────────────────────────────────────────
    if data == "admin:announcement":
        if not admin:
            await query.answer("Admin only.", show_alert=True)
            return
        await query.answer()
        from services.database import get_active_announcement
        import asyncio
        loop = asyncio.get_running_loop()
        ann = await loop.run_in_executor(None, get_active_announcement)
        if ann:
            from handlers.announcements import format_announcement_card
            from utils.keyboards import announcement_keyboard
            preview = format_announcement_card(ann.get("message", ""))
            text = (
                f"📌 <b>Announcement Manager</b>\n\n"
                f"<b>Status:</b> 🟢 ACTIVE\n"
                f"<b>Preview:</b>\n{preview}\n\n"
                f"<b>Commands:</b>\n"
                f"<code>/pin &lt;message&gt;</code> — New announcement\n"
                f"<code>/updateannouncement &lt;text&gt;</code> — Edit current\n"
                f"<code>/unpin</code> — Remove announcement\n"
                f"<code>/pinphoto &lt;url&gt;</code> — Add banner image\n"
                f"<code>/listannouncements</code> — View history"
            )
        else:
            text = (
                f"📌 <b>Announcement Manager</b>\n\n"
                f"<b>Status:</b> ⚫ No active announcement\n\n"
                f"<b>Commands:</b>\n"
                f"<code>/pin &lt;message&gt;</code> — Create announcement\n"
                f"<code>/listannouncements</code> — View history"
            )
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=admin_panel_keyboard())
        except Exception:
            pass
        return

    # ── Admin find-user prompt ────────────────────────────────────────────────
    if data == "admin:finduser":
        if not admin:
            await query.answer("Admin only.", show_alert=True)
            return
        await query.answer()
        await query.edit_message_text(
            "🔍 <b>Find User</b>\n\n"
            "Use the command:\n<code>/admin_user &lt;user_id&gt;</code>\n\n"
            "Example: <code>/admin_user 123456789</code>",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    # ── Admin onboarding stats ────────────────────────────────────────────────
    if data == "admin:onboarding_stats":
        if not admin:
            await query.answer("Admin only.", show_alert=True)
            return
        await query.answer("Fetching onboarding stats…")
        import asyncio
        from services.onboarding import get_onboarding_stats
        from config.settings import (
            TELEGRAM_CHANNEL_ID, TELEGRAM_CHANNEL_URL,
            TELEGRAM_GROUP_ID, TELEGRAM_GROUP_URL,
            ONBOARDING_CHANNEL_REWARD_CHAT, ONBOARDING_CHANNEL_REWARD_IMAGE,
            ONBOARDING_GROUP_REWARD_CHAT, ONBOARDING_GROUP_REWARD_IMAGE,
            ONBOARDING_REQUIRED,
        )
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        loop = asyncio.get_running_loop()
        stats = await loop.run_in_executor(None, get_onboarding_stats)
        text = (
            f"🚀 <b>Onboarding Stats</b>\n\n"
            f"Shown onboarding:   {stats['total']}\n"
            f"Completed:          {stats['complete']}\n"
            f"Joined channel:     {stats['channel']}\n"
            f"Joined group:       {stats['group']}\n\n"
            f"📢 Channel: <code>{TELEGRAM_CHANNEL_ID or 'Not set'}</code>\n"
            f"👥 Group:   <code>{TELEGRAM_GROUP_ID or 'Not set'}</code>\n"
            f"🔒 Required: {'Yes' if ONBOARDING_REQUIRED else 'No (users can skip)'}\n\n"
            f"Use <code>/admin_onboarding</code> for full settings."
        )
        kbd = InlineKeyboardMarkup([[
            InlineKeyboardButton("🔄 Refresh", callback_data="admin:onboarding_stats"),
            InlineKeyboardButton("« Admin Panel", callback_data="admin:panel"),
        ]])
        try:
            await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
        except Exception:
            pass
        return

    # ── Language menu ─────────────────────────────────────────────────────────
    if data == "menu:language":
        await query.answer()
        import asyncio
        loop = asyncio.get_running_loop()
        db_user = await loop.run_in_executor(
            None, lambda: get_or_create_user(user.id, first_name=user.first_name or "", username=user.username or "")
        )
        lang = get_user_language(db_user, user.id)
        text = get_string(lang, "choose_language")
        from handlers.language import _language_keyboard
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=_language_keyboard(db_user, user.id))
        return

    # ── Main menu navigation ──────────────────────────────────────────────────
    if data == "menu:back":
        await query.answer()
        import asyncio
        loop = asyncio.get_running_loop()
        db_user = await loop.run_in_executor(None, lambda: get_or_create_user(user.id))
        lang = get_user_language(db_user, user.id)
        await query.edit_message_text(
            get_string(lang, "welcome_back", name=user.first_name or "friend"),
            parse_mode="HTML",
            reply_markup=admin_main_menu() if admin else main_menu(),
        )

    elif data == "menu:chat":
        await query.answer()
        db_user = get_or_create_user(user.id)
        style = (db_user or {}).get("ai_style", "default").capitalize()
        await query.edit_message_text(
            f"🤖 <b>AI Chat</b>\n\nCurrent style: <b>{style}</b>\n\n"
            "Just send me any message to start chatting!\nOr pick a personality:",
            parse_mode="HTML",
            reply_markup=ai_styles_menu(),
        )

    elif data == "menu:image":
        await query.answer()
        await query.edit_message_text(
            "🎨 <b>Image Generation</b>\n\nChoose a style, then describe your image:",
            parse_mode="HTML",
            reply_markup=image_styles_menu(),
        )

    elif data == "menu:profile":
        await query.answer()
        from handlers.profile import profile_handler
        await profile_handler(update, context)

    elif data == "menu:stats":
        await query.answer()
        from handlers.profile import stats_handler
        await stats_handler(update, context)

    elif data == "menu:referral":
        await query.answer()
        from handlers.profile import referral_handler
        await referral_handler(update, context)

    elif data == "menu:help":
        await query.answer()
        from handlers.help import help_handler
        await help_handler(update, context)

    elif data == "menu:settings":
        await query.answer()
        db_user = get_or_create_user(user.id)
        style = (db_user or {}).get("ai_style", "default")
        notifs = (db_user or {}).get("notifications", True)
        import asyncio
        loop = asyncio.get_running_loop()
        db_user2 = await loop.run_in_executor(None, lambda: get_or_create_user(user.id))
        lang = get_user_language(db_user2, user.id)
        await query.edit_message_text(
            get_string(lang, "settings_title"),
            parse_mode="HTML",
            reply_markup=settings_menu(current_style=style, notifications=notifs),
        )

    elif data == "menu:vip":
        if admin:
            await query.answer()
            import asyncio
            loop = asyncio.get_running_loop()
            db_user = await loop.run_in_executor(None, lambda: get_or_create_user(user.id))
            lang = get_user_language(db_user, user.id)
            await query.edit_message_text(
                get_string(lang, "vip_admin_msg"),
                parse_mode="HTML",
                reply_markup=admin_panel_keyboard(),
            )
        else:
            await query.answer()
            from config.settings import VIP_PLANS
            plans = VIP_PLANS
            await query.edit_message_text(
                "💎 <b>VIP Plans</b>\n\n"
                "Pay with <b>Telegram Stars ⭐</b> — instant, secure, no card needed.\n\n"
                f"<b>⭐ Basic — {plans['basic']['stars']} Stars/mo</b>\n"
                f"  {plans['basic']['chat_limit']} chats + {plans['basic']['image_limit']} images/day\n\n"
                f"<b>💎 Pro — {plans['pro']['stars']} Stars/mo</b>\n"
                f"  {plans['pro']['chat_limit']} chats + {plans['pro']['image_limit']} images/day\n\n"
                f"<b>🚀 Elite — {plans['elite']['stars']} Stars/mo</b>\n"
                f"  Unlimited chats + {plans['elite']['image_limit']} images/day\n\n"
                "<i>Tap a plan below to pay with Stars:</i>",
                parse_mode="HTML",
                reply_markup=vip_menu(),
            )

    # ── AI Style selection ────────────────────────────────────────────────────
    elif data.startswith("style:"):
        style = data.split(":", 1)[1]
        update_user(user.id, ai_style=style)
        set_system_prompt(user.id, style)
        await query.answer(f"Style set to {style.capitalize()}!")
        await query.edit_message_text(
            f"✅ AI style set to <b>{style.capitalize()}</b>!\n\n"
            "Your next message will use this personality. Go ahead!",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )

    # ── Image style selection ─────────────────────────────────────────────────
    elif data.startswith("imgstyle:"):
        style = data.split(":", 1)[1]
        from handlers.image import handle_image_style_choice
        await handle_image_style_choice(update, context, style)

    # ── Settings actions ──────────────────────────────────────────────────────
    elif data == "settings:toggle_notif":
        db_user = get_or_create_user(user.id)
        current = (db_user or {}).get("notifications", True)
        update_user(user.id, notifications=not current)
        new_state = not current
        style = (db_user or {}).get("ai_style", "default")
        await query.answer("Notifications " + ("enabled" if new_state else "disabled"))
        await query.edit_message_text(
            f"✅ Notifications {'enabled' if new_state else 'disabled'}.",
            parse_mode="HTML",
            reply_markup=settings_menu(current_style=style, notifications=new_state),
        )

    elif data == "settings:clear_history":
        clear_conversation(user.id)
        await query.answer("Chat history cleared!")
        await query.edit_message_text(
            "🧹 <b>Chat history cleared!</b>\n\nStarting fresh.",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )

    elif data == "settings:export":
        await query.answer()
        from services.database import get_credits, get_image_history
        db_user = get_or_create_user(user.id)
        credits = get_credits(user.id)
        images  = get_image_history(user.id, limit=5)
        text = (
            f"📤 <b>Your Data Export</b>\n\n"
            f"User ID: {user.id}\n"
            f"Name: {user.first_name}\n"
            f"AI Style: {(db_user or {}).get('ai_style','default')}\n"
            f"Language: {(db_user or {}).get('language','en')}\n"
            f"Chat credits used (today): {credits.get('chat_today',0)}\n"
            f"Image credits used (today): {credits.get('image_today',0)}\n"
            f"All-time chats: {credits.get('chat_total',0)}\n"
            f"All-time images: {credits.get('image_total',0)}\n"
            f"Recent images: {len(images)}"
        )
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=back_to_menu())

    # ── VIP plan selection — blocked for admin ────────────────────────────────
    elif data.startswith("vip:"):
        if admin:
            await query.answer("Admin has unlimited access — no VIP needed.", show_alert=True)
            return
        tier = data.split(":", 1)[1]
        if tier == "stars_info":
            await query.answer()
            await query.edit_message_text(
                "⭐ <b>What are Telegram Stars?</b>\n\n"
                "Stars are Telegram's built-in currency.\n\n"
                "📱 <b>Mobile:</b> Telegram Settings → My Stars → Buy Stars\n"
                "💻 <b>Desktop:</b> Settings → Stars\n\n"
                "<i>Tap a plan to pay:</i>",
                parse_mode="HTML",
                reply_markup=vip_menu(),
            )
        elif tier in ("basic", "pro", "elite"):
            await query.answer()
            from handlers.payment import send_vip_invoice
            await send_vip_invoice(update, context, tier)
        else:
            await query.answer("Unknown plan.", show_alert=True)

    # ── Admin inline callbacks ────────────────────────────────────────────────
    elif data.startswith("admin:") and admin:
        await query.answer()
        action = data.split(":", 1)[1]
        await _handle_admin_callback(query, action)

    elif data.startswith("admin:") and not admin:
        await query.answer("Admin only.", show_alert=True)

    elif data == "cancel":
        await query.answer("Cancelled.")
        await query.edit_message_text(
            "✅ Cancelled.",
            reply_markup=admin_main_menu() if admin else main_menu(),
        )

    elif data == "noop":
        await query.answer()

    else:
        await query.answer()


async def _handle_admin_callback(query, action: str) -> None:
    """Handle /admin panel inline callbacks."""
    from services.database import get_banned_users
    from handlers.admin import handle_admin_panel_callback, handle_bot_settings_callback

    if action == "panel":
        return

    elif action == "stats":
        counts = count_users()
        totals = get_total_stats()
        q = queue_manager.stats()
        await query.edit_message_text(
            f"📊 <b>Live Statistics</b>\n\n"
            f"👥 {counts['total']} users  |  {counts['vip']} VIP  |  {counts['banned']} banned\n"
            f"💬 {format_number(totals['total_chats'])} chats  |  "
            f"🎨 {format_number(totals['total_images'])} images\n"
            f"🔄 Queue: {q['queue_size']} queued  |  {q['active_users']} active\n"
            f"✅ Processed: {format_number(q['processed'])}  |  ❌ Errors: {q['errors']}",
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(),
        )

    elif action == "users":
        users = get_all_users(limit=15)
        lines = ["<b>Latest 15 Users:</b>\n"]
        for u in users:
            badge = "🛡️" if is_admin(u.get("user_id", 0)) else ("💎" if u.get("is_vip") else "🆓")
            ban   = " 🚫" if u.get("is_banned") else ""
            lang  = u.get("language", "en")
            lines.append(f"{badge} <code>{u['user_id']}</code> {u.get('first_name','')}{ban} [{lang}]")
        await query.edit_message_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=admin_panel_keyboard()
        )

    elif action == "banned":
        banned = get_banned_users()
        if not banned:
            await query.edit_message_text("✅ No banned users.", reply_markup=admin_panel_keyboard())
            return
        lines = ["<b>🚫 Banned Users:</b>\n"]
        for u in banned:
            lines.append(
                f"<code>{u['user_id']}</code> {u.get('first_name','')} — {u.get('ban_reason','')}"
            )
        await query.edit_message_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=admin_panel_keyboard()
        )

    elif action == "logs":
        errors = get_recent_errors(10)
        if not errors:
            await query.edit_message_text("✅ No recent errors.", reply_markup=admin_panel_keyboard())
            return
        lines = ["<b>📋 Recent Errors:</b>\n"]
        for e in errors[:10]:
            lines.append(
                f"⚠️ <code>{e.get('error_type','?')}</code> — {e.get('message','')[:80]}\n"
                f"   <i>{time_ago(e.get('created_at',''))}</i>"
            )
        await query.edit_message_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=admin_panel_keyboard()
        )

    elif action == "queue":
        q = queue_manager.stats()
        await query.edit_message_text(
            f"🔄 <b>Queue Status</b>\n\n"
            f"In queue: {q['queue_size']}\n"
            f"Active users: {q['active_users']}\n"
            f"Total processed: {format_number(q['processed'])}\n"
            f"Total errors: {q['errors']}",
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(),
        )

    elif action == "images":
        images = get_all_images(limit=10)
        if not images:
            await query.edit_message_text("No images yet.", reply_markup=admin_panel_keyboard())
            return
        lines = ["<b>🖼️ Recent Images:</b>\n"]
        for img in images:
            lines.append(
                f"User <code>{img.get('user_id')}</code> — {img.get('style','')}\n"
                f"  {img.get('prompt','')[:60]}\n"
                f"  <i>{time_ago(img.get('created_at',''))}</i>"
            )
        await query.edit_message_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=admin_panel_keyboard()
        )

    elif action == "health":
        from services.ai_service import check_provider_health
        await query.edit_message_text("🔍 Checking provider health…")
        statuses = check_provider_health()
        lines = ["<b>🩺 AI Provider Health:</b>\n"]
        for provider, status in statuses.items():
            lines.append(f"  {provider}: {status}")
        await query.edit_message_text(
            "\n".join(lines), parse_mode="HTML", reply_markup=admin_panel_keyboard()
        )

    elif action == "botsettings":
        await handle_bot_settings_callback(query)

    elif action in ("vip", "credits", "broadcast"):
        await query.edit_message_text(
            f"Use command-based admin tools for this action:\n"
            f"• /admin_setvip &lt;user_id&gt; &lt;tier&gt;\n"
            f"• /admin_addcredits &lt;user_id&gt; &lt;chat|image&gt; &lt;amount&gt;\n"
            f"• /admin_broadcast &lt;message&gt;",
            parse_mode="HTML",
            reply_markup=admin_panel_keyboard(),
        )
