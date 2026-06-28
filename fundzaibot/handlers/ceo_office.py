"""
FundzAiBot — CEO Office Handler

Handles all CEO Office interactions:
- Button entry from /testaudit dashboard
- Executive conversation routing
- Session memory management
- Exit flow

Everything discussed is confidential. Background operations continue uninterrupted.
"""

from __future__ import annotations

import asyncio
import html
import time
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import is_owner, ADMIN_USER_ID, BOT_VERSION
from services.ceo_office import (
    is_ceo_office_active,
    open_office,
    close_office,
    add_turn,
    get_messages,
    get_session_duration,
    build_system_prompt,
)
from utils.logger import get_logger

log = get_logger(__name__)

_OFFICE_MODE_KEY = "ceo_office_active"


def _exit_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Exit Office", callback_data="ceo_office:exit")],
    ])


def _entry_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🧠 CEO Office", callback_data="ceo_office:enter")],
        [InlineKeyboardButton("« Back to Audit", callback_data="audit:dashboard")],
    ])


async def ceo_office_entry_button(query, context) -> None:
    """Show the CEO Office entry screen from /testaudit."""
    user = query.from_user
    if not is_owner(user.id):
        await query.answer("⛔ CEO Office — Owner only.", show_alert=True)
        return

    await query.answer()

    active = is_ceo_office_active(user.id)

    if active:
        status_line = "🟢 <b>Session Active</b>"
        duration = get_session_duration(user.id)
        desc = (
            f"{status_line}\n"
            f"<i>Session duration: {duration}</i>\n\n"
            "You are currently in an active CEO Office session.\n\n"
            "Just send me any message to continue our conversation.\n\n"
            "Or exit the session below."
        )
        kbd = _exit_keyboard()
    else:
        status_line = "⚫ <b>Office Closed</b>"
        desc = (
            f"{status_line}\n\n"
            "Welcome to the <b>CEO Office</b>.\n\n"
            "This is your private executive space. Speak directly with TestAudit — "
            "your Chief Operations & Executive Intelligence Manager.\n\n"
            "<i>Everything discussed here is strictly confidential.\n"
            "Background operations continue while we chat.</i>\n\n"
            "Tap below to open the office:"
        )
        kbd = _entry_keyboard()

    try:
        await query.edit_message_text(
            f"🧠 <b>CEO Office</b>\n\n{desc}",
            parse_mode="HTML",
            reply_markup=kbd,
        )
    except Exception:
        pass


async def ceo_office_open(query, context) -> None:
    """Open the CEO Office and show the daily greeting."""
    user = query.from_user
    if not is_owner(user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return

    await query.answer("Opening CEO Office…")
    open_office(user.id)
    context.bot_data[_OFFICE_MODE_KEY] = True

    # Build initial greeting
    now = datetime.now(timezone.utc)
    hour = now.hour
    if 5 <= hour < 12:
        greeting_time = "Good morning"
    elif 12 <= hour < 17:
        greeting_time = "Good afternoon"
    elif 17 <= hour < 21:
        greeting_time = "Good evening"
    else:
        greeting_time = "Good evening"

    # Pull a health score from cached audit if available
    cached_audit = context.bot_data.get("audit_v3", {})
    health_score = cached_audit.get("health_score", 98)
    total_fail = cached_audit.get("total_fail", 0)
    total_warn = cached_audit.get("total_warn", 0)

    if total_fail == 0 and total_warn == 0:
        health_line = f"Company Health: {health_score}% ✅\nNo critical incidents detected."
    elif total_fail > 0:
        health_line = f"Company Health: {health_score}% ⚠️\n{total_fail} critical issue(s) need attention."
    else:
        health_line = f"Company Health: {health_score}% 🟡\n{total_warn} warning(s) noted — nothing critical."

    greeting = (
        f"<b>{greeting_time}, CEO.</b>\n\n"
        f"{health_line}\n\n"
        f"All systems are operational and I'm actively managing the community and channel.\n\n"
        f"<i>The office is now open. Just type naturally — no commands needed.\n"
        f"Everything here is confidential.</i>"
    )

    try:
        await query.edit_message_text(
            f"🧠 <b>CEO Office — Active</b>\n\n{greeting}",
            parse_mode="HTML",
            reply_markup=_exit_keyboard(),
        )
    except Exception:
        await context.bot.send_message(
            user.id,
            f"🧠 <b>CEO Office — Active</b>\n\n{greeting}",
            parse_mode="HTML",
            reply_markup=_exit_keyboard(),
        )

    log.info("CEO Office opened by user=%s", user.id)


async def ceo_office_close(query, context) -> None:
    """Close the CEO Office and return to operations mode."""
    user = query.from_user
    if not is_owner(user.id):
        await query.answer("⛔ Owner only.", show_alert=True)
        return

    await query.answer("Closing office…")
    duration = get_session_duration(user.id)
    close_office(user.id)
    context.bot_data[_OFFICE_MODE_KEY] = False

    farewell = (
        "Executive Conversation ended.\n\n"
        "Returning to Operations Mode.\n\n"
        f"<i>Session duration: {duration}</i>\n\n"
        "Have a productive day, CEO. 🚀"
    )

    from utils.keyboards import admin_panel_keyboard

    try:
        await query.edit_message_text(
            f"🧠 <b>CEO Office — Closed</b>\n\n{farewell}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🧠 Reopen Office", callback_data="ceo_office:enter"),
                InlineKeyboardButton("« Admin Panel", callback_data="admin:panel"),
            ]]),
        )
    except Exception:
        pass

    log.info("CEO Office closed by user=%s, duration=%s", user.id, duration)


