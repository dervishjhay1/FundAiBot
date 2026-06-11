"""
FundzAiBot — Master inline-keyboard callback dispatcher.
Admin gets special routing: admin panel, bot settings, feature flag toggles.
VIP menu is blocked for admin. Onboarding and announcement callbacks routed here.
"""

import asyncio
import html

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import ADMIN_USER_ID, is_admin
from services.database import (
    get_or_create_user, update_user, set_system_prompt,
    clear_conversation, get_recent_errors, count_users,
    get_total_stats, get_all_users, get_all_images,
)
from services.queue_manager import queue_manager
from utils.helpers import time_ago, format_number
from utils.keyboards import (
    main_menu, admin_main_menu, admin_panel_keyboard,
    ai_styles_menu, image_styles_menu,
    settings_menu, vip_menu, back_to_menu,
    admin_announcements_keyboard,
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

    # ── Audit center callbacks ────────────────────────────────────────────────
    if data.startswith("audit:"):
        if not admin:
            await query.answer("Admin only.", show_alert=True)
            return
        await query.answer()
        action = data.split("audit:", 1)[1]
        from handlers.audit import audit_callback
        await audit_callback(query, context, action)
        return

    # ── Announcement callbacks ────────────────────────────────────────────────
    if data.startswith("announcement:"):
        action = data.split(":", 1)[1]
        await _handle_announcement_callback(query, context, action, admin)
        return

    # ── Admin panel shortcut ──────────────────────────────────────────────────
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

    # ── Admin announcements panel ─────────────────────────────────────────────
    if data == "admin:announcements":
        if not admin:
            await query.answer("Admin only.", show_alert=True)
            return
        await query.answer()
        await _show_admin_announcements_panel(query, context)
        return

    # ── Admin onboarding stats ────────────────────────────────────────────────
    if data == "admin:onboarding_stats":
        if not admin:
            await query.answer("Admin only.", show_alert=True)
            return
        await query.answer("Fetching onboarding stats…")
        from services.onboarding import get_onboarding_stats
        from config.settings import (
            TELEGRAM_CHANNEL_ID, TELEGRAM_CHANNEL_URL,
            TELEGRAM_GROUP_ID, TELEGRAM_GROUP_URL,
            ONBOARDING_CHANNEL_REWARD_CHAT, ONBOARDING_CHANNEL_REWARD_IMAGE,
            ONBOARDING_GROUP_REWARD_CHAT, ONBOARDING_GROUP_REWARD_IMAGE,
            ONBOARDING_REQUIRED,
        )
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

    # ── Main menu navigation ──────────────────────────────────────────────────
    if data == "menu:back":
        await query.answer()
        await query.edit_message_text(
            "🏠 <b>Main Menu</b>\n\nChoose an option:",
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
        await query.edit_message_text(
            "⚙️ <b>Settings</b>\n\nCustomise your FundzAiBot experience:",
            parse_mode="HTML",
            reply_markup=settings_menu(current_style=style, notifications=notifs),
        )

    elif data == "menu:vip":
        if admin:
            await query.answer()
            await query.edit_message_text(
                "🛡️ <b>You are the Administrator.</b>\n\n"
                "Admin accounts have <b>unlimited access</b> — no VIP subscription needed.\n\n"
                "Use the Admin Panel to manage VIP for your users.",
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
        await _handle_admin_callback(query, action, context)

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


# ── Announcement callbacks ─────────────────────────────────────────────────────

async def _handle_announcement_callback(query, context, action: str, admin: bool) -> None:
    """Handle all announcement: prefixed callbacks."""
    loop = asyncio.get_running_loop()

    if action == "dismiss":
        # User dismissed the announcement — unpin and delete the message
        await query.answer("Announcement dismissed.", show_alert=False)
        try:
            await context.bot.unpin_chat_message(
                chat_id=query.message.chat_id,
                message_id=query.message.message_id,
            )
        except Exception:
            pass
        try:
            await query.message.delete()
        except Exception:
            # If can't delete, just remove the keyboard
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
        return

    # Admin-only announcement management
    if not admin:
        await query.answer("Admin only.", show_alert=True)
        return

    from services.database import get_active_announcement, deactivate_announcements, get_announcement_history
    from handlers.announcements import post_to_channel, post_to_group, format_announcement_card

    if action == "post_channel":
        ann = await loop.run_in_executor(None, get_active_announcement)
        if not ann:
            await query.answer("No active announcement to post.", show_alert=True)
            return
        await query.answer("Posting to channel…")
        ok, status = await post_to_channel(context.bot, ann)
        try:
            await query.edit_message_text(
                f"<b>Channel Post Result:</b>\n{status}",
                parse_mode="HTML",
                reply_markup=admin_announcements_keyboard(),
            )
        except Exception:
            pass

    elif action == "post_group":
        ann = await loop.run_in_executor(None, get_active_announcement)
        if not ann:
            await query.answer("No active announcement to post.", show_alert=True)
            return
        await query.answer("Posting to group…")
        ok, status = await post_to_group(context.bot, ann)
        try:
            await query.edit_message_text(
                f"<b>Group Post Result:</b>\n{status}",
                parse_mode="HTML",
                reply_markup=admin_announcements_keyboard(),
            )
        except Exception:
            pass

    elif action == "post_both":
        ann = await loop.run_in_executor(None, get_active_announcement)
        if not ann:
            await query.answer("No active announcement to post.", show_alert=True)
            return
        await query.answer("Posting to channel and group…")
        from config.settings import TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID
        results = []
        if TELEGRAM_CHANNEL_ID:
            ok, st = await post_to_channel(context.bot, ann)
            results.append(f"📢 {st}")
        if TELEGRAM_GROUP_ID:
            ok, st = await post_to_group(context.bot, ann)
            results.append(f"👥 {st}")
        if not results:
            results = ["❌ No channel or group configured."]
        try:
            await query.edit_message_text(
                "<b>Broadcast Result:</b>\n" + "\n".join(results),
                parse_mode="HTML",
                reply_markup=admin_announcements_keyboard(),
            )
        except Exception:
            pass

    elif action == "unpin":
        await loop.run_in_executor(None, deactivate_announcements)
        await query.answer("Announcement unpinned.", show_alert=False)
        try:
            await query.edit_message_text(
                "✅ <b>Announcement unpinned.</b>\n\n"
                "Users will no longer see a pinned banner on /start.",
                parse_mode="HTML",
                reply_markup=admin_panel_keyboard(),
            )
        except Exception:
            pass

    elif action == "history":
        history = await loop.run_in_executor(None, lambda: get_announcement_history(limit=10))
        if not history:
            await query.answer("No announcement history yet.", show_alert=True)
            return
        import html as _html
        lines = ["📌 <b>Announcement History</b>\n"]
        for i, a in enumerate(history, 1):
            status  = "🟢 ACTIVE" if a.get("is_active") else "⚫ archived"
            preview = _html.escape((a.get("message") or "")[:80])
            if len(a.get("message") or "") > 80:
                preview += "…"
            photo = " 🖼️" if a.get("photo_url") else ""
            lines.append(
                f"{i}. [{status}]{photo}\n"
                f"   {preview}\n"
                f"   <i>{time_ago(a.get('created_at', ''))}</i>"
            )
        await query.answer()
        try:
            await query.edit_message_text(
                "\n\n".join(lines),
                parse_mode="HTML",
                reply_markup=admin_announcements_keyboard(),
            )
        except Exception:
            pass
    else:
        await query.answer()


async def _show_admin_announcements_panel(query, context) -> None:
    """Show the admin announcement management panel inline."""
    from services.database import get_active_announcement
    from handlers.announcements import format_announcement_card

    loop = asyncio.get_running_loop()
    ann = await loop.run_in_executor(None, get_active_announcement)

    if ann:
        preview = format_announcement_card(ann.get("message", ""))
        text = (
            f"📌 <b>Announcement Management</b>\n\n"
            f"<b>Active announcement:</b>\n{preview}\n\n"
            f"📷 Photo: {'✅ Set' if ann.get('photo_url') else '❌ None'}\n\n"
            f"Use the buttons to post to channel/group, or commands:\n"
            f"<code>/pin &lt;msg&gt;</code> — create/replace\n"
            f"<code>/updateannouncement &lt;msg&gt;</code> — edit\n"
            f"<code>/pinphoto &lt;url&gt;</code> — add image\n"
            f"<code>/announce_both</code> — post to all"
        )
    else:
        text = (
            "📌 <b>Announcement Management</b>\n\n"
            "❌ No active announcement.\n\n"
            "Create one with:\n<code>/pin &lt;your message&gt;</code>"
        )

    try:
        await query.edit_message_text(
            text,
            parse_mode="HTML",
            reply_markup=admin_announcements_keyboard(),
        )
    except Exception:
        pass


# ── Admin callbacks ────────────────────────────────────────────────────────────

async def _handle_admin_callback(query, action: str, context=None) -> None:
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
            lines.append(f"{badge} <code>{u['user_id']}</code> {u.get('first_name','')}{ban}")
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
