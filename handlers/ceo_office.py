"""
FundzAiBot — CEO Office Handler

Thin async bridge between Telegram message events and the synchronous
CEO Office service (services/ceo_office.py).

Session model:
  • CEO Office is exclusively for admin users (ADMIN_USER_ID + secondary admins).
  • A session starts via /ceo_office command, "🏢 CEO Office" button in /testaudit,
    or /schedule_meeting.
  • While a session is active, every private text message routes to TestAudit
    instead of the regular AI chat handler.
  • Sessions auto-expire after 30 min of idle.
  • Typing "exit", "quit", or /exit ends the session immediately.

Integration:
  • main.py calls handle_ceo_message() first; returns True if handled.
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
      • /schedule_meeting command handler
      • audit callback 'ceo:open'
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


# ── Keyboard helper ───────────────────────────────────────────────────────────

def _main_kbd() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 My Agenda",    callback_data="ceo:agenda"),
        InlineKeyboardButton("📊 Dashboard",    callback_data="audit:dashboard"),
        InlineKeyboardButton("🚪 Exit",         callback_data="ceo:exit"),
    ]])


def _session_kbd() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 Agenda",       callback_data="ceo:agenda"),
        InlineKeyboardButton("🚪 Exit Office",  callback_data="ceo:exit"),
    ]])


# ── /ceo_office command handler ───────────────────────────────────────────────

async def ceo_office_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ceo_office — Opens the CEO Office for the admin. Silently denied for non-admins."""
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    if _session_alive(user.id):
        await update.message.reply_text(
            "Already in here. Just type — I'm listening.\n"
            "Send <code>exit</code> when you're done.",
            parse_mode="HTML",
        )
        return

    start_ceo_session(user.id)

    await update.message.reply_text(
        "🏢 <b>CEO Office</b>\n\n"
        "What do you need?",
        parse_mode="HTML",
        reply_markup=_main_kbd(),
    )


# ── /schedule_meeting command handler ─────────────────────────────────────────

async def schedule_meeting_command_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    /schedule_meeting — Opens the CEO Office directly in meeting-scheduling mode.
    Shows upcoming agenda and prompts to book a new meeting.
    """
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    # Ensure CEO Office session is active
    if not _session_alive(user.id):
        start_ceo_session(user.id)

    # Show current agenda + booking instructions
    loop = asyncio.get_running_loop()
    try:
        from services.meeting_manager import get_upcoming_meetings, format_agenda
        meetings = await loop.run_in_executor(None, lambda: get_upcoming_meetings(limit=10))
        agenda_text = format_agenda(meetings)
    except Exception as exc:
        log.warning("schedule_meeting_command: could not load agenda: %s", exc)
        agenda_text = "📅 <b>Meeting Agenda</b>\n\nCould not load meetings right now."

    kbd = InlineKeyboardMarkup([[
        InlineKeyboardButton("📅 Refresh Agenda", callback_data="ceo:agenda"),
        InlineKeyboardButton("🚪 Exit Office",    callback_data="ceo:exit"),
    ]])

    await update.message.reply_text(
        f"{agenda_text}\n\n"
        "—\n"
        "To schedule a new meeting, just tell me:\n"
        "<i>\"Schedule a product review for Monday at 3pm\"</i>\n"
        "<i>\"Book a strategy call for July 15 at 14:00 to discuss the roadmap\"</i>",
        parse_mode="HTML",
        reply_markup=kbd,
    )


# ── Agenda callback (ceo:agenda) ──────────────────────────────────────────────

async def handle_agenda_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handles the 📅 Agenda inline button — shows upcoming meetings."""
    query = update.callback_query
    if not query:
        return
    await query.answer()

    user = query.from_user
    if not user or not is_admin(user.id):
        return

    loop = asyncio.get_running_loop()
    try:
        from services.meeting_manager import get_upcoming_meetings, format_agenda
        meetings = await loop.run_in_executor(None, lambda: get_upcoming_meetings(limit=10))
        agenda_text = format_agenda(meetings)
    except Exception as exc:
        log.warning("handle_agenda_callback: %s", exc)
        agenda_text = "Could not load agenda right now. Try again in a moment."

    kbd = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Refresh",       callback_data="ceo:agenda"),
        InlineKeyboardButton("🚪 Exit Office",   callback_data="ceo:exit"),
    ]])

    try:
        await query.edit_message_text(
            agenda_text,
            parse_mode="HTML",
            reply_markup=kbd,
        )
    except Exception:
        await query.message.reply_text(agenda_text, parse_mode="HTML", reply_markup=kbd)


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
                "👋 Office closed. Use /ceo_office to come back.",
                parse_mode="HTML",
            )
            return True
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
            "Something went wrong on my end. Give me a moment and try again.",
            parse_mode="HTML",
        )
        return True

    if not response:
        return True

    try:
        await message.reply_text(response, parse_mode="HTML", reply_markup=_session_kbd())
    except Exception:
        # Fallback: strip markup and retry if Telegram rejects the HTML
        try:
            await message.reply_text(response, reply_markup=_session_kbd())
        except Exception as exc2:
            log.warning("CEO Office reply failed: %s", exc2)

    return True
