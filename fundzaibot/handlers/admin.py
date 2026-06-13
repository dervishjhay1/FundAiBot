"""
FundzAiBot — Full admin panel handler.
All commands and callbacks are gated behind ADMIN_USER_ID.
Admin has unlimited access, cannot subscribe VIP or earn referral rewards.
"""

import asyncio
import functools
import html
from datetime import datetime

from telegram import Update
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
#
# Flow:
#   Step 1  /broadcast <message>  →  preview card + Confirm / Cancel buttons
#   Step 2  Confirm               →  sends DMs to all active users
#                                    + posts to channel if configured
#   Cancel removes the preview card.
#
# The raw message text is stored in context.bot_data["_bcast_pending"][admin_id]
# so the confirm callback can retrieve it without re-parsing.
# ─────────────────────────────────────────────────────────────────────────────

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

    # Store pending broadcast text keyed by admin user ID
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

    # Channel post
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
    status_block = "  " + "  |  ".join(status_lines) + "\n\n" if status_lines else ""

    text = (
        f"🛡️ <b>{BOT_NAME} v{BOT_VERSION} — Admin Panel</b>\n\n"
        f"{status_block}"
        f"👥 <b>{format_number(counts['total'])}</b> users  |  "
        f"{counts['vip']} VIP  |  {counts['banned']} banned\n"
        f"💬 <b>{format_number(totals['total_chats'])}</b> chats  |  "
        f"🎨 <b>{format_number(totals['total_images'])}</b> images\n"
        f"🔄 Queue: {q['queue_size']} queued  |  {q['errors']} errors\n\n"
        f"<i>Select an action:</i>"
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=admin_panel_keyboard())


async def handle_bot_settings_callback(query) -> None:
    """Show bot settings / feature flags panel."""
    ff = FEATURE_FLAGS
    text = (
        f"⚙️ <b>Bot Settings</b>\n\n"
        f"Toggle features on/off. Changes take effect immediately.\n"
        f"<i>(Reset on bot restart — stored in-memory)</i>\n\n"
        f"💬 Chat:        {'✅ ON' if ff['chat_enabled']      else '❌ OFF'}\n"
        f"🎨 Image Gen:   {'✅ ON' if ff['image_enabled']     else '❌ OFF'}\n"
        f"🌐 New Users:   {'✅ ON' if ff['new_users_enabled'] else '❌ OFF'}\n"
        f"🚧 Maintenance: {'🚧 ON — users see maintenance msg' if ff['maintenance_mode'] else '✅ OFF'}"
    )
    await query.edit_message_text(text, parse_mode="HTML", reply_markup=bot_settings_keyboard(ff))


async def handle_botsetting_toggle(query, flag_key: str) -> None:
    """Toggle a feature flag and refresh the settings panel."""
    if flag_key not in FEATURE_FLAGS:
        await query.answer("Unknown setting.", show_alert=True)
        return
    FEATURE_FLAGS[flag_key] = not FEATURE_FLAGS[flag_key]
    new_val = FEATURE_FLAGS[flag_key]
    log.info("Admin toggled %s → %s", flag_key, new_val)
    label_map = {
        "chat_enabled":      "Chat",
        "image_enabled":     "Image Gen",
        "new_users_enabled": "New Users",
        "maintenance_mode":  "Maintenance",
    }
    await query.answer(f"{label_map.get(flag_key, flag_key)}: {'ON' if new_val else 'OFF'}")
    await handle_bot_settings_callback(query)


# ── /admin_dm <user_id> <message> ────────────────────────────────────────────

