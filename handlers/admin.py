"""
FundzAiBot — Full admin panel handler.
All commands and callbacks are gated behind ADMIN_USER_ID.
Admin has unlimited access, cannot subscribe VIP or earn referral rewards.

Phase 6 upgrade: /admin_help command with grouped button dashboard.
"""

import asyncio
import functools
import html
from datetime import datetime

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import ADMIN_USER_ID, BOT_NAME, BOT_VERSION, is_admin, FEATURE_FLAGS
from services.database import (
    get_all_users, get_user, ban_user, update_user,
    add_bonus_credits, set_bonus_credits, count_users,
    get_total_stats, get_recent_errors, get_all_images,
    get_credits, log_error, clear_conversation,
    reset_daily_usage, clear_error_logs,
    get_admin_accounts, add_admin_account, remove_admin_account,
)
from services.queue_manager import queue_manager
from utils.helpers import time_ago, format_number
from utils.keyboards import (
    admin_main_menu, admin_panel_keyboard, bot_settings_keyboard, back_to_menu,
)
from utils.logger import get_logger
from utils.rate_limiter import reset_user as reset_rate_limit

log = get_logger(__name__)


def admin_only(func):
    """Decorator — silently rejects any non-admin caller."""
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user = update.effective_user
        if not user or not is_admin(user.id):
            await update.effective_message.reply_text("⛔ Admin only.")
            return
        try:
            from services.autonomous_mode import record_ceo_activity
            record_ceo_activity(func.__name__)
        except Exception:
            pass
        return await func(update, context)
    return wrapper


# ── /admin — main dashboard ───────────────────────────────────────────────────

@admin_only
async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loop = asyncio.get_running_loop()
    counts = await loop.run_in_executor(None, count_users)
    totals = await loop.run_in_executor(None, get_total_stats)
    q = queue_manager.stats()
    ff = FEATURE_FLAGS

    status_lines = []
    if ff["maintenance_mode"]:
        status_lines.append("🚧 <b>MAINTENANCE MODE ON</b>")
    if not ff["chat_enabled"]:
        status_lines.append("💬 Chat is <b>DISABLED</b>")
    if not ff["image_enabled"]:
        status_lines.append("🎨 Images are <b>DISABLED</b>")
    if not ff["new_users_enabled"]:
        status_lines.append("🌐 New users are <b>BLOCKED</b>")
    status_block = "\n".join(status_lines) + "\n\n" if status_lines else ""

    text = (
        f"🛡️ <b>{BOT_NAME} v{BOT_VERSION} — Admin Panel</b>\n\n"
        f"{status_block}"
        f"<b>👥 Users:</b>  {format_number(counts['total'])} total  |  "
        f"{counts['vip']} VIP  |  {counts['banned']} banned\n"
        f"<b>💬 Chats:</b>  {format_number(totals['total_chats'])}  |  "
        f"<b>🎨 Images:</b>  {format_number(totals['total_images'])}\n"
        f"<b>🔄 Queue:</b>  {q['queue_size']} queued  |  "
        f"{q['active_users']} active  |  {q['errors']} errors\n\n"
        f"<i>Select an action below:</i>"
    )
    await update.effective_message.reply_text(
        text, parse_mode="HTML", reply_markup=admin_panel_keyboard()
    )


# ── /admin_help — grouped command reference dashboard ─────────────────────────