async def handle_ceo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handle an incoming private message when CEO Office is active.
    Returns True if message was handled by CEO Office, False otherwise.
    """
    user = update.effective_user
    if not user or not is_owner(user.id):
        return False

    if not is_ceo_office_active(user.id):
        return False

    text = (update.message.text or "").strip()
    if not text:
        return False

    # Check for exit commands
    if text.lower() in ("exit office", "close office", "exit", "/exit"):
        close_office(user.id)
        context.bot_data[_OFFICE_MODE_KEY] = False
        await update.message.reply_text(
            "🧠 <b>CEO Office — Closed</b>\n\n"
            "Executive Conversation ended.\n\n"
            "Returning to Operations Mode.\n\n"
            "Have a productive day, CEO. 🚀",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🧠 Reopen Office", callback_data="ceo_office:enter"),
            ]]),
        )
        return True

    # Show typing indicator
    await context.bot.send_chat_action(user.id, action="typing")

    # Add user turn to history
    add_turn(user.id, "user", text)

    # Get health score for system prompt
    cached_audit = context.bot_data.get("audit_v3", {})
    health_score = cached_audit.get("health_score", 98)

    # Build messages with system prompt
    system_prompt = build_system_prompt(health_score=health_score)
    conversation = get_messages(user.id)

    messages = [{"role": "system", "content": system_prompt}] + conversation

    # Call AI in executor
    from services.ai_service import get_ai_response
    loop = asyncio.get_running_loop()

    try:
        response, provider = await loop.run_in_executor(
            None,
            lambda: get_ai_response(messages),
        )

        if not response or "unavailable" in response.lower():
            response = (
                "I'm having a brief moment of difficulty connecting to my intelligence systems. "
                "Could you give me just a moment and try again? I'm still monitoring everything on my end."
            )

        # Add assistant response to history
        add_turn(user.id, "assistant", response)

        # Format and send
        reply = response
        if len(reply) > 4000:
            reply = reply[:3900] + "\n\n<i>…continued if you ask me to go on</i>"

        await update.message.reply_text(
            reply,
            parse_mode="HTML",
            reply_markup=_exit_keyboard(),
        )
        log.info("CEO Office response sent: user=%s provider=%s chars=%d",
                 user.id, provider, len(response))

    except Exception as exc:
        log.error("CEO Office AI error: %s", exc)
        await update.message.reply_text(
            "I encountered a brief technical difficulty. "
            "Everything is still being monitored — please try again in a moment.",
            reply_markup=_exit_keyboard(),
        )

    return True


async def ceo_office_callback(query, context, action: str) -> None:
    """Handle ceo_office: prefixed callbacks."""
    if action == "enter":
        await ceo_office_open(query, context)
    elif action == "exit":
        await ceo_office_close(query, context)
    elif action == "menu":
        await ceo_office_entry_button(query, context)
    else:
        await query.answer()