@admin_only
async def admin_dm_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_dm — Send a private direct message to a specific user."""
    args = context.args or []
    if len(args) < 2:
        await update.effective_message.reply_text(
            "📩 <b>Direct Message</b>\n\n"
            "Usage: <code>/admin_dm &lt;user_id&gt; &lt;message&gt;</code>\n\n"
            "Example:\n"
            "<code>/admin_dm 123456789 Hi! Your VIP has been activated. Enjoy! 🚀</code>\n\n"
            "<i>The user receives it as a FundzAiBot support message.</i>",
            parse_mode="HTML",
        )
        return

    try:
        uid = int(args[0])
        message = " ".join(args[1:])

        outbox = (
            f"📩 <b>Message from FundzAiBot Support:</b>\n\n"
            f"{html.escape(message)}\n\n"
            f"<i>Need help? Use /help or contact support.</i>"
        )

        await context.bot.send_message(chat_id=uid, text=outbox, parse_mode="HTML")

        await update.effective_message.reply_text(
            f"✅ <b>Message delivered!</b>\n\n"
            f"📬 To: <code>{uid}</code>\n"
            f"📝 Message: {html.escape(message[:80])}{'…' if len(message) > 80 else ''}",
            parse_mode="HTML",
        )
        log.info("Admin DM sent: from=%s to=%s len=%d", update.effective_user.id, uid, len(message))

    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID — must be a number.")
    except Exception as exc:
        err = str(exc)
        hint = ""
        if "blocked" in err.lower() or "403" in err:
            hint = "\n<i>💡 The user has likely blocked the bot.</i>"
        elif "chat not found" in err.lower() or "400" in err:
            hint = "\n<i>💡 User ID not found — they may not have started the bot.</i>"
        await update.effective_message.reply_text(
            f"❌ <b>Failed to send message.</b>\n\n"
            f"<code>{html.escape(err[:200])}</code>{hint}",
            parse_mode="HTML",
        )
        log.warning("Admin DM failed: to=%s error=%s", args[0] if args else "?", exc)


# ── /admin_config ──────────────────────────────────────────────────────────────

@admin_only
async def admin_config_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_config — Show full bot configuration."""
    from config.settings import (
        FREE_DAILY_CHAT, FREE_DAILY_IMAGE, VIP_DAILY_CHAT, VIP_DAILY_IMAGE,
        RATE_LIMIT_MESSAGES, RATE_LIMIT_WINDOW, MAX_QUEUE_SIZE,
        OPENROUTER_MODEL, GEMINI_MODEL, HF_CHAT_MODEL, SECONDARY_ADMINS,
        VIP_PLANS,
    )
    ff = FEATURE_FLAGS
    text = (
        f"⚙️ <b>{BOT_NAME} v{BOT_VERSION} — Configuration</b>\n\n"
        f"<b>Feature Flags:</b>\n"
        f"  💬 Chat:        {'✅ ON'  if ff['chat_enabled']      else '❌ OFF'}\n"
        f"  🎨 Images:      {'✅ ON'  if ff['image_enabled']     else '❌ OFF'}\n"
        f"  🌐 New Users:   {'✅ ON'  if ff['new_users_enabled'] else '❌ OFF'}\n"
        f"  🚧 Maintenance: {'🚧 ON' if ff['maintenance_mode']  else '✅ OFF'}\n\n"
        f"<b>Credit Limits:</b>\n"
        f"  Free:  {FREE_DAILY_CHAT} chats / {FREE_DAILY_IMAGE} images per day\n"
        f"  VIP:   {VIP_DAILY_CHAT} chats / {VIP_DAILY_IMAGE} images per day\n\n"
        f"<b>VIP Pricing (Stars):</b>\n"
        f"  ⭐ Basic:  {VIP_PLANS['basic']['stars']}  |  💎 Pro: {VIP_PLANS['pro']['stars']}  |  🚀 Elite: {VIP_PLANS['elite']['stars']}\n\n"
        f"<b>Rate Limiting:</b>\n"
        f"  {RATE_LIMIT_MESSAGES} msgs per {RATE_LIMIT_WINDOW}s window\n\n"
        f"<b>Queue:</b> max {MAX_QUEUE_SIZE} requests\n\n"
        f"<b>AI Models:</b>\n"
        f"  OpenRouter: {OPENROUTER_MODEL}\n"
        f"  Gemini:     {GEMINI_MODEL}\n"
        f"  HuggingFace:{HF_CHAT_MODEL}\n\n"
        f"<b>Admins:</b>\n"
        f"  👑 Owner: 1 (env ADMIN_USER_ID)\n"
        f"  🛡️ Secondary: {len(SECONDARY_ADMINS)}"
    )
    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=back_to_menu())


# ── /admin_setcredits <user_id> <chat|image> <amount> ─────────────────────────

@admin_only
async def admin_setcredits_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_setcredits — Set absolute bonus credit value (overrides existing)."""
    args = context.args or []
    if len(args) < 3:
        await update.effective_message.reply_text(
            "Usage: /admin_setcredits &lt;user_id&gt; &lt;chat|image&gt; &lt;amount&gt;\n\n"
            "<i>Sets the user's bonus credits to exactly &lt;amount&gt;.</i>\n"
            "Use /admin_addcredits to add on top of existing.",
            parse_mode="HTML",
        )
        return
    try:
        uid    = int(args[0])
        kind   = args[1].lower()
        amount = int(args[2])
        if kind not in ("chat", "image"):
            raise ValueError("type must be chat or image")
        if amount < 0:
            raise ValueError("amount must be >= 0")
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: set_bonus_credits(
                uid,
                chat=amount  if kind == "chat"  else None,
                image=amount if kind == "image" else None,
            ),
        )
        await update.effective_message.reply_text(
            f"✅ User <code>{uid}</code> bonus {kind} credits set to <b>{amount}</b>.",
            parse_mode="HTML",
        )
        log.info("Admin setcredits: user=%s kind=%s amount=%s", uid, kind, amount)
    except ValueError as exc:
        await update.effective_message.reply_text(f"❌ {html.escape(str(exc))}")


# ── /admin_resetuser <user_id> ────────────────────────────────────────────────

@admin_only
async def admin_resetuser_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_resetuser — Reset a user's daily chat + image usage to 0."""
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: /admin_resetuser &lt;user_id&gt;", parse_mode="HTML"
        )
        return
    try:
        uid = int(args[0])
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, reset_daily_usage, uid)
        await update.effective_message.reply_text(
            f"✅ Daily usage reset for user <code>{uid}</code>.\n"
            f"Their chat_today and image_today are now 0.",
            parse_mode="HTML",
        )
        log.info("Admin reset daily usage: user=%s", uid)
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── /admin_clearlogs ──────────────────────────────────────────────────────────

