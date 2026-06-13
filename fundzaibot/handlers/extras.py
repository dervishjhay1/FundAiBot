"""
FundzAiBot — Extra commands: /feedback, /leaderboard, /streak.
These were part of the original bot and are preserved here.
"""

import asyncio
from datetime import datetime, date

from telegram import Update
from telegram.ext import ContextTypes

from services.database import get_or_create_user, get_referral_count, get_all_users, get_credits
from utils.keyboards import back_to_menu, main_menu
from utils.logger import get_logger

log = get_logger(__name__)


# ── /feedback ─────────────────────────────────────────────────────────────────

async def feedback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/feedback — let users send a message to the admin."""
    user = update.effective_user
    if not user:
        return

    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "📝 <b>Send Feedback</b>\n\n"
            "Use this command to send a suggestion, bug report, or message to the admin:\n\n"
            "<code>/feedback Your message here</code>\n\n"
            "<i>All feedback is read personally. Thank you! 🙏</i>",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    message = " ".join(args)
    if len(message) > 1000:
        await update.effective_message.reply_text("❌ Feedback too long (max 1000 characters).")
        return

    from config.settings import ADMIN_USER_ID
    from html import escape

    # Forward to admin
    if ADMIN_USER_ID:
        try:
            name = escape(user.first_name or "")
            uname = f"@{escape(user.username)}" if user.username else ""
            admin_text = (
                f"📩 <b>New Feedback</b>\n\n"
                f"From: {name} {uname} (<code>{user.id}</code>)\n\n"
                f"<i>{escape(message)}</i>"
            )
            await context.bot.send_message(chat_id=ADMIN_USER_ID, text=admin_text, parse_mode="HTML")
        except Exception as exc:
            log.warning("Could not forward feedback to admin: %s", exc)

    await update.effective_message.reply_text(
        "✅ <b>Feedback sent!</b>\n\nThank you for helping improve FundzAiBot. 🙏",
        parse_mode="HTML",
        reply_markup=back_to_menu(),
    )
    log.info("/feedback from user=%s: %s", user.id, message[:60])


# ── /leaderboard ──────────────────────────────────────────────────────────────

async def leaderboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/leaderboard — top referrers."""
    loop = asyncio.get_running_loop()
    users = await loop.run_in_executor(None, lambda: get_all_users(limit=200))

    # Compute referral counts per user
    from services.database import get_referral_count
    scored = []
    for u in users[:50]:
        uid = u.get("user_id")
        if not uid:
            continue
        count = await loop.run_in_executor(None, get_referral_count, uid)
        if count > 0:
            scored.append((uid, u.get("first_name", "User"), count))

    scored.sort(key=lambda x: x[2], reverse=True)
    top = scored[:10]

    if not top:
        await update.effective_message.reply_text(
            "🏆 <b>Referral Leaderboard</b>\n\n"
            "No referrals yet! Be the first.\n\n"
            "Use /referral to get your link and start earning bonus credits!",
            parse_mode="HTML",
            reply_markup=back_to_menu(),
        )
        return

    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = ["🏆 <b>Top Referrers</b>\n"]
    user = update.effective_user
    in_top = False

    for i, (uid, name, count) in enumerate(top):
        badge = medals[i]
        highlight = " ← you" if user and uid == user.id else ""
        if user and uid == user.id:
            in_top = True
        from html import escape
        lines.append(f"{badge} {escape(name[:20])} — <b>{count}</b> referrals{highlight}")

    if user and not in_top:
        my_count = await loop.run_in_executor(None, get_referral_count, user.id)
        lines.append(f"\n👤 You: <b>{my_count}</b> referrals")

    lines.append("\n<i>Invite friends with /referral to earn +10 chat & +2 image credits each!</i>")

    await update.effective_message.reply_text(
        "\n".join(lines), parse_mode="HTML", reply_markup=back_to_menu()
    )


# ── /streak ───────────────────────────────────────────────────────────────────

async def streak_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/streak — show user's daily chat streak and all-time usage."""
    user = update.effective_user
    if not user:
        return

    loop = asyncio.get_running_loop()
    credits = await loop.run_in_executor(None, get_credits, user.id)
    db_user = await loop.run_in_executor(None, get_or_create_user, user.id)

    chat_total = credits.get("chat_total", 0)
    image_total = credits.get("image_total", 0)
    chat_today = credits.get("chat_today", 0)

    # Streak: count consecutive days with at least 1 chat
    # We approximate from last_reset date vs today
    last_reset = credits.get("last_reset") or str(date.today())
    try:
        last_date = date.fromisoformat(str(last_reset)[:10])
        days_since = (date.today() - last_date).days
        streak = 1 if days_since == 0 and chat_today > 0 else 0
    except Exception:
        streak = 0

    # Determine activity tier
    if chat_total >= 1000:
        tier, tier_emoji = "Legend", "🌟"
    elif chat_total >= 500:
        tier, tier_emoji = "Expert", "💎"
    elif chat_total >= 100:
        tier, tier_emoji = "Active", "⭐"
    elif chat_total >= 10:
        tier, tier_emoji = "Regular", "🙂"
    else:
        tier, tier_emoji = "Newcomer", "🌱"

    text = (
        f"🔥 <b>Your Activity Stats</b>\n\n"
        f"<b>Today:</b>\n"
        f"  💬 Chats: {chat_today}\n\n"
        f"<b>All-Time:</b>\n"
        f"  💬 Chats: {chat_total:,}\n"
        f"  🎨 Images: {image_total:,}\n\n"
        f"<b>Streak:</b> {'🔥 Active today!' if streak else '❄️ No chats yet today'}\n\n"
        f"<b>Tier:</b> {tier_emoji} {tier}\n\n"
        f"<i>Keep chatting daily to climb the ranks!</i>"
    )

    await update.effective_message.reply_text(text, parse_mode="HTML", reply_markup=back_to_menu())
    log.info("/streak user=%s total=%d", user.id, chat_total)