@admin_only
async def admin_help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_help — Enterprise admin command reference with grouped inline buttons."""
    text = (
        f"🛡️ <b>{BOT_NAME} v{BOT_VERSION} — Admin Command Reference</b>\n\n"
        f"Tap a category below to see all commands in that group.\n"
        f"Or use /admin for the live dashboard.\n\n"
        f"<i>All commands are admin-only and gated by your user ID.</i>"
    )

    kbd = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 User Management",    callback_data="adminhelp:users"),
            InlineKeyboardButton("📢 Broadcasting",       callback_data="adminhelp:broadcast"),
        ],
        [
            InlineKeyboardButton("💎 Credits & VIP",      callback_data="adminhelp:credits"),
            InlineKeyboardButton("📌 Announcements",      callback_data="adminhelp:announcements"),
        ],
        [
            InlineKeyboardButton("🛡️ Multi-Admin",        callback_data="adminhelp:admins"),
            InlineKeyboardButton("🩺 Audit & Health",     callback_data="adminhelp:audit"),
        ],
        [
            InlineKeyboardButton("⚙️ Bot Settings",       callback_data="adminhelp:settings"),
            InlineKeyboardButton("🚀 Onboarding",         callback_data="adminhelp:onboarding"),
        ],
        [
            InlineKeyboardButton("🧠 FundzAudit Manager", callback_data="adminhelp:fundzaudit"),
        ],
        [
            InlineKeyboardButton("🔙 Admin Panel",        callback_data="admin:panel"),
        ],
    ])

    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=kbd)


# ── /admin_users ──────────────────────────────────────────────────────────────

@admin_only
async def admin_users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loop = asyncio.get_running_loop()
    users = await loop.run_in_executor(None, lambda: get_all_users(limit=20))
    if not users:
        await update.effective_message.reply_text("No users yet.")
        return
    lines = ["<b>👥 Recent Users (latest 20):</b>\n"]
    for u in users:
        badge = "🛡️" if is_admin(u.get("user_id", 0)) else ("💎" if u.get("is_vip") else "🆓")
        ban = " 🚫" if u.get("is_banned") else ""
        name = html.escape(u.get("first_name", "N/A"))
        uid  = u.get("user_id")
        uname = f"@{html.escape(u['username'])}" if u.get("username") else ""
        lines.append(f"{badge} <code>{uid}</code> {name} {uname}{ban}")
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=back_to_menu()
    )


# ── /admin_user <user_id> ─────────────────────────────────────────────────────

@admin_only
async def admin_userinfo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /admin_user &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        uid = int(args[0])
        loop = asyncio.get_running_loop()
        u = await loop.run_in_executor(None, get_user, uid)
        if not u:
            await update.effective_message.reply_text(f"User {uid} not found.")
            return
        c = await loop.run_in_executor(None, get_credits, uid)
        vip_info = "No"
        if u.get("is_vip"):
            tier = u.get("vip_tier", "?")
            exp  = (u.get("vip_expires_at") or "")[:10] or "no expiry"
            vip_info = f"Yes — {tier} (expires {exp})"
        role = "🛡️ Administrator" if is_admin(uid) else ("💎 VIP" if u.get("is_vip") else "🆓 Free")
        text = (
            f"<b>User Info — <code>{uid}</code></b>\n\n"
            f"Name:     {html.escape(u.get('first_name',''))} {html.escape(u.get('last_name',''))}\n"
            f"Username: @{html.escape(u.get('username') or 'N/A')}\n"
            f"Role:     {role}\n"
            f"VIP:      {vip_info}\n"
            f"Banned:   {'Yes — ' + html.escape(u.get('ban_reason') or '') if u.get('is_banned') else 'No'}\n"
            f"Style:    {u.get('ai_style','default')}\n"
            f"Joined:   {(u.get('created_at') or '')[:10]}\n"
            f"Last seen:{time_ago(u.get('last_seen',''))}\n\n"
            f"Credits today: chat {c.get('chat_today',0)}, image {c.get('image_today',0)}\n"
            f"Bonus:    +{c.get('bonus_chat',0)} chat  +{c.get('bonus_image',0)} image\n"
            f"All-time: {c.get('chat_total',0)} chats, {c.get('image_total',0)} images\n"
            f"Referral: {u.get('referral_code','')}"
        )
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=back_to_menu())
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── /admin_ban <user_id> [reason] ─────────────────────────────────────────────

@admin_only
async def admin_ban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /admin_ban &lt;user_id&gt; [reason]", parse_mode="HTML")
        return
    try:
        uid = int(args[0])
        if is_admin(uid):
            await update.effective_message.reply_text("❌ Cannot ban the admin account.")
            return
        reason = " ".join(args[1:]) or "Banned by admin"
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, ban_user, uid, reason, True)
        await update.effective_message.reply_text(
            f"✅ User <code>{uid}</code> banned.\nReason: {html.escape(reason)}", parse_mode="HTML"
        )
        log.warning("Admin banned user %s: %s", uid, reason)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── /admin_unban <user_id> ────────────────────────────────────────────────────

@admin_only
async def admin_unban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /admin_unban &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        uid = int(args[0])
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, ban_user, uid, "", False)
        await update.effective_message.reply_text(
            f"✅ User <code>{uid}</code> unbanned.", parse_mode="HTML"
        )
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── /admin_setvip <user_id> <basic|pro|elite|none> ────────────────────────────

@admin_only
async def admin_setvip_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage: /admin_setvip &lt;user_id&gt; &lt;basic|pro|elite|none&gt;", parse_mode="HTML"
        )
        return
    try:
        uid = int(args[0])
        if is_admin(uid):
            await update.effective_message.reply_text("❌ Admin account does not need VIP.")
            return
        tier = args[1].lower()
        if tier not in ("basic", "pro", "elite", "none"):
            await update.effective_message.reply_text("❌ Tier must be: basic, pro, elite, or none")
            return
        is_vip = tier != "none"
        vip_tier_val = tier if is_vip else None
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: update_user(uid, is_vip=is_vip, vip_tier=vip_tier_val, vip_expires_at=None),
        )
        status = f"VIP ({tier})" if is_vip else "Free"
        await update.effective_message.reply_text(
            f"✅ User <code>{uid}</code> → <b>{status}</b>", parse_mode="HTML"
        )
        log.info("Admin setvip: user=%s tier=%s", uid, tier)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── /admin_addcredits <user_id> <chat|image> <amount> ────────────────────────

@admin_only
async def admin_addcredits_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if len(args) < 3:
        await update.effective_message.reply_text(
            "Usage: /admin_addcredits &lt;user_id&gt; &lt;chat|image&gt; &lt;amount&gt;", parse_mode="HTML"
        )
        return
    try:
        uid = int(args[0])
        kind = args[1].lower()
        amount = int(args[2])
        if kind not in ("chat", "image"):
            raise ValueError("type must be chat or image")
        if amount <= 0:
            raise ValueError("amount must be positive")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: add_bonus_credits(
                uid,
                chat=amount if kind == "chat" else 0,
                image=amount if kind == "image" else 0,
            ),
        )
        await update.effective_message.reply_text(
            f"✅ Added <b>{amount}</b> {kind} credits to user <code>{uid}</code>.", parse_mode="HTML"
        )
        log.info("Admin addcredits: user=%s kind=%s amount=%s", uid, kind, amount)
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {html.escape(str(exc))}")


# ── /broadcast  /admin_broadcast ──────────────────────────────────────────────

@admin_only
async def admin_broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/broadcast <message>  — Preview then confirm broadcast."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from config.settings import TELEGRAM_CHANNEL_ID, TELEGRAM_CHANNEL_NAME

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "📢 <b>Broadcast Usage</b>\n\n"
            "<code>/broadcast Your message here</code>\n\n"
            "You'll see a preview before anything is sent.\n"
            "Supports basic HTML: <b>bold</b>, <i>italic</i>, <code>code</code>",
            parse_mode="HTML",
        )
        return

    raw_text = " ".join(args)

    pending = context.bot_data.setdefault("_bcast_pending", {})
    pending[update.effective_user.id] = raw_text

    loop = asyncio.get_running_loop()
    users = await loop.run_in_executor(None, lambda: get_all_users(limit=2000))
    active_users = [u for u in users if not u.get("is_banned") and not is_admin(u.get("user_id", 0))]
    chan_note = f"\n📢 + will post to <b>{html.escape(TELEGRAM_CHANNEL_NAME)}</b>" if TELEGRAM_CHANNEL_ID else ""

    preview_text = (
        f"<b>📢 Broadcast Preview</b>\n\n"
        f"<b>Recipients:</b> {len(active_users)} active users{chan_note}\n\n"
        f"<b>Message:</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"{raw_text}\n"
        f"━━━━━━━━━━━━━━━━\n\n"
        f"⚠️ <i>This will send a DM to every active user. Confirm?</i>"
    )

    kbd = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Confirm & Send", callback_data="broadcast:confirm"),
            InlineKeyboardButton("❌ Cancel",          callback_data="broadcast:cancel"),
        ]
    ])

    await update.effective_message.reply_text(preview_text, parse_mode="HTML", reply_markup=kbd)


async def _execute_broadcast(bot, admin_id: int, raw_text: str, status_msg) -> None:
    """Worker: sends DMs to all active users + channel post. Edits status_msg when done."""
    from config.settings import TELEGRAM_CHANNEL_ID, TELEGRAM_CHANNEL_NAME

    loop = asyncio.get_running_loop()
    users = await loop.run_in_executor(None, lambda: get_all_users(limit=2000))
    active_users = [u for u in users if not u.get("is_banned") and not is_admin(u.get("user_id", 0))]

    sent = failed = 0
    for u in active_users:
        try:
            await bot.send_message(
                chat_id=u["user_id"],
                text=f"📢 <b>Announcement from {BOT_NAME}:</b>\n\n{raw_text}",
                parse_mode="HTML",
            )
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)

    chan_result = ""
    if TELEGRAM_CHANNEL_ID:
        try:
            await bot.send_message(
                chat_id=TELEGRAM_CHANNEL_ID,
                text=f"📢 <b>{BOT_NAME} Announcement</b>\n\n{raw_text}",
                parse_mode="HTML",
            )
            chan_result = f"\n📢 Channel: ✅ posted to {html.escape(TELEGRAM_CHANNEL_NAME)}"
        except Exception as exc:
            chan_result = f"\n📢 Channel: ❌ {str(exc)[:60]}"

    log.info("Broadcast: admin=%s sent=%d failed=%d", admin_id, sent, failed)

    try:
        await status_msg.edit_text(
            f"<b>📢 Broadcast Complete</b>\n\n"
            f"✅ Sent: <b>{sent}</b>  ❌ Failed: {failed}{chan_result}",
            parse_mode="HTML",
        )
    except Exception:
        pass


# ── /admin_logs ───────────────────────────────────────────────────────────────

@admin_only
async def admin_logs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loop = asyncio.get_running_loop()
    errors = await loop.run_in_executor(None, lambda: get_recent_errors(limit=15))
    if not errors:
        await update.effective_message.reply_text("✅ No recent errors logged.")
        return
    lines = ["<b>📋 Recent Errors (latest 15):</b>\n"]
    for e in errors:
        uid = e.get("user_id") or "system"
        lines.append(
            f"⚠️ <code>{html.escape(e.get('error_type','unknown'))}</code> — user {uid}\n"
            f"   {html.escape(e.get('message','')[:100])}\n"
            f"   <i>{time_ago(e.get('created_at',''))}</i>"
        )
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=back_to_menu()
    )


# ── /admin_stats ──────────────────────────────────────────────────────────────

@admin_only
async def admin_stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loop = asyncio.get_running_loop()
    counts = await loop.run_in_executor(None, count_users)
    totals = await loop.run_in_executor(None, get_total_stats)
    q = queue_manager.stats()
    text = (
        f"📊 <b>Platform Statistics</b>\n\n"
        f"<b>Users:</b>\n"
        f"  Total:  {format_number(counts['total'])}\n"
        f"  VIP:    {format_number(counts['vip'])}\n"
        f"  Free:   {format_number(counts['free'])}\n"
        f"  Banned: {format_number(counts['banned'])}\n\n"
        f"<b>All-Time:</b>\n"
        f"  💬 Chats:  {format_number(totals['total_chats'])}\n"
        f"  🎨 Images: {format_number(totals['total_images'])}\n\n"
        f"<b>Queue (live):</b>\n"
        f"  Queued:    {q['queue_size']}\n"
        f"  Active:    {q['active_users']}\n"
        f"  Processed: {format_number(q['processed'])}\n"
        f"  Errors:    {q['errors']}"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=back_to_menu())


# ── /admin_images ─────────────────────────────────────────────────────────────

@admin_only
async def admin_images_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(None, lambda: get_all_images(limit=20))
    if not images:
        await update.effective_message.reply_text("No images generated yet.")
        return
    lines = ["<b>🖼️ Recent Images (latest 20):</b>\n"]
    for img in images:
        lines.append(
            f"• User <code>{img.get('user_id')}</code> — <b>{html.escape(img.get('style',''))}</b>\n"
            f"  {html.escape(img.get('prompt','')[:80])}\n"
            f"  <i>{time_ago(img.get('created_at',''))}</i>"
        )
    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=back_to_menu()
    )


# ── /admin_health ─────────────────────────────────────────────────────────────

@admin_only
async def admin_health_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from services.ai_service import check_provider_health
    from config.settings import OPENROUTER_MODEL, GEMINI_MODEL, HF_CHAT_MODEL, OPENROUTER_API_KEY, GEMINI_API_KEY, HUGGINGFACE_API_KEY
    msg = await update.effective_message.reply_text("🔍 Checking AI provider health… (may take ~10s)")
    loop = asyncio.get_running_loop()
    statuses = await loop.run_in_executor(None, check_provider_health)

    or_key  = "✅ set" if OPENROUTER_API_KEY  else "❌ missing"
    gem_key = "✅ set" if GEMINI_API_KEY      else "❌ missing"
    hf_key  = "✅ set" if HUGGINGFACE_API_KEY else "❌ missing"

    lines = [
        "<b>🩺 AI Provider Health</b>\n",
        "<b>Active Models:</b>",
        f"  🔷 OpenRouter: <code>{OPENROUTER_MODEL}</code> (key: {or_key})",
        f"  🔷 Gemini:     <code>{GEMINI_MODEL}</code> (key: {gem_key})",
        f"  🔷 HuggingFace:<code>{HF_CHAT_MODEL}</code> (key: {hf_key})",
        "",
        "<b>Live Status:</b>",
    ]
    for provider, status in statuses.items():
        lines.append(f"  {provider}: {status}")

    lines += [
        "",
        "<i>Priority: OpenRouter → Gemini → HuggingFace</i>",
        "<i>Use /admin_config for full config view.</i>",
    ]
    await msg.edit_text("\n".join(lines), parse_mode="HTML")


# ── /admin_resetlimit <user_id> ───────────────────────────────────────────────

@admin_only
async def admin_resetlimit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: /admin_resetlimit &lt;user_id&gt;", parse_mode="HTML"
        )
        return
    try:
        uid = int(args[0])
        reset_rate_limit(uid)
        await update.effective_message.reply_text(
            f"✅ Rate limit cleared for user <code>{uid}</code>.", parse_mode="HTML"
        )
        log.info("Admin reset rate limit for user %s", uid)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── /admin_clearchat <user_id> ────────────────────────────────────────────────

@admin_only
async def admin_clearchat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Clear conversation history for any user."""
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: /admin_clearchat &lt;user_id&gt;", parse_mode="HTML"
        )
        return
    try:
        uid = int(args[0])
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, clear_conversation, uid)
        await update.effective_message.reply_text(
            f"✅ Conversation history cleared for user <code>{uid}</code>.", parse_mode="HTML"
        )
        log.info("Admin cleared chat for user %s", uid)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── Inline callback handlers (called from callbacks.py) ───────────────────────

