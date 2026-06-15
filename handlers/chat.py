"""
FundzAiBot — AI chat handler.
Handles free-text messages and /chat command.
Uses Supabase-persisted conversation memory.
"""

import asyncio
import html

from telegram import Update
from telegram.ext import ContextTypes

from config.settings import is_admin, FEATURE_FLAGS, WEB_SEARCH_ENABLED, WEB_SEARCH_MAX_RESULTS
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

    log.info("[CHAT] STAGE 1 — message received: user=%s text=%.60r", uid, text)

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

    thinking = None
    try:
        loop = asyncio.get_running_loop()

        # ── STAGE 2: Load/create user from DB ─────────────────────────────────
        log.info("[CHAT] STAGE 2 — loading user from DB: user=%s", uid)
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

        # ── STAGE 3: Credit check ──────────────────────────────────────────────
        log.info("[CHAT] STAGE 3 — checking credits: user=%s", uid)
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

        # ── STAGE 4: Send typing + thinking indicator ──────────────────────────
        log.info("[CHAT] STAGE 4 — sending thinking indicator: user=%s", uid)
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
        thinking = await message.reply_text("💭 <i>Thinking…</i>", parse_mode="HTML")

        # ── STAGE 5: Load conversation history ────────────────────────────────
        log.info("[CHAT] STAGE 5 — loading conversation history: user=%s", uid)
        history = await loop.run_in_executor(None, get_conversation, uid, 20)
        if not any(m["role"] == "system" for m in history):
            style = db_user.get("ai_style", "default")
            await loop.run_in_executor(None, set_system_prompt, uid, style)
            history = await loop.run_in_executor(None, get_conversation, uid, 20)

        messages_for_ai = history + [{"role": "user", "content": prompt}]
        log.info("[CHAT] STAGE 5 — history loaded: %d messages", len(messages_for_ai))

        # ── STAGE 5b: Web search context injection ─────────────────────────────
        # Auto-inject live web results for time-sensitive or current-events queries.
        # DuckDuckGo search — no API key required.
        if FEATURE_FLAGS.get("web_search_enabled", True) and WEB_SEARCH_ENABLED:
            try:
                from services.web_search import should_search, search_web, format_search_context, extract_urls, fetch_url_text, format_url_context
                urls = extract_urls(prompt)
                if urls:
                    url_text = await loop.run_in_executor(None, fetch_url_text, urls[0])
                    if url_text:
                        ctx = format_url_context(urls[0], url_text)
                        messages_for_ai = [{"role": "system", "content": ctx}] + messages_for_ai
                        log.info("[CHAT] URL context injected: %s", urls[0][:60])
                elif should_search(prompt):
                    results = await loop.run_in_executor(None, search_web, prompt, WEB_SEARCH_MAX_RESULTS)
                    if results:
                        ctx = format_search_context(results, prompt)
                        messages_for_ai = [{"role": "system", "content": ctx}] + messages_for_ai
                        log.info("[CHAT] Web search context injected: %d results", len(results))
            except Exception as _ws_exc:
                log.debug("[CHAT] Web search skipped: %s", _ws_exc)

        # ── STAGE 6: Call AI provider ──────────────────────────────────────────
        log.info("[CHAT] STAGE 6 — calling AI provider: user=%s", uid)
        response, provider = await loop.run_in_executor(None, get_ai_response, messages_for_ai)
        log.info("[CHAT] STAGE 6 — AI response received: provider=%s len=%d", provider, len(response))

        if not response or not response.strip():
            log.error("[CHAT] STAGE 6 — AI returned empty response: user=%s", uid)
            response = "⚠️ AI returned an empty response. Please try again."

        # ── STAGE 7: Persist conversation ──────────────────────────────────────
        log.info("[CHAT] STAGE 7 — saving conversation: user=%s", uid)
        await loop.run_in_executor(None, save_message, uid, "user", prompt)
        await loop.run_in_executor(None, save_message, uid, "assistant", response)
        await loop.run_in_executor(None, increment_chat, uid)

        # ── STAGE 8: Send reply ────────────────────────────────────────────────
        log.info("[CHAT] STAGE 8 — sending reply: user=%s chunks=%d", uid, len(chunk_text(response)))
        try:
            await thinking.delete()
        except Exception:
            pass
        thinking = None

        reply_markup = admin_main_menu() if admin else main_menu()
        chunks = chunk_text(response, size=4000)

        if not chunks:
            log.error("[CHAT] STAGE 8 — chunk_text returned empty list: user=%s", uid)
            await message.reply_text(
                "⚠️ Received an empty response. Please try again.",
                reply_markup=reply_markup,
            )
            return

        sent_any = False
        for i, chunk in enumerate(chunks):
            is_last = i == len(chunks) - 1
            try:
                await message.reply_text(
                    chunk,
                    reply_markup=reply_markup if is_last else None,
                )
                sent_any = True
            except Exception as exc:
                log.error("[CHAT] STAGE 8 — failed to send chunk %d: %s", i, exc)

        if not sent_any:
            log.error("[CHAT] STAGE 8 — all chunks failed to send: user=%s", uid)
            try:
                await message.reply_text(
                    "⚠️ Failed to deliver the AI response. Please try again.",
                    reply_markup=reply_markup,
                )
            except Exception as exc:
                log.error("[CHAT] STAGE 8 — fallback reply also failed: %s", exc)

        log.info("[CHAT] DONE: user=%s admin=%s provider=%s len=%d", uid, admin, provider, len(response))

    except Exception as exc:
        log.error("[CHAT] UNHANDLED EXCEPTION: user=%s error=%s", uid, exc, exc_info=True)
        try:
            await loop.run_in_executor(
                None,
                lambda: log_error("chat_handler_crash", str(exc)[:500], user_id=uid),
            )
        except Exception:
            pass
        if thinking:
            try:
                await thinking.delete()
            except Exception:
                pass
        try:
            reply_markup = admin_main_menu() if admin else main_menu()
            await message.reply_text(
                "⚠️ <b>Something went wrong processing your message.</b>\n\n"
                "Please try again in a moment. If the issue persists, use /help for support.",
                parse_mode="HTML",
                reply_markup=reply_markup,
            )
        except Exception as final_exc:
            log.error("[CHAT] Could not send error fallback: %s", final_exc)


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