@admin_only
async def admin_clearlogs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_clearlogs — Delete all rows from the error_logs table."""
    loop = asyncio.get_running_loop()
    count = await loop.run_in_executor(None, clear_error_logs)
    await update.effective_message.reply_text(
        f"🗑️ <b>Error logs cleared.</b>\n\nDeleted <b>{count}</b> log entries.",
        parse_mode="HTML",
        reply_markup=back_to_menu(),
    )
    log.info("Admin cleared error logs (%d entries)", count)


# ── /admin_addadmin <user_id> — OWNER ONLY ────────────────────────────────────

@admin_only
async def admin_addadmin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_addadmin — Promote a user to admin (owner only)."""
    from config.settings import is_owner
    user = update.effective_user
    if not is_owner(user.id):
        await update.effective_message.reply_text(
            "👑 <b>Owner-only command.</b>\n\nOnly the primary bot owner can add admins.",
            parse_mode="HTML",
        )
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: /admin_addadmin &lt;user_id&gt;", parse_mode="HTML"
        )
        return
    try:
        uid = int(args[0])
        if is_admin(uid):
            await update.effective_message.reply_text(
                f"ℹ️ User <code>{uid}</code> is already an admin.", parse_mode="HTML"
            )
            return
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, lambda: add_admin_account(uid, user.id))
        if ok:
            await update.effective_message.reply_text(
                f"✅ User <code>{uid}</code> is now a <b>secondary admin</b>.\n\n"
                f"They can use all /admin commands but cannot add/remove other admins.",
                parse_mode="HTML",
            )
            log.info("Owner %s promoted user %s to admin", user.id, uid)
        else:
            await update.effective_message.reply_text("❌ Failed to add admin. Check logs.")
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── /admin_removeadmin <user_id> — OWNER ONLY ─────────────────────────────────

@admin_only
async def admin_removeadmin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_removeadmin — Revoke admin access (owner only)."""
    from config.settings import is_owner
    user = update.effective_user
    if not is_owner(user.id):
        await update.effective_message.reply_text(
            "👑 <b>Owner-only command.</b>", parse_mode="HTML"
        )
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage: /admin_removeadmin &lt;user_id&gt;", parse_mode="HTML"
        )
        return
    try:
        uid = int(args[0])
        if is_owner(uid):
            await update.effective_message.reply_text(
                "❌ Cannot remove the primary owner from admin."
            )
            return
        loop = asyncio.get_running_loop()
        ok = await loop.run_in_executor(None, lambda: remove_admin_account(uid))
        if ok:
            await update.effective_message.reply_text(
                f"✅ Admin access revoked for user <code>{uid}</code>.",
                parse_mode="HTML",
            )
            log.info("Owner %s revoked admin from user %s", user.id, uid)
        else:
            await update.effective_message.reply_text("❌ Failed. Check logs.")
    except ValueError:
        await update.effective_message.reply_text("❌ Invalid user ID.")


# ── /admin_listadmins ─────────────────────────────────────────────────────────

@admin_only
async def admin_listadmins_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin_listadmins — List all admins (owner + secondary)."""
    from config.settings import is_owner, SECONDARY_ADMINS
    loop = asyncio.get_running_loop()
    secondary = await loop.run_in_executor(None, get_admin_accounts)

    lines = [
        f"🛡️ <b>Admin Accounts</b>\n",
        f"👑 <b>Owner</b> (primary, env-based):\n  <code>{ADMIN_USER_ID}</code>\n",
    ]

    if secondary:
        lines.append(f"🛡️ <b>Secondary Admins ({len(secondary)}):</b>")
        for row in secondary:
            uid = row.get("user_id")
            added_by = row.get("added_by", "?")
            joined = (row.get("created_at") or "")[:10]
            lines.append(f"  • <code>{uid}</code> — added by <code>{added_by}</code> on {joined}")
    else:
        lines.append("🛡️ <b>Secondary Admins:</b> none\n\n"
                     "<i>Use /admin_addadmin &lt;user_id&gt; to promote a user.</i>")

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=back_to_menu()
    )