async def handle_admin_panel_callback(query, context) -> None:
    """Show the full admin panel inline — called by callback_handler."""
    loop = asyncio.get_running_loop()
    counts = await loop.run_in_executor(None, count_users)
    totals = await loop.run_in_executor(None, get_total_stats)
    q = queue_manager.stats()
    ff = FEATURE_FLAGS

    status_lines = []
    if ff["maintenance_mode"]:   status_lines.append("🚧 Maintenance ON")
    if not ff["chat_enabled"]:   status_lines.append("💬 Chat OFF")
    if not ff["image_enabled"]:  status_lines.append("🎨 Images OFF")
    if not ff["new_users_enabled"]: status_lines.append("🌐 New Users BLOCKED")
    status_block = " | ".join(status_lines) + "\n\n" if status_lines else ""

    text = (
        f"🛡️ <b>{BOT_NAME} v{BOT_VERSION} — Admin Panel</b>\n\n"
        f"{status_block}"
        f"<b>👥 Users:</b>  {format_number(counts['total'])} total  |  "
        f"{counts['vip']} VIP  |  {counts['banned']} banned\n"
        f"<b>💬 Chats:</b>  {format_number(totals['total_chats'])}  |  "
        f"<b>🎨 Images:</b>  {format_number(totals['total_images'])}\n"
        f"<b>🔄 Queue:</b>  {q['queue_size']} queued  |  "
        f"{q['active_users']} active  |  {q['errors']} errors\n\n"
        f"<i>Select an action below:</i>"
    )
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=admin_panel_keyboard())
    except Exception:
        pass


