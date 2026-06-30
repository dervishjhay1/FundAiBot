"""
FundzAiBot — CEO Office Handler (Phase 2)

Thin async bridge between main.py's _smart_text_handler and the synchronous
CEO Office service in services/ceo_office.py.

Session model:
  • CEO Office is exclusively for admin users (ADMIN_USER_ID + secondary admins).
  • A session starts when the admin clicks "🏢 CEO Office" in /testaudit, or
    sends the /ceo_office command.
  • While a session is active, every private text message routes to TestAudit
    instead of the regular AI chat handler.
  • Sessions auto-expire after 30 min of idle (mirrored from service layer).
  • Typing "exit", "quit", or /exit ends the session immediately.

Integration:
  • main.py calls handle_ceo_message() first; it returns True if handled.
  • callbacks.py routes ceo:open / ceo:exit inline-button presses here.
  • audit.py (testaudit_handler) calls start_ceo_session() from its
    "CEO Office" section callback.
"""

from __future__ import annotations

import asyncio
import time

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config.settings import is_admin
from utils.logger import get_logger

log = get_logger(__name__)

# ── Session state ─────────────────────────────────────────────────────────────

_active_sessions: set[int] = set()
_session_ts: dict[int, float] = {}
_SESSION_TTL = 1800  # 30 minutes — must match service layer _SESSION_IDLE_SECS

_EXIT_PHRASES = {"exit", "quit", "bye", "/exit", "/quit", "close", "done", "exit office"}


def _session_alive(user_id: int) -> bool:
    """Return True if user has an active, non-expired CEO Office session."""
    if user_id not in _active_sessions:
        return False
    if time.time() - _session_ts.get(user_id, 0) > _SESSION_TTL:
        _active_sessions.discard(user_id)
        _session_ts.pop(user_id, None)
        return False
    return True


def start_ceo_session(user_id: int) -> None:
    """
    Activate a CEO Office session for this user.
    Called from:
      • /ceo_office command handler
      • audit callback 'ceo:open'
      • any other entry-point that wants to hand off to CEO Office
    """
    _active_sessions.add(user_id)
    _session_ts[user_id] = time.time()
    log.info("CEO Office session started for user=%s", user_id)


def end_ceo_session(user_id: int) -> None:
    """Deactivate CEO Office session for this user."""
    _active_sessions.discard(user_id)
    _session_ts.pop(user_id, None)
    log.info("CEO Office session ended for user=%s", user_id)


def is_ceo_session_active(user_id: int) -> bool:
    """Public check — used by other handlers and callbacks."""
    return _session_alive(user_id)


# ── /ceo_office command handler ───────────────────────────────────────────────

async def ceo_office_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /ceo_office — Opens the CEO Office for the admin.
    Denied silently for non-admins.
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    if _session_alive(user.id):
        await update.message.reply_text(
            "🏢 <b>CEO Office is already open.</b>\n\n"
            "Just type — TestAudit is listening.\n"
            "Send <code>exit</code> to close the session.",
            parse_mode="HTML",
        )
        return

    start_ceo_session(user.id)

    kbd = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚪 Exit CEO Office", callback_data="ceo:exit"),
        InlineKeyboardButton("📊 /testaudit",      callback_data="audit:dashboard"),
    ]])

    await update.message.reply_text(
        "🏢 <b>CEO Office — Open</b>\n\n"
        "I'm TestAudit, your Operations Manager. Ask me anything:\n\n"
        "• Company health &amp; metrics\n"
        "• Product strategy &amp; roadmap\n"
        "• Community insights &amp; feedback\n"
        "• Register a product or bot token\n"
        "• Or just talk — I'm here\n\n"
        "<i>Session active (30-min idle timeout). Send <code>exit</code> to close.</i>",
        parse_mode="HTML",
        reply_markup=kbd,
    )


# ── Main message handler (called from _smart_text_handler in main.py) ─────────

async def handle_ceo_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Called for every private text message by _smart_text_handler.

    Returns True  → message was handled by CEO Office (caller must NOT fall through).
    Returns False → not a CEO Office session; caller falls through to chat_handler.
    """
    user = update.effective_user
    message = update.message
    if not user or not message or not message.text:
        return False

    user_id = user.id

    # CEO Office is admin-only
    if not is_admin(user_id):
        return False

    text = message.text.strip()

    # Handle exit commands before checking session
    if text.lower() in _EXIT_PHRASES:
        if _session_alive(user_id):
            end_ceo_session(user_id)
            await message.reply_text(
                "👋 <b>CEO Office closed.</b>\n\n"
                "You're back in regular mode.\n"
                "Use /ceo_office or /testaudit → CEO Office to return.",
                parse_mode="HTML",
            )
            return True
        # Not in a session — don't consume the message
        return False

    # Not in a CEO Office session → fall through to regular chat
    if not _session_alive(user_id):
        return False

    # ── Active session ─────────────────────────────────────────────────────────

    # Refresh session timestamp
    _session_ts[user_id] = time.time()

    # Record CEO activity for Autonomous Mode tracking
    try:
        from services.autonomous_mode import record_ceo_activity
        record_ceo_activity()
    except Exception:
        pass

    # Typing indicator
    try:
        await context.bot.send_chat_action(chat_id=message.chat_id, action="typing")
    except Exception:
        pass

    # Run synchronous CEO Office service in thread pool
    loop = asyncio.get_running_loop()
    try:
        from services.ceo_office import chat_with_ceo_office
        response = await loop.run_in_executor(
            None, lambda: chat_with_ceo_office(text)
        )
    except Exception as exc:
        log.error("CEO Office processing error for user=%s: %s", user_id, exc)
        await message.reply_text(
            "⚠️ <b>CEO Office error.</b>\n"
            "TestAudit hit a temporary issue. Please try again.",
            parse_mode="HTML",
        )
        return True

    if not response:
        return True

    kbd = InlineKeyboardMarkup([[
        InlineKeyboardButton("🚪 Exit CEO Office", callback_data="ceo:exit"),
        InlineKeyboardButton("📊 Dashboard",        callback_data="audit:dashboard"),
    ]])

    try:
        await message.reply_text(response, parse_mode="HTML", reply_markup=kbd)
    except Exception:
        # Fallback: strip markup and retry if Telegram rejects the HTML
        try:
            await message.reply_text(response, reply_markup=kbd)
        except Exception as exc2:
            log.warning("CEO Office reply failed: %s", exc2)

    return True
