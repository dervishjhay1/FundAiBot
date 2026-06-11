"""
FundzAiBot — AI chat handler.
Handles free-text messages and /chat command.
Uses Supabase-persisted conversation memory.
"""

import asyncio
import html

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS
from services.ai_service import get_ai_response
from services.database import (
    get_or_create_user, can_use_chat, increment_chat,
    save_message, get_conversation, set_system_prompt,
    log_error, check_and_fix_vip_expiry,
)
from utils.helpers import chunk_text, sanitise_prompt
from utils.keyboards import main_menu, admin_main_menu
from utils.rate_limiter import is_rate_limited, get_wait_time
from utils.logger import get_logger

log = get_logger(__name__)


async def chat_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Entry point for all plain-text AI chat messages."""
    user = update.effective_user
    message = update.effective_message
    if not user or not message:
        return

    text = (message.text or "").strip()
    if not text:
        return

    uid = user.id
    admin = is_admin(uid)

    # ── Maintenance mode — only admin can proceed ──────────────────────────────
    if FEATURE_FLAGS["maintenance_mode"] and not admin:
        await message.reply_text(
            "🚧 <b>FundzAiBot is under maintenance.</b>\n\n"
            "We'll be back shortly. Sorry for the wait!",
            parse_mode="HTML",
        )
        return

    # ── Feature flag: chat disabled ────────────────────────────────────────────
    if not FEATURE_FLAGS["chat_enabled"] and not admin:
        await message.reply_text(
            "💬 <b>AI Chat is temporarily disabled.</b>\n\n"
            "Check back soon!",
            parse_mode="HTML",
        )
        return

    # ── Rate limiting — admin is exempt ───────────────────────────────────────
    if not admin and is_rate_limited(uid):
        wait = get_wait_time(uid)
        await message.reply_text(
            f"⏳ <b>Slow down!</b> You're sending messages too fast.\n"
            f"Please wait <b>{wait}s</b> before your next message.",
            parse_mode="HTML",
        )
        return

    loop = asyncio.get_running_loop()

    # All DB calls are blocking — run in executor so we don't block the event loop
    db_user = await loop.run_in_executor(
        None,
        lambda: get_or_create_user(
            uid,
            first_name=user.first_name or "",
            last_name=user.last_name or "",
            username=user.username or "",
        ),
    )

    if db_user.get("is_banned"):
        await message.reply_text("🚫 You have been banned from using FundzAiBot.")
        return

    # Admin is never VIP-gated; check expiry for regular users
    is_vip = True if admin else await loop.run_in_executor(None, check_and_fix_vip_expiry, db_user)

    allowed, reason = await loop.run_in_executor(None, can_use_chat, uid, is_vip)
    if not allowed:
        await message.reply_text(
            f"❌ <b>{html.escape(reason)}</b>\n\n"
            "💡 Earn more credits:\n"
            "• Invite friends with /referral (+10 chats each)\n"
            "• Upgrade to 💎 VIP for 500+/day",
            parse_mode="HTML",
            reply_markup=main_menu(),
        )
        return

    prompt = sanitise_prompt(text)

    # Show typing indicator + loading message
    await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    thinking = await message.reply_text("💭 <i>Thinking…</i>", parse_mode="HTML")

    # Build conversation history from Supabase (blocking — run in executor)
    history = await loop.run_in_executor(None, get_conversation, uid, 20)
    if not any(m["role"] == "system" for m in history):
        style = db_user.get("ai_style", "default")
        await loop.run_in_executor(None, set_system_prompt, uid, style)
        history = await loop.run_in_executor(None, get_conversation, uid, 20)

    # Add user message to history for this request
    messages_for_ai = history + [{"role": "user", "content": prompt}]

    # ── CRITICAL: AI call is synchronous (requests) — must run in executor ──────
    response, provider = await loop.run_in_executor(None, get_ai_response, messages_for_ai)

    # Persist both turns to Supabase (non-blocking in executor)
    await loop.run_in_executor(None, save_message, uid, "user", prompt)
    await loop.run_in_executor(None, save_message, uid, "assistant", response)

    # Deduct credit (admin usage is tracked but not limited)
    await loop.run_in_executor(None, increment_chat, uid)

    try:
        await thinking.delete()
    except Exception:
        pass

    # Use admin main menu for admin, regular menu otherwise
    reply_markup = admin_main_menu() if admin else main_menu()

    chunks = chunk_text(response, size=4000)
    for i, chunk in enumerate(chunks):
        is_last = i == len(chunks) - 1
        try:
            await message.reply_text(
                chunk,
                reply_markup=reply_markup if is_last else None,
            )
        except Exception as exc:
            log.warning("Failed to send chunk %d: %s", i, exc)

    log.info("Chat: user=%s admin=%s provider=%s len=%d", uid, admin, provider, len(response))


async def clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clear — wipe the user's conversation history from Supabase."""
    from services.database import clear_conversation
    user = update.effective_user
    if not user:
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, clear_conversation, user.id)
    markup = admin_main_menu() if is_admin(user.id) else main_menu()
    await update.effective_message.reply_text(
        "🧹 <b>Conversation history cleared!</b>\n\nStarting fresh — what's on your mind?",
        parse_mode="HTML",
        reply_markup=markup,
    )
    log.info("History cleared: user=%s", user.id)