async def handle_bot_settings_callback(query) -> None:
    """Show the bot settings panel inline."""
    text = (
        f"⚙️ <b>{BOT_NAME} — Bot Settings</b>\n\n"
        "Toggle features below. Changes take effect immediately.\n\n"
        "<i>⚠️ Disabling chat will affect all users. Use with care.</i>"
    )
    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=bot_settings_keyboard(FEATURE_FLAGS)
        )
    except Exception:
        pass


async def handle_botsetting_toggle(query, flag_key: str) -> None:
    """Toggle a feature flag and refresh the settings panel."""
    if flag_key not in FEATURE_FLAGS:
        await query.answer("Unknown setting.", show_alert=True)
        return
    FEATURE_FLAGS[flag_key] = not FEATURE_FLAGS[flag_key]
    new_val = FEATURE_FLAGS[flag_key]
    await query.answer(f"{'Enabled' if new_val else 'Disabled'}: {flag_key}")
    text = (
        f"⚙️ <b>{BOT_NAME} — Bot Settings</b>\n\n"
        f"✅ <b>{flag_key}</b> set to {'ON' if new_val else 'OFF'}.\n\n"
        "Toggle features below. Changes take effect immediately."
    )
    try:
        await query.edit_message_text(
            text, parse_mode="HTML", reply_markup=bot_settings_keyboard(FEATURE_FLAGS)
        )
    except Exception:
        pass


