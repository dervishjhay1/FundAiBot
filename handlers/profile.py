"""
FundzAiBot — User profile, stats, referral, and history handlers.
Admin sees a special profile with "🛡️ Admin" badge and no VIP/credit display.
"""

import asyncio
from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS
from services.database import (
    get_or_create_user, get_credits, get_referral_count,
    get_image_history, get_referrals,
)
from utils.helpers import time_ago, format_number, progress_bar
from utils.keyboards import back_to_menu, main_menu, admin_main_menu
from utils.logger import get_logger

log = get_logger(__name__)


async def profile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/profile — full user stats card."""
    user = update.effective_user
    if not user:
        return

    uid = user.id
    loop = asyncio.get_running_loop()

    # ── Admin profile ──────────────────────────────────────────────────────────
    if is_admin(uid):
        ff = FEATURE_FLAGS
        text = (
            f"🛡️ <b>Admin Profile</b>\n\n"
            f"<b>Name:</b> {user.first_name or 'N/A'}\n"
            f"<b>User ID:</b> <code>{uid}</code>\n"
            f"<b>Role:</b> 🛡️ Administrator\n"
            f"<b>Access:</b> Unlimited (no daily limits)\n\n"
            f"<b>⚙️ Bot Feature Flags:</b>\n"
            f"  💬 Chat:       {'✅ ON' if ff['chat_enabled']      else '❌ OFF'}\n"
            f"  🎨 Images:     {'✅ ON' if ff['image_enabled']     else '❌ OFF'}\n"
            f"  🌐 New Users:  {'✅ ON' if ff['new_users_enabled'] else '❌ OFF'}\n"
            f"  🚧 Maintenance:{'🚧 ON' if ff['maintenance_mode']  else '✅ OFF'}\n\n"
            f"<i>Use /admin to manage all bot controls.</i>"
        )
        await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=admin_main_menu())
        return

    # ── Regular user profile ───────────────────────────────────────────────────
    db_user = await loop.run_in_executor(
        None,
        lambda: get_or_create_user(uid, first_name=user.first_name or "", username=user.username or ""),
    )
    credits = await loop.run_in_executor(None, get_credits, uid)
    ref_count = await loop.run_in_executor(None, get_referral_count, uid)

    from config.settings import FREE_DAILY_CHAT, FREE_DAILY_IMAGE, VIP_DAILY_CHAT, VIP_DAILY_IMAGE
    is_vip = db_user.get("is_vip", False)
    chat_limit = VIP_DAILY_CHAT if is_vip else FREE_DAILY_CHAT
    img_limit = VIP_DAILY_IMAGE if is_vip else FREE_DAILY_IMAGE
    chat_bonus = credits.get("bonus_chat", 0)
    img_bonus = credits.get("bonus_image", 0)
    chat_used = credits.get("chat_today", 0)
    img_used = credits.get("image_today", 0)
    chat_total_limit = chat_limit + chat_bonus
    img_total_limit = img_limit + img_bonus

    vip_badge = f"💎 VIP ({db_user.get('vip_tier', '').capitalize()})" if is_vip else "🆓 Free"
    style = db_user.get("ai_style", "default").capitalize()
    joined = (db_user.get("created_at") or "")[:10] or "Unknown"

    text = (
        f"👤 <b>Your Profile</b>\n\n"
        f"<b>Name:</b> {user.first_name or 'N/A'} {user.last_name or ''}\n"
        f"<b>Username:</b> {'@' + user.username if user.username else 'N/A'}\n"
        f"<b>User ID:</b> <code>{uid}</code>\n"
        f"<b>Account:</b> {vip_badge}\n"
        f"<b>Joined:</b> {joined}\n"
        f"<b>AI Style:</b> {style}\n\n"
        f"<b>📊 Daily Usage:</b>\n"
        f"  💬 Chat:  {chat_used}/{chat_total_limit}  {progress_bar(chat_used, chat_total_limit)}\n"
        f"  🎨 Image: {img_used}/{img_total_limit}  {progress_bar(img_used, img_total_limit)}\n\n"
        f"<b>🏆 All-Time:</b>\n"
        f"  💬 Chats: {format_number(credits.get('chat_total', 0))}\n"
        f"  🎨 Images: {format_number(credits.get('image_total', 0))}\n"
        f"  🔗 Referrals: {ref_count}\n\n"
        f"<b>💳 Bonus Credits:</b>\n"
        f"  +{chat_bonus} chat  +{img_bonus} image\n\n"
        f"<b>🔑 Referral Code:</b> <code>{db_user.get('referral_code', '')}</code>"
    )

    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=back_to_menu())
    log.info("/profile user=%s", uid)


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats — alias for profile."""
    await profile_handler(update, context)


async def referral_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/referral — personal referral link and stats. Admin cannot use referrals."""
    user = update.effective_user
    if not user:
        return

    if is_admin(user.id):
        await update.effective_message.reply_text(
            "🛡️ <b>Admin accounts cannot use the referral system.</b>\n\n"
            "The referral program is for regular users only.",
            parse_mode="HTML",
            reply_markup=admin_main_menu(),
        )
        return

    loop = asyncio.get_running_loop()
    db_user = await loop.run_in_executor(None, get_or_create_user, user.id)
    ref_code = db_user.get("referral_code", f"REF{user.id}")
    ref_count = await loop.run_in_executor(None, get_referral_count, user.id)
    referrals = await loop.run_in_executor(None, get_referrals, user.id)

    bot_info = await context.bot.get_me()
    link = f"https://t.me/{bot_info.username}?start={ref_code}"

    recent = ""
    if referrals:
        recent = "\n\n<b>Recent Referrals:</b>\n"
        for r in referrals[:5]:
            recent += f"  • User {r.get('referred_id')} — {time_ago(r.get('created_at', ''))}\n"

    text = (
        f"🔗 <b>Your Referral Link</b>\n\n"
        f"<code>{link}</code>\n\n"
        f"<b>How it works:</b>\n"
        f"1. Share your link with friends\n"
        f"2. When they join, you both benefit!\n"
        f"3. You earn: <b>+10 chat & +2 image</b> credits per referral\n\n"
        f"<b>📊 Your Stats:</b>\n"
        f"  🔗 Total referrals: <b>{ref_count}</b>\n"
        f"  💳 Bonus earned: +{ref_count * 10} chat, +{ref_count * 2} image"
        f"{recent}"
    )

    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=back_to_menu())
    log.info("/referral user=%s refs=%d", user.id, ref_count)


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/history — recent image generation history."""
    user = update.effective_user
    if not user:
        return

    loop = asyncio.get_running_loop()
    images = await loop.run_in_executor(None, lambda: get_image_history(user.id, limit=10))
    if not images:
        await update.effective_message.reply_text(
            "📭 <b>No image history yet.</b>\n\nUse /image to generate your first image!",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    lines = ["🖼️ <b>Your Recent Images:</b>\n"]
    for i, img in enumerate(images, 1):
        lines.append(
            f"{i}. <b>{img.get('style', '').capitalize()}</b> — "
            f"{img.get('prompt', '')[:60]}…\n"
            f"   <i>{time_ago(img.get('created_at', ''))}</i>"
        )

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=back_to_menu()
    )