# ── /admin_help category callbacks ────────────────────────────────────────────

_ADMINHELP_PAGES: dict[str, tuple[str, str]] = {
    "users": (
        "👥 <b>User Management Commands</b>\n\n"
        "<code>/admin_users</code> — List recent 20 users\n"
        "<code>/admin_user &lt;id&gt;</code> — View user profile & credits\n"
        "<code>/admin_ban &lt;id&gt; [reason]</code> — Ban a user\n"
        "<code>/admin_unban &lt;id&gt;</code> — Unban a user\n"
        "<code>/admin_resetlimit &lt;id&gt;</code> — Clear rate limit\n"
        "<code>/admin_resetuser &lt;id&gt;</code> — Full user reset\n"
        "<code>/admin_clearchat &lt;id&gt;</code> — Clear chat history\n"
        "<code>/admin_dm &lt;id&gt; &lt;msg&gt;</code> — DM any user\n\n"
        "<i>Find user by ID in the admin panel → 🔍 Find User</i>",
        "adminhelp:users"
    ),
    "broadcast": (
        "📢 <b>Broadcasting & Communication</b>\n\n"
        "<code>/broadcast &lt;msg&gt;</code> — Broadcast to all users (with preview)\n"
        "<code>/admin_broadcast &lt;msg&gt;</code> — Same as /broadcast\n"
        "<code>/admin_dm &lt;id&gt; &lt;msg&gt;</code> — Direct message a user\n"
        "<code>/testbroadcast</code> — Preview active announcement\n\n"
        "<b>Announcement Channel/Group Push:</b>\n"
        "<code>/announce_channel</code> — Push to official channel\n"
        "<code>/announce_group</code> — Push to community group\n"
        "<code>/announce_both</code> — Push to channel + group\n\n"
        "<i>All broadcasts require confirmation before sending.</i>",
        "adminhelp:broadcast"
    ),
    "credits": (
        "💎 <b>Credits & VIP Management</b>\n\n"
        "<code>/admin_addcredits &lt;id&gt; &lt;chat|image&gt; &lt;n&gt;</code> — Add bonus credits\n"
        "<code>/admin_setcredits &lt;id&gt; &lt;chat|image&gt; &lt;n&gt;</code> — Set credits to exact amount\n"
        "<code>/admin_setvip &lt;id&gt; &lt;basic|pro|elite|none&gt;</code> — Grant/revoke VIP\n\n"
        "<b>VIP Tiers:</b>\n"
        "  ⭐ basic — 500 chats + 50 images/day\n"
        "  💎 pro   — 2000 chats + 100 images/day\n"
        "  🚀 elite — Unlimited chats + 200 images/day\n\n"
        "<i>Admin accounts have unlimited access and cannot earn VIP.</i>",
        "adminhelp:credits"
    ),
    "announcements": (
        "📌 <b>Announcement System</b>\n\n"
        "<code>/pin &lt;message&gt;</code> — Create/replace active announcement\n"
        "<code>/unpin</code> — Remove active announcement\n"
        "<code>/updateannouncement &lt;text&gt;</code> — Edit current announcement text\n"
        "<code>/pinphoto &lt;url|remove&gt;</code> — Attach/remove banner image\n"
        "<code>/listannouncements</code> — View announcement history (last 10)\n\n"
        "<b>Announcement is shown:</b>\n"
        "  • On /start for new users (always)\n"
        "  • On /start for returning users (smart: only new/important ones)\n"
        "  • Admin can push it to channel/group manually\n\n"
        "<i>Set ANNOUNCEMENT_PRIORITY=high to always show regardless of seen status.</i>",
        "adminhelp:announcements"
    ),
    "admins": (
        "🛡️ <b>Multi-Admin Management (Owner Only)</b>\n\n"
        "<code>/admin_addadmin &lt;user_id&gt;</code> — Promote to admin\n"
        "<code>/admin_removeadmin &lt;user_id&gt;</code> — Remove admin access\n"
        "<code>/admin_listadmins</code> — List all admins\n\n"
        "<b>Admin hierarchy:</b>\n"
        "  👑 Owner (ADMIN_USER_ID) — Permanent, irremovable super-admin\n"
        "  🛡️ Admin — Promoted by owner, stored in Supabase\n\n"
        "<b>Admin cache:</b> 60-second TTL — changes propagate within a minute.\n\n"
        "<i>Only the owner can add/remove other admins.</i>",
        "adminhelp:admins"
    ),
    "audit": (
        "🩺 <b>Audit, Health & Monitoring</b>\n\n"
        "<code>/testaudit</code> — Enterprise audit center (14 sections)\n"
        "<code>/status</code> or <code>/health</code> — Live status dashboard\n"
        "<code>/admin_health</code> — AI provider health check\n"
        "<code>/admin_logs</code> — Recent error logs (last 15)\n"
        "<code>/admin_clearlogs</code> — Clear old error log entries\n"
        "<code>/admin_stats</code> — Full platform statistics\n"
        "<code>/admin_config</code> — Full environment configuration view\n\n"
        "<b>Audit health score:</b>\n"
        "  🟢 90%+ = Production Ready\n"
        "  🟡 70%+ = Review Warnings\n"
        "  🔴 <70% = Needs Attention\n\n"
        "<i>Full audit runs 14 sections concurrently and caches results for 2 minutes.</i>",
        "adminhelp:audit"
    ),
    "settings": (
        "⚙️ <b>Bot Settings & Feature Flags</b>\n\n"
        "Access via Admin Panel → ⚙️ Bot Settings\n\n"
        "<b>Feature flags (toggle in-panel):</b>\n"
        "  💬 Chat enabled — AI chat on/off globally\n"
        "  🎨 Image Gen — Image generation on/off\n"
        "  🌐 New Users — Allow/block new user registrations\n"
        "  🚧 Maintenance Mode — Maintenance message to all\n\n"
        "<b>Environment overrides (Railway → Variables):</b>\n"
        "  <code>MEMBERSHIP_GATE_ENABLED=true</code> — Gate all commands\n"
        "  <code>ONBOARDING_REQUIRED=true</code> — Require join before access\n"
        "  <code>FREE_DAILY_CHAT=30</code> — Free daily chat credits\n"
        "  <code>FREE_DAILY_IMAGE=5</code> — Free daily image credits\n\n"
        "<i>Feature flags reset on restart. Use env vars for persistent changes.</i>",
        "adminhelp:settings"
    ),
    "onboarding": (
        "🚀 <b>Onboarding System</b>\n\n"
        "<code>/admin_onboarding</code> — View onboarding stats & configuration\n\n"
        "<b>Environment vars (Railway → Variables):</b>\n"
        "  <code>TELEGRAM_CHANNEL_ID</code> — @channel or numeric ID\n"
        "  <code>TELEGRAM_CHANNEL_URL</code> — Invite link for channel\n"
        "  <code>TELEGRAM_CHANNEL_NAME</code> — Display name\n"
        "  <code>TELEGRAM_GROUP_ID</code> — @group or numeric ID\n"
        "  <code>TELEGRAM_GROUP_URL</code> — Invite link for group\n"
        "  <code>TELEGRAM_GROUP_NAME</code> — Display name\n"
        "  <code>ONBOARDING_CHANNEL_REWARD_CHAT=5</code>\n"
        "  <code>ONBOARDING_CHANNEL_REWARD_IMAGE=1</code>\n"
        "  <code>ONBOARDING_REQUIRED=false</code> — Require before access\n"
        "  <code>MEMBERSHIP_GATE_ENABLED=false</code> — Gate all commands\n\n"
        "<i>Bot must be admin in channel/group for membership verification.</i>",
        "adminhelp:onboarding"
    ),
    "fundzaudit": (
        "🧠 <b>FundzAudit Manager — CEO Advisor</b>\n\n"
        "The FundzAudit Manager is your intelligent advisor role within /testaudit.\n\n"
        "<b>What it does:</b>\n"
        "  • Analyzes bot health across all 14 audit sections\n"
        "  • Generates executive-level reports with priority rankings\n"
        "  • Provides CEO-level recommendations (no auto-actions taken)\n"
        "  • Tracks health trend over audit history\n"
        "  • Identifies systemic risks vs isolated failures\n\n"
        "<b>Access:</b>\n"
        "  /testaudit → 📄 Report → generates advisor summary\n"
        "  Or tap 🧠 CEO Advisor in the audit dashboard\n\n"
        "<b>Philosophy:</b>\n"
        "  FundzAudit reports findings and recommendations only.\n"
        "  No auto-destructive actions. Admin must approve all fixes.\n"
        "  Auto-fix only does safe in-memory repairs (cache refresh, re-seed).\n\n"
        "<i>Built for production confidence — know your bot's health at a glance.</i>",
        "adminhelp:fundzaudit"
    ),
}


async def handle_adminhelp_callback(query, action: str) -> None:
    """Handle /admin_help category button callbacks."""
    page = _ADMINHELP_PAGES.get(action)
    if not page:
        await query.answer("Unknown category.", show_alert=True)
        return

    text, cb_key = page
    kbd = InlineKeyboardMarkup([
        [InlineKeyboardButton("« Back to Admin Help", callback_data="adminhelp:index")],
        [InlineKeyboardButton("🛡️ Admin Panel", callback_data="admin:panel")],
    ])
    await query.answer()
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
    except Exception:
        pass


async def handle_adminhelp_index_callback(query) -> None:
    """Return to the /admin_help index page."""
    from config.settings import BOT_NAME, BOT_VERSION
    text = (
        f"🛡️ <b>{BOT_NAME} v{BOT_VERSION} — Admin Command Reference</b>\n\n"
        f"Tap a category below to see all commands in that group.\n"
        f"Or use /admin for the live dashboard.\n\n"
        f"<i>All commands are admin-only and gated by your user ID.</i>"
    )
    kbd = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("👥 User Management",    callback_data="adminhelp:users"),
            InlineKeyboardButton("📢 Broadcasting",       callback_data="adminhelp:broadcast"),
        ],
        [
            InlineKeyboardButton("💎 Credits & VIP",      callback_data="adminhelp:credits"),
            InlineKeyboardButton("📌 Announcements",      callback_data="adminhelp:announcements"),
        ],
        [
            InlineKeyboardButton("🛡️ Multi-Admin",        callback_data="adminhelp:admins"),
            InlineKeyboardButton("🩺 Audit & Health",     callback_data="adminhelp:audit"),
        ],
        [
            InlineKeyboardButton("⚙️ Bot Settings",       callback_data="adminhelp:settings"),
            InlineKeyboardButton("🚀 Onboarding",         callback_data="adminhelp:onboarding"),
        ],
        [
            InlineKeyboardButton("🧠 FundzAudit Manager", callback_data="adminhelp:fundzaudit"),
        ],
        [
            InlineKeyboardButton("🔙 Admin Panel",        callback_data="admin:panel"),
        ],
    ])
    await query.answer()
    try:
        await query.edit_message_text(text, parse_mode="HTML", reply_markup=kbd)
    except Exception:
        pass


# ── Remaining admin handlers (stubs delegating to existing services) ──────────

@admin_only
async def admin_config_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show full bot configuration."""
    from config.settings import (
        OPENROUTER_MODEL, GEMINI_MODEL, HF_CHAT_MODEL,
        TELEGRAM_CHANNEL_ID, TELEGRAM_GROUP_ID,
        FREE_DAILY_CHAT, FREE_DAILY_IMAGE, IS_RAILWAY,
        MEMBERSHIP_GATE_ENABLED, ONBOARDING_REQUIRED,
    )
    text = (
        f"⚙️ <b>{BOT_NAME} v{BOT_VERSION} — Configuration</b>\n\n"
        f"<b>AI Models:</b>\n"
        f"  OpenRouter: <code>{OPENROUTER_MODEL}</code>\n"
        f"  Gemini:     <code>{GEMINI_MODEL}</code>\n"
        f"  HuggingFace:<code>{HF_CHAT_MODEL}</code>\n\n"
        f"<b>Credits:</b>\n"
        f"  Free daily chat:  {FREE_DAILY_CHAT}\n"
        f"  Free daily image: {FREE_DAILY_IMAGE}\n\n"
        f"<b>Community:</b>\n"
        f"  Channel ID:  <code>{TELEGRAM_CHANNEL_ID or 'Not set'}</code>\n"
        f"  Group ID:    <code>{TELEGRAM_GROUP_ID or 'Not set'}</code>\n\n"
        f"<b>Access Control:</b>\n"
        f"  Membership gate: {'✅ ON' if MEMBERSHIP_GATE_ENABLED else '❌ OFF'}\n"
        f"  Onboarding required: {'✅ ON' if ONBOARDING_REQUIRED else '❌ OFF'}\n\n"
        f"<b>Deployment:</b>\n"
        f"  Railway: {'✅ Production' if IS_RAILWAY else '⚠️ Dev mode'}\n\n"
        f"<b>Feature Flags:</b>\n"
        + "\n".join(f"  {k}: {'✅' if v else '❌'}" for k, v in FEATURE_FLAGS.items())
    )
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=back_to_menu())


@admin_only
async def admin_setcredits_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if len(args) < 3:
        await update.effective_message.reply_text(
            "Usage: /admin_setcredits &lt;user_id&gt; &lt;chat|image&gt; &lt;amount&gt;", parse_mode="HTML"
        )
        return
    try:
        uid = int(args[0])
        kind = args[1].lower()
        amount = int(args[2])
        if kind not in ("chat", "image"):
            raise ValueError("type must be chat or image")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: set_bonus_credits(
                uid,
                chat=amount if kind == "chat" else None,
                image=amount if kind == "image" else None,
            ),
        )
        await update.effective_message.reply_text(
            f"✅ Set {kind} credits to <b>{amount}</b> for user <code>{uid}</code>.", parse_mode="HTML"
        )
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {html.escape(str(exc))}")


@admin_only
async def admin_resetuser_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /admin_resetuser &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        uid = int(args[0])
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, lambda: reset_daily_usage(uid))
        await loop.run_in_executor(None, lambda: clear_conversation(uid))
        await update.effective_message.reply_text(
            f"✅ User <code>{uid}</code> daily usage reset and chat history cleared.", parse_mode="HTML"
        )
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


@admin_only
async def admin_clearlogs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, clear_error_logs)
    await update.effective_message.reply_text("✅ Error logs cleared.")


@admin_only
async def admin_addadmin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from config.settings import is_owner
    user = update.effective_user
    if not is_owner(user.id):
        await update.effective_message.reply_text("⛔ Owner only.")
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /admin_addadmin &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        uid = int(args[0])
        loop = asyncio.get_running_loop()
        ok, msg = await loop.run_in_executor(None, lambda: add_admin_account(uid, added_by=user.id))
        await update.effective_message.reply_text(msg, parse_mode="HTML")
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


@admin_only
async def admin_removeadmin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from config.settings import is_owner
    user = update.effective_user
    if not is_owner(user.id):
        await update.effective_message.reply_text("⛔ Owner only.")
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text("Usage: /admin_removeadmin &lt;user_id&gt;", parse_mode="HTML")
        return
    try:
        uid = int(args[0])
        loop = asyncio.get_running_loop()
        ok, msg = await loop.run_in_executor(None, lambda: remove_admin_account(uid))
        await update.effective_message.reply_text(msg, parse_mode="HTML")
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


@admin_only
async def admin_listadmins_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    loop = asyncio.get_running_loop()
    admins = await loop.run_in_executor(None, get_admin_accounts)
    if not admins:
        await update.effective_message.reply_text("No additional admins configured.")
        return
    lines = ["<b>🛡️ Admin List:</b>\n"]
    for a in admins:
        role = "👑 Owner" if a.get("role") == "owner" else "🛡️ Admin"
        uname = f"@{html.escape(a.get('username',''))}" if a.get("username") else ""
        lines.append(f"{role} <code>{a.get('user_id')}</code> {uname}")
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=back_to_menu())


@admin_only
async def admin_dm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "Usage: /admin_dm &lt;user_id&gt; &lt;message&gt;", parse_mode="HTML"
        )
        return
    try:
        uid = int(args[0])
        msg_text = " ".join(args[1:])
        await context.bot.send_message(
            chat_id=uid,
            text=f"📬 <b>Message from {BOT_NAME} Admin:</b>\n\n{msg_text}",
            parse_mode="HTML",
        )
        await update.effective_message.reply_text(f"✅ DM sent to user <code>{uid}</code>.", parse_mode="HTML")
        log.info("Admin DM: to=%s admin=%s", uid, update.effective_user.id)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")
    except Exception as exc:
        await update.effective_message.reply_text(f"❌ Failed: {html.escape(str(exc))}")
